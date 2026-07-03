"""剪辑决策核心 - Phase 4 大脑(DEV-PLAN 测试策略指定要单测)。

两个场景共用一个 agent 引擎(Spec FLOW-3 设计),但职责不同:
    场景 A decide_highlights:GLM 视觉看抽帧 -> 输出剪辑决策 JSON(切点时间码)
    场景 B plan_scenes_from_script:DeepSeek 文本 -> 把 Flow-2 脚本转渲染用 scene plan

设计要点:
    - 复用 Phase 1 llm/client.py:vision() 走 GLM 看图,chat() 走 DeepSeek 文本
    - 复用 copywriter.parse_llm_json() 做 JSON 容错(同源工具,不重写)
    - 失败重试 3 次(照抄 copywriter MAX_RETRIES 模式,Spec 5.3 兜底)
    - GLM 看图前可选缩放(由调用方 frames.scale_frame 做,agent 不重复)

Spec FLOW-3 已知约束(写死,不靠运行时发现):
    - GLM 看抽帧是"近似看视频",适合口播/讲解/图文类,不适合舞蹈/体育强节奏
    - GLM 中文 OCR 偏弱,真实视频帧需二次验证(POC 已知水分,DEV-PLAN 已记)
    - DeepSeek 看不了图,只能做文本决策;GLM 能看图做视觉决策(Spec 5.2 POC 已验证)
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from app.llm.client import LLMError, get_llm
from app.services.copywriter import parse_llm_json

logger = logging.getLogger(__name__)

# Spec 5.3:API 失败重试 3 次(与 copywriter 一致)
MAX_RETRIES = 3
# GLM glm-4v-flash 多图上限实测 5 张(6 张报错"图片数量超过限制")。
# 分批决策:每批 5 帧调一次 GLM,Python 聚合各批高光判断。
# glm-4.6v-flash 虽支持 300 图但高峰期持续 429 限流,故用 4v-flash 分批。
GLM_BATCH_SIZE = 5


# ---------- 数据结构 ----------

@dataclass
class HighlightSegment:
    """单个高光片段:起止时间(秒)+ 选取理由。"""
    start: float
    end: float
    reason: str = ""


@dataclass
class ClipDecision:
    """剪辑决策:一组高光片段 + 内容摘要。

    GLM 看抽帧后输出,extractor.py 据此用 ffmpeg 剪切拼接成片。
    """
    segments: List[HighlightSegment] = field(default_factory=list)
    summary: str = ""

    def total_duration(self) -> float:
        """所有片段总时长(秒)。"""
        return sum(max(0.0, s.end - s.start) for s in self.segments)

    def to_dict(self) -> Dict[str, Any]:
        """序列化存 Content.clip_decision(JSON 列)。"""
        return {
            "segments": [
                {"start": s.start, "end": s.end, "reason": s.reason}
                for s in self.segments
            ],
            "summary": self.summary,
        }


@dataclass
class ScenePlan:
    """场景 B 渲染用单镜计划。

    从 Flow-2 的 video_script 转来,Remotion 渲染时消费:
      - narration:TTS 配音文本(也是字幕文本)
      - visual:画面描述(Pexels 搜索关键词由此衍生)
      - asset_keyword:Pexels 搜索用的英文关键词(LLM 从 visual 提炼)
      - duration:该镜秒数
      - asset_path:素材文件本地路径(assets.py 找到后回填,None=待找/兜底上传)
    """
    index: int
    narration: str
    visual: str
    asset_keyword: str = ""
    duration: int = 5
    asset_path: Optional[str] = None

    @property
    def needs_asset(self) -> bool:
        """该镜是否还需找素材(True=未找/需兜底)。"""
        return not self.asset_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "narration": self.narration,
            "visual": self.visual,
            "asset_keyword": self.asset_keyword,
            "duration": self.duration,
            "asset_path": self.asset_path,
        }


class AgentError(Exception):
    """剪辑决策异常(重试耗尽仍失败)。"""


# ---------- 场景 A:GLM 看抽帧找高光(分批决策) ----------

def _build_batch_prompt(
    batch_index: int,
    total_batches: int,
    batch_size: int,
    fps: float,
    start_time: float,
    end_time: float,
) -> str:
    """构造单批 GLM 看图找高光的 prompt。

    glm-4v-flash 多图上限 5 张,故每批 5 帧。GLM 只看本批帧,
    判断"这一小段视频是否值得放入高光 + 推荐的精华子区间",
    Python 聚合各批判断后按目标时长选取。

    Args:
        batch_index: 本批序号(从 0 开始)
        total_batches: 总批数
        batch_size: 本批帧数(≤5)
        fps: 抽帧帧率
        start_time: 本批第一帧对应的视频时间(秒)
        end_time: 本批最后一帧对应的视频时间(秒)

    Returns:
        prompt 文本
    """
    return f"""你是一位专业视频剪辑师。下面是口播视频第 {batch_index + 1}/{total_batches} 段的 {batch_size} 张连续关键帧。

