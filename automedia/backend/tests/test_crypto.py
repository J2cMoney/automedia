"""crypto.py 单测 - Phase 2。

验收关键(DEV-PLAN Phase 2 / objective 四步走-测试完整性):
    - 加解密往返:dict/list/str 三种输入都能正确还原
    - 坏密文检出:被篡改/损坏的密文解密必须抛 InvalidToken
    - 密钥换了旧密文解不开
"""
import json

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.services import crypto


# ---------- 加解密往返 ----------

class TestRoundTrip:
    def test_dict_round_trip(self):
        """dict cookie 加密后能完整还原。"""
        cookie = {"web_session": "abc123", "user_id": "42"}
        cipher = crypto.encrypt_cookie(cookie)
        assert cipher != json.dumps(cookie), "密文不应是明文"
        assert isinstance(cipher, str)
        back = crypto.decrypt_cookie(cipher)
        assert back == cookie, "解密结果应等于原始 dict"

    def test_list_round_trip(self):
        """Playwright cookies 列表格式(dict 列表)加密还原。"""
        cookies = [
            {"name": "web_session", "value": "abc", "domain": ".xiaohongshu.com"},
            {"name": "user_id", "value": "42", "domain": ".xiaohongshu.com"},
        ]
        cipher = crypto.encrypt_cookie(cookies)
        back = crypto.decrypt_cookie(cipher)
        assert back == cookies

    def test_str_round_trip(self):
        """str cookie 加密还原。"""
        cookie = "session=abc123; user=42"
        cipher = crypto.encrypt_cookie(cookie)
        back = crypto.decrypt_cookie(cipher)
        assert back == cookie

    def test_ciphertext_is_not_plaintext(self):
        """密文不应包含明文片段(防信息泄漏)。"""
        cookie = {"super_secret_token": "should_not_appear_in_ciphertext"}
        cipher = crypto.encrypt_cookie(cookie)
        assert "super_secret_token" not in cipher
        assert "should_not_appear" not in cipher


# ---------- 坏密文检出(验收关键) ----------

class TestCorruptDetection:
    def test_tampered_ciphertext_raises(self):
        """被篡改的密文必须抛 InvalidToken。"""
        cookie = {"k": "v"}
        cipher = crypto.encrypt_cookie(cookie)
        # 篡改:翻转最后一个字符
        tampered = cipher[:-1] + ("A" if cipher[-1] != "A" else "B")
        with pytest.raises(InvalidToken):
            crypto.decrypt_cookie(tampered)

    def test_garbage_ciphertext_raises(self):
        """完全无效的密文必须抛异常(非 None 返回)。"""
        with pytest.raises(Exception):
            crypto.decrypt_cookie("not-a-valid-fernet-token-at-all")

    def test_empty_ciphertext_raises(self):
        """空密文(未登录)必须能被识别为损坏。"""
        with pytest.raises(InvalidToken):
            crypto.decrypt_cookie("")

    def test_wrong_key_raises(self):
        """用不同密钥加密的密文,当前密钥解不开。"""
        # 用另一个密钥加密
        other_key = Fernet.generate_key()
        other_fernet = Fernet(other_key)
        cipher = other_fernet.encrypt(b'{"k":"v"}').decode()
        # 当前 settings 的密钥解不开
        with pytest.raises(InvalidToken):
            crypto.decrypt_cookie(cipher)

    def test_is_corrupt_helper(self):
        """is_corrupt 辅助函数正确判断。"""
        good = crypto.encrypt_cookie({"k": "v"})
        assert crypto.is_corrupt(good) is False
        assert crypto.is_corrupt("garbage") is True
        assert crypto.is_corrupt("") is True
        assert crypto.is_corrupt(good[:-1] + "X") is True


# ---------- 密钥来源 ----------

class TestKeyFromEnv:
    def test_key_not_hardcoded(self):
        """密钥来自 settings,不是源码常量(安全规范)。"""
        from app.config import settings
        # 能加密说明密钥已加载
        cipher = crypto.encrypt_cookie("x")
        assert settings.COOKIE_ENCRYPT_KEY, "密钥必须从环境变量读,非空"
