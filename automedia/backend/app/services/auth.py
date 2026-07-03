"""登录态获取服务 - Phase 2 FLOW-6。

用 Playwright 打开浏览器,用户手动完成平台登录,脚本检测到登录成功后抓取 cookie,
经 crypto.encrypt_cookie 加密后写回 account.auth_state。

设计:
    - 同步 Playwright API(登录流程本身是阻塞交互式,async 收益不大,同步更稳)
    - 平台登录检测:轮询关键 cookie 出现(各平台登录态 cookie 名)
    - 超时控制:默认 180s,超时抛 TimeoutError
    - 由 API 路由在线程池里调用(FastAPI run_in_threadpool),不阻塞事件循环

各平台登录态判定 cookie(出现即视为已登录):
    - 小红书: web_session
    - 抖音:   sessionid
    - 快手:   userId / kuaishou.server.web_st
    - 视频号: (微信域,放 pas)
"""
import logging
from typing import Dict, List, Optional

from app.config import settings
from app.models.account import Platform
from app.services.crypto import encrypt_cookie

logger = logging.getLogger(__name__)


# 各平台登录页 + 登录态判定 cookie 名(出现任一即视为登录成功)
# v1.6 修订:登录目标指向各平台创作者中心(发布/回评实际操作页),
#   而非主站。原因:创作者中心是独立子站,有独立登录态,主站 cookie 不通用。
#   Phase 5 真号验证发现 creator.xiaohongshu.com 直接 401(见 Spec A-8)。
PLATFORM_LOGIN: Dict[Platform, Dict] = {
    Platform.XHS: {
        # 创作者中心(发布页所在域),登录后抓这个域的 cookie
        "url": "https://creator.xiaohongshu.com/publish/publish",
        "login_cookies": ["web_session", "customer-sso-sid", "galaxy_creator_session_id"],
        "name": "小红书",
    },
    Platform.DOUYIN: {
        # 抖音创作者服务中心
        "url": "https://creator.douyin.com/creator-micro/content/upload",
        "login_cookies": ["sessionid", "passport_csrf_token", "LOGIN_STATUS"],
        "name": "抖音",
    },
    Platform.KUAISHOU: {
        # 快手创作者服务平台
        "url": "https://cp.kuaishou.com/article/publish/video",
        "login_cookies": ["userId", "kuaishou.server.web_st", "passToken"],
        "name": "快手",
    },
    Platform.WECHAT: {
        # 视频号半自动,登录态获取同样走微信扫码
        "url": "https://channels.weixin.qq.com",
        "login_cookies": ["login_type", "mm_lang"],
        "name": "视频号",
    },
}


def fetch_cookies_for_platform(
    platform: Platform,
    timeout_seconds: int = 180,
    headless: bool = False,
) -> List[Dict]:
    """打开浏览器让用户登录指定平台,登录成功后抓取 cookie。

    Args:
        platform: 目标平台
        timeout_seconds: 等待用户登录的最长时间(秒)
        headless: 是否无头(登录流程需用户操作,默认 False 有头)

    Returns:
        cookie 列表(Playwright cookie dict 格式)

    Raises:
        TimeoutError: 用户在 timeout 内未完成登录
        RuntimeError: Playwright 未安装或启动失败
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    cfg = PLATFORM_LOGIN[platform]
    login_cookies = set(cfg["login_cookies"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            logger.info("[%s] 打开登录页: %s", cfg["name"], cfg["url"])
            page.goto(cfg["url"], wait_until="domcontentloaded")

            # 轮询检测登录态 cookie
            deadline_step = 1.0  # 每 1s 检查一次
            elapsed = 0.0
            got = False
            while elapsed < timeout_seconds:
                cookies = context.cookies()
                names = {c["name"] for c in cookies}
                if login_cookies & names:
                    got = True
                    logger.info("[%s] 检测到登录态 cookie: %s",
                                cfg["name"], login_cookies & names)
                    break
                page.wait_for_timeout(int(deadline_step * 1000))
                elapsed += deadline_step

            if not got:
                raise TimeoutError(
                    f"{cfg['name']} 登录超时({timeout_seconds}s),未检测到登录态 cookie"
                )

            # 登录成功,多等 2s 让 cookie 落全
            page.wait_for_timeout(2000)
            cookies = context.cookies()
            logger.info("[%s] 抓取到 %d 条 cookie", cfg["name"], len(cookies))
            return cookies
        finally:
            browser.close()


def login_and_store_cookie(
    platform: Platform,
    account_id: Optional[int] = None,
    timeout_seconds: int = 180,
    headless: bool = False,
) -> str:
    """完整登录流程:开浏览器登录 -> 抓 cookie -> 加密。

    Args:
        platform: 平台
        account_id: 关联账号 id(仅日志用)
        timeout_seconds: 登录超时
        headless: 是否无头

    Returns:
        加密后的 cookie 密文(由调用方写回 account.auth_state)
    """
    logger.info("开始登录流程 account_id=%s platform=%s", account_id, platform.value)
    cookies = fetch_cookies_for_platform(
        platform, timeout_seconds=timeout_seconds, headless=headless
    )
    if not cookies:
        raise RuntimeError(f"登录成功但未抓取到 cookie(platform={platform.value})")
    cipher = encrypt_cookie(cookies)
    logger.info("登录态加密完成 account_id=%s cookie数=%d", account_id, len(cookies))
    return cipher
