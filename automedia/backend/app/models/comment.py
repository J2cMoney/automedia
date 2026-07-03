"""Comment 模型 - 评论与 AI 回复(Phase 5 FLOW-5)。

对应 Product-Spec.md 4.4 Comment:
    id, content_id, platform_comment_id,
    author, text, ai_reply,
    status(待回/已回/转人工), replied_at

Phase 5 新建表。回评服务抓到新评论入 PENDING,生成 ai_reply 后调 Playwright
回评,成功转 REPLIED,失败或质量存疑转 MANUAL 等人工处理。
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CommentStatus(str, enum.Enum):
    """评论回复状态机。抓到即 PENDING -> 回成功 REPLIED -> 失败/存疑 MANUAL。"""
    PENDING = "pending"    # 新抓到,待生成回复 + 待发送
    REPLIED = "replied"    # 已自动回复成功
    MANUAL = "manual"      # 转人工(LLM 失败/Playwright 失败/质量存疑)


class Comment(TimestampMixin, Base):
    """一条平台评论 + AI 生成的回复。每条评论绑定一条 Content(发布出去的内容)。"""
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 关联 Content(评论是发在这条 Content 对应的帖子下)
    content_id: Mapped[int] = mapped_column(
        ForeignKey("contents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 平台侧评论 id(用于去重 + 定位 DOM 回评)
    platform_comment_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    # 评论作者昵称(平台抓到的)
    author: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    # 评论原文
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # AI 生成的回复(生成后存,即便发送失败也留底)
    ai_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 回复状态
    status: Mapped[CommentStatus] = mapped_column(
        Enum(CommentStatus), nullable=False, default=CommentStatus.PENDING, index=True
    )
    # 回复成功时间(用于限速统计 + 审计)
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 失败/转人工原因
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Comment {self.id} content={self.content_id} status={self.status.value}>"


__all__ = ["Comment", "CommentStatus"]
