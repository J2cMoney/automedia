"""数据库连接与 Session 管理。

SQLAlchemy 2.x,async engine 供 FastAPI 用,sync engine 供脚本/测试/建表用。
SQLite 单机,Phase 1 只建 accounts + task_runs 两张骨架表。
"""
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import BASE_DIR, settings

# 确保 data 目录存在(SQLite 文件要放这)
Path(BASE_DIR / "data").mkdir(exist_ok=True)


def _resolve_db_url(url: str) -> str:
    """把 SQLite 相对路径解析成基于 BASE_DIR 的绝对路径。

    避免不同工作目录(uvicorn 在 automedia/、worker 在 backend/)导致
    SQLite 文件位置漂移。三段斜杠(相对)和四段斜杠(绝对)都处理。
    """
    # sqlite+aiosqlite:///./data/automedia.db  -> 相对路径
    # 只处理相对路径(含 . 或不以 / 开头的数据文件部分)
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            path_part = url[len(prefix):]
            # 绝对路径(Win 盘符或 unix /)不动
            if path_part and not path_part.startswith(("/", "\\")) and not (len(path_part) > 1 and path_part[1] == ":"):
                # 相对路径,基于 BASE_DIR 解析
                abs_path = str((BASE_DIR / path_part).resolve()).replace("\\", "/")
                return prefix + abs_path
    return url


def _make_sync_url() -> str:
    """async URL 转 sync,建表和测试用。同时解析绝对路径。"""
    url = _resolve_db_url(settings.DATABASE_URL)
    return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql")


# sync engine(建表、脚本、测试用)
sync_engine = create_engine(
    _make_sync_url(),
    echo=False,
    connect_args={"check_same_thread": False},  # SQLite 多线程
)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)


# async engine(FastAPI 路由用)
async_engine = create_async_engine(
    _resolve_db_url(settings.DATABASE_URL),
    echo=False,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用的 async session。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def init_db() -> None:
    """建表。启动时调一次,idempotent。

    SQLite 的 create_all 只建不补列,Phase 2 给 accounts 加了 auth_status 列,
    旧库需手动补。这里做轻量迁移:create_all 后检查并补缺失列。
    """
    # 导入模型触发注册(Phase 1 accounts/task_runs + Phase 3 topics/contents + Phase 5 comments)
    from app.models import account, comment, content, task_run, topic  # noqa: F401
    from app.models.base import Base
    from sqlalchemy import inspect, text

    Base.metadata.create_all(sync_engine)

    # 轻量列迁移:补 Phase 2 新增的 auth_status 列(仅 SQLite,ALTER TABLE ADD COLUMN)
    inspector = inspect(sync_engine)
    if "accounts" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("accounts")}
        if "auth_status" not in existing_cols:
            with sync_engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE accounts ADD COLUMN auth_status VARCHAR "
                        "DEFAULT 'unknown' NOT NULL"
                    )
                )

    # Phase 4 迁移:给 contents 表补视频字段(video_path / script_scenes / clip_decision)
    # Phase 3 建表时只建文案字段,Phase 4 增量加列(DEV-PLAN 明确的增量计划)
    if "contents" in inspector.get_table_names():
        content_cols = {c["name"] for c in inspector.get_columns("contents")}
        with sync_engine.begin() as conn:
            if "video_path" not in content_cols:
                conn.execute(text("ALTER TABLE contents ADD COLUMN video_path VARCHAR(1000)"))
            if "script_scenes" not in content_cols:
                conn.execute(text("ALTER TABLE contents ADD COLUMN script_scenes TEXT"))
            if "clip_decision" not in content_cols:
                conn.execute(text("ALTER TABLE contents ADD COLUMN clip_decision TEXT"))
