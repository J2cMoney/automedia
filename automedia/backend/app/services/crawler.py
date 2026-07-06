"""热点采集服务 - Phase 3 FLOW-1。

设计(A-2 POC 修订后):
    用 Phase 2 已有的 Playwright + 账号加密 cookie 自主爬三平台热榜公开页。
    MediaCrawler 推到 Phase 5 评论爬取,本模块不依赖外部仓库。

职责:
    1. 拿账号 cookie(解密) -> 注入 Playwright context -> 打开热榜页 -> 抓词条
    2. 抓到的热榜按账号 topic_theme 过滤排序(FLOW-1 MUST:主题过滤可配置 + 排除词)
    3. 输出 Topic 候选(含来源平台/热度/匹配账号/匹配度)

健壮性:
    - 单平台抓取失败不阻塞其他平台(热榜页结构易变,失败优雅降级)
    - cookie 失效前置校验(FLOW-6:爬取前校验,失效抛 AuthExpiredError)
    - 抓取逻辑隔离成策略函数,页面结构变了只改对应策略

用法:
    from app.services.crawler import crawl_hot_topics, score_topics_by_theme
    candidates = crawl_hot_topics(account)  # 抓单账号所在平台热榜
    ranked = score_topics_by_theme(raw_items, topic_theme, exclude_words)
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app.models.account import Account, AuthState, Platform
from app.services.auth_health import check_account_health
from app.services.crypto import decrypt_cookie

logger = logging.getLogger(__name__)


# ---------- 数据结构 ----------

@dataclass
class RawTopic:
    """从热榜页抓到的原始词条(未过滤未打分)。"""
    source_platform: Platform
    title: str
    heat_score: float = 0.0
    source_url: Optional[str] = None


@dataclass
class ScoredTopic(RawTopic):
    """打分后的选题(含主题匹配度 + 命中账号 id)。"""
    match_score: float = 0.0
    matched_account_ids: List[int] = field(default_factory=list)


class CrawlerError(Exception):
    """爬取异常基类。"""


class AuthExpiredError(CrawlerError):
    """账号登录态失效,爬取前校验未过。"""


# ---------- 主题过滤与打分(纯函数,可单测) ----------

def _normalize(text: str) -> str:
    """文本归一化:小写 + 去多余空白。主题匹配用。"""
    return re.sub(r"\s+", "", (text or "").lower())


def score_topic_by_theme(
    title: str,
    topic_theme: str,
    exclude_words: Optional[List[str]] = None,
) -> float:
    """计算单条选题与账号主题的匹配度(0-1)。

    匹配规则(简单稳健,不引 LLM,保证可单测):
        - topic_theme 为空 -> 返回 0(不过滤即所有都算候选,但匹配度为 0)
        - 命中任一主题关键词 -> 基础分 0.5
        - 命中多个关键词 -> 加分(最多到 1.0)
        - 命中排除词 -> 直接返回 -1(标记弃用)
    """
    norm_title = _normalize(title)
    if not _normalize(topic_theme):
        return 0.0

    # 排除词优先:命中即弃用
    if exclude_words:
        for w in exclude_words:
            nw = _normalize(w)
            if nw and nw in norm_title:
                return -1.0

    # 关键词从原始 theme 切(保留分隔符),每个再归一化用于比较
    keywords = [_normalize(k) for k in _split_keywords(topic_theme)]
    keywords = [k for k in keywords if k]
    if not keywords:
        return 0.0

    hits = sum(1 for kw in keywords if kw in norm_title)
    if hits == 0:
        return 0.0
    # 基础 0.5 + 每多命中一个 +0.25,封顶 1.0
    return min(1.0, 0.5 + (hits - 1) * 0.25)


def _split_keywords(theme: str) -> List[str]:
    """把主题字符串切成关键词列表。

    支持中英文混排:空格/逗号/顿号/斜杠分隔。
    中文词组(如"AI编程")整词作为一个关键词,不拆单字。
    """
    parts = re.split(r"[,\s、，/]+", (theme or "").strip())
    return [p for p in parts if p]


def score_topics_by_theme(
    topics: List[RawTopic],
    topic_theme: str,
    exclude_words: Optional[List[str]] = None,
) -> List[ScoredTopic]:
    """对一批原始选题按主题打分,返回排序后的候选(过滤掉排除词命中的)。

    排序:match_score 降序 -> heat_score 降序。
    返回 ScoredTopic 列表(match_score >= 0 的,排除词命中的已剔除)。
    """
    scored: List[ScoredTopic] = []
    for t in topics:
        ms = score_topic_by_theme(t.title, topic_theme, exclude_words)
        if ms < 0:
            continue  # 排除词命中,弃用
        scored.append(ScoredTopic(
            source_platform=t.source_platform,
            title=t.title,
            heat_score=t.heat_score,
            source_url=t.source_url,
            match_score=ms,
        ))
    # 先按匹配度再按热度排序
    scored.sort(key=lambda x: (x.match_score, x.heat_score), reverse=True)
    return scored


# ---------- 平台热榜抓取策略(可注入,测试时 mock) ----------

# 每平台一个抓取函数:输入 Playwright page(已打开热榜页),输出 RawTopic 列表。
# 热榜页结构易变,这里每平台独立实现,失败只影响该平台。
TopicExtractor = Callable[["object"], List[RawTopic]]


def _extract_xhs(page) -> List[RawTopic]:
    """小红书 explore 信息流抽取。2026-07 实测 DOM 为准。

    小红书 explore 页的热榜板块 selector 已失效(class 改版),改抓信息流笔记
    卡片标题(section a[class*=title])。信息流是登录态个性化推荐,作为热点
    候选足够用(主题过滤会进一步筛)。
    """
    items: List[RawTopic] = []
    # 信息流笔记标题:多 selector 兜底
    nodes = page.query_selector_all(
        "section a[class*='title'], [class*='note-item'] [class*='title'], "
        "[class*='footer'] [class*='title']"
    )
    seen_titles: set = set()
    rank = 0
    for node in nodes[:50]:
        title = (node.inner_text() or "").strip()
        if not title or len(title) < 2:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        rank += 1
        heat = float(50 - rank)  # 按排名递减
        items.append(RawTopic(Platform.XHS, title, heat_score=heat))
    return items


def _extract_dy(page) -> List[RawTopic]:
    """抖音热榜抽取。抖音热榜条目通常带「#话题」或事件标题。"""
    items: List[RawTopic] = []
    nodes = page.query_selector_all("[class*='hot'] [class*='title'], [class*='rank'] [class*='word']")
    for i, node in enumerate(nodes[:50]):
        title = (node.inner_text() or "").strip()
        if not title or len(title) < 2:
            continue
        heat = float(50 - i)
        items.append(RawTopic(Platform.DOUYIN, title, heat_score=heat))
    return items


