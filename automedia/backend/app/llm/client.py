"""LLM 客户端 - 统一封装 DeepSeek(文本) + GLM(视觉)。

POC 已验证(见 poc/q8_*.py):
    - DeepSeek 文本强+便宜,看不了图 -> 文案/回评
    - 智谱 GLM 视觉能看抽帧 -> 视频剪辑决策

调用方式(均 OpenAI 兼容格式):
    - DeepSeek: api.deepseek.com, OpenAI SDK
    - GLM: open.bigmodel.cn(智谱官方), OpenAI SDK,image_url 传 base64

两个方法:
    chat(messages)  -> str          # 文本对话(DeepSeek)
    vision(frames, prompt) -> str   # 视觉理解(GLM 看图)

密钥全从 config 读,绝不硬编码。API key 不落日志。
"""
import base64
import logging
from pathlib import Path
from typing import List, Optional, Union

import httpx
from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)
# 注意:不记录 API key,即使开 DEBUG 也不打 client 配置


def _make_http_client() -> httpx.Client:
    """构造不走环境变量代理的 httpx client。

    国内访问 DeepSeek(api.deepseek.com)/ 智谱(open.bigmodel.cn)官方 API 直连即可,
    无需 SOCKS 代理。但用户机器若有 ALL_PROXY=socks5://... 环境变量,openai SDK
    默认会继承导致 SOCKS 依赖报错。这里显式 trust_env=False 绕过。
    """
    return httpx.Client(trust_env=False, timeout=httpx.Timeout(120.0, connect=30.0))


class LLMError(Exception):
    """LLM 调用异常,包一层避免裸抛 SDK 错误。"""


class LLMClient:
    """统一 LLM 客户端:chat 走 DeepSeek,vision 走 GLM。"""

    def __init__(self) -> None:
        # DeepSeek - OpenAI 兼容格式(api.deepseek.com)
        self._ds = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            http_client=_make_http_client(),
        )
        # GLM - 智谱官方,OpenAI 兼容格式(open.bigmodel.cn)
        self._glm = OpenAI(
            api_key=settings.GLM_API_KEY,
            base_url=settings.GLM_BASE_URL,
            http_client=_make_http_client(),
        )

    # ---------- 文本(DeepSeek) ----------

    def chat(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        history: Optional[List[dict]] = None,
        max_tokens: int = 2000,
        temperature: float = 0.7,
    ) -> str:
        """文本对话。返回模型回复文本。

        Args:
            prompt: 用户输入
            system: 系统提示词(可选)
            history: 历史对话 [{"role": "user"|"assistant", "content": "..."}]
            max_tokens: 最大输出 token
            temperature: 温度
        """
        messages: List[dict] = []
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        try:
            resp = self._ds.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            # 不打印 key,只打异常类型和消息
            logger.error("DeepSeek chat 调用失败: %s: %s", type(e).__name__, e)
            raise LLMError(f"DeepSeek 文本调用失败: {type(e).__name__}") from e

    # ---------- 视觉(GLM) ----------

    def vision(
        self,
        frames: List[Union[str, Path]],
        prompt: str,
        *,
        max_tokens: int = 1000,
    ) -> str:
        """视觉理解:让 GLM 看图片(视频抽帧),回答问题。

        智谱官方 GLM-4V 系列,OpenAI 兼容格式,image_url 传 base64 data URI。

        Args:
            frames: 图片路径列表(PNG/JPG),按时间顺序
            prompt: 要问的问题(如"挑高光时刻"或"输出剪辑决策 JSON")
            max_tokens: 最大输出 token

        Returns:
            模型回复文本
        """
        if not frames:
            raise LLMError("vision 需要至少一张图片")

        # 构造 OpenAI 多模态 content(image_url 用 base64 data URI)
        content: List[dict] = [{"type": "text", "text": prompt}]
        for f in frames:
            path = Path(f)
            media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            b64 = self._encode_image(path)
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64}"},
            })

        try:
            resp = self._glm.chat.completions.create(
                model=settings.GLM_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": content}],
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("GLM vision 调用失败: %s: %s", type(e).__name__, e)
            raise LLMError(f"GLM 视觉调用失败: {type(e).__name__}") from e

    @staticmethod
    def _encode_image(path: Path) -> str:
        """图片转 base64。"""
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()


# 模块级单例
_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    """获取 LLM 客户端单例。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
