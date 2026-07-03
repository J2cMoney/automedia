"""人机协同辅助发布 - Phase 5 v1.6 修订(Spec A-8)。

设计哲学:
    自动化做 95% 的脏活(打开页面、上传视频、填标题正文标签),
    把"点发布"这 5 秒留给真人。既省时间又几乎零封号风险——
    因为没有批量自动化点击行为,平台风控基本不触发。

流程:
    1. 有头浏览器打开创作者中心(持久化 profile,首次真人登录后免登)
    2. 自动上传视频
    3. 自动填标题/正文/标签
    4. 停住,等用户在浏览器里扫一眼 + 手动点发布
    5. 轮询检测 URL 变化(用户点发布后页面会跳转到帖子/管理页)
    6. 抓到新 URL 回填,关闭浏览器

与全自动 Publisher 的区别:
    - 不自动点发布按钮(最敏感的动作留给真人)
    - 有头模式(用户看得见,能干预)
    - 用 URL 变化判断发布完成,而非自动点击后等跳转
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.models.account import Account, Platform
from app.models.content import Content
from app.services.publish.base import PublishContext, PublishResult

logger = logging.getLogger(__name__)

# 等用户点发布的最长时间(分钟)。超时视为用户放弃。
ASSIST_TIMEOUT_MINUTES = 5


@dataclass
class AssistResult:
    """辅助发布结果。"""
    success: bool
    post_url: Optional[str] = None
    error: Optional[str] = None
    platform: Optional[str] = None
    content_id: Optional[int] = None


# 各平台"发布后"URL 特征(跳转到这些 URL 说明发布成功)
POST_PUBLISH_URL_PATTERNS = {
    Platform.XHS: ["/explore/", "/discovery/item/", "/user/profile"],
    Platform.DOUYIN: ["/content/manage", "/creator-micro/content"],
    Platform.KUAISHOU: ["/article/manage", "/account/manage"],
}


def assist_publish(account: Account, content: Content, *,
                   timeout_minutes: int = ASSIST_TIMEOUT_MINUTES) -> AssistResult:
    """人机协同辅助发布(三平台通用)。

    自动上传 + 填文案,停住等用户点发布,抓 URL 回填。
    持久化 profile 复用登录态(首次真人登录后免登)。

    Args:
        account: 账号(用其持久 profile + cookie)
        content: 要发布的内容(取 video_path/title/body/tags)
        timeout_minutes: 等用户点发布的超时(默认 5 分钟)

    Returns:
        AssistResult。用户点发布且 URL 跳转成功 -> success=True + post_url;
        用户超时未点 -> success=False + error;上传/填文案失败 -> success=False。
    """
    if not content.video_path:
        return AssistResult(False, error="内容无视频文件(video_path 为空)",
                            platform=account.platform.value, content_id=content.id)

    patterns = POST_PUBLISH_URL_PATTERNS.get(account.platform, [])
    if not patterns:
        return AssistResult(False, error=f"平台 {account.platform.value} 不支持辅助发布",
                            platform=account.platform.value, content_id=content.id)

    try:
        # 有头模式(用户看得见,能操作)
        with PublishContext(account, headless=False) as (page, ctx):
            return _do_assist(page, account, content, patterns, timeout_minutes)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error("[辅助发布] 异常 content=%s: %s", content.id, err)
        return AssistResult(False, error=err,
                            platform=account.platform.value, content_id=content.id)


def _do_assist(page, account: Account, content: Content,
               patterns: list, timeout_minutes: int) -> AssistResult:
    """执行辅助发布主流程(在 PublishContext 内)。"""
    from pathlib import Path
    from app.services.publish.base import PublishError

    # 1. 打开发布页(每平台不同,用持久 profile 已有的登录态)
    publish_url = _get_publish_url(account.platform)
    logger.info("[%s] 辅助发布:打开 %s", account.platform.value, publish_url)
    page.goto(publish_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # 检测是否被踢到登录页(持久 profile 未登录或 cookie 失效)
    if _is_login_page(page.url):
        return AssistResult(
            False,
            error="未登录或登录态失效。请在弹出的浏览器中手动登录一次(登录后会自动持久化,下次免登)",
            platform=account.platform.value, content_id=content.id,
        )

    # 2. 上传视频
    if not Path(content.video_path).exists():
        return AssistResult(False, error=f"视频文件不存在: {content.video_path}",
                            platform=account.platform.value, content_id=content.id)

    file_sel = _find_file_input(page)
    if not file_sel:
        return AssistResult(False, error="找不到视频上传入口(selector 未命中,页面 DOM 可能已变)",
                            platform=account.platform.value, content_id=content.id)
    logger.info("[辅助发布] 上传视频: %s", content.video_path)
    page.set_input_files(file_sel, content.video_path)

    # 等上传完成:小红书发布页是分步的,上传完才渲染标题/正文区。
    # 以标题输入框出现为标志(真实 placeholder:'填写标题会有更多赞哦'),最多等 120s
    try:
        page.wait_for_selector(
            "input[placeholder*='填写标题'], input.d-text[type='text']",
            timeout=120000,
        )
        logger.info("[辅助发布] 视频上传完成,标题区已渲染")
    except Exception:
        logger.warning("[辅助发布] 等待上传完成超时,继续尝试填文案")

    # 3. 填标题(真实 selector:input[placeholder='填写标题会有更多赞哦'] class=d-text)
    _try_fill_title(page, content.title or "")
    # 4. 填正文 + 标签(真实:.tiptap.ProseMirror 富文本 contenteditable)
    body_with_tags = content.body or ""
    if content.tags:
        body_with_tags = (body_with_tags + " " + " ".join(content.tags)).strip()
    _try_fill_body(page, body_with_tags)

    # 5. 停住,等用户点发布(轮询检测发布成功)
    # 小红书发布成功后:URL 可能不变(只弹提示),也可能跳笔记管理页。
    # 检测三种信号:① URL 跳到管理/笔记页 ② 页面出现"发布成功"提示 ③ 发布按钮消失
    logger.info("[辅助发布] 已填好文案,请在浏览器中确认并点「发布」按钮(最长等 %d 分钟)",
                timeout_minutes)
    deadline = time.monotonic() + timeout_minutes * 60
    original_url = page.url
    while time.monotonic() < deadline:
        try:
            current_url = page.url
            # 信号 1:URL 跳到管理页/笔记页
            if current_url != original_url and any(p in current_url for p in patterns):
                logger.info("[辅助发布] 发布成功(URL 跳转): %s", current_url)
                return AssistResult(True, post_url=current_url,
                                    platform=account.platform.value, content_id=content.id)
            # 信号 2:页面出现"发布成功"提示(toast/弹窗)
            success_toast = page.query_selector(
                "[class*='success'], [class*='toast']:has-text('成功'), [class*='message']:has-text('成功')"
            )
            if success_toast:
                logger.info("[辅助发布] 发布成功(检测到成功提示)")
                return AssistResult(True, post_url=current_url,
                                    platform=account.platform.value, content_id=content.id)
            # 信号 3:发布按钮消失(发布后页面会变)
            publish_btn = page.query_selector("div.publish-video, span:has-text('发布笔记')")
            if not publish_btn and current_url != original_url:
                logger.info("[辅助发布] 发布成功(发布按钮消失,URL 变化)")
                return AssistResult(True, post_url=current_url,
                                    platform=account.platform.value, content_id=content.id)
        except Exception as e:
            # 页面可能正在跳转导致查询失败,继续轮询
            logger.debug("[辅助发布] 轮询异常(可能正在跳转): %s", e)
        page.wait_for_timeout(2000)

    # 超时(用户没点发布)
    logger.warning("[辅助发布] 等待用户点发布超时(%d 分钟),视为放弃", timeout_minutes)
    return AssistResult(False, error=f"等待用户确认发布超时({timeout_minutes} 分钟未检测到发布动作)",
                        platform=account.platform.value, content_id=content.id)


# ---------- 平台特定 selector ----------

def _get_publish_url(platform: Platform) -> str:
    return {
        Platform.XHS: "https://creator.xiaohongshu.com/publish/publish",
        Platform.DOUYIN: "https://creator.douyin.com/creator-micro/content/upload",
        Platform.KUAISHOU: "https://cp.kuaishou.com/article/publish/video",
    }.get(platform, "")


def _is_login_page(url: str) -> bool:
    """判断是否被重定向到登录页。"""
    url_lower = url.lower()
    return any(k in url_lower for k in ["/login", "redirectreason=401", "website-login"])


def _find_file_input(page) -> Optional[str]:
    """多备选 selector 找视频上传入口。"""
    for sel in [
        "input[type='file'][accept*='video']",
        "input[type='file']",
        "[class*='upload'] input[type='file']",
    ]:
        if page.query_selector(sel):
            return sel
    return None


def _try_fill_title(page, title: str) -> None:
    """多备选 selector 填标题,失败不阻塞(用户可手动补)。
    真实 DOM(2026-07):input[placeholder='填写标题会有更多赞哦'] class=d-text
    """
    for sel in [
        "input[placeholder='填写标题会有更多赞哦']",
        "input[placeholder*='填写标题']",
        "input.d-text[type='text']",
        "input[placeholder*='标题']",
        "[class*='title'] input",
    ]:
        try:
            node = page.query_selector(sel)
            if node:
                node.fill(title)
                logger.info("[辅助发布] 标题已填(selector=%s)", sel)
                return
        except Exception:
            continue
    logger.warning("[辅助发布] 标题 selector 未命中,请用户手动填写")


def _try_fill_body(page, body: str) -> None:
    """多备选 selector 填正文。真实 DOM:.tiptap.ProseMirror(contenteditable 富文本)。
    失败不阻塞(用户可手动补)。"""
    for sel in [
        ".tiptap.ProseMirror",
        "div[class*='ProseMirror']",
        "[contenteditable]",  # 不限定 ='true'(tiptap 属性值可能不是字符串)
        "[contenteditable='true']",
        "div[class*='ql-editor']",
        "textarea[class*='desc']",
    ]:
        try:
            node = page.query_selector(sel)
            if node:
                node.click()
                page.keyboard.type(body)
                logger.info("[辅助发布] 正文已填(selector=%s)", sel)
                return
        except Exception:
            continue
    logger.warning("[辅助发布] 正文 selector 未命中,请用户手动填写")


__all__ = ["assist_publish", "AssistResult", "ASSIST_TIMEOUT_MINUTES"]
