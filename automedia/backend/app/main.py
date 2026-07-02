"""FastAPI 入口 - Phase 1 基础设施 + Phase 2 账号管理。

路由:
    GET  /health          - 健康检查(DB + Redis 连通性)
    POST /tasks/test      - 提交测试任务(sleep N 秒),验证队列
    GET  /tasks/{task_id} - 查任务状态
    POST /tasks/{task_id}/retry - 重试失败任务
    /api/accounts/*       - 账号矩阵 CRUD + 登录态 + 健康检查(Phase 2)

启动事件:建表 + 重启恢复(running->pending)。
CORS:开发态允许前端域 localhost:5173(Vite dev server)。
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.accounts import router as accounts_router
from app.api.contents import router as contents_router
from app.api.topics import router as topics_router
from app.config import settings
from app.db import init_db
from app.queue import (
    get_redis_info,
    recover_on_startup,
    retry,
    status,
    submit,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动:建表 + 恢复中断任务;关闭:无特殊处理。"""
    logger.info("=== AutoMedia 后端启动 ===")
    init_db()
    logger.info("建表完成")
    recovered = recover_on_startup()
    if recovered:
        logger.warning("恢复 %d 个中断任务(running->pending)", recovered)
    logger.info("环境: %s, 端口: %s", settings.APP_ENV, settings.APP_PORT)
    yield
    logger.info("=== AutoMedia 后端关闭 ===")


app = FastAPI(
    title="AutoMedia API",
    description="自媒体全自动运营流水线 - Phase 1 基础设施 + Phase 2 账号管理 + Phase 3 热点采集与文案生成",
    version="0.3.0",
    lifespan=lifespan,
)


# ---------- CORS(Phase 2:允许 Vite dev server 跨域) ----------
# 开发态前端 localhost:5173,生产部署时按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Phase 2:账号矩阵路由 ----------
app.include_router(accounts_router)

# ---------- Phase 3:选题 + 内容路由 ----------
app.include_router(topics_router)
app.include_router(contents_router)


# ---------- 响应模型 ----------

class HealthResponse(BaseModel):
    status: str = Field(description="healthy / degraded / unhealthy")
    db: bool
    redis: dict
    env: str


class TaskSubmitRequest(BaseModel):
    seconds: int = Field(default=3, ge=1, le=60, description="sleep 秒数")


class TaskSubmitResponse(BaseModel):
    task_id: int
    flow_type: str
    message: str


class TaskStatusResponse(BaseModel):
    id: int
    flow_type: str
    status: str
    message_id: Optional[str]
    retry_count: int
    started_at: Optional[str]
    finished_at: Optional[str]
    error_log: Optional[str]
    result: Optional[str]


# ---------- 路由 ----------

@app.get("/health", response_model=HealthResponse, tags=["系统"])
async def health():
    """健康检查:DB 连通 + Redis 连通。"""
    db_ok = False
    try:
        from app.db import SyncSessionLocal
        from sqlalchemy import text
        with SyncSessionLocal() as s:
            s.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error("健康检查 DB 失败: %s", e)

    redis_info = get_redis_info()
    redis_ok = redis_info.get("connected", False)

    overall = "healthy" if (db_ok and redis_ok) else ("degraded" if (db_ok or redis_ok) else "unhealthy")
    return HealthResponse(status=overall, db=db_ok, redis=redis_info, env=settings.APP_ENV)


@app.post("/tasks/test", response_model=TaskSubmitResponse, tags=["任务"])
async def submit_test_task(req: TaskSubmitRequest):
    """提交测试任务(sleep N 秒),验证队列状态流转。

    前置:worker 需单独运行 `dramatiq app.queue`。
    """
    task_id = submit("test", "sleep_task", seconds=req.seconds)
    return TaskSubmitResponse(
        task_id=task_id,
        flow_type="test",
        message=f"已提交 sleep {req.seconds}s 任务,worker 运行后自动执行",
    )


@app.get("/tasks/{task_id}", response_model=TaskStatusResponse, tags=["任务"])
async def get_task_status(task_id: int):
    """查任务状态。"""
    info = status(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return TaskStatusResponse(**info)


@app.post("/tasks/{task_id}/retry", tags=["任务"])
async def retry_task(task_id: int):
    """重试失败任务。"""
    ok = retry(task_id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"任务 {task_id} 不存在或非失败状态")
    return {"task_id": task_id, "message": "已标记重试,等待重新提交"}


@app.get("/", tags=["系统"])
async def root():
    """根路径,返回服务信息。"""
    return {
        "name": "AutoMedia API",
        "version": "0.1.0",
        "phase": 1,
        "docs": "/docs",
        "health": "/health",
    }