def _extract_ks(page) -> List[RawTopic]:
    """快手热榜抽取。"""
    items: List[RawTopic] = []
    nodes = page.query_selector_all("[class*='hot'] [class*='title'], [class*='rank-item'] [class*='title']")
    for i, node in enumerate(nodes[:50]):
        title = (node.inner_text() or "").strip()
        if not title or len(title) < 2:
            continue
        heat = float(50 - i)
        items.append(RawTopic(Platform.KUAISHOU, title, heat_score=heat))
    return items


EXTRACTORS: Dict[Platform, TopicExtractor] = {
    Platform.XHS: _extract_xhs,
    Platform.DOUYIN: _extract_dy,
    Platform.KUAISHOU: _extract_ks,
}


# 各平台热榜 URL(公开页)
HOTLIST_URL: Dict[Platform, str] = {
    Platform.XHS: "https://www.xiaohongshu.com/explore",
    Platform.DOUYIN: "https://www.douyin.com/hot",
    Platform.KUAISHOU: "https://www.kuaishou.com/",
    # 视频号无公开热榜页,Phase 3 不爬(半自动账号不参与热点采集)
}


# ---------- 主流程 ----------

def crawl_hot_topics(
    account: Account,
    *,
    exclude_words: Optional[List[str]] = None,
    extractor_map: Optional[Dict[Platform, TopicExtractor]] = None,
    max_results: int = 20,
) -> List[ScoredTopic]:
    """爬取账号所在平台热榜,按账号主题过滤排序,返回候选选题。

    Args:
        account: 账号(用其 cookie 认证 + topic_theme 过滤)
        exclude_words: 排除词列表(Spec FLOW-1 MUST)
        extractor_map: 抓取策略注入(测试用,默认 EXTRACTORS)
        max_results: 最多返回多少条候选

    Returns:
        打分排序后的候选选题列表

    Raises:
        AuthExpiredError: 账号 cookie 失效,爬取前校验未过
        CrawlerError: 爬取流程异常
    """
    # 1. cookie 健康检查(FLOW-6:爬取前校验)
    health = check_account_health(account)
    if not health.healthy:
        logger.warning("账号 %s cookie 不健康: %s,放弃爬取", account.id, health.message)
        raise AuthExpiredError(f"账号 {account.nickname} 登录态失效: {health.message}")

    # 视频号无公开热榜页
    if account.platform == Platform.WECHAT:
        logger.info("视频号账号 %s 无公开热榜,跳过", account.id)
        return []

    # 2. 解密 cookie
    try:
        cookies = decrypt_cookie(account.auth_state)
    except Exception as e:
        raise AuthExpiredError(f"账号 {account.nickname} cookie 解密失败: {e}") from e

    # 3. Playwright 抓热榜页
    extractors = extractor_map or EXTRACTORS
    extractor = extractors.get(account.platform)
    if extractor is None:
        logger.info("平台 %s 无热榜抽取器,跳过", account.platform.value)
        return []

    raw_topics = _fetch_with_playwright(
        account.platform, cookies, extractor
    )
    logger.info("账号 %s 平台 %s 抓到 %d 条原始热榜",
                account.id, account.platform.value, len(raw_topics))

    # 4. 按主题过滤排序
    scored = score_topics_by_theme(raw_topics, account.topic_theme, exclude_words)
    # 5. 标记命中账号
    for s in scored:
        s.matched_account_ids = [account.id]

    return scored[:max_results]


def _fetch_with_playwright(
    platform: Platform,
    cookies,
    extractor: TopicExtractor,
) -> List[RawTopic]:
    """用 Playwright 打开热榜页,注入 cookie,调 extractor 抽词条。

    同步 Playwright API(爬取本身是阻塞 IO,放线程池跑,见 API 层调用)。
    """
    from playwright.sync_api import sync_playwright

    url = HOTLIST_URL[platform]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            # 注入 cookie(Playwright 要求 dict 列表)
            if isinstance(cookies, list):
                context.add_cookies(cookies)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 给热榜渲染时间
            page.wait_for_timeout(3000)
            return extractor(page)
        finally:
            browser.close()


__all__ = [
    "RawTopic",
    "ScoredTopic",
    "CrawlerError",
    "AuthExpiredError",
    "score_topic_by_theme",
    "score_topics_by_theme",
    "crawl_hot_topics",
    "EXTRACTORS",
    "HOTLIST_URL",
]
