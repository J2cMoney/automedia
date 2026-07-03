"""评论路由 - Phase 5 FLOW-5。

对应 Product-Spec.md FLOW-5(自动回评论)。

路由:
    GET  /api/comments                评论列表(按 content_id 筛选)
    POST /api/comments/{content_id}/reply  提交自动回评任务(异步)

异步模式:回评提交返回 task_id(含限速,长任务),前端轮询 /tasks/{task_id} 看进度。
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.account import Account, Platform
from app.models.comment import Comment, CommentStatus
from app.queue import submit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comments", tags=["评论"])


# ---------- 请求/响应模型 ----------

class ReplyRequest(BaseModel):
    """自动回评请求。max_replies 留空则用 config.REPLY_MAX_PER_POLL。"""
    max_replies: Optional[int] = None


class TaskResponse(BaseModel):
    task_id: int
    content_id: int
    message: str


class CommentOut(BaseModel):
    id: int
    content_id: int
    platform_comment_id: Optional[str]
    author: Optional[str]
    text: str
    ai_reply: Optional[str]
    status: CommentStatus
    replied_at: Optional[str]
    error_log: Optional[str]
    created_at: str

    model_config = {"from_attributes": True}


def _to_out(c: Comment) -> CommentOut:
    return CommentOut(
        id=c.id,
        content_id=c.content_id,
        platform_comment_id=c.platform_comment_id,
        author=c.author,
        text=c.text,
        ai_reply=c.ai_reply,
        status=c.status,
        replied_at=c.replied_at.isoformat() if c.replied_at else None,
        error_log=c.error_log,
        created_at=c.created_at.isoformat() if c.created_at else "",
    )


# ---------- 评论列表 ----------

@router.get("", response_model=List[CommentOut])
async def list_comments(
    content_id: Optional[int] = Query(default=None, description="按内容筛选"),
    status_filter: Optional[CommentStatus] = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """评论列表。支持按 content_id / status 筛选,按创建时间倒序。"""
    stmt = select(Comment).order_by(Comment.created_at.desc())
    if content_id is not None:
        stmt = stmt.where(Comment.content_id == content_id)
    if status_filter is not None:
        stmt = stmt.where(Comment.status == status_filter)
    result = await db.execute(stmt)
    return [_to_out(c) for c in result.scalars().all()]


# ---------- 自动回评 ----------

@router.post("/{content_id}/reply", response_model=TaskResponse)
async def trigger_reply(
    content_id: int,
    req: ReplyRequest,
    db: AsyncSession = Depends(get_db),
):
    """提交自动回评任务(异步)。

    前置:Content 已发布(platform_post_url 有值)+ 关联账号 cookie 有效。
    任务内:fetch 评论 -> DeepSeek 生成回复 -> Playwright 模拟回复,带限速防风控。
    视频号不支持自动回评(Spec A-5 半自动)。
    """
    from app.models.content import Content

    content = await db.get(Content, content_id)
    if content is None:
        raise HTTPException(404, f"Content {content_id} 不存在")

    if not content.platform_post_url:
        raise HTTPException(400, f"Content {content_id} 未发布(无 platform_post_url),无法抓评论")

    account = await db.get(Account, content.account_id) if content.account_id else None
    if account is None:
        raise HTTPException(400, f"Content {content_id} 无关联账号")

    if account.platform == Platform.WECHAT:
        raise HTTPException(400, "视频号不支持自动回评(Spec A-5 半自动)")

    kwargs = {"content_id": content_id}
    if req.max_replies is not None:
        kwargs["max_replies"] = req.max_replies

    task_id = submit(
        "reply", "reply_comments_task",
        run_content_id=content_id,
        run_account_id=content.account_id,
        **kwargs,
    )
    return TaskResponse(
        task_id=task_id,
        content_id=content_id,
        message=f"自动回评任务已提交,轮询 /tasks/{task_id}",
    )
