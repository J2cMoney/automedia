"""账号矩阵 CRUD 路由 - Phase 2 FLOW-6。

对应 Product-Spec.md:
    - 4.4 Account 数据模型
    - FLOW-6 多账号矩阵管理(CRUD + 登录态加密 + 可建必可删)
    - AC-4 账号可删除(本地数据清除 + 提示平台侧登录态需自行退出)

路由:
    GET    /api/accounts              列表(支持平台筛选)
    POST   /api/accounts              创建(平台/昵称/主题)
    GET    /api/accounts/{id}         详情
    PUT    /api/accounts/{id}         更新(主题/昵称/状态)
    DELETE /api/accounts/{id}         删除(级联清 task_runs,AC-4)
    POST   /api/accounts/{id}/health-check  登录态健康检查(FLOW-6 MUST)

安全:
    - auth_state(加密 cookie)绝不进入响应体
    - 平台枚举强校验,拒绝未知平台
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db import get_db
from app.models.account import Account, AccountStatus, AuthState, Platform
from app.services.auth import login_and_store_cookie
from app.services.auth_health import check_account_health

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["账号管理"])


# ---------- 平台元信息(前端展示用) ----------
PLATFORM_LABELS = {
    Platform.XHS: "小红书",
    Platform.DOUYIN: "抖音",
    Platform.KUAISHOU: "快手",
    Platform.WECHAT: "视频号",
}


# ---------- 请求/响应模型 ----------

class AccountCreate(BaseModel):
    """创建账号。登录态通过单独的登录流程获取,不在此接口传。"""
    platform: Platform
    nickname: str = Field(min_length=1, max_length=100)
    topic_theme: str = Field(default="", max_length=200)


class AccountUpdate(BaseModel):
    """更新账号。所有字段可选。"""
    nickname: Optional[str] = Field(default=None, min_length=1, max_length=100)
    topic_theme: Optional[str] = Field(default=None, max_length=200)
    status: Optional[AccountStatus] = None


class AccountOut(BaseModel):
    """账号响应。auth_state 绝不输出。"""
    id: int
    platform: Platform
    platform_label: str
    nickname: str
    topic_theme: str
    auth_status: AuthState
    has_auth: bool = Field(description="是否已获取登录态(cookie 非空)")
    status: AccountStatus
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class HealthCheckResponse(BaseModel):
    """健康检查结果。"""
    id: int
    auth_status: AuthState
    healthy: bool
    message: str


class LoginRequest(BaseModel):
    """触发浏览器登录。"""
    timeout_seconds: int = Field(default=180, ge=30, le=600)
    headless: bool = Field(default=False, description="登录需用户操作,默认有头")


# ---------- 内部工具 ----------

def _to_out(acc: Account) -> AccountOut:
    """ORM 转 响应模型,屏蔽 auth_state。"""
    return AccountOut(
        id=acc.id,
        platform=acc.platform,
        platform_label=PLATFORM_LABELS[acc.platform],
        nickname=acc.nickname,
        topic_theme=acc.topic_theme,
        auth_status=acc.auth_status,
        has_auth=bool(acc.auth_state),
        status=acc.status,
        created_at=acc.created_at.isoformat() if acc.created_at else "",
        updated_at=acc.updated_at.isoformat() if acc.updated_at else "",
    )


# ---------- CRUD ----------

@router.get("", response_model=List[AccountOut])
async def list_accounts(
    platform: Optional[Platform] = Query(default=None, description="按平台筛选"),
    db: AsyncSession = Depends(get_db),
):
    """账号列表。支持平台筛选,按创建时间倒序。"""
    stmt = select(Account).order_by(Account.created_at.desc())
    if platform is not None:
        stmt = stmt.where(Account.platform == platform)
    result = await db.execute(stmt)
    return [_to_out(a) for a in result.scalars().all()]


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(req: AccountCreate, db: AsyncSession = Depends(get_db)):
    """创建账号。登录态初始为 UNKNOWN,需后续走登录流程获取。"""
    acc = Account(
        platform=req.platform,
        nickname=req.nickname,
        topic_theme=req.topic_theme,
        auth_state="",
        auth_status=AuthState.UNKNOWN,
        status=AccountStatus.ACTIVE,
    )
    db.add(acc)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"创建失败: {e.orig}")
    await db.refresh(acc)
    logger.info("创建账号 id=%s platform=%s nickname=%s", acc.id, acc.platform.value, acc.nickname)
    return _to_out(acc)


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """账号详情。"""
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {account_id} 不存在")
    return _to_out(acc)


@router.put("/{account_id}", response_model=AccountOut)
async def update_account(account_id: int, req: AccountUpdate, db: AsyncSession = Depends(get_db)):
    """更新账号(主题/昵称/状态)。只更新非 None 字段。"""
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {account_id} 不存在")
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(acc, k, v)
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"更新失败: {e.orig}")
    await db.refresh(acc)
    logger.info("更新账号 id=%s fields=%s", account_id, list(data.keys()))
    return _to_out(acc)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """删除账号。

    Spec AC-4(可建必可删):
        - 本地:该账号所有 task_runs 一并清除(task_runs.account_id ON DELETE CASCADE)
        - 本地 auth_state 清除(登录态失效)
        - 平台侧登录态:本工具无法主动登出,需用户自行到平台 App 退出
          (此提示在前端删除确认弹窗给出,见 AC-4)
    """
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {account_id} 不存在")
    await db.delete(acc)  # task_runs 外键 CASCADE 自动清
    await db.commit()
    logger.info("删除账号 id=%s platform=%s nickname=%s(关联 task_runs 级联清除)",
                account_id, acc.platform.value, acc.nickname)


@router.post("/{account_id}/health-check", response_model=HealthCheckResponse)
async def health_check_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """登录态健康检查(FLOW-6 MUST)。

    校验该账号加密存储的 cookie 是否仍有效:
        - 有效 -> auth_status=VALID
        - 失效/损坏/未登录 -> auth_status=INVALID,提示重新登录
    结果写回 account.auth_status,前端徽章据此显示有效/失效。
    """
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {account_id} 不存在")
    result = check_account_health(acc)  # 同步本地校验,不阻塞
    # 健康检查结果写库
    acc.auth_status = result.auth_status
    await db.commit()
    logger.info("健康检查 id=%s -> %s (%s)", account_id, result.auth_status.value, result.message)
    return HealthCheckResponse(
        id=account_id,
        auth_status=result.auth_status,
        healthy=result.healthy,
        message=result.message,
    )


@router.post("/{account_id}/login", response_model=AccountOut)
async def login_account(
    account_id: int,
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """触发浏览器登录,抓取 cookie 加密入库(FLOW-6)。

    流程:开浏览器(有头) -> 用户手动登录平台 -> 检测登录态 cookie -> 抓全 cookie ->
          Fernet 加密 -> 写 account.auth_state + auth_status=VALID。

    该接口会阻塞直到用户完成登录或超时(默认 180s)。Playwright 同步 API 在线程池跑,
    不阻塞事件循环。
    """
    acc = await db.get(Account, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail=f"账号 {account_id} 不存在")
    try:
        # Playwright 同步流程在线程池跑,避免阻塞 FastAPI 事件循环
        cipher = await run_in_threadpool(
            login_and_store_cookie,
            platform=acc.platform,
            account_id=acc.id,
            timeout_seconds=req.timeout_seconds,
            headless=req.headless,
        )
    except TimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e))
    except RuntimeError as e:
        # Playwright 未安装浏览器等
        raise HTTPException(status_code=500, detail=f"登录流程失败: {e}")
    acc.auth_state = cipher
    acc.auth_status = AuthState.VALID
    await db.commit()
    await db.refresh(acc)
    logger.info("登录态已入库 id=%s platform=%s", account_id, acc.platform.value)
    return _to_out(acc)
