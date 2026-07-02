"""配置加载 - 所有密钥只从 .env 读,绝不硬编码。

用法:
    from app.config import settings
    settings.DEEPSEEK_API_KEY

安全规范:
    - .env 不提交,.env.example 提交
    - API key 不落日志,不写默认值
"""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录(automedia/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """应用配置。敏感字段无默认值,强制从 .env 读。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- DeepSeek(文本:文案/回评) ----
    DEEPSEEK_API_KEY: str = Field(..., description="DeepSeek API key,无默认值,必须从 .env 读")
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"

    # ---- 智谱 GLM 视觉(视频剪辑大脑) ----
    GLM_API_KEY: str = Field(..., description="智谱官方 API key,无默认值")
    GLM_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    GLM_MODEL: str = "glm-4v-flash"

    # ---- Cookie 加密密钥(Phase 2 FLOW-6:登录态 Fernet 加密) ----
    # 生成方式:python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # 绝不硬编码,只从 .env 读;换了密钥旧密文无法解密
    COOKIE_ENCRYPT_KEY: str = Field(..., description="Fernet 密钥,登录态加密用,无默认值")

    # ---- Redis(Dramatiq broker) ----
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # ---- SQLite ----
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/automedia.db"

    # ---- 应用 ----
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ---- 调度(FLOW-8) ----
    MAX_BROWSER_CONCURRENCY: int = 3
    MAX_RENDER_CONCURRENCY: int = 2
    PUBLISH_INTERVAL_MINUTES: int = 30

    @property
    def redis_url(self) -> str:
        """Dramatiq broker 用的 Redis URL。"""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache
def get_settings() -> Settings:
    """单例,避免每次读 .env。"""
    return Settings()


# 模块级便捷引用
settings = get_settings()
