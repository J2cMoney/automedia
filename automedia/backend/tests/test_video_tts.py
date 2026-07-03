"""video/tts.py Edge-TTS 配音单测 - Phase 4。

全 mock edge_tts(不联网),覆盖:
    - synthesize: 正常合成 / 文本为空 / 合成失败抛错 / 音频为空抛错
    - cues_to_srt: SRT 格式转换
    - _format_srt_time: 时间码格式
    - list_voices: 返回音色列表 / 失败返空
"""
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from app.services.video import tts as tts_mod
from app.services.video.tts import (
    DEFAULT_VOICE_ZH,
    SubtitleCue,
    TTSResult,
    TTSError,
    cues_to_srt,
    list_voices,
    synthesize,
    _format_srt_time,
)


# ---------- _format_srt_time / cues_to_srt ----------

class TestSrtFormat:
    def test_format_zero(self):
        assert _format_srt_time(0.0) == "00:00:00,000"

    def test_format_seconds(self):
        assert _format_srt_time(5.5) == "00:00:05,500"

    def test_format_minutes(self):
        assert _format_srt_time(65.0) == "00:01:05,000"

    def test_format_hours(self):
        assert _format_srt_time(3661.25) == "01:01:01,250"

    def test_cues_to_srt_basic(self):
        cues = [
            SubtitleCue(0.0, 2.5, "你好"),
            SubtitleCue(2.5, 5.0, "世界"),
        ]
        srt = cues_to_srt(cues)
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:02,500" in srt
        assert "你好" in srt
        assert "2\n" in srt
        assert "00:00:02,500 --> 00:00:05,000" in srt
        assert "世界" in srt

    def test_cues_to_srt_empty(self):
        assert cues_to_srt([]) == ""


# ---------- synthesize(mock edge_tts) ----------

class TestSynthesize:
    def _make_async_gen(self, chunks):
        """造一个异步生成器,按顺序 yield chunks。"""
        async def gen():
            for c in chunks:
                yield c
        return gen()

    def test_normal_synthesize(self, tmp_path):
        """正常合成:音频写入 + 字幕 cue 提取。"""
        audio_chunks = [
            {"type": "audio", "data": b"aud1"},
            {"type": "WordBoundary", "offset": 0, "duration": 10_000_000, "text": "你"},  # 0-1s
            {"type": "audio", "data": b"aud2"},
            {"type": "WordBoundary", "offset": 10_000_000, "duration": 10_000_000, "text": "好"},  # 1-2s
        ]
        out = tmp_path / "tts.mp3"

        mock_comm = MagicMock()
        mock_comm.stream = MagicMock(return_value=self._make_async_gen(audio_chunks))

        with patch.object(tts_mod.edge_tts, "Communicate", return_value=mock_comm):
            result = synthesize("你好", out)

        assert result.audio_path == out
        assert out.read_bytes() == b"aud1aud2"
        assert len(result.cues) == 2
        assert result.cues[0].text == "你"
        assert result.cues[0].start == 0.0
        assert result.cues[0].end == 1.0
        assert result.cues[1].text == "好"
        assert result.cues[1].start == 1.0
        assert result.voice == DEFAULT_VOICE_ZH
        assert result.duration == 2.0  # 最后 cue 的 end

    def test_custom_voice(self, tmp_path):
        out = tmp_path / "tts.mp3"
        mock_comm = MagicMock()
        mock_comm.stream = MagicMock(return_value=self._make_async_gen(
            [{"type": "audio", "data": b"x"}]
        ))
        with patch.object(tts_mod.edge_tts, "Communicate", return_value=mock_comm) as mock_c:
            synthesize("测试", out, voice="zh-CN-YunyangNeural")
            # Communicate 构造时传了自定义音色
            args, kwargs = mock_c.call_args
            assert args[1] == "zh-CN-YunyangNeural" or kwargs.get("voice") == "zh-CN-YunyangNeural"

    def test_empty_text_raises(self, tmp_path):
        with pytest.raises(TTSError, match="文本为空"):
            synthesize("", tmp_path / "x.mp3")
        with pytest.raises(TTSError, match="文本为空"):
            synthesize("   ", tmp_path / "x.mp3")

    def test_synthesize_failure_raises(self, tmp_path):
        """合成异常(联网失败)抛 TTSError。"""
        out = tmp_path / "tts.mp3"
        mock_comm = MagicMock()
        mock_comm.stream = MagicMock(side_effect=ConnectionError("微软云不可达"))
        with patch.object(tts_mod.edge_tts, "Communicate", return_value=mock_comm):
            with pytest.raises(TTSError, match="合成失败"):
                synthesize("你好", out)
        # 半成品音频被清理
        assert not out.exists()

    def test_empty_audio_raises(self, tmp_path):
        """合成完音频为空(NoAudio bug)抛错。"""
        out = tmp_path / "tts.mp3"
        mock_comm = MagicMock()
        # 只 yield WordBoundary 不 yield audio -> 音频文件为空
        mock_comm.stream = MagicMock(return_value=self._make_async_gen([
            {"type": "WordBoundary", "offset": 0, "duration": 10_000_000, "text": "x"}
        ]))
        with patch.object(tts_mod.edge_tts, "Communicate", return_value=mock_comm):
            with pytest.raises(TTSError, match="音频为空"):
                synthesize("你好", out)

    def test_creates_parent_dir(self, tmp_path):
        """输出目录不存在自动建。"""
        out = tmp_path / "sub" / "deep" / "tts.mp3"
        mock_comm = MagicMock()
        mock_comm.stream = MagicMock(return_value=self._make_async_gen(
            [{"type": "audio", "data": b"x"}]
        ))
        with patch.object(tts_mod.edge_tts, "Communicate", return_value=mock_comm):
            synthesize("测试", out)
        assert out.exists()


# ---------- list_voices ----------

class TestListVoices:
    def test_returns_zh_voices(self):
        async def fake_list():
            return [
                {"ShortName": "zh-CN-XiaoxiaoNeural"},
                {"ShortName": "zh-CN-YunyangNeural"},
                {"ShortName": "en-US-EmmaNeural"},  # 非中文,过滤掉
            ]
        with patch.object(tts_mod.edge_tts, "list_voices", new=fake_list):
            voices = list_voices("zh")
        assert "zh-CN-XiaoxiaoNeural" in voices
        assert "zh-CN-YunyangNeural" in voices
        assert all(v.startswith("zh") for v in voices)

    def test_failure_returns_empty(self):
        async def fake_list():
            raise ConnectionError("离线")
        with patch.object(tts_mod.edge_tts, "list_voices", new=fake_list):
            voices = list_voices("zh")
        assert voices == []
