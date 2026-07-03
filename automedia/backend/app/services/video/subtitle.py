"""faster-whisper 字幕对齐 - Phase 4 场景 B。

职责:
    1. align_subtitle:TTS 音频 -> 带时间轴的字幕(segment 级 + 可选逐词)
    2. to_srt:字幕数据转 SRT 格式供 Remotion 渲染
    3. merge_with_tts:Edge-TTS 自带时间轴优先,Whisper 做二次校准

2026-07 联网确认(写入注释,不靠记忆):
    - 弃 openai-whisper(慢、Windows 装 PyTorch 麻烦),用 faster-whisper 1.2.1
    - 后端 CTranslate2,pip 一行装,自带 Windows wheel
    - CPU 用 small(466MB)起步,TTS 清晰音频 small 够;medium 更准但慢
    - 模型缓存在 ~/.cache/huggingface/hub/(首次下载,可离线复制)
    - 关键参数:language="zh" + vad_filter=True + word_timestamps=True
    - Windows 坑:cuBLAS/cuDNN(GPU 用),CPU 模式无坑

设计:
    - 模型加载慢(~5-10s),用模块级单例缓存,首次加载后复用
    - TTS 音频是标准发音无噪声,Whisper 准确率高;时间轴以 TTS 自带为主,
      Whisper 做交叉验证(subtitle.py 不覆盖 TTS 时间,只提供备选)
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from app.services.video.tts import SubtitleCue, cues_to_srt

logger = logging.getLogger(__name__)

# 默认模型(CPU 友好,中文准确率够)。medium 更准但慢 3 倍、大 3 倍
DEFAULT_MODEL = "small"
DEFAULT_DEVICE = "cpu"
DEFAULT_COMPUTE_TYPE = "int8"  # CPU 量化,速度快内存低


class SubtitleError(Exception):
    """字幕识别异常。"""


@dataclass
class WordTimestamp:
    """逐词时间戳(word-level)。"""
    start: float
    end: float
    word: str


@dataclass
class WhisperSegment:
    """Whisper 识别出的一段(segment 级,含逐词)。"""
    start: float
    end: float
    text: str
    words: List[WordTimestamp] = field(default_factory=list)

    def to_cue(self) -> SubtitleCue:
        """转成 TTS 模块的 SubtitleCue(统一字幕结构)。"""
        return SubtitleCue(start=self.start, end=self.end, text=self.text.strip())


@dataclass
class SubtitleData:
    """完整字幕数据:segment 列表 + 检测语言信息。"""
    segments: List[WhisperSegment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0

    @property
    def cues(self) -> List[SubtitleCue]:
        """转成统一 cue 列表(复用 TTS 模块结构)。"""
        return [s.to_cue() for s in self.segments]

    @property
    def srt(self) -> str:
        """转 SRT 格式。"""
        return cues_to_srt(self.cues)


# ---------- 模型单例(加载慢,缓存) ----------

_model_instance = None
_model_key = None  # (model, device, compute_type) 缓存键


def _get_model(model: str, device: str, compute_type: str):
    """获取或加载 Whisper 模型单例。

    模型加载 5-10s + 首次下载,用单例避免重复加载。
    切换模型参数会重新加载(缓存键变化)。
    """
    global _model_instance, _model_key
    key = (model, device, compute_type)
    if _model_instance is not None and _model_key == key:
        return _model_instance

    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise SubtitleError(
            f"faster-whisper 未安装: {e}。pip install faster-whisper==1.2.1"
        ) from e

    logger.info("加载 Whisper 模型: %s (%s/%s)", model, device, compute_type)
    try:
        _model_instance = WhisperModel(model, device=device, compute_type=compute_type)
        _model_key = key
    except Exception as e:
        raise SubtitleError(f"Whisper 模型加载失败: {type(e).__name__}: {e}") from e

    return _model_instance


def reset_model_cache():
    """重置模型缓存(测试用)。"""
    global _model_instance, _model_key
    _model_instance = None
    _model_key = None


def align_subtitle(
    audio_path: Union[str, Path],
    *,
    language: str = "zh",
    model: str = DEFAULT_MODEL,
    device: str = DEFAULT_DEVICE,
    compute_type: str = DEFAULT_COMPUTE_TYPE,
    beam_size: int = 5,
    vad_filter: bool = True,
    word_timestamps: bool = True,
    model_loader=None,
) -> SubtitleData:
    """对齐字幕:faster-whisper 把音频转成带时间轴的字幕。

    Args:
        audio_path: 音频文件路径(mp3/wav)
        language: 语言代码(默认 "zh",指定中文避免误判语种)
        model: 模型大小 tiny/base/small/medium/large-v3(默认 small)
        device: cpu/cuda(默认 cpu)
        compute_type: 计算精度(默认 int8,CPU 量化)
        beam_size: 束搜索宽度(默认 5,越大越准越慢)
        vad_filter: 静音过滤(默认 True,提升速度与准确率)
        word_timestamps: 逐词时间戳(默认 True)
        model_loader: 模型加载器注入(测试用,默认 _get_model 单例)

    Returns:
        SubtitleData(segments + language)

    Raises:
        SubtitleError: 音频不存在 / 模型加载失败 / 识别失败

    注意:
        场景 B 的字幕时间轴优先用 TTS 自带的 WordBoundary(更精确,因为 TTS
        本身就是按时间合成的)。Whisper 这里做交叉验证或 TTS 失败时的备选。
    """
    src = Path(audio_path)
    if not src.exists():
        raise SubtitleError(f"音频文件不存在: {src}")

    loader = model_loader or _get_model
    try:
        whisper_model = loader(model, device, compute_type)
    except SubtitleError:
        raise
    except Exception as e:
        raise SubtitleError(f"模型加载失败: {e}") from e

    logger.info("Whisper 识别: %s (lang=%s, model=%s)", src.name, language, model)
    try:
        segments_gen, info = whisper_model.transcribe(
            str(src),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
        )
        # segments 是生成器,强制求值
        segments: List[WhisperSegment] = []
        for seg in segments_gen:
            words = []
            if word_timestamps and getattr(seg, "words", None):
                for w in seg.words:
                    words.append(WordTimestamp(
                        start=float(w.start),
                        end=float(w.end),
                        word=str(w.word).strip(),
                    ))
            segments.append(WhisperSegment(
                start=float(seg.start),
                end=float(seg.end),
                text=str(seg.text).strip(),
                words=words,
            ))
    except Exception as e:
        raise SubtitleError(f"Whisper 识别失败: {type(e).__name__}: {e}") from e

    logger.info("Whisper 完成: %d 段, 语言=%s(%.2f)",
                len(segments), info.language, info.language_probability)
    return SubtitleData(
        segments=segments,
        language=str(info.language),
        language_probability=float(info.language_probability),
    )


def merge_with_tts(
    tts_cues: List[SubtitleCue],
    whisper_data: Optional[SubtitleData] = None,
) -> List[SubtitleCue]:
    """合并 TTS 时间轴和 Whisper 识别结果。

    策略(Spec 场景 B:TTS 时间轴优先):
      - TTS cues 非空 -> 直接用 TTS(逐词精确,因为 TTS 按时间合成)
      - TTS cues 空 -> 退化用 Whisper(若提供)
      - 两者都空 -> 返回空(无字幕)

    本函数不搞复杂对齐算法(TTS 时间轴理论准,Whisper 仅备选/校验)。
    若发现 TTS 时间轴实测有偏差,这里再补校准逻辑。

    Args:
        tts_cues: TTS 模块产的字幕时间轴
        whisper_data: Whisper 识别结果(可选)

    Returns:
        最终字幕 cue 列表
    """
    if tts_cues:
        return tts_cues
    if whisper_data is not None:
        return whisper_data.cues
    return []


__all__ = [
    "DEFAULT_MODEL",
    "SubtitleError",
    "WordTimestamp",
    "WhisperSegment",
    "SubtitleData",
    "align_subtitle",
    "merge_with_tts",
    "reset_model_cache",
]
