"""Prompt 模板 - Phase 3 文案/脚本 + Phase 5 回评。

核心 prompt(对应 DEV-PLAN backend/app/prompts/):
    1. hotspot_filter: 热点筛选(Phase 3,从一批热榜词条里挑与主题最相关的)
    2. copywriter:     文案生成(Phase 3,标题/正文/标签,按平台调性适配)
    3. video_script:   视频脚本(Phase 3,分镜列表,含口播+画面描述)
    4. reply:          回评(Phase 5,针对单条评论生成自然回复)

设计原则:
    - 模板是纯函数(输入参数 -> 输出 prompt 字符串),不调 LLM,便于单测
    - 平台调性差异(小红书长文标签多/抖音短平快)在 copywriter 里体现
    - 输出格式约束 JSON,LLM 返回后 copywriter.py 解析

Spec 依据:
    - FLOW-2 MUST:不同平台文案长度/风格自动适配
    - FLOW-5:抓评论 -> AI 生成回复 -> Playwright 模拟回复
    - 5.2 选型:DeepSeek(文本强+便宜,看不了图)
    - 5.3 失败兜底:API 失败重试 3 次(在 copywriter.py / replier.py 实现)
"""
from app.prompts.copywriter import (
    build_copywriter_prompt,
    build_hotspot_filter_prompt,
    build_video_script_prompt,
    PLATFORM_TONE,
)
from app.prompts.reply import build_reply_prompt

__all__ = [
    "build_hotspot_filter_prompt",
    "build_copywriter_prompt",
    "build_video_script_prompt",
    "build_reply_prompt",
    "PLATFORM_TONE",
]
