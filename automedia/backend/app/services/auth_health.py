"""登录态健康检查 - Phase 2 FLOW-6 MUST。

发布/爬取前校验 cookie 有效性,失效则标记 INVALID + 提示重新登录。

校验层次(由轻到重):
    1. 密文可解密(Fernet 密钥匹配 + 未被篡改)
    2. 解出的 cookie 列表非空
    3. 该平台的关键登录态 cookie 存在(如小红书 web_session)

Phase 2 只做本地校验(不联网打平台,避免风控 + 联网成本)。
远程真校验(请求平台个人页判断 401/重定向登录页)留到 Phase 5 发布前按需加。

验收关键(DEV-PLAN Phase 2 / objective 第3条):
    手动改坏 cookie -> 必须被检出标 INVALID。
"""
import logging
from dataclasses import dataclass
from typing import Iterable, Set

from cryptography.fernet import InvalidToken

from app.models.account import Account, AuthState, Platform
from app.services.auth import PLATFORM_LOGIN
from app.services.crypto import decrypt_cookie

logger = logging.getLogger(__name__)


@dataclass
class HealthResult:
    """健康检查结果。"""
    auth_status: AuthState
    healthy: bool
    message: str


# ---------- 校验逻辑 ----------

def _cookie_names(cookies) -> Set[str]:
    """从 cookie 列表(Playwright dict 格式)取 name 集合。"""
    names = set()
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c:
                names.add(c["name"])
    elif isinstance(cookies, dict):
        names = set(cookies.keys())
    return names


def check_account_health(account: Account) -> HealthResult:
    """检查单个账号的登录态健康度。

    Args:
        account: Account ORM 实例(需有 auth_state 密文)

    Returns:
        HealthResult: VALID/INVALID + message
    """
    cipher = account.auth_state or ""

    # 1. 未登录(空密文)
    if not cipher:
        return HealthResult(
            AuthState.UNKNOWN,
            healthy=False,
            message="未登录,请先获取登录态",
        )

    # 2. 密文能否解密(密钥不匹配 / 被篡改 / 损坏)
    try:
        cookies = decrypt_cookie(cipher)
    except InvalidToken:
        logger.warning("账号 %s cookie 解密失败(损坏/密钥不匹配)", account.id)
        return HealthResult(
            AuthState.INVALID,
            healthy=False,
            message="登录态已损坏(密文无法解密),请重新登录",
        )
    except Exception as e:
        logger.warning("账号 %s cookie 解密异常: %s", account.id, e)
        return HealthResult(
            AuthState.INVALID,
            healthy=False,
            message=f"登录态异常,请重新登录({type(e).__name__})",
        )

    # 3. cookie 列表非空
    if not cookies:
        return HealthResult(
            AuthState.INVALID,
            healthy=False,
            message="登录态为空,请重新登录",
        )

    # 4. 该平台关键登录态 cookie 存在
    expected = set(PLATFORM_LOGIN[account.platform]["login_cookies"])
    got = _cookie_names(cookies)
    if expected and not (expected & got):
        logger.warning("账号 %s 关键 cookie 缺失,期望 %s 实际 %s",
                       account.id, expected, got)
        return HealthResult(
            AuthState.INVALID,
            healthy=False,
            message=f"登录态已失效(关键 cookie 缺失),请重新登录",
        )

    # 全部通过
    return HealthResult(
        AuthState.VALID,
        healthy=True,
        message="登录态有效",
    )
