"""分发基类 - Phase 5 FLOW-4。

提供发布/回评共用的浏览器上下文与模板方法。设计要点:

1. **持久化 Chrome profile**(Spec FLOW-8 资源隔离 + FLOW-4 复用登录态降风控):
   每账号独占 user_data_dir,指纹/缓存/登录态最接近真实浏览器,反检测最强。
   cookie 解密后注入该 profile(Phase 2 cookie 范式的延伸)。

2. **stealth 反检测**:注入 init JS 隐藏 navigator.webdriver 等基础自动化痕迹。
   2026 实测:Playwright 直接用易被封,Patchright 是最强方案但引入新依赖,
   这里先做 stealth + 持久 profile 平衡可用性与维护成本。

3. **模板方法**:publish() 校验健康 → 开浏览器 → 调子类 _do_publish → 返回结果。
   子类只填 selector 逻辑(base 不管平台 DOM)。

4. **限速**(Spec FLOW-8):发布前查 task_runs,同账号同平台 30 分钟内有成功记录则拒绝。

复用 Phase 2:auth_health.check_account_health(发布前校验)+ crypto.decrypt_cookie。
"""
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from app.config import BASE_DIR, settings
from app.models.account import Account, Platform
from app.models.content import Content
from app.services.auth_health import check_account_health
from app.services.crypto import decrypt_cookie

logger = logging.getLogger(__name__)


# ---------- 异常与结果 ----------

class PublishError(Exception):
    """发布流程异常。"""


class AuthExpiredError(PublishError):
    """账号登录态失效,发布前校验未过。"""


class RateLimitedError(PublishError):
    """触发平台风控限速(同账号同平台两次发布间隔不足)。"""


@dataclass
class PublishResult:
    """单次发布结果。success=False 时 post_url 为空,error 有失败原因。"""
    success: bool
    post_url: Optional[str] = None
    error: Optional[str] = None
    platform: Optional[str] = None
    content_id: Optional[int] = None


# ---------- 浏览器上下文(持久 profile + cookie + stealth)----------

