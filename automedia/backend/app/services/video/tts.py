"""Edge-TTS 配音 - Phase 4 场景 B。

职责:
    1. synthesize:中文文本 -> mp3 音频 + 字幕时间轴(WordBoundary 逐词)
    2. list_voices:列出可用中文音色(启动校验,不硬编码防音色漂移)

2026-07 联网确认(写入注释,不靠记忆):
    - edge-tts 7.2.8(2026-03-22),活跃维护
    - 必须联网(走微软云端 WSS 接口),失败即失败(用户决策:不降级豆包)
    - 原生支持 WordBoundary 事件 -> SubMaker 直出 SRT 字幕
    - 锁 ≥7.2.4(修了 403 握手 + NoAudio 已知 bug)
    - 音色随版本漂移,不硬编码,启动 list_voices 校验
    - 推荐中文音色:zh-CN-XiaoxiaoNeural(女,自然)/ zh-CN-YunyangNeural(男)

设计:
    - edge-tts 是异步(aiohttp),用 asyncio.run 在同步 worker 进程里跑
    - 字幕时间轴优先用 TTS 自带(逐词精确),Whisper 做二次校准(subtitle.py)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import edge_tts

logger = logging.getLogger(__name__)

# 默认中文女声(自然度好)。不硬编码假设永远存在,synthesize 前会校验
DEFAULT_VOICE_ZH = "zh-CN-XiaoxiaoNeural"


class TTSError(Exception):
    """TTS 配音异常。"""


@dataclass
class SubtitleCue:
    """单条字幕:起止时间(秒)+ 文本。

    edge-tts WordBoundary 事件按词粒度,这里聚合后供 Remotion 渲染。
    时间单位:秒(从 100ns tick 转换)。
    """
    start: float
    end: float
    text: str


@dataclass
class TTSResult:
    """TTS 合成结果:音频路径 + 字幕时间轴。"""
    audio_path: Path
    cues: List[SubtitleCue] = field(default_factory=list)
    voice: str = ""
    duration: float = 0.0

    @property
    def srt(self) -> str:
        """转 SRT 格式字符串。"""
        return cues_to_srt(self.cues)


def _format_srt_time(seconds: float) -> str:
    """秒 -> SRT 时间码 HH:MM:SS,mmm"""
    total_ms = int(seconds * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: List[SubtitleCue]) -> str:
    """字幕 cue 列表转 SRT 格式字符串。"""
    lines: List[str] = []
    for i, cue in enumerate(cues, start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}")
        lines.append(cue.text)
        lines.append("")  # 空行分隔
    return "\n".join(lines)


async def _list_voices_async(lang_prefix: str = "zh") -> List[str]:
    """异步列出指定语言的可用音色。"""
    voices = await edge_tts.list_voices()
    return [
        v["ShortName"] for v in voices
        if isinstance(v, dict) and v.get("ShortName", "").startswith(lang_prefix)
    ]


def list_voices(lang_prefix: str = "zh") -> List[str]:
    """列出可用音色(同步封装)。

    用于启动时校验音色是否还存在(微软服务端会动态调整,防硬编码漂移)。
    失败返回空列表(不阻塞,用默认音色试)。

    Args:
        lang_prefix: 语言前缀过滤,默认 "zh"(中文)

    Returns:
        音色 ShortName 列表(如 ["zh-CN-XiaoxiaoNeural", ...])
    """
    try:
        return asyncio.run(_list_voices_async(lang_prefix))
    except Exception as e:
        logger.warning("edge-tts list_voices 失败(可能离线),用默认音色: %s", e)
        return []


async def _synthesize_async(
    text: str,
    voice: str,
    audio_path: Path,
) -> List[SubtitleCue]:
    """异步合成核心:流式收音频 + WordBoundary 事件。

    Edge-TTS 默认 boundary="SentenceBoundary"(只产句级),需显式指定
    "WordBoundary" 才能拿到逐词时间轴用于字幕对齐。
    """
    # 显式指定 boundary="WordBoundary"(默认是 SentenceBoundary,无逐词时间轴)
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    cues: List[SubtitleCue] = []

    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # WordBoundary 事件:offset(100ns tick)+ duration + text
                # 时间从 100ns tick 转秒
                offset_sec = chunk.get("offset", 0) / 10_000_000  # 100ns -> 秒
                duration_sec = chunk.get("duration", 0) / 10_000_000
                cue_text = chunk.get("text", "")
                if cue_text:
                    cues.append(SubtitleCue(
                        start=offset_sec,
                        end=offset_sec + duration_sec,
                        text=cue_text,
                    ))

    return cues


def synthesize(
    text: str,
    output_path: Union[str, Path],
    *,
    voice: Optional[str] = None,
) -> TTSResult:
    """合成中文语音 + 字幕时间轴。

    Args:
        text: 要合成的文本(中文)
        output_path: 音频输出路径(.mp3)
        voice: 音色 ShortName,None 用默认 zh-CN-XiaoxiaoNeural

    Returns:
        TTSResult(audio_path, cues, voice, duration)

    Raises:
        TTSError: 合成失败(联网失败/音色无效等)。用户决策:失败即失败不降级。

    注意:
        edge-tts 走微软云端,必须联网。失败抛 TTSError,上层 actor 标记任务失败重试。
    """
    if not text.strip():
        raise TTSError("TTS 文本为空")

    use_voice = voice or DEFAULT_VOICE_ZH
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info("TTS 合成: voice=%s, 文本 %d 字 -> %s", use_voice, len(text), out.name)
    try:
        cues = asyncio.run(_synthesize_async(text, use_voice, out))
    except Exception as e:
        # 清理半成品音频
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass
        raise TTSError(f"Edge-TTS 合成失败(可能离线或音色无效): {type(e).__name__}: {e}") from e

    if not out.exists() or out.stat().st_size == 0:
        raise TTSError("Edge-TTS 合成完成但音频为空(NoAudio 已知 bug,需 ≥7.2.4)")

    # 估算时长(最后一个 cue 的 end,近似)
    duration = cues[-1].end if cues else 0.0

    logger.info("TTS 完成: %s (%d 字幕 cue, 约 %.1fs)", out.name, len(cues), duration)
    return TTSResult(
        audio_path=out,
        cues=cues,
        voice=use_voice,
        duration=duration,
    )


__all__ = [
    "DEFAULT_VOICE_ZH",
    "SubtitleCue",
    "TTSResult",
    "TTSError",
    "cues_to_srt",
    "list_voices",
    "synthesize",
]
