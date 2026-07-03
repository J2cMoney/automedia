"""评论抓取服务 - Phase 5 FLOW-5 自动回评论。

职责:
    用 Playwright 打开笔记/视频页,自主爬评论 DOM(不依赖 MediaCrawler)。
    Phase 3 A-2 已知 MediaCrawler 集成成本高,自主爬 selector 更可控,
    DOM 易变故每个平台 extractor 的 selector 多备选。

设计要点:
    1. extractor 策略模式:每平台一个 _extract_* 函数,EXTRACTORS 字典分派,
       支持注入(测试用)。
    2. fetch_comments 走 PublishContext(复用 cookie+stealth+持久 profile,
       与发布同源,登录态最真实,反检测最强)。
    3. 抓取流程:打开笔记页 → 等渲染 → 滚动加载更多 → 调 extractor 抽评论。
    4. 限流:单页最多抓 30 条(DOM 遍历上限),返回前 max_count 截断。

不解析精确 platform_comment_id(DOM 难稳定取到 id),留空让上层按文本去重;
2026 实测平台 DOM 不稳定,id 抓取成功率低,文本匹配更可靠(照 commenter 同思路)。
"""
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from app.models.account import Account, Platform
from app.models.content import Content
from app.services.publish.base import PublishContext

logger = logging.getLogger(__name__)


# ---------- 数据结构 ----------

@dataclass
class RawComment:
    """从平台抓到的原始评论(未回复)。

    platform_comment_id 留空(DOM 难稳定取),上层按 text 去重;
    author 可为空(部分 DOM 不暴露昵称节点)。
    """
    platform_comment_id: Optional[str]  # 平台侧评论 id(去重+定位用)
    author: Optional[str]               # 评论作者昵称
    text: str                           # 评论原文


class FetcherError(Exception):
    """评论抓取异常。"""


# 评论抽取策略函数类型:输入 page(已打开笔记页),输出 RawComment 列表
CommentExtractor = Callable[["object"], List[RawComment]]


# ---------- 各平台 extractor(selector 多备选,DOM 易变)----------

def _extract_xhs_comments(page) -> List[RawComment]:
    """小红书笔记评论抽取。selector 多备选(DOM 易变)。

    评论容器通常 [class*='comment-item'],作者 [class*='author']/[class*='name'],
    文本 [class*='content']/[class*='note-text']。
    """
    items = []
    # 多备选容器:优先精确 comment-item,退到 comment 区块下的 content
    nodes = page.query_selector_all(
        "[class*='comment-item'], [class*='comment'] [class*='content']"
    )
    for node in nodes[:30]:  # 最多抓 30 条防过多
        text = (node.inner_text() or "").strip()
        # 过滤空/过短(纯标点/换行噪声)
        if text and len(text) > 1:
            items.append(RawComment(platform_comment_id=None, author=None, text=text))
    return items


def _extract_dy_comments(page) -> List[RawComment]:
    """抖音视频评论抽取。selector 多备选。

    评论容器 [class*='comment-item']/[class*='CommentList'] 下的行,
    文本 [class*='content']/[class*='text']。
    """
    items = []
    nodes = page.query_selector_all(
        "[class*='comment-item'] [class*='content'], "
        "[class*='CommentList'] [class*='content'], "
        "[class*='comment'] [class*='text']"
    )
    for node in nodes[:30]:
        text = (node.inner_text() or "").strip()
        if text and len(text) > 1:
            items.append(RawComment(platform_comment_id=None, author=None, text=text))
    return items


def _extract_ks_comments(page) -> List[RawComment]:
    """快手视频评论抽取。selector 多备选。

    评论容器 [class*='comment-item']/[class*='comment-list'] 下的行,
    文本 [class*='content']/[class*='text']。
    """
    items = []
    nodes = page.query_selector_all(
        "[class*='comment-item'] [class*='content'], "
        "[class*='comment-list'] [class*='content'], "
        "[class*='comment'] [class*='text']"
    )
    for node in nodes[:30]:
        text = (node.inner_text() or "").strip()
        if text and len(text) > 1:
            items.append(RawComment(platform_comment_id=None, author=None, text=text))
    return items


# 平台 → extractor 分派表
EXTRACTORS: dict = {
    Platform.XHS: _extract_xhs_comments,
    Platform.DOUYIN: _extract_dy_comments,
    Platform.KUAISHOU: _extract_ks_comments,
}


# ---------- 抓取入口 ----------

def fetch_comments(
    account: Account,
    content: Content,
    *,
    extractor: Optional[CommentExtractor] = None,
    max_count: int = 20,
) -> List[RawComment]:
    """抓取某条 Content 对应帖子下的评论。

    用 PublishContext 打开笔记页(content.platform_post_url),调 extractor 抽评论。
    extractor 可注入(测试用),默认按 account.platform 选。

    Args:
        account: 账号(决定平台 + PublishContext 用其 cookie)
        content: 内容(取 platform_post_url)
        extractor: 评论抽取函数(注入用),None 则按平台从 EXTRACTORS 选
        max_count: 最多返回多少条(截断),默认 20

    Returns:
        RawComment 列表(最多 max_count 条)

    Raises:
        FetcherError: 无发布链接 / 无对应平台 extractor / 浏览器流程异常
    """
    if not content.platform_post_url:
        raise FetcherError("内容无发布链接(platform_post_url 为空),无法抓评论")

    ext = extractor or EXTRACTORS.get(account.platform)
    if ext is None:
        raise FetcherError(f"平台 {account.platform.value} 无评论抽取器")

    try:
        with PublishContext(account) as (page, _ctx):
            logger.info("[%s] 抓评论 content=%s url=%s",
                        account.platform.value, content.id, content.platform_post_url)
            page.goto(content.platform_post_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)  # 给评论渲染时间
            # 滚动加载更多评论(评论常懒加载,滚 3 次触发)
            for _ in range(3):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(800)
            raw = ext(page)
    except FetcherError:
        raise
    except Exception as e:
        # PublishContext / 浏览器流程异常统一转 FetcherError(上层编排只 catch 它)
        raise FetcherError(f"评论抓取浏览器流程失败: {e}") from e

    logger.info("[%s] 抓到 %d 条评论 content=%s",
                account.platform.value, len(raw), content.id)
    return raw[:max_count]


__all__ = [
    "RawComment",
    "FetcherError",
    "CommentExtractor",
    "EXTRACTORS",
    "fetch_comments",
]