【本段时间区间】
- 这 {batch_size} 张图按时间先后排列,覆盖视频 {start_time:.1f}s 到 {end_time:.1f}s 这一段
- 这一段约 {end_time - start_time:.1f} 秒

【你的任务】
判断这一段视频是否值得放入"高光精华短片",以及如果值得,推荐的精华子区间。

【判断标准】
- worth_clip=true 的条件:有核心观点/金句/重要信息/情绪高点
- worth_clip=false 的情况:冷场/重复/铺垫过长/口误停顿/无信息量
- 推荐的子区间(start/end)用视频绝对秒数,必须是 [{start_time:.1f}, {end_time:.1f}] 范围内的值

【输出格式】严格的 JSON,不要 markdown 代码块,不要多余解释:
{{"worth_clip": true, "start": 12.0, "end": 15.5, "reason": "讲了核心方法论", "topic": "这段讲的是XX"}}

如果这一段不值得剪辑:
{{"worth_clip": false, "topic": "这段是铺垫,无核心内容"}}"""


def decide_highlights(
    frames: List[Union[str, Path]],
    *,
    fps: float = 1.0,
    target_duration: int = 60,
    llm=None,
) -> ClipDecision:
    """场景 A 核心:GLM 分批看抽帧,聚合输出剪辑决策(高光片段切点)。

    glm-4v-flash 多图上限 5 张,故采用分批策略:
      1. 帧序列按 GLM_BATCH_SIZE(5)分批
      2. 每批调 GLM,让它判断该段是否值得剪 + 推荐精华子区间
      3. Python 聚合所有 worth_clip=true 的段,按目标时长排序选取
      4. 合并相邻/重叠的段,保证成片连贯

    Args:
        frames: 帧图片路径列表(按时间顺序)
        fps: 抽帧帧率(用于推算每批时间区间)
        target_duration: 目标成片秒数(默认 60)
        llm: LLM 客户端注入(测试用,默认单例)

    Returns:
        ClipDecision(segments + summary)

    Raises:
        AgentError: 重试 MAX_RETRIES 次仍失败,或所有批都 worth_clip=false
    """
    if not frames:
        raise AgentError("decide_highlights 需要至少一帧图片")

    client = llm or get_llm()

    # 分批(GLM_BATCH_SIZE 张/批)
    batches = [
        frames[i:i + GLM_BATCH_SIZE]
        for i in range(0, len(frames), GLM_BATCH_SIZE)
    ]
    total_batches = len(batches)
    interval = 1.0 / fps if fps > 0 else 1.0

    logger.info("GLM 分批决策: %d 帧 → %d 批(每批≤%d)", len(frames), total_batches, GLM_BATCH_SIZE)

    raw_segments: List[HighlightSegment] = []
    topics: List[str] = []

    for batch_idx, batch in enumerate(batches):
        # 本批时间区间(用帧在原 frames 序列里的全局索引推算)
        global_start_idx = batch_idx * GLM_BATCH_SIZE  # 本批首帧的全局索引
        start_time = global_start_idx * interval
        # 末帧索引(本批最后一帧)
        end_time = (global_start_idx + len(batch) - 1) * interval + interval

        prompt = _build_batch_prompt(
            batch_idx, total_batches, len(batch), fps, start_time, end_time,
        )

        # 单批重试(网络/解析失败),整批失败则跳过(不阻塞其他批)
        batch_segment: Optional[HighlightSegment] = None
        batch_topic = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw = client.vision(batch, prompt, max_tokens=400)
                data = parse_llm_json(raw)
                worth = bool(data.get("worth_clip", False))
                batch_topic = str(data.get("topic", "")).strip()
                if worth:
                    seg_start = float(data.get("start", start_time))
                    seg_end = float(data.get("end", end_time))
                    # 约束在本批时间区间内(GLM 可能越界)
                    seg_start = max(seg_start, start_time)
                    seg_end = min(seg_end, end_time)
                    if seg_end > seg_start:
                        batch_segment = HighlightSegment(
                            start=seg_start,
                            end=seg_end,
                            reason=str(data.get("reason", "")).strip(),
                        )
                break  # 解析成功(无论 worth 与否),跳出重试
            except Exception as e:
                logger.warning("批次 %d/%d 第 %d 次失败: %s", batch_idx + 1, total_batches, attempt, e)
        else:
            logger.warning("批次 %d/%d 全部重试失败,跳过", batch_idx + 1, total_batches)

        if batch_segment is not None:
            raw_segments.append(batch_segment)
        if batch_topic:
            topics.append(batch_topic)

    if not raw_segments:
        raise AgentError("GLM 判断所有段落都不值得剪辑(可能整段视频信息密度低,或 GLM 识别失败)")

    # 聚合:按目标时长选取 + 合并相邻段
    selected = _select_and_merge_segments(raw_segments, target_duration)
    if not selected:
        # 合并后为空(目标时长太小),用原始段
        selected = raw_segments

    summary = "高光片段聚合:" + ";".join(topics[:5]) if topics else "已提取高光片段"
    return ClipDecision(segments=selected, summary=summary)


def _select_and_merge_segments(
    segments: List[HighlightSegment],
    target_duration: int,
) -> List[HighlightSegment]:
    """聚合各批的高光段:按时长排序选取到目标时长,合并相邻/重叠。

    策略:
      1. 按时间排序
      2. 合并重叠或相邻(间隔<1s)的段
      3. 若合并后总时长 > target,保留时长最长的几段(按段时长降序)直到接近目标
      4. 若 < target,全部保留
    """
    if not segments:
        return []

    # 按起始时间排序
    sorted_segs = sorted(segments, key=lambda s: s.start)

    # 合并重叠/相邻(<1s 间隔视为相邻)
    merged: List[HighlightSegment] = []
    for seg in sorted_segs:
        if merged and seg.start - merged[-1].end < 1.0:
            # 合并到上一段
            merged[-1].end = max(merged[-1].end, seg.end)
            if seg.reason and seg.reason not in merged[-1].reason:
                merged[-1].reason = (merged[-1].reason + " / " + seg.reason).strip(" / ")
        else:
            merged.append(HighlightSegment(
                start=seg.start, end=seg.end, reason=seg.reason,
            ))

    # 若总时长超目标,按时长降序保留 top(覆盖目标时长)
    total = sum(s.end - s.start for s in merged)
    if total > target_duration:
        # 按段时长降序排,选到累计达 target
        by_duration = sorted(merged, key=lambda s: s.end - s.start, reverse=True)
        selected: List[HighlightSegment] = []
        acc = 0.0
        for s in by_duration:
            if acc >= target_duration:
                break
            selected.append(s)
            acc += s.end - s.start
        # 再按时间排序(成片按原顺序拼接)
        selected.sort(key=lambda s: s.start)
        return selected

    return merged


def _build_clip_decision(data: dict) -> ClipDecision:
    """从 GLM 解析后的 dict 构造 ClipDecision,做校验。

    容错:
      - segments 缺失/空 -> 抛 ValueError(触发上层重试)
      - 单个片段缺字段 -> 跳过该片段
      - 时间非法(end<=start/负数) -> 跳过该片段
    """
    segments_raw = data.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        raise ValueError("GLM 输出缺少 segments 数组或为空")

    segments: List[HighlightSegment] = []
    for s in segments_raw:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start", -1))
            end = float(s.get("end", -1))
        except (TypeError, ValueError):
            continue
        if start < 0 or end < 0 or end <= start:
            continue  # 时间非法,跳过
        segments.append(HighlightSegment(
            start=start,
            end=end,
            reason=str(s.get("reason", "")).strip(),
        ))

    if not segments:
        raise ValueError("GLM 输出的 segments 全部无效")

    return ClipDecision(
        segments=segments,
        summary=str(data.get("summary", "")).strip(),
    )


# ---------- 场景 B:脚本转渲染用 scene plan ----------

def _build_scene_plan_prompt(scenes: List[dict], topic_theme: str) -> str:
    """构造 DeepSeek 把分镜脚本转渲染计划的 prompt。

    关键:为每镜提炼一个英文 asset_keyword(Pexels 英文搜索效果好)。
    """
    scenes_block = "\n".join(
        f"镜{i+1}: 口播={s.get('narration','')} | 画面={s.get('visual','')} | 时长={s.get('duration',5)}秒"
        for i, s in enumerate(scenes)
    )
    return f"""你是一位短视频素材策划。请把下面的分镜脚本转成可执行的素材采购计划,为每镜提炼一个适合在 Pexels(无版权素材库)搜索的英文关键词。

