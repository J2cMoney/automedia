"""发布路由 - Phase 5 FLOW-4(v1.6 人机协同)。

对应 Product-Spec.md FLOW-4(三平台人机协同 + 视频号纯手动)。

路由:
    POST /api/publish/{content_id}              启动辅助发布(线程池跑有头浏览器)
    GET  /api/publish/assist/{token}/status     轮询辅助发布状态
    POST /api/publish/wx/{content_id}/package   视频号纯手动打包

辅助发布架构(v1.6 修订):
    不走 Dramatiq worker(worker fork 进程跑 Playwright 有头浏览器会触发 Bad file descriptor)。
    改用后端线程池(run_in_executor)直接跑同步 Playwright,模块级 dict 存任务状态,
    前端轮询 GET status。适合"等用户在浏览器里点发布"这种分钟级交互长任务。
"""
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncSessionLocal, get_db
from app.models.account import Account, Platform
from app.models.content import Content, ContentStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/publish", tags=["发布"])

# 辅助发布专用线程池(隔离,避免阻塞其他请求)
_assist_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assist")

# 模块级任务状态(进程内,重启丢失——辅助发布是即时交互任务,不需要持久化)
_assist_tasks: Dict[str, dict] = {}


# ---------- 请求/响应模型 ----------

class PublishRequest(BaseModel):
    """辅助发布请求(v1.6 人机协同)。"""
    timeout_minutes: int = Field(default=5, ge=1, le=15, description="等用户点发布的超时(分钟)")


class AssistStartResponse(BaseModel):
    """辅助发布启动响应。"""
    token: str = Field(description="任务令牌,用于轮询状态")
    content_id: int
    message: str


class AssistStatusResponse(BaseModel):
    """辅助发布状态查询。"""
    token: str
    status: str = Field(description="running / success / failed / timeout")
    post_url: Optional[str] = None
    error: Optional[str] = None
    content_id: int


class WxPackageOut(BaseModel):
    """视频号待发布打包数据(对照设计稿 CMP-007)。"""
    title: str
    body: str
    tags: List[str]
    video_path: str
    cover_path: Optional[str] = None
    copy_text: str
    channels_url: str = "https://channels.weixin.qq.com"

    model_config = {"from_attributes": True}


# ---------- 人机协同辅助发布(小红书/抖音/快手)----------

@router.post("/{content_id}", response_model=AssistStartResponse)
async def publish_content_route(
    content_id: int,
    req: PublishRequest,
    db: AsyncSession = Depends(get_db),
):
    """启动辅助发布(v1.6 人机协同,线程池跑,不走队列)。

    后端起一个有头浏览器:自动上传视频 + 填文案 → 停住等用户点发布 → 抓 URL。
    持久化 profile 保留登录态(首次真人登录后免登)。
    视频号走纯手动打包(POST /api/publish/wx/{id}/package)。

    返回 token,前端用 GET /api/publish/assist/{token}/status 轮询。
    """
    content = await db.get(Content, content_id)
    if content is None:
        raise HTTPException(404, f"Content {content_id} 不存在")

    if not content.video_path:
        raise HTTPException(400, f"Content {content_id} 无视频文件,需先跑 Phase 4 生成成片")

    account = await db.get(Account, content.account_id) if content.account_id else None
    if account is None:
        raise HTTPException(400, f"Content {content_id} 无关联账号")

    if account.platform == Platform.WECHAT:
        raise HTTPException(
            400, "视频号走纯手动模式,请用 POST /api/publish/wx/"
                 f"{content_id}/package 打包后手动发布"
        )

    platform_name = {"xhs": "小红书", "dy": "抖音", "ks": "快手"}.get(
        account.platform.value, account.platform.value)

    # 生成任务 token + 初始化状态
    token = uuid.uuid4().hex[:12]
    _assist_tasks[token] = {
        "status": "running",
        "post_url": None,
        "error": None,
        "content_id": content_id,
    }

    # 标记 Content 发布中
    content.status = ContentStatus.PUBLISHING
    await db.commit()

    # 拷贝账号/内容数据传给线程(避免跨线程持有 async session 对象)
    account_id = account.id
    video_path = content.video_path
    timeout_minutes = req.timeout_minutes

    # 线程池跑同步 Playwright(不阻塞事件循环)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        _assist_executor,
        _run_assist_in_thread,
        token, account_id, content_id, video_path, timeout_minutes,
    )

    return AssistStartResponse(
        token=token,
        content_id=content_id,
        message=f"{platform_name}辅助发布已启动,浏览器即将弹出,请在浏览器中确认并点发布",
    )


