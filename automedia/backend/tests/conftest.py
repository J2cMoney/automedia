"""pytest 公共 fixture。

测试隔离:
    - 用临时 SQLite 库(不污染开发库),测完清掉
    - LLM 用 mock(不烧钱、不依赖网络)
    - 队列测试用真 Redis(Docker 跑的)
"""
import os
import sys
import tempfile
from pathlib import Path

# 把 backend 加进 sys.path,让测试能 import app.*
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 测试用的占位 key(config 必填,但 LLM 测试全 mock 不真调)
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-for-pytest")
os.environ.setdefault("GLM_API_KEY", "test-key-for-pytest")
# Phase 2:Cookie 加密密钥(真实 Fernet key,固定值便于测试可复现)
# 生成方式:from cryptography.fernet import Fernet; Fernet.generate_key()
os.environ.setdefault(
    "COOKIE_ENCRYPT_KEY",
    "CH7rJwjPrcDu5xEMf69LWE-iuSRIAzwzLNCB0qJ-_6I=",
)

# 用临时 SQLite 库,测完自动删
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp.name}"

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """session 级:建表一次。"""
    from app.db import init_db
    init_db()
    yield
    # 清理临时库
    try:
        os.unlink(_tmp.name)
    except OSError:
        pass
