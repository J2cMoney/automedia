"""Topic 模型 - 选题候选。

对应 Product-Spec.md 4.4 Topic:
    id, source_platform, title, heat_score,
    matched_account_ids[], status(候选/采纳/弃用), created_at

Phase 3 建表+业务:热点爬取产出选题候选,文案生成消费已采纳的选题。
主题过滤(Spec FLOW-1 MUST)在 crawler 层做,这里只存过滤排序后的结果。

matched_account_ids:与账号主题匹配度排序后的候选账号 id 列表,
    crawler 按各账号 topic_theme 打分后填入,文案生成时按此分发。
    用 JSON 字符串存(SQLite 无原生数组)。
"""
import enum
import json
from typing import List, Optional

from sqlalchemy import Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.models.account import Platform
from app.models.base import Base, TimestampMixin


class TopicStatus(str, enum.Enum):
    """选题状态机。候选 -> 采纳(进文案生成) / 弃用。"""
    CANDIDATE = "candidate"   # 候选,待用户确认
    ADOPTED = "adopted"       # 已采纳,进入文案生成
    DISCARDED = "discarded"   # 弃用


class _IntList(TypeDecorator):
    """SQLite 下用 Text 存 JSON 整数列表的自定义类型。
    读写自动序列化/反序列化,业务层直接拿到 list[int]。
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(list(value), ensure_ascii=False)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return []
        try:
            return [int(x) for x in json.loads(value)]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []


class Topic(TimestampMixin, Base):
    """一条热点选题候选,由 FLOW-1 热点采集产出。"""
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 来源平台(热榜来源)
    source_platform: Mapped[Platform] = mapped_column(
        Enum(Platform), nullable=False, index=True
    )
    # 热榜标题/选题文本(热榜词条或笔记标题)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # 热度分(平台热榜原始热度,用于排序)
    heat_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 来源 URL(可选,有的平台热榜带跳转链接)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    # 与账号主题匹配的候选账号 id 列表(crawler 按 topic_theme 打分排序后填入)
    matched_account_ids: Mapped[List[int]] = mapped_column(
        _IntList, nullable=False, default=list
    )
    # 选题状态
    status: Mapped[TopicStatus] = mapped_column(
        Enum(TopicStatus), nullable=False, default=TopicStatus.CANDIDATE, index=True
    )
    # 主题匹配度(0-1,crawler 综合计算,用于前端展示"匹配度")
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    def __repr__(self) -> str:
        return f"<Topic {self.source_platform.value}:{self.title[:20]}>"


__all__ = ["Topic", "TopicStatus"]
