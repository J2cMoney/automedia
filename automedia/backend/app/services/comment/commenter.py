"""回评发送服务 - Phase 5 FLOW-5 自动回评论。

职责:
    用 Playwright 在笔记/视频页定位指定评论 → 点回复 → 填文本 → 提交。
    与 fetcher.py 同走 PublishContext(cookie+stealth+持久 profile)。

设计要点:
    1. replier 策略模式:每平台一个 _reply_* 函数,_REPLIERS 字典分派,
       支持注入(测试用)。
    2. selector 多备选(DOM 易变):评论行/回复按钮/输入框/发送按钮都备 2-3 个。
    3. 评论定位按文本匹配(平台 DOM 不稳定,id 抓取成功率低,文本更可靠)。
    4. 任何步骤失败抛 CommenterError,上层编排 catch 后跳过该条不阻塞其余。

2026 实测风险:平台 DOM 频繁变动,这里 selector 是经验值,需结合实跑调优;
回评人工抽检(Spec FLOW-5 护栏),不达标再修 prompt 或 selector。
"""
import logging

from app.models.account import Account, Platform
from app.models.content import Content
from app.services.publish.base import PublishContext, PublishError

logger = logging.getLogger(__name__)


class CommenterError(Exception):
    """回评发送异常。"""


# ---------- 各平台回评实现(selector 多备选)----------

def _reply_xhs(page, comment_text: str, reply_text: str) -> bool:
    """小红书回评:找含 comment_text 的评论 → 点回复按钮 → 填文本 → 提交。

    selector 多备选(DOM 易变)。Returns True=成功。
    找不到评论或发送失败抛 CommenterError。
    """
    # 1. 定位评论行:遍历所有评论容器,文本匹配(精确 id 抓不到,用文本)
    row_selectors = [
        "[class*='comment-item']",
        "[class*='comment'] [class*='content']",
    ]
    target_row = None
    for sel in row_selectors:
        for node in page.query_selector_all(sel):
            try:
                if comment_text and comment_text in (node.inner_text() or ""):
                    target_row = node
                    break
            except Exception:
                continue
        if target_row is not None:
            break

    if target_row is None:
        raise CommenterError(f"小红书未找到评论: {comment_text[:30]}")

    # 2. 点"回复"按钮(在该评论行内,多备选)
    reply_btn_selectors = [
        "text=回复",
        "[class*='reply']",
        "button:has-text('回复')",
    ]
    clicked = False
    for sel in reply_btn_selectors:
        try:
            btn = target_row.query_selector(sel) or page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        raise CommenterError("小红书回复按钮未点开")

    # 3. 填回复文本(contenteditable 或 textarea,多备选)
    page.wait_for_timeout(500)  # 等输入框展开
    input_selectors = [
        "[contenteditable='true']",
        "textarea[class*='reply']",
        "textarea[class*='input']",
        "textarea",
    ]
    filled = False
    for sel in input_selectors:
        try:
            box = page.query_selector(sel)
            if box and box.is_visible():
                box.click()
                box.type(reply_text, delay=30)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        raise CommenterError("小红书回复输入框未找到")

    # 4. 点发送(多备选)
    send_selectors = [
        "text=发送",
        "[class*='submit']",
        "button:has-text('发送')",
        "[class*='send']",
    ]
    for sel in send_selectors:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                logger.info("小红书回评已发送: %s", comment_text[:30])
                return True
        except Exception:
            continue
    raise CommenterError("小红书发送按钮未点中")


def _reply_dy(page, comment_text: str, reply_text: str) -> bool:
    """抖音回评:找含 comment_text 的评论 → 点回复 → 填文本 → 提交。selector 多备选。"""
    row_selectors = [
        "[class*='comment-item']",
        "[class*='CommentList'] [class*='content']",
        "[class*='comment'] [class*='text']",
    ]
    target_row = None
    for sel in row_selectors:
        for node in page.query_selector_all(sel):
            try:
                if comment_text and comment_text in (node.inner_text() or ""):
                    target_row = node
                    break
            except Exception:
                continue
        if target_row is not None:
            break

    if target_row is None:
        raise CommenterError(f"抖音未找到评论: {comment_text[:30]}")

    for sel in ["text=回复", "[class*='reply']", "button:has-text('回复')"]:
        try:
            btn = target_row.query_selector(sel) or page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                break
        except Exception:
            continue

    page.wait_for_timeout(500)
    filled = False
    for sel in ["[contenteditable='true']", "textarea[class*='input']", "textarea"]:
        try:
            box = page.query_selector(sel)
            if box and box.is_visible():
                box.click()
                box.type(reply_text, delay=30)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        raise CommenterError("抖音回复输入框未找到")

    for sel in ["text=发送", "[class*='submit']", "button:has-text('发送')"]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                logger.info("抖音回评已发送: %s", comment_text[:30])
                return True
        except Exception:
            continue
    raise CommenterError("抖音发送按钮未点中")


