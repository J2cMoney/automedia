"""场景 B:从零生成视频 - Phase 4 FLOW-3 场景 B。

完整链路(Flow-2 文案脚本 → 全新拼装视频):
    1. plan_scenes_from_script(agent.py):脚本转渲染用 scene plan(每镜含 asset_keyword)
    2. 逐镜找素材(assets.py find_or_fallback):Pexels 找为主,找不到标记需兜底上传
    3. TTS 配音(tts.py synthesize):所有镜口播拼成一段,Edge-TTS 合成 + 字幕时间轴
    4. 字幕对齐(subtitle.py):TTS 时间轴优先,Whisper 备选(场景B TTS 时间准,通常不需)
    5. Remotion 渲染(render.py render_video):scene plan + 音频 + 字幕 → 9:16 成片

混合素材模式(Spec FLOW-3 MUST):
    Pexels 找到 -> asset_path 回填
    Pexels 找不到 / key 未配 -> asset_path 保持 None,Remotion 渲染纯色背景兜底
    (前端可后续加手动上传接口补素材,本 Phase 先用兜底渲染跑通)

中间产物保留(Spec FLOW-3 MUST:失败可重试):
    output/{task_id}/scene_plan.json    分镜计划(含素材路径)
    output/{task_id}/tts.mp3            配音音频
    output/{task_id}/subtitles.json     字幕时间轴
    output/{task_id}/assets/            下载的 Pexels 素材
    output/{task_id}/render_props.json  Remotion 渲染参数
    output/{task_id}/video.mp4          最终成片
"""
import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

from app.config import BASE_DIR
from app.services.video import agent as agent_mod
from app.services.video import assets as assets_mod
from app.services.video import render as render_mod
from app.services.video import subtitle as sub_mod
from app.services.video import tts as tts_mod
from app.services.video.agent import ScenePlan
from app.services.video.tts import SubtitleCue

logger = logging.getLogger(__name__)

OUTPUT_ROOT = BASE_DIR / "output"


class GeneratorError(Exception):
    """场景 B 生成异常。"""


