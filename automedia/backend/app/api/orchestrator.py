"""编排 API - Phase 6 全链路串联(Spec FLOW-7 面板 + FLOW-8 编排 + A-8 人机协同)。

路由:
    POST /api/orchestrator/daily              启动"今日运营"全链路(后台跑,立即返回 batch_id)
    GET  /api/orchestrator/batches/{batch_id} 查批次状态(各账号进度)
    GET  /api/orchestrator/pending            列所有待发布 Content(approved 态)

架构(方案 B 进程内协调器):
    POST /daily 校验账号后,调 orchestrator.run_daily_pipeline() 起 asyncio 后台任务,
    立即返回 batch_id。前端轮询 GET /batches/{batch_id} 看进度,到"待发布"停。
    发布由用户逐条触发(走已有 publish.py 线程池,A-8 人机协同铁律)。
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.account import Account, AuthState
from app.models.content import Content, ContentStatus
from app.services import orchestrator as orch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestrator", tags=["编排"])


# ---------- 请求/响应模型 ----------

class DailyStartRequest(BaseModel):
    """启动全链路请求。"""
    account_ids: List[int] = Field(..., min_length=1, description="要跑的账号 id 列表")
    exclude_words: Optional[List[str]] = Field(default=None, description="热点排除词")
    max_topics: int = Field(default=20, ge=1, le=100)
    scene_count: int = Field(default=6, ge=1, le=20)
    video_whisper_fallback: bool = Field(default=False)


class DailyStartResponse(BaseModel):
    batch_id: str
    account_ids: List[int]
    message: str


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    account_ids: List[int]
    summary: dict
    results: dict


class PendingContentOut(BaseModel):
    content_id: int
    account_id: Optional[int] = None
    account_nickname: Optional[str] = None
    platform: Optional[str] = None
    title: Optional[str] = None
    video_path: Optional[str] = None
    has_video: bool


# ---------- 路由 ----------

@router.post("/daily", response_model=DailyStartResponse)
async def start_daily_pipeline(req: DailyStartRequest, db: AsyncSession = Depends(get_db)):
    """启动"今日运营"全链路(后台 async 跑,立即返回 batch_id)。

    前置校验(Spec FLOW-1 输入校验):
        - 所有账号存在
        - 所有账号 auth_status = valid(登录态有效)
        - 所有账号 topic_theme 非空(无主题无法生成文案)

    编排器到"视频成片"就停(A-8),产出待发布 Content(approved),
    发布由用户逐条触发(走已有 POST /api/publish/{content_id})。
    """
    # 拉账号
    stmt = sa_select(Account).where(Account.id.in_(req.account_ids))
    accounts = (await db.execute(stmt)).scalars().all()
    found_ids = {a.id for a in accounts}
    missing = set(req.account_ids) - found_ids
    if missing:
        raise HTTPException(404, f"账号不存在: {sorted(missing)}")

    # 校验登录态 + 主题
    for acc in accounts:
        if acc.auth_status != AuthState.VALID:
            raise HTTPException(
                400,
                f"账号 {acc.nickname}({acc.id})登录态无效({acc.auth_status.value}),"
                f"请先在账号管理重新登录",
            )
        if not (acc.topic_theme or "").strip():
            raise HTTPException(
                400,
                f"账号 {acc.nickname}({acc.id})未配置主题(topic_theme),"
                f"请先在账号管理配置主题",
            )

    # 启动后台编排(asyncio.create_task 在事件循环里跑)
    batch_id = await orch.run_daily_pipeline(
        account_ids=req.account_ids,
        exclude_words=req.exclude_words,
        max_topics=req.max_topics,
        scene_count=req.scene_count,
        video_whisper_fallback=req.video_whisper_fallback,
    )

    return DailyStartResponse(
        batch_id=batch_id,
        account_ids=req.account_ids,
        message="全链路已启动(热点→文案→视频成片),到成片即停,发布请到流水线页逐条触发",
    )


@router.get("/batches/{batch_id}", response_model=BatchStatusResponse)
async def get_batch_status_route(batch_id: str):
    """查批次状态(各账号进度 + 摘要)。"""
    st = orch.get_batch_status(batch_id)
    if st is None:
        raise HTTPException(404, f"批次 {batch_id} 不存在或已过期")
    return BatchStatusResponse(**st)


@router.get("/pending", response_model=List[PendingContentOut])
async def list_pending_contents_route():
    """列所有待发布 Content(approved 态,供前端"待发布"区展示)。"""
    items = orch.list_pending_publish_contents()
    return [PendingContentOut(**it) for it in items]