# stealth init script:隐藏基础自动化痕迹(2026 实测够用,非 100% 安全)
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
window.chrome = { runtime: {} };
"""

# profile 根目录:每账号一个,持久化保留指纹/缓存/登录态
PROFILE_ROOT = BASE_DIR / "data" / "profiles"


def _profile_dir(platform: Platform, account_id: int) -> Path:
    """每账号一个 Chrome profile 目录(FLOW-8 资源隔离:不串 cookie)。"""
    return PROFILE_ROOT / f"{platform.value}_{account_id}"


class PublishContext:
    """发布/回评共用的浏览器上下文管理器。

    持久化 user_data_dir(每账号独立 profile)+ 注入 cookie + 注入 stealth JS。
    用法:
        with PublishContext(account) as (page, ctx):
            page.goto(...)
            ctx.cookies()  # Playwright BrowserContext

    同步 Playwright API(发布是阻塞交互式,放线程池跑,见 API 层调用)。
    """

    def __init__(
        self,
        account: Account,
        *,
        headless: bool = True,
        playwright_factory=None,
    ):
        self.account = account
        self.headless = headless
        # 测试注入:默认走真 sync_playwright,测试传 mock
        self._playwright_factory = playwright_factory
        self._pw_cm = None   # sync_playwright() 返回的上下文管理器
        self._pw = None      # Playwright 实例(cm.__enter__() 的结果)
        self._context = None
        self._page = None

    def __enter__(self):
        # 懒导入,避免模块加载时就依赖 playwright(测试可 mock)
        if self._playwright_factory is None:
            from playwright.sync_api import sync_playwright
            self._playwright_factory = sync_playwright

        # sync_playwright() 返回上下文管理器,要保存它用于 __exit__
        self._pw_cm = self._playwright_factory()
        self._pw = self._pw_cm.__enter__()
        profile_dir = _profile_dir(self.account.platform, self.account.id)
        profile_dir.mkdir(parents=True, exist_ok=True)

        logger.info("[%s] 启动持久 profile: %s",
                    self.account.platform.value, profile_dir)
        # 持久化 context:复用 Chrome profile,指纹最真实(反检测)
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self.headless,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
        )
        # 注入 stealth(在每个新页面加载前执行)
        self._context.add_init_script(_STEALTH_JS)

        # 注入解密的 cookie(Phase 2 范式延伸)
        cookies = _load_cookies(self.account)
        if isinstance(cookies, list):
            self._context.add_cookies(cookies)

        self._page = self._context.new_page()
        return self._page, self._context

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 先关 context(浏览器),再退 playwright cm(释放 playwright 进程)
        try:
            if self._context is not None:
                self._context.close()
        except Exception as e:
            logger.warning("PublishContext 关闭 context 异常: %s", e)
        try:
            if self._pw_cm is not None:
                self._pw_cm.__exit__(exc_type, exc_val, exc_tb)
        except Exception as e:
            logger.warning("PublishContext 退出 playwright 异常: %s", e)
        return False


def _load_cookies(account: Account):
    """解密账号 cookie,失败抛 AuthExpiredError。"""
    try:
        return decrypt_cookie(account.auth_state)
    except Exception as e:
        raise AuthExpiredError(f"账号 {account.nickname} cookie 解密失败: {e}") from e


# ---------- 限速校验 ----------

def check_publish_rate_limit(account_id: int, platform: Platform,
                             session_factory=None) -> bool:
    """检查同账号同平台是否满足发布间隔(Spec FLOW-8:≥30 分钟)。

    查 task_runs 表该账号该平台最近是否有 FINISHED 的 publish 任务,
    间隔不足 config.PUBLISH_INTERVAL_MINUTES 则拒绝发布。

    Args:
        account_id: 账号 id
        platform: 平台
        session_factory: SyncSessionLocal(测试注入)

    Returns:
        True=允许发布(间隔足够), False=被限速(间隔不足)
    """
    if session_factory is None:
        from app.db import SyncSessionLocal as session_factory

    from app.models.task_run import TaskRun, TaskStatus
    from sqlalchemy import select

    cutoff = datetime.utcnow() - timedelta(minutes=settings.PUBLISH_INTERVAL_MINUTES)
    with session_factory() as s:
        recent = s.execute(
            select(TaskRun).where(
                TaskRun.account_id == account_id,
                TaskRun.flow_type == "publish",
                TaskRun.status == TaskStatus.FINISHED,
                TaskRun.finished_at.isnot(None),
                TaskRun.finished_at >= cutoff,
            )
        ).scalars().first()
    if recent is not None:
        logger.warning("账号 %s 平台 %s 限速:最近一次发布 %s,不足 %d 分钟",
                       account_id, platform.value, recent.finished_at,
                       settings.PUBLISH_INTERVAL_MINUTES)
        return False
    return True


# ---------- 发布基类(模板方法)----------

class BasePublisher(ABC):
    """平台发布器基类。子类实现 _do_publish 填各平台 selector 逻辑。

    模板方法 publish():校验健康 → 限速 → 开浏览器 → 调 _do_publish → 返回结果。
    子类只需关心 selector,通用流程(健康检查/限速/浏览器/异常)由基类兜底。
    """

    # 子类覆盖:平台发布页 URL
    publish_url: str = ""
    # 子类覆盖:平台中文名(日志用)
    platform_name: str = ""

    def __init__(self, *, headless: bool = True, playwright_factory=None):
        self.headless = headless
        self.playwright_factory = playwright_factory

    def publish(self, account: Account, content: Content) -> PublishResult:
        """模板方法:完整发布流程。返回 PublishResult(不抛异常,失败进 result.error)。"""
        # 1. cookie 健康检查(FLOW-6:发布前校验)
        health = check_account_health(account)
        if not health.healthy:
            logger.warning("账号 %s cookie 不健康: %s,放弃发布",
                           account.id, health.message)
            return PublishResult(
                success=False,
                error=f"登录态失效: {health.message}",
                platform=account.platform.value,
                content_id=content.id,
            )

        # 2. 限速校验
        if not check_publish_rate_limit(account.id, account.platform):
            return PublishResult(
                success=False,
                error=f"平台风控限速:同账号同平台两次发布间隔需 ≥{settings.PUBLISH_INTERVAL_MINUTES} 分钟",
                platform=account.platform.value,
                content_id=content.id,
            )

        # 3. 校验视频文件存在
        if not content.video_path:
            return PublishResult(
                success=False,
                error="内容无视频文件(video_path 为空),无法发布",
                platform=account.platform.value,
                content_id=content.id,
            )

        # 4. 开浏览器执行平台特定发布逻辑
        try:
            ctx = PublishContext(
                account, headless=self.headless,
                playwright_factory=self.playwright_factory,
            )
            with ctx as (page, _browser_ctx):
                logger.info("[%s] 开始发布 content=%s", self.platform_name, content.id)
                post_url = self._do_publish(page, content)
                logger.info("[%s] 发布成功 content=%s url=%s",
                            self.platform_name, content.id, post_url)
                return PublishResult(
                    success=True,
                    post_url=post_url,
                    platform=account.platform.value,
                    content_id=content.id,
                )
        except PublishError as e:
            logger.error("[%s] 发布失败 content=%s: %s",
                         self.platform_name, content.id, e)
            return PublishResult(
                success=False,
                error=str(e),
                platform=account.platform.value,
                content_id=content.id,
            )
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            logger.error("[%s] 发布异常 content=%s: %s",
                         self.platform_name, content.id, err)
            return PublishResult(
                success=False,
                error=err,
                platform=account.platform.value,
                content_id=content.id,
            )

    @abstractmethod
    def _do_publish(self, page, content: Content) -> str:
        """子类实现:打开发布页 → 上传视频 → 填文案 → 点发布,返回帖子 URL。

        Raises:
            PublishError: 发布失败(selector 找不到/上传失败/等)
        """

    # ---------- 通用工具(子类复用)----------

    def _wait_and_upload(self, page, file_input_selector: str,
                         video_path: str, *, timeout_ms: int = 120000) -> None:
        """通用视频上传:等 input[file] 可见 → 上传 → 等进度条消失。

        DOM 易变,进度条 selector 用多备选。
        """
        from pathlib import Path
        if not Path(video_path).exists():
            raise PublishError(f"视频文件不存在: {video_path}")

        file_input = page.wait_for_selector(file_input_selector, state="attached",
                                            timeout=timeout_ms)
        file_input.set_input_files(video_path)
        logger.info("已上传视频文件: %s", video_path)
        # 等上传完成:进度条/处理中提示消失(多备选 selector)
        self._wait_gone(page, [
            "[class*='progress']",
            "[class*='uploading']",
            "[class*='processing']",
            "[class*='loading']",
        ], timeout_ms=timeout_ms)

    def _safe_fill(self, page, selector: str, text: str,
                   *, timeout_ms: int = 10000) -> None:
        """通用文本填充:等可见 → fill。selector 多备选用 or_。"""
        try:
            page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
            page.fill(selector, text or "")
        except Exception as e:
            raise PublishError(f"填充失败 selector={selector}: {e}") from e

    def _wait_gone(self, page, selectors: List[str], *, timeout_ms: int = 30000) -> None:
        """等元素列表中任一存在的消失(上传完成判断用)。"""
        import playwright.sync_api as pw
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            any_visible = False
            for sel in selectors:
                try:
                    node = page.query_selector(sel)
                    if node and node.is_visible():
                        any_visible = True
                        break
                except Exception:
                    continue
            if not any_visible:
                return
            page.wait_for_timeout(500)
        logger.warning("等待元素消失超时(selectors=%s),继续后续步骤", selectors)


__all__ = [
    "PublishError",
    "AuthExpiredError",
    "RateLimitedError",
    "PublishResult",
    "PublishContext",
    "BasePublisher",
    "check_publish_rate_limit",
    "PROFILE_ROOT",
]