def generate_from_script(
    video_script: List[dict],
    *,
    task_id: int,
    topic_theme: str = "",
    voice: Optional[str] = None,
    whisper_fallback: bool = False,
    llm=None,
    tts_synthesizer=None,
    whisper_aligner=None,
    renderer=None,
) -> Tuple[Path, List[ScenePlan], List[SubtitleCue]]:
    """场景 B 主入口:Flow-2 脚本 → 全新拼装视频。

    Args:
        video_script: Content.video_script(分镜列表,Flow-2 产出)
        task_id: 任务 id(决定中间产物目录)
        topic_theme: 账号主题(辅助 LLM 理解素材方向)
        voice: TTS 音色,None 用默认 zh-CN-XiaoxiaoNeural
        whisper_fallback: TTS 时间轴为空时是否用 Whisper 备选(默认 False,TTS 通常够)
        llm: LLM 客户端注入(测试用)
        tts_synthesizer: TTS 合成函数注入(测试用,签名同 tts.synthesize)
        whisper_aligner: Whisper 对齐函数注入(测试用,签名同 subtitle.align_subtitle)
        renderer: 渲染函数注入(测试用,签名同 render.render_video)

    Returns:
        (output_path, scene_plans, cues):成片路径 + 分镜计划 + 字幕时间轴

    Raises:
        GeneratorError: 任何环节失败
    """
    if not video_script:
        raise GeneratorError("video_script 为空,场景 B 必须有 Flow-2 脚本")

    work_dir = OUTPUT_ROOT / str(task_id)
    assets_dir = work_dir / "assets"
    audio_path = work_dir / "tts.mp3"
    output_path = work_dir / "video.mp4"

    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 脚本 -> scene plan(LLM 提炼 asset_keyword)
    logger.info("场景B task=%s: 脚本转 scene plan", task_id)
    try:
        plans = agent_mod.plan_scenes_from_script(
            video_script, topic_theme, llm=llm,
        )
    except Exception as e:
        raise GeneratorError(f"scene plan 生成失败: {e}") from e

    # 2. 逐镜找素材(Pexels 为主,找不到 asset_path 保持 None 兜底)
    logger.info("场景B task=%s: 逐镜找 Pexels 素材(%d 镜)", task_id, len(plans))
    for plan in plans:
        if not plan.asset_keyword:
            logger.info("场景B 镜 %d 无素材关键词,用兜底渲染", plan.index)
            continue
        asset_path = assets_mod.find_or_fallback(
            plan.asset_keyword, assets_dir,
            filename=f"scene_{plan.index:03d}.mp4",
        )
        if asset_path is not None:
            plan.asset_path = str(asset_path)
            logger.info("场景B 镜 %d 素材: %s", plan.index, asset_path.name)
        else:
            logger.info("场景B 镜 %d 素材未找到,用兜底渲染", plan.index)

    # scene plan 落盘(便于失败重试复用 + 前端查看素材缺失情况)
    (work_dir / "scene_plan.json").write_text(
        json.dumps([p.to_dict() for p in plans], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3. TTS 配音(所有镜口播拼成一段,保持顺序)
    full_narration = " ".join(p.narration for p in plans)
    logger.info("场景B task=%s: TTS 配音(%d 字)", task_id, len(full_narration))
    synth = tts_synthesizer or tts_mod.synthesize
    try:
        tts_result = synth(full_narration, audio_path, voice=voice)
    except Exception as e:
        raise GeneratorError(f"TTS 配音失败: {e}") from e

    # 用 TTS 实际时长重算每镜 duration,保证音画同步(Spec H4)
    # TTS 时长由字数决定,和 LLM 给的 duration(默认5s)几乎不可能相等。
    # 这里按各镜口播字数比例,把 TTS 总时长分配给每镜,让画面跟上配音。
    _resync_durations(plans, tts_result.duration)

    cues = list(tts_result.cues)

    # 4. 字幕对齐(TTS 时间轴优先;空时按配置决定是否用 Whisper)
    if not cues and whisper_fallback:
        logger.info("场景B task=%s: TTS 无字幕,Whisper 备选对齐", task_id)
        aligner = whisper_aligner or sub_mod.align_subtitle
        try:
            whisper_data = aligner(audio_path)
            cues = sub_mod.merge_with_tts([], whisper_data)
        except Exception as e:
            logger.warning("Whisper 备选对齐失败,继续无字幕渲染: %s", e)

    # 字幕落盘
    (work_dir / "subtitles.json").write_text(
        json.dumps([{"start": c.start, "end": c.end, "text": c.text} for c in cues],
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 5. Remotion 渲染
    logger.info("场景B task=%s: Remotion 渲染成片", task_id)
    do_render = renderer or render_mod.render_video
    try:
        result_path = do_render(plans, audio_path, output_path, cues=cues)
    except Exception as e:
        raise GeneratorError(f"Remotion 渲染失败: {e}") from e

    logger.info("场景B task=%s 完成: %s", task_id, result_path)
    return result_path, plans, cues


def _resync_durations(plans: List[ScenePlan], tts_duration: float) -> None:
    """用 TTS 实际时长重算每镜 duration,保证音画同步。

    问题:LLM 给的 scene.duration(默认5s)和 TTS 实际时长(按字数)几乎不可能相等,
    直接用 LLM 时长会导致音画错位(音频提前结束或被截断)。

    方案:按各镜口播字数(narration 长度)比例,把 TTS 总时长分配给每镜。
    最短 1 秒(防过短闪过),向上取整。
    字数长度剔除空格和标点。
    """
    if not plans or tts_duration <= 0:
        return

    # 算每镜"有效字符数"(去空格/标点,中文按字,英文按词近似)
    import re
    def char_weight(s: str) -> int:
        cleaned = re.sub(r"[\s，。！？,.!?;:、]", "", s)
        return max(1, len(cleaned))

    weights = [char_weight(p.narration) for p in plans]
    total_weight = sum(weights)

    allocated = 0.0
    for i, p in enumerate(plans):
        if i == len(plans) - 1:
            # 最后一镜吃掉剩余(消除浮点累积误差)
            p.duration = max(1, round(tts_duration - allocated))
        else:
            dur = tts_duration * weights[i] / total_weight
            p.duration = max(1, round(dur))
            allocated += p.duration


def get_missing_asset_scenes(plans: List[ScenePlan]) -> List[int]:
    """找出素材未找到的镜 index(供前端提示手动上传)。

    Args:
        plans: scene plan 列表

    Returns:
        缺素材的镜 index 列表(空=全部找到)
    """
    return [p.index for p in plans if p.needs_asset]


__all__ = [
    "OUTPUT_ROOT",
    "GeneratorError",
    "generate_from_script",
    "get_missing_asset_scenes",
]
