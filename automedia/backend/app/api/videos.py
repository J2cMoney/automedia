"""视频生成路由 - Phase 4 FLOW-3 场景 A/B。

对应 Product-Spec.md FLOW-3(场景 A 高光提取 + 场景 B 从零生成)。

路由:
    POST /api/videos/upload         上传源长视频(场景 A 输入)
    POST /api/videos/extract        提交场景 A 高光提取任务
    POST /api/videos/generate       提交场景 B 从零生成任务
    GET  /api/videos/{content_id}   查视频生成状态(复用 Content 字段)

异步模式:提交返回 task_id,前端轮询 /tasks/{task_id} 看进度,完成查 /api/contents/{id} 看成片。
"""
import logging
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import BASE_DIR, settings
from app.db import get_db
from app.models.content import Content, ContentStatus
from app.queue import submit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["视频生成"])

# 上传文件存放目录(automedia/data/uploads/,已 gitignore)
UPLOADS_DIR = BASE_DIR / "data" / "uploads"


# ---------- 请求/响应模型 ----------

class VideoExtractRequest(BaseModel):
    """场景 A:高光提取请求。"""
    content_id: int = Field(..., description="关联的 Content id(成片写回)")
    source_video_path: str = Field(..., description="源长视频本地路径(上传返回的路径)")
    target_duration: int = Field(default=60, ge=15, le=180, description="目标成片秒数")


class VideoGenerateRequest(BaseModel):
    """场景 B:从零生成请求。"""
    content_id: int = Field(..., description="关联的 Content id(需已有 video_script)")
    whisper_fallback: bool = Field(default=False, description="TTS 无字幕时是否用 Whisper 备选")


class TaskResponse(BaseModel):
    """异步任务提交响应。"""
    task_id: int
    content_id: int
    message: str


class UploadResponse(BaseModel):
    """文件上传响应。"""
    path: str
    filename: str
    size: int


class VideoStatusOut(BaseModel):
    """视频生成状态查询。"""
    content_id: int
    video_path: Optional[str] = None
    script_scenes: Optional[dict] = None
    clip_decision: Optional[dict] = None
    status: ContentStatus

    model_config = {"from_attributes": True}


# ---------- 文件上传(场景 A 输入)----------

@router.post("/upload", response_model=UploadResponse)
async def upload_source_video(file: UploadFile = File(...)):
    """上传源长视频(场景 A 高光提取的输入)。

    存到 automedia/data/uploads/,返回本地路径供 /extract 使用。
    限制:仅视频格式(mp4/mov/avi/mkv),大小上限 500MB。
    """
    allowed = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    # 安全:只取纯文件名(防路径穿越,如 filename="../../evil.dll")
    safe_name = Path(file.filename or "upload.mp4").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"不支持的格式 {suffix},仅支持 {allowed}")

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    # 时间戳前缀防冲突,用纯文件名(已消毒)
    import time
    dest = UPLOADS_DIR / f"{int(time.time())}_{safe_name}"

    size = 0
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):  # 1MB chunks
            f.write(chunk)
            size += len(chunk)
            if size > 500 * 1024 * 1024:
                f.close()
                dest.unlink()
                raise HTTPException(413, "文件超过 500MB 上限")

    logger.info("上传源视频: %s (%.1fMB)", dest.name, size / (1024 * 1024))
    return UploadResponse(path=str(dest), filename=dest.name, size=size)


# ---------- 场景 A:高光提取 ----------

@router.post("/extract", response_model=TaskResponse)
async def extract_highlights(req: VideoExtractRequest):
    """提交场景 A 高光提取任务(异步)。

    用户上传长视频 -> 抽帧 -> GLM 看帧找高光 -> 剪切拼接成 60s 短片。
    返回 task_id,前端轮询 /tasks/{task_id},完成后查 /api/contents/{id} 看成片。

    安全:source_video_path 必须在 UPLOADS_DIR 下(防路径穿越)。
    """
    from pathlib import Path as _Path

    src = _Path(req.source_video_path).resolve()
    uploads_resolved = UPLOADS_DIR.resolve()
    # 路径穿越防护:只允许读 UPLOADS_DIR 下的文件(用户上传的)
    try:
        src.relative_to(uploads_resolved)
    except ValueError:
        raise HTTPException(400, "源视频路径非法,必须通过 /api/videos/upload 上传")

    if not src.exists():
        raise HTTPException(400, f"源视频不存在: {req.source_video_path}")

    task_id = submit(
        "video_extract", "extract_highlights_task",
        content_id=req.content_id,
        source_video_path=str(src),
        target_duration=req.target_duration,
        run_content_id=req.content_id,
    )
    return TaskResponse(
        task_id=task_id,
        content_id=req.content_id,
        message=f"场景 A 高光提取任务已提交(目标 {req.target_duration}s),轮询 /tasks/{task_id}",
    )


# ---------- 场景 B:从零生成 ----------

@router.post("/generate", response_model=TaskResponse)
async def generate_video(req: VideoGenerateRequest, db: AsyncSession = Depends(get_db)):
    """提交场景 B 从零生成任务(异步)。

    Flow-2 脚本 -> scene plan -> Pexels 素材 + TTS + 字幕 -> Remotion 渲染。
    前置:Content 必须有 video_script(Flow-2 产出)。
    返回 task_id,前端轮询 /tasks/{task_id},完成后查 /api/contents/{id} 看成片。
    """
    content = await db.get(Content, req.content_id)
    if content is None:
        raise HTTPException(404, f"Content {req.content_id} 不存在")

    if not content.video_script:
        raise HTTPException(400, f"Content {req.content_id} 无 video_script,需先跑 Flow-2 文案/脚本生成")

    task_id = submit(
        "video_generate", "generate_video_task",
        content_id=req.content_id,
        whisper_fallback=req.whisper_fallback,
        run_content_id=req.content_id,
        run_account_id=content.account_id,
    )
    return TaskResponse(
        task_id=task_id,
        content_id=req.content_id,
        message=f"场景 B 从零生成任务已提交,轮询 /tasks/{task_id}",
    )


# ---------- 状态查询 ----------

@router.get("/{content_id}", response_model=VideoStatusOut)
async def get_video_status(content_id: int, db: AsyncSession = Depends(get_db)):
    """查视频生成状态(复用 Content 字段)。

    返回 video_path(成片)、script_scenes(场景B 分镜计划)、
    clip_decision(场景A 剪辑决策)、status。
    """
    content = await db.get(Content, content_id)
    if content is None:
        raise HTTPException(404, f"Content {content_id} 不存在")
    return VideoStatusOut(
        content_id=content.id,
        video_path=content.video_path,
        script_scenes=content.script_scenes,
        clip_decision=content.clip_decision,
        status=content.status,
    )
