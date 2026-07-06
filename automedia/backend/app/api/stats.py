"""统计 + 任务日志 + 配置 API - Phase 6(SCREEN-5 数据概览 + SCREEN-6 日志与设置)。

路由:
    GET /api/stats   聚合 Content + Comment → 发布数/待发布/评论数/已回复率(NON-7 边界:只回显)
    GET /api/tasks   任务列表(查 task_runs,带 limit/status/flow_type 筛选)
    GET /api/config  只读配置展示(DeepSeek/GLM 模型名 + 风控参数,不返回任何密钥)

Spec NON-7 边界:v1 只做基础数据回显,不做趋势/归因/预测等 BI 能力。
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models.comment import Comment, CommentStatus
from app.models.content import Content, ContentStatus
from app.models.task_run import TaskRun, TaskStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["统计与日志"])


# ---------- 响应模型 ----------

class StatsResponse(BaseModel):
    """数据概览聚合(NON-7:基础回显,不做 BI)。"""
    contents_total: int
    published: int
    pending_publish: int
    pending_review: int
    failed: int
    comments_total: int
    replied: int
    reply_pending: int
    replied_rate: float  # 0.0-1.0


class TaskOut(BaseModel):
    id: int
    flow_type: str
    status: str
    message_id: Optional[str] = None
    retry_count: int
    account_id: Optional[int] = None
    content_id: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error_log: Optional[str] = None
    result: Optional[str] = None
    created_at: str


class ConfigOut(BaseModel):
    """只读配置展示(SCREEN-6 日志与设置,绝不返回密钥)。"""
    deepseek_model: str
    glm_model: str
    glm_base_url: str
    max_browser_concurrency: int
    max_render_concurrency: int
    publish_interval_minutes: int
    reply_interval_seconds: int
    reply_max_per_poll: int


# ---------- 路由 ----------

@router.get("/stats", response_model=StatsResponse)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """数据概览聚合(Content + Comment,SQL 一把梭)。"""
    # Content 按状态分组计数
    stmt = sa_select(Content.status, func.count(Content.id)).group_by(Content.status)
    rows = (await db.execute(stmt)).all()
    status_counts = {r[0]: r[1] for r in rows}
    total = sum(status_counts.values())

    # Comment 按状态分组计数
    cstmt = sa_select(Comment.status, func.count(Comment.id)).group_by(Comment.status)
    crows = (await db.execute(cstmt)).all()
    c_counts = {r[0]: r[1] for r in crows}
    c_total = sum(c_counts.values())
    c_replied = c_counts.get(CommentStatus.REPLIED, 0)
    replied_rate = (c_replied / c_total) if c_total > 0 else 0.0

    return StatsResponse(
        contents_total=total,
        published=status_counts.get(ContentStatus.PUBLISHED, 0),
        pending_publish=status_counts.get(ContentStatus.APPROVED, 0),
        pending_review=status_counts.get(ContentStatus.PENDING_REVIEW, 0),
        failed=status_counts.get(ContentStatus.FAILED, 0),
        comments_total=c_total,
        replied=c_replied,
        reply_pending=c_counts.get(CommentStatus.PENDING, 0),
        replied_rate=round(replied_rate, 4),
    )


@router.get("/tasks", response_model=List[TaskOut])
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=500),
    status: Optional[str] = Query(default=None, description="pending/running/finished/failed/cancelled"),
    flow_type: Optional[str] = Query(default=None, description="hotspot/copy/video/publish/reply"),
    db: AsyncSession = Depends(get_db),
):
    """任务列表(查 task_runs,带筛选,默认最近 50 条)。"""
    stmt = sa_select(TaskRun).order_by(TaskRun.created_at.desc()).limit(limit)
    if status:
        try:
            task_status = TaskStatus(status)
            stmt = stmt.where(TaskRun.status == task_status)
        except ValueError:
            pass  # 非法 status 忽略
    if flow_type:
        stmt = stmt.where(TaskRun.flow_type == flow_type)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        TaskOut(
            id=t.id, flow_type=t.flow_type, status=t.status.value,
            message_id=t.message_id, retry_count=t.retry_count,
            account_id=t.account_id, content_id=t.content_id,
            started_at=t.started_at.isoformat() if t.started_at else None,
            finished_at=t.finished_at.isoformat() if t.finished_at else None,
            error_log=t.error_log, result=t.result,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )
        for t in rows
    ]


@router.get("/config", response_model=ConfigOut)
async def get_config():
    """只读配置展示(SCREEN-6,绝不返回任何密钥,只返回模型名和风控参数)。"""
    return ConfigOut(
        deepseek_model=settings.DEEPSEEK_MODEL,
        glm_model=settings.GLM_MODEL,
        glm_base_url=settings.GLM_BASE_URL,
        max_browser_concurrency=settings.MAX_BROWSER_CONCURRENCY,
        max_render_concurrency=settings.MAX_RENDER_CONCURRENCY,
        publish_interval_minutes=settings.PUBLISH_INTERVAL_MINUTES,
        reply_interval_seconds=settings.REPLY_INTERVAL_SECONDS,
        reply_max_per_poll=settings.REPLY_MAX_PER_POLL,
    )
