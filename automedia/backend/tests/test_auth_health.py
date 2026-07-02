"""auth_health.py 单测 - Phase 2 FLOW-6 MUST。

验收关键(DEV-PLAN Phase 2 / objective 第3条硬门槛):
    "验收时构造一个坏 cookie 必须被检出。"

覆盖:
    - 坏 cookie(篡改/损坏/空/关键 cookie 缺失) -> INVALID,healthy=False
    - 好 cookie(可解密 + 关键 cookie 在) -> VALID,healthy=True
    - 未登录(空 auth_state) -> UNKNOWN
"""
import pytest

from app.models.account import Account, AccountStatus, AuthState, Platform
from app.services import crypto
from app.services.auth_health import check_account_health


def _make_account(platform=Platform.XHS, auth_state="", auth_status=AuthState.UNKNOWN) -> Account:
    """构造一个内存 Account 实例(正常实例化,不落库)。"""
    acc = Account(
        platform=platform,
        nickname="测试号",
        topic_theme="科技",
        auth_state=auth_state,
        auth_status=auth_status,
        status=AccountStatus.ACTIVE,
    )
    # id 不入库时由 SQLAlchemy 默认 None,健康检查不依赖它
    return acc


# ---------- 好的 cookie(各平台关键 cookie 齐全) ----------

class TestHealthyCookie:
    def test_xhs_valid(self):
        """小红书 cookie 含 web_session -> VALID。"""
        cookies = [{"name": "web_session", "value": "abc"}, {"name": "x", "value": "y"}]
        acc = _make_account(Platform.XHS, crypto.encrypt_cookie(cookies))
        r = check_account_health(acc)
        assert r.healthy is True
        assert r.auth_status == AuthState.VALID

    def test_douyin_valid(self):
        cookies = [{"name": "sessionid", "value": "abc"}]
        acc = _make_account(Platform.DOUYIN, crypto.encrypt_cookie(cookies))
        r = check_account_health(acc)
        assert r.healthy is True
        assert r.auth_status == AuthState.VALID

    def test_kuaishou_valid(self):
        cookies = [{"name": "userId", "value": "123"}]
        acc = _make_account(Platform.KUAISHOU, crypto.encrypt_cookie(cookies))
        r = check_account_health(acc)
        assert r.healthy is True
        assert r.auth_status == AuthState.VALID


# ---------- 坏 cookie 必须被检出(验收硬门槛) ----------

class TestCorruptCookieDetected:
    def test_tampered_ciphertext_detected(self):
        """密文被篡改 -> INVALID(验收关键)。"""
        good_cipher = crypto.encrypt_cookie([{"name": "web_session", "value": "x"}])
        tampered = good_cipher[:-1] + ("A" if good_cipher[-1] != "A" else "B")
        acc = _make_account(Platform.XHS, tampered)
        r = check_account_health(acc)
        assert r.healthy is False, "被篡改的 cookie 必须被检出为失效"
        assert r.auth_status == AuthState.INVALID
        assert "损坏" in r.message or "重新登录" in r.message

    def test_garbage_ciphertext_detected(self):
        """完全无效的密文 -> INVALID。"""
        acc = _make_account(Platform.XHS, "totally-bad-not-fernet-token!!!")
        r = check_account_health(acc)
        assert r.healthy is False
        assert r.auth_status == AuthState.INVALID

    def test_missing_critical_cookie_detected(self):
        """关键登录态 cookie 缺失(解密成功但 web_session 不在) -> INVALID。"""
        cookies = [{"name": "other_cookie", "value": "x"}]  # 无 web_session
        acc = _make_account(Platform.XHS, crypto.encrypt_cookie(cookies))
        r = check_account_health(acc)
        assert r.healthy is False, "缺少关键 cookie 应判失效"
        assert r.auth_status == AuthState.INVALID

    def test_empty_cookie_list_detected(self):
        """解出来是空列表 -> INVALID。"""
        acc = _make_account(Platform.XHS, crypto.encrypt_cookie([]))
        r = check_account_health(acc)
        assert r.healthy is False
        assert r.auth_status == AuthState.INVALID


# ---------- 未登录态 ----------

class TestNotLoggedIn:
    def test_empty_auth_state_is_unknown(self):
        """从未登录(空 auth_state) -> UNKNOWN(非 INVALID)。"""
        acc = _make_account(Platform.XHS, "")
        r = check_account_health(acc)
        assert r.healthy is False
        assert r.auth_status == AuthState.UNKNOWN
        assert "未登录" in r.message