@router.get("/assist/{token}/status", response_model=AssistStatusResponse)
async def get_assist_status(token: str):
    """轮询辅助发布状态。"""
    task = _assist_tasks.get(token)
    if task is None:
        raise HTTPException(404, f"任务 {token} 不存在或已过期")
    return AssistStatusResponse(
        token=token,
        status=task["status"],
        post_url=task["post_url"],
        error=task["error"],
        content_id=task["content_id"],
    )


def _run_assist_in_thread(token: str, account_id: int, content_id: int,
                          video_path: str, timeout_minutes: int) -> None:
    """在线程池里跑辅助发布(同步 Playwright)。

    完成后更新 _assist_tasks[token] + 写回 Content 状态。
    用同步 session(SyncSessionLocal),因为这是子线程不是 async。
    """
    from app.db import SyncSessionLocal
    from app.services.publish.assist import assist_publish

    try:
        with SyncSessionLocal() as s:
            account = s.get(Account, account_id)
            content = s.get(Content, content_id)
            if account is None or content is None:
                raise RuntimeError("账号或内容不存在")
            # 确保 video_path 是最新的
            content.video_path = video_path
            result = assist_publish(account, content, timeout_minutes=timeout_minutes)

        # 写回 Content 状态
        with SyncSessionLocal() as s:
            content = s.get(Content, content_id)
            if content is not None:
                if result.success:
                    content.status = ContentStatus.PUBLISHED
                    content.platform_post_url = result.post_url
                    content.published_at = datetime.utcnow()
                    content.error_log = None
                elif result.error and "超时" in result.error:
                    content.status = ContentStatus.APPROVED  # 超时回退,可再发
                else:
                    content.status = ContentStatus.FAILED
                content.error_log = result.error
                s.commit()

        # 更新任务状态
        if token in _assist_tasks:
            if result.success:
                _assist_tasks[token].update(status="success", post_url=result.post_url)
            elif result.error and "超时" in result.error:
                _assist_tasks[token].update(status="timeout", error=result.error)
            else:
                _assist_tasks[token].update(status="failed", error=result.error)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error("[辅助发布 token=%s] 线程异常: %s", token, err)
        if token in _assist_tasks:
            _assist_tasks[token].update(status="failed", error=err)
        with SyncSessionLocal() as s:
            content = s.get(Content, content_id)
            if content is not None:
                content.status = ContentStatus.FAILED
                content.error_log = err
                s.commit()
    finally:
        # 清理过期任务(保留最近 50 个,防内存泄漏)
        if len(_assist_tasks) > 50:
            oldest = list(_assist_tasks.keys())[:-50]
            for k in oldest:
                _assist_tasks.pop(k, None)


# ---------- 视频号纯手动打包 ----------

@router.post("/wx/{content_id}/package", response_model=WxPackageOut)
async def package_wx_route(
    content_id: int,
    db: AsyncSession = Depends(get_db),
):
    """视频号纯手动打包(Spec FLOW-4 半自动档 + A-5)。

    不开浏览器,纯数据组装:把 Content 的标题/正文/标签/视频路径打包成 WxPackage,
    前端渲染成待发布卡片(CMP-007),用户复制后到视频号助手手动发布。
    """
    from app.services.publish.wx import package_wx_content

    content = await db.get(Content, content_id)
    if content is None:
        raise HTTPException(404, f"Content {content_id} 不存在")

    if not content.video_path:
        raise HTTPException(400, f"Content {content_id} 无视频文件,无法打包")

    pack = package_wx_content(content)
    return WxPackageOut(
        title=pack.title,
        body=pack.body,
        tags=pack.tags,
        video_path=pack.video_path,
        cover_path=pack.cover_path,
        copy_text=pack.copy_text,
        channels_url=pack.channels_url,
    )
