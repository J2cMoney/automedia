"""内容 CRUD 路由 - Phase 3 FLOW-2 产出物查看。

对应 Product-Spec.md:
    - 4.4 Content 数据模型
    - FLOW-2 文案产出后查看

路由:
    GET /api/contents             内容列表(支持账号/状态筛选)
    GET /api/contents/{id}        内容详情(文案+脚本)

Phase 5 加发布相关路由(发布/重试),Phase 3 只读。
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.content import Content, ContentStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contents", tags=["内容"])


class ContentOut(BaseModel):
    id: int
    account_id: int
    topic_id: Optional[int]
    title: Optional[str]
    body: Optional[str]
    tags: List[str]
    video_script: List[dict]
    status: ContentStatus
    platform_post_url: Optional[str]
    error_log: Optional[str]
    # Phase 4 视频字段
    video_path: Optional[str] = None
    script_scenes: Optional[Dict[str, Any]] = None
    clip_decision: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _to_out(c: Content) -> ContentOut:
    return ContentOut(
        id=c.id,
        account_id=c.account_id,
        topic_id=c.topic_id,
        title=c.title,
        body=c.body,
        tags=c.tags or [],
        video_script=c.video_script or [],
        status=c.status,
        platform_post_url=c.platform_post_url,
        error_log=c.error_log,
        video_path=c.video_path,
        script_scenes=c.script_scenes,
        clip_decision=c.clip_decision,
        created_at=c.created_at.isoformat() if c.created_at else "",
        updated_at=c.updated_at.isoformat() if c.updated_at else "",
    )


@router.get("", response_model=List[ContentOut])
async def list_contents(
    account_id: Optional[int] = Query(default=None),
    status_filter: Optional[ContentStatus] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """内容列表。支持按账号/状态筛选,按创建时间倒序。"""
    stmt = select(Content).order_by(Content.created_at.desc())
    if account_id is not None:
        stmt = stmt.where(Content.account_id == account_id)
    if status_filter is not None:
        stmt = stmt.where(Content.status == status_filter)
    result = await db.execute(stmt)
    return [_to_out(c) for c in result.scalars().all()]


@router.get("/{content_id}", response_model=ContentOut)
async def get_content(content_id: int, db: AsyncSession = Depends(get_db)):
    c = await db.get(Content, content_id)
    if c is None:
        raise HTTPException(status_code=404, detail=f"内容 {content_id} 不存在")
    return _to_out(c)
