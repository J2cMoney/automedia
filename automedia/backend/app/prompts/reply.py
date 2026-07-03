"""回评 prompt 模板 - Phase 5 FLOW-5 自动回评论。

与 copywriter.py 的区别:
    - 回评输出是单段自然语言(回复文本),不需要 JSON 解析,直接用 raw
    - 必须贴合评论上下文 + 视频内容 + 账号人设,语气自然不机械
    - 严守风控底线:不引战、不承诺、不敏感、不偏离主题

Spec 依据:
    - FLOW-5:抓评论 -> AI 生成回复 -> Playwright 模拟回复
    - 5.3 失败兜底:生成失败跳过不强行回,进待处理队列
    - 护栏:回复内容人工抽检,不达标调优 prompt
"""
from typing import Optional

from app.models.account import Platform
from app.prompts.copywriter import PLATFORM_TONE


def build_reply_prompt(
    comment_text: str,
    video_title: str,
    platform: Platform,
    account_persona: Optional[str] = None,
    comment_author: Optional[str] = None,
) -> str:
    """回评 prompt:针对单条评论生成一条自然的回复。

    Args:
        comment_text: 评论原文(用户在视频下留的言)
        video_title: 被评论的视频标题(给 LLM 上下文,避免回复跑题)
        platform: 平台(决定回复语气:小红书活泼/抖音短平快/快手接地气)
        account_persona: 账号人设描述(如"AI 编程博主,风格专业但不端着")。
            空则用通用友善人设。
        comment_author: 评论作者昵称(可选,让回复更自然,如"@XX 感谢")。

    Returns:
        prompt 字符串(LLM 直接返回回复文本,非 JSON)
    """
    tone = PLATFORM_TONE.get(platform, PLATFORM_TONE[Platform.XHS])
    persona = account_persona or "一个友善、专业、真诚的内容创作者"
    author_hint = f"\n【评论作者】{comment_author}" if comment_author else ""

    return f"""你是 {persona}。有人在你发布的视频下留了言,请写一条回复。

【视频标题】{video_title}
【平台】{tone["name"]}
【评论内容】{comment_text}{author_hint}

【回复要求】
1. 直接回复评论内容,不要跑题,不要套话
2. 语气符合 {tone["name"]} 调性:{tone["style"]}
3. 长度控制在 15-60 字,简短自然,像真人随手回的
4. 如果评论是提问,简短作答;如果是夸赞,真诚致谢;如果是讨论,自然接话
5. 严禁:引战、抬杠、做任何承诺、涉及敏感话题、推销、刷量话术

【输出格式】只输出回复内容本身,不要"回复:"前缀,不要引号,不要解释,直接给文本。"""


__all__ = ["build_reply_prompt"]