【账号主题】{topic_theme}

【分镜脚本】
{scenes_block}

【要求】
1. 每镜输出一个 asset_keyword(英文,2-4 个词,适合 Pexels 搜索,如 "office work laptop"、"city night street")
2. 关键词要贴合该镜画面描述(visual),能搜到相关视频素材
3. 保留原 narration(口播文案,用于 TTS 配音和字幕)和 duration
4. index/narration/visual/duration 原样透传,不要改写口播文案

【输出格式】严格的 JSON,不要 markdown 代码块,不要多余解释:
{{"scenes": [
  {{"index": 1, "narration": "原口播", "visual": "原画面", "asset_keyword": "english keyword", "duration": 5}}
]}}"""


def plan_scenes_from_script(
    video_script: List[dict],
    topic_theme: str = "",
    *,
    llm=None,
) -> List[ScenePlan]:
    """场景 B 核心:把 Flow-2 的视频脚本转成渲染用 scene plan。

    Flow-2 的 video_script 字段是分镜列表(每镜 index/narration/visual/duration),
    这里调 DeepSeek 为每镜提炼 Pexels 搜索关键词(asset_keyword)。

    若 video_script 为空,抛 AgentError(场景 B 必须有脚本)。

    Args:
        video_script: Content.video_script(JSON 列表),Flow-2 产出
        topic_theme: 账号主题(辅助 LLM 理解素材方向)
        llm: LLM 注入(测试用)

    Returns:
        List[ScenePlan],每镜含 asset_keyword,asset_path 待 assets.py 回填

    Raises:
        AgentError: 脚本为空或重试耗尽
    """
    if not video_script or not isinstance(video_script, list):
        raise AgentError("video_script 为空,场景 B 必须有 Flow-2 脚本")

    # 过滤无效分镜(口播为空的跳过,与 copywriter.generate_script 一致)
    valid_scenes = [
        s for s in video_script
        if isinstance(s, dict) and str(s.get("narration", "")).strip()
    ]
    if not valid_scenes:
        raise AgentError("video_script 解析后无有效分镜(口播全空)")

    client = llm or get_llm()
    prompt = _build_scene_plan_prompt(valid_scenes, topic_theme)

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = client.chat(prompt, temperature=0.5)
            data = parse_llm_json(raw)
            return _build_scene_plans(data)
        except Exception as e:
            last_err = e
            logger.warning("scene plan 生成第 %d/%d 次失败: %s", attempt, MAX_RETRIES, e)

    raise AgentError(f"scene plan 生成失败(重试 {MAX_RETRIES} 次): {last_err}")


def _build_scene_plans(data: dict) -> List[ScenePlan]:
    """从 LLM 解析后的 dict 构造 ScenePlan 列表。

    容错:
      - scenes 缺失/空 -> 抛 ValueError
      - 单镜缺 asset_keyword -> 用空串(后续 assets.py 找不到走兜底)
      - 缺 duration -> 默认 5
    """
    scenes_raw = data.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise ValueError("LLM 输出缺少 scenes 数组或为空")

    plans: List[ScenePlan] = []
    for i, s in enumerate(scenes_raw):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration", "")).strip()
        if not narration:
            continue
        plans.append(ScenePlan(
            index=int(s.get("index", i + 1)),
            narration=narration,
            visual=str(s.get("visual", "")).strip(),
            asset_keyword=str(s.get("asset_keyword", "")).strip(),
            duration=int(s.get("duration", 5)),
        ))

    if not plans:
        raise ValueError("scene plan 解析后无有效分镜")

    return plans


__all__ = [
    "HighlightSegment",
    "ClipDecision",
    "ScenePlan",
    "AgentError",
    "decide_highlights",
    "plan_scenes_from_script",
]
