"""文案生成服务 - Phase 3 FLOW-2。

职责:
    1. 按选题+账号主题+平台调性调 DeepSeek 生成文案(标题/正文/标签)
    2. 基于文案生成视频脚本(分镜列表)
    3. LLM 返回 JSON 解析(容错:剥离 markdown 包裹 + 重试)
    4. 失败兜底(Spec 5.3:重试 3 次仍失败标记待人工,不阻塞)

依赖:
    - app.llm.client.get_llm().chat()  DeepSeek 文本(Phase 1 已封装)
    - app.prompts 模板

输出结构:
    CopyResult(title, body, tags)        文案
    ScriptResult(scenes)                 脚本(分镜列表)
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.llm.client import LLMError, get_llm
from app.models.account import Platform
from app.prompts import (
    build_copywriter_prompt,
    build_hotspot_filter_prompt,
    build_video_script_prompt,
)

logger = logging.getLogger(__name__)

# Spec 5.3:API 失败重试 3 次
MAX_RETRIES = 3


# ---------- 结果数据结构 ----------

@dataclass
class CopyResult:
    """文案生成结果。"""
    title: str
    body: str
    tags: List[str] = field(default_factory=list)


@dataclass
class ScriptScene:
    """单个分镜。"""
    index: int
    narration: str
    visual: str
    duration: int = 5


@dataclass
class ScriptResult:
    """视频脚本结果。"""
    scenes: List[ScriptScene] = field(default_factory=list)


class CopywriterError(Exception):
    """文案生成异常(重试耗尽仍失败)。"""


# ---------- JSON 解析(容错) ----------

def _strip_markdown_fence(text: str) -> str:
    """LLM 经常把 JSON 包在 ```json ... ``` 里,剥离掉。

    DeepSeek 偶尔不遵守"不要 markdown"的约束,这里兜底。
    """
    # 匹配 ```json\n...\n``` 或 ```\n...\n```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def parse_llm_json(text: str) -> dict:
    """从 LLM 输出解析 JSON。

    容错:剥离 markdown fence + 截取第一个 { 到最后一个 }。
    解析失败抛 ValueError(由上层 catch 决定是否重试)。

    Args:
        text: LLM 原始输出

    Returns:
        解析后的 dict

    Raises:
        ValueError: 无法解析成 JSON 对象
    """
    cleaned = _strip_markdown_fence(text)
    # 兜底:LLM 前后可能带解释文字,截最外层 {}
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"LLM 输出找不到 JSON 对象: {cleaned[:100]}")
    payload = cleaned[start:end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}; 原文片段: {payload[:100]}") from e


# ---------- 文案生成 ----------

def generate_copy(
    topic_title: str,
    topic_theme: str,
    platform: Platform,
    *,
    llm=None,
) -> CopyResult:
    """生成文案(标题/正文/标签)。

    Args:
        topic_title: 选题标题
        topic_theme: 账号主题
        platform: 目标平台(决定调性)
        llm: LLM 客户端注入(测试用,默认单例)

    Returns:
        CopyResult

    Raises:
        CopywriterError: 重试 MAX_RETRIES 次仍失败
    """
    client = llm or get_llm()
    prompt = build_copywriter_prompt(topic_title, topic_theme, platform)

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = client.chat(prompt, temperature=0.8)
            data = parse_llm_json(raw)
            return _build_copy_result(data)
        except Exception as e:
            # LLM 调用任何异常(网络/超时/解析/校验)都重试,Spec 5.3 兜底
            last_err = e
            logger.warning("文案生成第 %d/%d 次失败: %s", attempt, MAX_RETRIES, e)

    raise CopywriterError(f"文案生成失败(重试 {MAX_RETRIES} 次): {last_err}")


def _build_copy_result(data: dict) -> CopyResult:
    """从解析后的 dict 构造 CopyResult,做基本校验。"""
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    tags = [str(t).strip() for t in tags if str(t).strip()]

    # 兜底:LLM 经常把标签写在 body 末尾而非 tags 字段(实测高频问题)
    # 若 tags 为空,从 body 末尾提取 #标签,并从 body 中移除
    if not tags:
        tags, body = _extract_trailing_tags(body)

    if not title or not body:
        raise ValueError(f"文案缺少必要字段(title/body): title={title!r}, body={body[:30]!r}")
    return CopyResult(title=title, body=body, tags=tags)


def _extract_trailing_tags(body: str) -> tuple:
    """从正文末尾提取 #标签(中文/英文/数字),返回 (tags, cleaned_body)。

    处理 LLM 把标签塞进 body 的常见模式:
        "...正文内容\\n#AI编程 #程序员 #干货"
    提取所有 #标签,body 去掉这部分。
    """
    # 匹配 # 后跟非空白字符的标签(中英文数字下划线)
    tag_pattern = re.compile(r"#([\w\u4e00-\u9fff]+)")
    # 只看 body 末尾连续的标签行(最后一个非标签文本之后)
    # 简化:找 body 里所有标签,如果有就提取
    found = tag_pattern.findall(body)
    if not found:
        return [], body
    tags = [f"#{t}" for t in found]
    # 从 body 移除这些标签原文
    cleaned = tag_pattern.sub("", body)
    # 清理移除后残留的行尾空白
    cleaned = re.sub(r"\n\s*\n\s*$", "", cleaned).strip()
    return tags, cleaned


# ---------- 视频脚本生成 ----------

def generate_script(
    topic_title: str,
    topic_theme: str,
    copy_body: str,
    *,
    scene_count: int = 6,
    llm=None,
) -> ScriptResult:
    """生成视频脚本(分镜列表)。

    Args:
        topic_title: 选题
        topic_theme: 主题
        copy_body: 已生成文案正文(脚本基于它延展)
        scene_count: 分镜数
        llm: LLM 注入(测试用)

    Returns:
        ScriptResult

    Raises:
        CopywriterError: 重试耗尽
    """
    client = llm or get_llm()
    prompt = build_video_script_prompt(topic_title, topic_theme, copy_body, scene_count)

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = client.chat(prompt, temperature=0.7)
            data = parse_llm_json(raw)
            return _build_script_result(data)
        except Exception as e:
            last_err = e
            logger.warning("脚本生成第 %d/%d 次失败: %s", attempt, MAX_RETRIES, e)

    raise CopywriterError(f"视频脚本生成失败(重试 {MAX_RETRIES} 次): {last_err}")


def _build_script_result(data: dict) -> ScriptResult:
    """从 dict 构造 ScriptResult。"""
    scenes_raw = data.get("scenes") or []
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError("脚本缺少 scenes 数组或为空")
    scenes: List[ScriptScene] = []
    for i, s in enumerate(scenes_raw):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or "").strip()
        visual = str(s.get("visual") or "").strip()
        if not narration:
            continue  # 跳过空口播镜
        scenes.append(ScriptScene(
            index=int(s.get("index", i + 1)),
            narration=narration,
            visual=visual,
            duration=int(s.get("duration", 5)),
        ))
    if not scenes:
        raise ValueError("脚本解析后无有效分镜")
    return ScriptResult(scenes=scenes)


# ---------- 热点筛选(可选,LLM 精筛) ----------

def filter_hotspots(
    topic_theme: str,
    hot_titles: List[str],
    *,
    top_n: int = 5,
    llm=None,
) -> List[str]:
    """LLM 精筛热点(crawler 规则粗筛后的补充,可选调用)。

    Returns:
        选中的标题列表(原序),失败返回空列表(不阻塞,降级用规则结果)
    """
    if not hot_titles:
        return []
    client = llm or get_llm()
    prompt = build_hotspot_filter_prompt(topic_theme, hot_titles, top_n)
    try:
        raw = client.chat(prompt, temperature=0.3)
        data = parse_llm_json(raw)
        selected = data.get("selected") or []
        return [str(t) for t in selected if str(t).strip()]
    except Exception as e:
        logger.warning("热点 LLM 精筛失败,降级用规则结果: %s", e)
        return []


__all__ = [
    "CopyResult",
    "ScriptScene",
    "ScriptResult",
    "CopywriterError",
    "generate_copy",
    "generate_script",
    "filter_hotspots",
    "parse_llm_json",
]
