"""发布基类单测 - Phase 5 关键服务(DEV-PLAN 测试策略指定)。

覆盖重点:
    - PublishContext:持久 user_data_dir + cookie 注入 + stealth JS(mock Playwright)
    - check_publish_rate_limit:30 分钟内有/无 FINISHED 记录的限速判断
    - BasePublisher.publish 模板方法:cookie 失效/限速/视频缺失/子类异常的兜底

真实 Playwright 发布不在单测范围(需真账号 cookie + 平台风控 + DOM 易变),
用 mock 验证流程编排,真实发布靠 Phase 验收手动跑(照 test_crawler 注释范式)。
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.account import Account, AuthState, Platform, AccountStatus
from app.models.content import Content, ContentStatus
from app.services.publish.base import (
    AuthExpiredError,
    BasePublisher,
    PublishContext,
    PublishResult,
    check_publish_rate_limit,
)


# ---------- 测试工具 ----------

def _make_account(platform=Platform.XHS, auth_state="cipher", auth_status=AuthState.VALID) -> Account:
    """造一个测试账号(auth_state 非空绕过空检查)。"""
    return Account(
        id=1, platform=platform, nickname="测试号",
        topic_theme="AI编程", auth_state=auth_state, auth_status=auth_status,
        status=AccountStatus.ACTIVE,
    )


def _make_content() -> Content:
    return Content(
        id=10, account_id=1, title="测试标题", body="测试正文",
        tags=["#测试"], video_path="/tmp/test.mp4", status=ContentStatus.APPROVED,
    )


class _MockPlaywright:
    """模拟 sync_playwright 上下文 + launch_persistent_context。"""
    def __init__(self):
        self.browser = MagicMock()
        self.browser.add_init_script = MagicMock()
        self.browser.add_cookies = MagicMock()
        self.browser.new_page = MagicMock(return_value=MagicMock())
        # chromium 属性指向含 launch_persistent_context 的 mock
        self.chromium = MagicMock()
        self.chromium.launch_persistent_context = MagicMock(return_value=self.browser)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------- PublishContext ----------

class TestPublishContext:
    @pytest.fixture(autouse=True)
    def _stub_cookie_decrypt(self, monkeypatch):
        """类级默认:cookie 解密返回有效列表(个别测试覆盖测失败场景)。"""
        monkeypatch.setattr(
            "app.services.publish.base.decrypt_cookie",
            lambda cipher: [{"name": "web_session", "value": "x"}],
        )

    def test_uses_persistent_profile_per_account(self, monkeypatch):
        """每个账号一个独立 user_data_dir(FLOW-8 资源隔离)。"""
        mock_pw = _MockPlaywright()
        acc = _make_account(Platform.XHS)

        with PublishContext(acc, playwright_factory=lambda: mock_pw) as (page, ctx):
            pass

        mock_pw.chromium.launch_persistent_context.assert_called_once()
        kwargs = mock_pw.chromium.launch_persistent_context.call_args.kwargs
        assert "user_data_dir" in kwargs
        assert "xhs_1" in kwargs["user_data_dir"]

    def test_different_platforms_different_profile(self):
        """不同平台不同 profile 目录,不串 cookie。"""
        from app.services.publish.base import _profile_dir
        xhs_dir = _profile_dir(Platform.XHS, 1)
        dy_dir = _profile_dir(Platform.DOUYIN, 1)
        assert xhs_dir != dy_dir
        assert "xhs_1" in str(xhs_dir)
        assert "dy_1" in str(dy_dir)

    def test_injects_stealth_js(self):
        """注入 stealth init script(隐藏 webdriver 痕迹)。"""
        mock_pw = _MockPlaywright()
        with PublishContext(_make_account(), playwright_factory=lambda: mock_pw):
            pass
        mock_pw.browser.add_init_script.assert_called_once()
        script = mock_pw.browser.add_init_script.call_args.args[0]
        assert "webdriver" in script

    def test_injects_decrypted_cookies(self):
        """解密 cookie 注入 context(Phase 2 范式延伸)。"""
        mock_pw = _MockPlaywright()
        with PublishContext(_make_account(), playwright_factory=lambda: mock_pw):
            pass
        mock_pw.browser.add_cookies.assert_called_once()
        cookies = mock_pw.browser.add_cookies.call_args.args[0]
        assert cookies[0]["name"] == "web_session"

    def test_cookie_decrypt_failure_raises_auth_error(self, monkeypatch):
        """cookie 解密失败抛 AuthExpiredError(健康检查兜底)。"""
        monkeypatch.setattr(
            "app.services.publish.base.decrypt_cookie",
            lambda cipher: (_ for _ in ()).throw(Exception("decrypt fail")),
        )
        mock_pw = _MockPlaywright()
        with pytest.raises(AuthExpiredError):
            with PublishContext(_make_account(), playwright_factory=lambda: mock_pw):
                pass


# ---------- check_publish_rate_limit ----------

class TestRateLimit:
    def test_no_recent_publish_allows(self, monkeypatch):
        """无最近发布记录,允许发布。"""
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        session.return_value.__exit__ = MagicMock(return_value=False)
        # 让 session.execute 返回无记录
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock(
            execute=MagicMock(return_value=result_mock)
        ))
        ctx.__exit__ = MagicMock(return_value=False)
        factory = MagicMock(return_value=ctx)

        assert check_publish_rate_limit(1, Platform.XHS, session_factory=factory) is True

    def test_recent_publish_blocks(self):
        """30 分钟内有成功记录,限速拒绝。"""
        from app.models.task_run import TaskRun, TaskStatus
        from app.db import SyncSessionLocal
        # 造一条最近成功的 publish task
        with SyncSessionLocal() as s:
            t = TaskRun(
                flow_type="publish", account_id=1,
                status=TaskStatus.FINISHED,
                finished_at=datetime.utcnow() - timedelta(minutes=5),
            )
            s.add(t)
            s.commit()

        assert check_publish_rate_limit(1, Platform.XHS) is False

    def test_old_publish_allows(self):
        """超过 30 分钟的记录,允许发布。"""
        from app.models.task_run import TaskRun, TaskStatus
        from app.db import SyncSessionLocal
        with SyncSessionLocal() as s:
            t = TaskRun(
                flow_type="publish", account_id=2,
                status=TaskStatus.FINISHED,
                finished_at=datetime.utcnow() - timedelta(minutes=45),
            )
            s.add(t)
            s.commit()

        assert check_publish_rate_limit(2, Platform.XHS) is True


# ---------- BasePublisher.publish 模板方法 ----------

class _DummyPublisher(BasePublisher):
    """测试用具体实现:记录 _do_publish 调用。"""
    publish_url = "https://example.com/publish"
    platform_name = "测试平台"

    def __init__(self, do_publish_result=None, do_publish_raises=None, **kwargs):
        super().__init__(**kwargs)
        self._result = do_publish_result or "https://post.url/123"
        self._raises = do_publish_raises
        self.calls = []

    def _do_publish(self, page, content):
        self.calls.append((page, content))
        if self._raises:
            raise self._raises
        return self._result


class TestBasePublisher:
    @pytest.fixture(autouse=True)
    def _stub_external(self, monkeypatch):
        """类级默认:cookie 解密返回有效,健康检查通过,不限速。
        个别测试覆盖具体场景测失败。"""
        monkeypatch.setattr(
            "app.services.publish.base.decrypt_cookie",
            lambda cipher: [{"name": "web_session", "value": "x"}],
        )

    def test_unhealthy_cookie_blocks_publish(self, monkeypatch):
        """cookie 不健康,直接返回失败结果,不开浏览器。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=False, message="cookie 损坏"),
        )
        pub = _DummyPublisher()
        result = pub.publish(_make_account(), _make_content())
        assert result.success is False
        assert "登录态失效" in result.error
        assert pub.calls == []  # 没开浏览器

    def test_rate_limited_blocks_publish(self, monkeypatch):
        """触发限速,拒绝发布。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=True, message="ok"),
        )
        monkeypatch.setattr(
            "app.services.publish.base.check_publish_rate_limit",
            lambda aid, p: False,
        )
        pub = _DummyPublisher()
        result = pub.publish(_make_account(), _make_content())
        assert result.success is False
        assert "限速" in result.error

    def test_no_video_path_blocks_publish(self, monkeypatch):
        """无视频文件,拒绝发布。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=True, message="ok"),
        )
        monkeypatch.setattr(
            "app.services.publish.base.check_publish_rate_limit",
            lambda aid, p: True,
        )
        content = _make_content()
        content.video_path = None
        pub = _DummyPublisher()
        result = pub.publish(_make_account(), content)
        assert result.success is False
        assert "video_path" in result.error

    def test_successful_publish_returns_url(self, monkeypatch):
        """正常发布:校验通过 → 开浏览器 → 调 _do_publish → 返回 URL。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=True, message="ok"),
        )
        monkeypatch.setattr(
            "app.services.publish.base.check_publish_rate_limit",
            lambda aid, p: True,
        )
        mock_pw = _MockPlaywright()
        pub = _DummyPublisher(playwright_factory=lambda: mock_pw)
        result = pub.publish(_make_account(), _make_content())
        assert result.success is True
        assert result.post_url == "https://post.url/123"
        assert len(pub.calls) == 1  # _do_publish 被调一次

    def test_do_publish_exception_caught(self, monkeypatch):
        """_do_publish 抛异常,被捕获进 result.error(不冒泡)。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=True, message="ok"),
        )
        monkeypatch.setattr(
            "app.services.publish.base.check_publish_rate_limit",
            lambda aid, p: True,
        )
        mock_pw = _MockPlaywright()
        from app.services.publish.base import PublishError
        pub = _DummyPublisher(
            do_publish_raises=PublishError("selector 找不到"),
            playwright_factory=lambda: mock_pw,
        )
        result = pub.publish(_make_account(), _make_content())
        assert result.success is False
        assert "selector 找不到" in result.error

    def test_result_carries_platform_and_content_id(self, monkeypatch):
        """结果带平台标识和 content_id,便于入库。"""
        monkeypatch.setattr(
            "app.services.publish.base.check_account_health",
            lambda a: MagicMock(healthy=False, message="失效"),
        )
        pub = _DummyPublisher()
        result = pub.publish(_make_account(Platform.DOUYIN), _make_content())
        assert result.platform == "dy"
        assert result.content_id == 10
