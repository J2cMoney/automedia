"""回评生成服务 - Phase 5 FLOW-5 自动回评论。

职责:
    调 DeepSeek 生成单条评论的回复文本。回评是单段自然语言(非 JSON),
    不需要解析,直接用 raw(与 copywriter.py 解析 JSON 的范式区分)。

照 copywriter.py:131-145 的 LLM 重试范式:
    - MAX_RETRIES=3 手写 for 循环(不用 system 参数)
    - get_llm().chat(prompt, temperature=0.7)
    - 任何异常(网络/超时/空回复)都重试,3 次仍失败抛 ReplyError
    - 上层编排(orchestrator)catch ReplyError 跳过该条不阻塞其余(Spec 5.3 兜底)

依赖:
    - app.llm.client.get_llm  DeepSeek 文本(Phase 1 封装)
    - app.prompts.build_reply_prompt  回评 prompt 模板(已实现,单段文本输出)
"""
import logging
from typing import Optional

from app.llm.client import LLMError, get_llm
from app.models.account import Account, Platform
from app.models.content import Content
from app.prompts import build_reply_prompt

logger = logging.getLogger(__name__)

# Spec 5.3:API 失败重试 3 次(与 copywriter.py 同口径)
MAX_RETRIES = 3


class ReplyError(Exception):
    """回复生成失败。"""


def generate_reply(
    comment_text: str,
    video_title: str,
    platform: Platform,
    *,
    account_persona: Optional[str] = None,
    comment_author: Optional[str] = None,
    llm=None,
) -> str:
    """生成单条评论的 AI 回复。照 copywriter.py:131-145 范式。

    调 get_llm().chat(build_reply_prompt(...), temperature=0.7),MAX_RETRIES=3 次重试。
    回评是单段自然语言,不需要 JSON 解析,直接用 raw。

    Args:
        comment_text: 评论原文
        video_title: 被评论的视频标题(给 LLM 上下文)
        platform: 平台(决定回复语气)
        account_persona: 账号人设(如"AI 编程博主"),None 用默认友善人设
        comment_author: 评论作者昵称(可选,让回复更自然)
        llm: LLM 客户端(注入用,测试 mock),None 则 get_llm()

    Returns:
        回复文本(strip 后)

    Raises:
        ReplyError: 重试 MAX_RETRIES 次仍失败
    """
    client = llm or get_llm()
    prompt = build_reply_prompt(
        comment_text, video_title, platform, account_persona, comment_author
    )

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = client.chat(prompt, temperature=0.7)
            reply = (raw or "").strip()
            if reply:
                return reply
            # 空回复视为失败,触发重试(LLM 偶发返回空)
            raise ValueError("LLM 返回空回复")
        except Exception as e:
            # LLM 调用任何异常(网络/超时/空回复)都重试,Spec 5.3 兜底
            last_err = e
            logger.warning("回复生成第 %d/%d 次失败: %s", attempt, MAX_RETRIES, e)

    raise ReplyError(f"回复生成失败(重试 {MAX_RETRIES} 次): {last_err}")


__all__ = ["ReplyError", "generate_reply", "MAX_RETRIES"]