def _reply_ks(page, comment_text: str, reply_text: str) -> bool:
    """快手回评:找含 comment_text 的评论 → 点回复 → 填文本 → 提交。selector 多备选。"""
    row_selectors = [
        "[class*='comment-item']",
        "[class*='comment-list'] [class*='content']",
        "[class*='comment'] [class*='text']",
    ]
    target_row = None
    for sel in row_selectors:
        for node in page.query_selector_all(sel):
            try:
                if comment_text and comment_text in (node.inner_text() or ""):
                    target_row = node
                    break
            except Exception:
                continue
        if target_row is not None:
            break

    if target_row is None:
        raise CommenterError(f"快手未找到评论: {comment_text[:30]}")

    for sel in ["text=回复", "[class*='reply']", "button:has-text('回复')"]:
        try:
            btn = target_row.query_selector(sel) or page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                break
        except Exception:
            continue

    page.wait_for_timeout(500)
    filled = False
    for sel in ["[contenteditable='true']", "textarea[class*='input']", "textarea"]:
        try:
            box = page.query_selector(sel)
            if box and box.is_visible():
                box.click()
                box.type(reply_text, delay=30)
                filled = True
                break
        except Exception:
            continue
    if not filled:
        raise CommenterError("快手回复输入框未找到")

    for sel in ["text=发送", "[class*='submit']", "button:has-text('发送')"]:
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                logger.info("快手回评已发送: %s", comment_text[:30])
                return True
        except Exception:
            continue
    raise CommenterError("快手发送按钮未点中")


# 平台 → 回评实现分派表
_REPLIERS: dict = {
    Platform.XHS: _reply_xhs,
    Platform.DOUYIN: _reply_dy,
    Platform.KUAISHOU: _reply_ks,
}


# ---------- 回评入口 ----------

def reply_comment(
    account: Account,
    content: Content,
    comment_text: str,
    reply_text: str,
    *,
    replier_func=None,
) -> bool:
    """在笔记页定位指定评论,模拟回复。

    replier_func 可注入(测试用),签名 (page, comment_text, reply_text) -> bool,
    默认按平台从 _REPLIERS 选。

    Args:
        account: 账号(决定平台 + PublishContext 用其 cookie)
        content: 内容(取 platform_post_url)
        comment_text: 要回复的评论原文(文本定位用)
        reply_text: 要发送的回复文本
        replier_func: 回评实现函数(注入用)

    Returns:
        True=成功

    Raises:
        CommenterError: 无链接 / 无平台实现 / 浏览器流程失败 / 找不到评论或发送失败
    """
    if not content.platform_post_url:
        raise CommenterError("内容无发布链接,无法回复评论")

    do_reply = replier_func or _REPLIERS.get(account.platform)
    if do_reply is None:
        raise CommenterError(f"平台 {account.platform.value} 无回评实现")

    try:
        with PublishContext(account) as (page, _ctx):
            logger.info("[%s] 回评 content=%s url=%s",
                        account.platform.value, content.id, content.platform_post_url)
            page.goto(content.platform_post_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)  # 等评论渲染
            return do_reply(page, comment_text, reply_text)
    except CommenterError:
        raise
    except PublishError as e:
        raise CommenterError(f"回评浏览器流程失败: {e}") from e
    except Exception as e:
        # 其他异常(浏览器崩/网络)统一转 CommenterError
        raise CommenterError(f"回评浏览器流程失败: {e}") from e


__all__ = ["CommenterError", "reply_comment", "_REPLIERS"]
