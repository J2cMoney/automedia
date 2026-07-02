"""Cookie 加密存储 - Phase 2 FLOW-6 MUST。

用 Fernet(对称加密,密文含时间戳,HMAC 防篡改)加密登录态 cookie。
密钥从 .env 的 COOKIE_ENCRYPT_KEY 读,绝不硬编码。

安全要点:
    - 密钥只从环境变量读(config.settings.COOKIE_ENCRYPT_KEY)
    - 加密后的密文存 account.auth_state
    - 解密只在服务端做,密文绝不返回前端
    - 密钥换了 -> 旧密文解不开 -> 健康检查标 INVALID(需重新登录)

用法:
    from app.services.crypto import encrypt_cookie, decrypt_cookie
    cipher = encrypt_cookie(cookie_dict)
    plain  = decrypt_cookie(cipher)  # 失败抛 InvalidToken
"""
import json
import logging
from typing import Any, Dict

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    """构造 Fernet 实例。密钥从 settings 读,每次现取(支持测试 monkeypatch)。"""
    return Fernet(settings.COOKIE_ENCRYPT_KEY.encode())


def encrypt_cookie(cookie: Any) -> str:
    """加密 cookie。接受 dict 或 str,统一序列化成 JSON 后加密。

    Args:
        cookie: dict(Playwright cookies 列表转的)或 str

    Returns:
        str: base64 密文
    """
    if isinstance(cookie, (dict, list)):
        payload = json.dumps(cookie, ensure_ascii=False)
    else:
        payload = str(cookie)
    token = _fernet().encrypt(payload.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_cookie(cipher_text: str) -> Any:
    """解密 cookie。返回原始 JSON 结构(dict/list)或 str。

    Args:
        cipher_text: encrypt_cookie 产出的密文

    Returns:
        原始 cookie(dict/list/str)

    Raises:
        InvalidToken: 密钥不匹配 / 密文被篡改 / 密文损坏
    """
    if not cipher_text:
        raise InvalidToken("空密文,未登录或已清除")
    plain = _fernet().decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    try:
        return json.loads(plain)
    except (json.JSONDecodeError, ValueError):
        return plain


def is_corrupt(cipher_text: str) -> bool:
    """判断密文是否损坏/无法解密。健康检查用。"""
    try:
        decrypt_cookie(cipher_text)
        return False
    except (InvalidToken, Exception):
        return True
