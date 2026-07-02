"""TaskRun 模型 - 任务运行记录。

对应 Product-Spec.md 4.4 TaskRun:
    id, flow_type, account_id, content_id,
    status, started_at, finished_at, error_log

Phase 1 核心:任务队列状态同步写这张表,服务重启读表恢复(Spec FLOW-8 / DEV-PLAN Phase 1)。
是"断点续跑"的根基。
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaskStatus(str, enum.Enum):
    """任务状态机。队列提交→pending→running→finished/failed/cancelled。

    重启恢复逻辑:running 状态在重启时回到 pending(未完成的重新入队)。
    """
    PENDING = "pending"      # 已提交,排队中
    RUNNING = "running"      # 执行中
    FINISHED = "finished"    # 成功完成
    FAILED = "failed"        # 失败(可重试)
    CANCELLED = "cancelled"  # 已取消


class TaskRun(TimestampMixin, Base):
    """一条任务的运行记录,贯穿全链路(FLOW-1 到 FLOW-5)。"""
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Dramatiq message_id,关联 broker 里的任务
    message_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    # 任务类型(hotspot/copywrite/video/publish/comment 等,各 Phase 定义)
    flow_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # 关联账号/内容(Phase 1 允许空,业务 Phase 填)
    account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content_id: Mapped[Optional[int]] = mapped_column(nullable=True, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # 错误日志(失败时记)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 重试次数(Spec FLOW-8:最多 3 次)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    # 任务结果(成功时存 JSON 字符串)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<TaskRun {self.flow_type}:{self.status.value}#{self.id}>"
