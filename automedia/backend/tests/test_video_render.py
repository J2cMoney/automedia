"""video/render.py Remotion 渲染封装单测 - Phase 4。

全 mock subprocess(不真渲染,测试要快),覆盖:
    - _build_props: scene plan 转 Remotion props / 时间区间计算
    - _npx_cmd: Windows 返回 npx.cmd
    - render_video: 命令构造 / 串行锁 / 无分镜抛错 / 音频不存在 / 渲染失败
    - ensure_browser: mock subprocess 调用

真实渲染靠手动验收(步骤 8 POC + 步骤 9 端到端)。
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.video import render as render_mod
from app.services.video.agent import ScenePlan
from app.services.video.render import RenderError, ensure_browser, render_video
from app.services.video.tts import SubtitleCue


# ---------- _build_props ----------

class TestBuildProps:
    def test_time_intervals(self):
        """每镜 start/end 按顺序累加。"""
        scenes = [
            ScenePlan(index=1, narration="n1", visual="v1", duration=3),
            ScenePlan(index=2, narration="n2", visual="v2", duration=5),
            ScenePlan(index=3, narration="n3", visual="v3", duration=4),
        ]
        props = render_mod._build_props(scenes, "audio.mp3", [])
        assert len(props["scenes"]) == 3
        assert props["scenes"][0]["start"] == 0.0
        assert props["scenes"][0]["end"] == 3.0
        assert props["scenes"][1]["start"] == 3.0
        assert props["scenes"][1]["end"] == 8.0
        assert props["scenes"][2]["start"] == 8.0
        assert props["scenes"][2]["end"] == 12.0
        assert props["audioPath"] == "audio.mp3"

    def test_cues_passthrough(self):
        scenes = [ScenePlan(index=1, narration="n", visual="v", duration=5)]
        cues = [SubtitleCue(0.0, 2.0, "你好"), SubtitleCue(2.0, 5.0, "世界")]
        props = render_mod._build_props(scenes, "a.mp3", cues)
        assert len(props["cues"]) == 2
        assert props["cues"][0]["text"] == "你好"

    def test_asset_path_none_kept(self):
        """asset_path 为 None 时保留(None 表示待兜底)。"""
        s = ScenePlan(index=1, narration="n", visual="v", asset_path=None)
        props = render_mod._build_props([s], "a.mp3", [])
        assert props["scenes"][0]["asset_path"] is None


# ---------- _npx_cmd ----------

class TestNpxCmd:
    def test_windows(self):
        with patch.object(render_mod.sys, "platform", "win32"):
            assert render_mod._npx_cmd() == "npx.cmd"

    def test_linux(self):
        with patch.object(render_mod.sys, "platform", "linux"):
            assert render_mod._npx_cmd() == "npx"


# ---------- render_video ----------

class TestRenderVideo:
    def test_empty_scenes_raises(self, tmp_path):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        with pytest.raises(RenderError, match="无分镜"):
            render_video([], audio, tmp_path / "out.mp4", ensure_browser_first=False)

    def test_missing_audio_raises(self, tmp_path):
        scenes = [ScenePlan(index=1, narration="n", visual="v", duration=3)]
        with pytest.raises(RenderError, match="配音音频不存在"):
            render_video(scenes, tmp_path / "nope.mp3", tmp_path / "out.mp4",
                         ensure_browser_first=False)

    def test_missing_remotion_dir_raises(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        scenes = [ScenePlan(index=1, narration="n", visual="v", duration=3)]
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", tmp_path / "nope")
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        with pytest.raises(RenderError, match="Remotion 项目目录不存在"):
            render_video(scenes, audio, tmp_path / "out.mp4", ensure_browser_first=False)

    def test_command_structure(self, tmp_path, monkeypatch):
        """渲染命令含 entry/composition/output/codec/props。"""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        scenes = [ScenePlan(index=1, narration="n", visual="v", duration=3)]
        out = tmp_path / "out.mp4"

        # 建假 remotion dir
        remotion_dir = tmp_path / "remotion"
        remotion_dir.mkdir()
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", remotion_dir)
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        render_mod.reset_render_state()

        with patch.object(render_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            # mock 输出文件存在
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"fake mp4")
            render_video(scenes, audio, out, ensure_browser_first=False)
            cmd = mock_run.call_args[0][0]
            assert "remotion" in cmd
            assert "render" in cmd
            assert render_mod.ENTRY_FILE in cmd
            assert render_mod.COMPOSITION_ID in cmd
            assert str(out) in cmd
            assert "--codec=h264" in cmd
            # props 文件参数
            props_args = [c for c in cmd if str(c).startswith("--props=")]
            assert len(props_args) == 1

    def test_render_failure_raises(self, tmp_path, monkeypatch):
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        scenes = [ScenePlan(index=1, narration="n", visual="v", duration=3)]

        remotion_dir = tmp_path / "remotion"
        remotion_dir.mkdir()
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", remotion_dir)
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        render_mod.reset_render_state()

        with patch.object(render_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="render error", stdout="")
            with pytest.raises(RenderError, match="渲染失败"):
                render_video(scenes, audio, tmp_path / "out.mp4", ensure_browser_first=False)

    def test_props_file_written(self, tmp_path, monkeypatch):
        """渲染前写 render_props.json,内容含 scenes/cues。"""
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        scenes = [ScenePlan(index=1, narration="n1", visual="v1", duration=3)]
        cues = [SubtitleCue(0, 3, "字幕")]
        out = tmp_path / "sub" / "out.mp4"

        remotion_dir = tmp_path / "remotion"
        remotion_dir.mkdir()
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", remotion_dir)
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        render_mod.reset_render_state()

        with patch.object(render_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"mp4")
            render_video(scenes, audio, out, cues=cues, ensure_browser_first=False)

        props_file = out.parent / "render_props.json"
        assert props_file.exists()
        data = json.loads(props_file.read_text(encoding="utf-8"))
        assert len(data["scenes"]) == 1
        assert data["scenes"][0]["narration"] == "n1"
        assert len(data["cues"]) == 1
        assert data["cues"][0]["text"] == "字幕"


# ---------- ensure_browser ----------

class TestEnsureBrowser:
    def test_caches_after_success(self, tmp_path, monkeypatch):
        """成功后进程内缓存,不重复调。"""
        remotion_dir = tmp_path / "remotion"
        remotion_dir.mkdir()
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", remotion_dir)
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        render_mod.reset_render_state()

        with patch.object(render_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            ensure_browser()
            ensure_browser()  # 第二次应跳过
            assert mock_run.call_count == 1

    def test_missing_remotion_dir(self, tmp_path, monkeypatch):
        render_mod.reset_render_state()
        monkeypatch.setattr(render_mod, "DEFAULT_REMOTION_DIR", tmp_path / "nope")
        monkeypatch.setattr(render_mod.settings, "REMOTION_PROJECT_DIR", "")
        with pytest.raises(RenderError, match="Remotion 项目目录不存在"):
            ensure_browser()
