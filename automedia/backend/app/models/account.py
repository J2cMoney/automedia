"""Account 模型 - 账号矩阵。

对应 Product-Spec.md 4.4 Account:
    id, platform, nickname, topic_theme,
    auth_state(加密 cookie/登录态), status(启用/禁用),
    created_at, updated_at

Phase 1 只建表 + 基础字段;登录态加密存 Phase 2 实现(Fernet)。
"""
import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Platform(str, enum.Enum):
    """支持平台。小红书/抖音/快手全自动,视频号半自动(Spec SCOPE-1)。"""
    XHS = "xhs"   # 小红书
    DOUYIN = "dy"  # 抖音
    KUAISHOU = "ks"  # 快手
    WECHAT = "wx"  # 视频号(半自动)


class AccountStatus(str, enum.Enum):
    """账号状态。"""
    ACTIVE = "active"      # 启用
    DISABLED = "disabled"  # 禁用


class AuthState(str, enum.Enum):
    """登录态有效性(Phase 2 FLOW-6:健康检查后持久化结果,供 UI 徽章四态)。

    - VALID:健康检查通过,cookie 有效
    - INVALID:cookie 失效/损坏,需重新登录
    - UNKNOWN:未登录或未检查过
    """
    VALID = "valid"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class Account(TimestampMixin, Base):
    """账号矩阵中的一条账号记录。"""
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), nullable=False, index=True)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    # 账号主题(各号独立主题,Spec FLOW-6 MUST)
    topic_theme: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # 加密后的登录态 cookie(Fernet 加密密文,绝不返回前端)
    auth_state: Mapped[str] = mapped_column(String, nullable=False, default="")
    # 登录态有效性(健康检查后更新,UI 徽章四态色依据)
    auth_status: Mapped[AuthState] = mapped_column(
        Enum(AuthState), nullable=False, default=AuthState.UNKNOWN
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE
    )

    def __repr__(self) -> str:
        return f"<Account {self.platform.value}:{self.nickname}>"
