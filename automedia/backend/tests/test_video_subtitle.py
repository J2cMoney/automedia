"""video/subtitle.py faster-whisper 字幕对齐单测 - Phase 4。

全 mock faster-whisper(不下载模型、不真跑),覆盖:
    - align_subtitle: 正常识别 / 音频不存在 / 模型加载失败 / 识别失败 / 逐词时间戳
    - SubtitleData.srt / cues 转换
    - merge_with_tts: TTS 优先 / TTS 空退化 Whisper / 都空
"""
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.video import subtitle as sub_mod
from app.services.video.subtitle import (
    SubtitleData,
    SubtitleError,
    WhisperSegment,
    WordTimestamp,
    align_subtitle,
    merge_with_tts,
    reset_model_cache,
)
from app.services.video.tts import SubtitleCue


# ---------- mock 工具 ----------

@dataclass
class _FakeWord:
    start: float
    end: float
    word: str


@dataclass
class _FakeSeg:
    start: float
    end: float
    text: str
    words: list


@dataclass
class _FakeInfo:
    language: str = "zh"
    language_probability: float = 0.99


def _make_mock_model(segs, info=None):
    """造一个 mock whisper model,transcribe 返回 (segs_gen, info)。"""
    model = MagicMock()
    def transcribe(*a, **kw):
        return (iter(segs), info or _FakeInfo())
    model.transcribe.side_effect = transcribe
    return model


# ---------- align_subtitle ----------

class TestAlignSubtitle:
    def test_missing_audio_raises(self, tmp_path):
        with pytest.raises(SubtitleError, match="音频文件不存在"):
            align_subtitle(tmp_path / "nope.mp3", model_loader=lambda *a: MagicMock())

    def test_model_load_failure(self, tmp_path):
        """模型加载失败抛错。"""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        with pytest.raises(SubtitleError, match="模型加载失败"):
            align_subtitle(audio, model_loader=MagicMock(side_effect=RuntimeError("boom")))

    def test_normal_transcription(self, tmp_path):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        segs = [
            _FakeSeg(0.0, 2.5, "你好世界", [_FakeWord(0.0, 0.5, "你"), _FakeWord(0.5, 1.0, "好")]),
            _FakeSeg(2.5, 5.0, "测试字幕", []),
        ]
        mock_model = _make_mock_model(segs)
        result = align_subtitle(audio, model_loader=lambda *a: mock_model)
        assert len(result.segments) == 2
        assert result.segments[0].text == "你好世界"
        assert result.segments[0].start == 0.0
        assert result.segments[0].end == 2.5
        assert len(result.segments[0].words) == 2
        assert result.segments[0].words[0].word == "你"
        assert result.language == "zh"
        assert result.language_probability == 0.99

    def test_transcribe_failure_raises(self, tmp_path):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("识别崩了")
        with pytest.raises(SubtitleError, match="识别失败"):
            align_subtitle(audio, model_loader=lambda *a: mock_model)

    def test_passes_language_zh(self, tmp_path):
        """默认传 language=zh 给 transcribe。"""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        mock_model = _make_mock_model([])
        align_subtitle(audio, model_loader=lambda *a: mock_model)
        call_kwargs = mock_model.transcribe.call_args[1]
        assert call_kwargs["language"] == "zh"
        assert call_kwargs["vad_filter"] is True
        assert call_kwargs["word_timestamps"] is True


# ---------- SubtitleData.srt / cues ----------

class TestSubtitleData:
    def test_cues_conversion(self):
        data = SubtitleData(
            segments=[
                WhisperSegment(0.0, 2.0, "你好", []),
                WhisperSegment(2.0, 4.0, "世界", []),
            ],
            language="zh",
        )
        cues = data.cues
        assert len(cues) == 2
        assert cues[0].text == "你好"
        assert cues[1].start == 2.0

    def test_srt_output(self):
        data = SubtitleData(
            segments=[WhisperSegment(0.0, 2.0, "你好", [])],
        )
        srt = data.srt
        assert "00:00:00,000 --> 00:00:02,000" in srt
        assert "你好" in srt


# ---------- merge_with_tts ----------

class TestMergeWithTts:
    def test_tts_preferred(self):
        """TTS cues 非空,直接用 TTS。"""
        tts_cues = [SubtitleCue(0, 1, "TTS字")]
        whisper = SubtitleData(segments=[WhisperSegment(0, 1, "Whisper字")])
        result = merge_with_tts(tts_cues, whisper)
        assert len(result) == 1
        assert result[0].text == "TTS字"

    def test_tts_empty_fallback_whisper(self):
        """TTS 空,退化用 Whisper。"""
        whisper = SubtitleData(segments=[WhisperSegment(0, 1, "Whisper字")])
        result = merge_with_tts([], whisper)
        assert len(result) == 1
        assert result[0].text == "Whisper字"

    def test_both_empty(self):
        assert merge_with_tts([], None) == []

    def test_tts_empty_whisper_none(self):
        assert merge_with_tts([], None) == []
