"""选题 CRUD + 热点爬取路由 - Phase 3 FLOW-1。

对应 Product-Spec.md:
    - FLOW-1 热点采集与选题(爬热榜 -> 按主题过滤 -> 候选选题)
    - 4.4 Topic 数据模型

路由:
    GET    /api/topics                 选题列表(支持状态/平台筛选)
    POST   /api/topics/crawl           触发热点爬取(异步任务,按账号爬热榜入库)
    GET    /api/topics/{id}            选题详情
    POST   /api/topics/{id}/adopt      采纳选题(进文案生成)
    POST   /api/topics/{id}/discard    弃用选题
    POST   /api/topics/{id}/generate   生成文案+脚本(采纳后调 copywriter 产出 Content)

热点爬取用 Dramatiq 异步(长任务),复用 Phase 1 queue.py submit/status。
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.models.account import Account, Platform
from app.models.content import Content, ContentStatus
from app.models.topic import Topic, TopicStatus
from app.queue import submit as submit_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/topics", tags=["选题与热点"])


# ---------- 请求/响应模型 ----------

class TopicOut(BaseModel):
    id: int
    source_platform: Platform
    title: str
    heat_score: float
    source_url: Optional[str]
    matched_account_ids: List[int]
    status: TopicStatus
    match_score: float
    created_at: str

    model_config = {"from_attributes": True}


class CrawlRequest(BaseModel):
    """触发热点爬取。"""
    account_id: int = Field(description="用哪个账号的 cookie 爬(决定平台 + 主题过滤)")
    exclude_words: List[str] = Field(default_factory=list, description="排除词(Spec FLOW-1 MUST)")
    max_results: int = Field(default=20, ge=1, le=100)


class CrawlResponse(BaseModel):
    task_id: int
    account_id: int
    message: str


class GenerateRequest(BaseModel):
    """基于选题生成文案+脚本。"""
    account_id: int = Field(description="为哪个账号生成(决定平台调性)")
    scene_count: int = Field(default=6, ge=3, le=15, description="视频分镜数")


# ---------- 内部工具 ----------

def _to_out(t: Topic) -> TopicOut:
    return TopicOut(
        id=t.id,
        source_platform=t.source_platform,
        title=t.title,
        heat_score=t.heat_score,
        source_url=t.source_url,
        matched_account_ids=t.matched_account_ids or [],
        status=t.status,
        match_score=t.match_score,
        created_at=t.created_at.isoformat() if t.created_at else "",
    )


# ---------- CRUD ----------

@router.get("", response_model=List[TopicOut])
async def list_topics(
    status_filter: Optional[TopicStatus] = Query(default=None, alias="status"),
    platform: Optional[Platform] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """选题列表。支持按状态/平台筛选,按匹配度+热度倒序。"""
    stmt = select(Topic).order_by(Topic.match_score.desc(), Topic.heat_score.desc())
    if status_filter is not None:
        stmt = stmt.where(Topic.status == status_filter)
    if platform is not None:
        stmt = stmt.where(Topic.source_platform == platform)
    result = await db.execute(stmt)
    return [_to_out(t) for t in result.scalars().all()]


@router.get("/{topic_id}", response_model=TopicOut)
async def get_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(Topic, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"选题 {topic_id} 不存在")
    return _to_out(t)


@router.post("/{topic_id}/adopt", response_model=TopicOut)
async def adopt_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    """采纳选题(状态 candidate -> adopted)。"""
    t = await db.get(Topic, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"选题 {topic_id} 不存在")
    if t.status != TopicStatus.CANDIDATE:
        raise HTTPException(status_code=400, detail=f"选题当前状态 {t.status.value},无法采纳")
    t.status = TopicStatus.ADOPTED
    await db.commit()
    await db.refresh(t)
    return _to_out(t)


@router.post("/{topic_id}/discard", response_model=TopicOut)
async def discard_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    """弃用选题。"""
    t = await db.get(Topic, topic_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"选题 {topic_id} 不存在")
    t.status = TopicStatus.DISCARDED
    await db.commit()
    await db.refresh(t)
    return _to_out(t)


# ---------- 热点爬取(异步任务) ----------

@router.post("/crawl", response_model=CrawlResponse)
async def crawl_hotspots(req: CrawlRequest, db: AsyncSession = Depends(get_db)):
    """触发热点爬取(异步任务)。

    流程:校验账号 -> 提交 Dramatiq 任务(crawl_hotspot_task) -> 返回 task_id。
    前端用 task_id 查 /tasks/{task_id} 状态,完成后查 /api/topics 看新候选。
    worker 需运行(dramatiq app.queue)。
    """
    acc = await db.get(Account, req.account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {req.account_id} 不存在")

    task_id = submit_task(
        "hotspot",
        "crawl_hotspot_task",
        # account_id 既是 actor 业务参数(爬哪个号),也关联 task_run
        account_id=req.account_id,
        run_account_id=req.account_id,
        exclude_words=req.exclude_words,
        max_results=req.max_results,
    )
    logger.info("提交热点爬取 task_id=%s account=%s", task_id, req.account_id)
    return CrawlResponse(
        task_id=task_id,
        account_id=req.account_id,
        message="热点爬取任务已提交,worker 运行后自动执行",
    )


# ---------- 文案+脚本生成 ----------

@router.post("/{topic_id}/generate", response_model=dict)
async def generate_from_topic(
    topic_id: int,
    req: GenerateRequest,
    db: AsyncSession = Depends(get_db),
):
    """基于选题生成文案+视频脚本,产出一条 Content。

    Spec FLOW-2:选题确认后按账号主题+平台调性生成。
    copywriter 调用(同步,DeepSeek 文本生成快,15s 内)。
    失败兜底(Spec 5.3):重试 3 次仍失败,Content 标 FAILED 不阻塞。
    """
    topic = await db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail=f"选题 {topic_id} 不存在")
    acc = await db.get(Account, req.account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {req.account_id} 不存在")
    if not acc.topic_theme:
        raise HTTPException(status_code=400, detail="账号未配置主题(topic_theme),无法生成文案")

    # 建 Content(初始 GENERATING),生成失败也保留记录
    content = Content(
        account_id=acc.id,
        topic_id=topic.id,
        status=ContentStatus.GENERATING,
    )
    db.add(content)
    await db.commit()
    await db.refresh(content)

    # copywriter 在线程池跑(不阻塞事件循环)
    try:
        result = await run_in_threadpool(
            _generate_copy_and_script,
            topic_title=topic.title,
            topic_theme=acc.topic_theme,
            platform=acc.platform,
            scene_count=req.scene_count,
        )
        content.title = result["title"]
        content.body = result["body"]
        content.tags = result["tags"]
        content.video_script = result["video_script"]
        content.status = ContentStatus.PENDING_REVIEW
        await db.commit()
        await db.refresh(content)
        logger.info("文案生成成功 content_id=%s topic=%s", content.id, topic_id)
        return {"content_id": content.id, "status": content.status.value}
    except Exception as e:
        content.status = ContentStatus.FAILED
        content.error_log = f"{type(e).__name__}: {e}"
        await db.commit()
        logger.error("文案生成失败 content_id=%s: %s", content.id, e)
        # detail 用固定文案,不把异常细节透传给前端(信息泄漏面)
        # 详细错误已在 content.error_log + 服务端日志,前端可查 /api/contents/{id}
        raise HTTPException(status_code=500, detail="文案生成失败,请查看内容详情或重试")


def _generate_copy_and_script(
    topic_title: str,
    topic_theme: str,
    platform: Platform,
    scene_count: int,
) -> dict:
    """同步生成文案+脚本(在线程池跑)。"""
    from app.services.copywriter import generate_copy, generate_script

    copy = generate_copy(topic_title, topic_theme, platform)
    script = generate_script(topic_title, topic_theme, copy.body, scene_count=scene_count)
    return {
        "title": copy.title,
        "body": copy.body,
        "tags": copy.tags,
        "video_script": [
            {
                "index": s.index,
                "narration": s.narration,
                "visual": s.visual,
                "duration": s.duration,
            }
            for s in script.scenes
        ],
    }
