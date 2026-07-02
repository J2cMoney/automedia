"""Content 模型 - 内容(文案/脚本/视频路径/发布状态)。

对应 Product-Spec.md 4.4 Content:
    id, account_id, topic_id,
    title, body, tags, video_script,
    status(生成中/待审/已审/发布中/已发布/失败),
    platform_post_url, created_at, published_at

Phase 3 只建文案相关字段(title/body/tags/video_script);
Phase 4 在本表加视频字段(video_path / script_scenes JSON / clip_decision JSON)。
这是 DEV-PLAN 明确的增量计划,不要在 Phase 3 提前加视频列。
"""
import enum
import json
from typing import List, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.models.base import Base, TimestampMixin


class ContentStatus(str, enum.Enum):
    """内容状态机。文案生成 -> 待审 -> 发布中 -> 已发布。
    FLOW-2 产出文案后进 GENERATING,生成完进 PENDING_REVIEW。
    """
    GENERATING = "generating"        # 文案/脚本生成中
    PENDING_REVIEW = "pending_review"  # 文案就绪,待人工审核(SHOULD 人工微调)
    APPROVED = "approved"            # 已审核,待进 Phase 4 视频环节
    PUBLISHING = "publishing"        # 发布中(Phase 5)
    PUBLISHED = "published"          # 已发布(Phase 5)
    FAILED = "failed"                # 生成/发布失败


class _JsonList(TypeDecorator):
    """SQLite 下用 Text 存 JSON 列表的自定义类型。
    用于标签列表(tags)和视频脚本分镜列表(video_script),读写自动序列化。
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return []
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return []


class Content(TimestampMixin, Base):
    """一条内容:选题 + 账号 -> 文案 + 视频脚本 (+ Phase 4 视频)。

    每条 Content 绑定一个账号(因平台调性不同,文案按账号生成)。
    同一选题分发给多个账号时,每个账号各一条 Content。
    """
    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 关联账号(文案按账号主题+平台调性生成)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 关联选题
    topic_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 文案(FLOW-2 产出)
    title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[List[str]] = mapped_column(_JsonList, nullable=False, default=list)
    # 视频脚本(分镜列表,FLOW-2 产出,FineScene 渲染在 Phase 4)
    # 每镜含 index/narration(口播)/visual(画面描述)/duration
    video_script: Mapped[List] = mapped_column(_JsonList, nullable=False, default=list)
    # 内容状态
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus), nullable=False, default=ContentStatus.GENERATING, index=True
    )
    # 发布链接(Phase 5 填,Phase 3 留空)
    platform_post_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    published_at: Mapped[Optional] = mapped_column(DateTime, nullable=True)
    # 失败原因(生成/发布失败时记)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Phase 4 待加列(video_path / script_scenes JSON / clip_decision JSON)----
    # Phase 3 不提前加,Phase 4 建表后用 init_db 轻量迁移 ALTER TABLE 补。

    def __repr__(self) -> str:
        return f"<Content {self.id} account={self.account_id} status={self.status.value}>"


__all__ = ["Content", "ContentStatus"]
