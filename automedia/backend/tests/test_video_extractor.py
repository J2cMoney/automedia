"""video/extractor.py 场景A 高光提取单测 - Phase 4。

编排层测试,mock 掉 ffmpeg subprocess + GLM 决策,验证:
    - 中间产物路径结构正确(frames/clip_decision.json/segments/highlight.mp4)
    - 决策 JSON 落盘可复用
    - 外部传入 decision 时不调 GLM
    - 源视频不存在/无有效片段时抛错
    - concat 列表文件格式正确

真实 GLM + ffmpeg 端到端验收靠手动跑(用户放测试视频后)。
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.video import extractor as ext
from app.services.video.agent import ClipDecision, HighlightSegment


class TestExtractHighlights:
    def test_missing_video_raises(self, tmp_path):
        with pytest.raises(ext.ExtractorError, match="源视频不存在"):
            ext.extract_highlights(tmp_path / "nope.mp4", task_id=1)

    def test_uses_external_decision_skips_llm(self, tmp_path, monkeypatch):
        """外部传入 decision 时不调 GLM(测试/复用场景)。"""
        # 造假视频 + 假抽帧 + mock subprocess
        video = tmp_path / "src.mp4"
        video.write_bytes(b"fake mp4")

        # mock 抽帧:在 OUTPUT_ROOT/{task_id}/frames 下造假帧
        def fake_extract(src, out_dir, **kw):
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for i in range(3):
                (out_dir / f"frame_{i+1:06d}.png").write_bytes(b"fakeimg")
            return sorted(out_dir.glob("frame_*.png"))

        # mock scale_frame:原样返回
        def fake_scale(p, **kw):
            return Path(p)

        decision = ClipDecision(
            segments=[HighlightSegment(0.0, 5.0, "开头"), HighlightSegment(10.0, 20.0, "高潮")],
            summary="测试",
        )

        llm_called = MagicMock()
        with patch.object(ext.frames_mod, "extract_frames", side_effect=fake_extract), \
             patch.object(ext.frames_mod, "scale_frame", side_effect=fake_scale), \
             patch.object(ext.subprocess, "run") as mock_run, \
             patch.object(ext, "OUTPUT_ROOT", tmp_path):
            # mock subprocess:剪切创建段文件,concat 创建成片
            def run_side_effect(cmd, **kw):
                # 识别剪切命令(含 -ss)+ concat 命令(含 -f concat)
                if any("-ss" in str(c) for c in cmd):
                    # 找输出路径(最后一个非 flag 参数)
                    out = Path(cmd[-1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"fake seg")
                elif any("concat" in str(c) for c in cmd):
                    out = Path(cmd[-1])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(b"fake mp4")
                return MagicMock(returncode=0, stderr="", stdout="")
            mock_run.side_effect = run_side_effect

            out_path, ret_decision = ext.extract_highlights(
                video, task_id=42, decision=decision, llm=llm_called,
            )

        # GLM 没被调(外部传了 decision)
        llm_called.vision.assert_not_called() if hasattr(llm_called, "vision") else None
        # 中间产物目录结构
        work = tmp_path / "42"
        assert (work / "clip_decision.json").exists()
        assert (work / "highlight.mp4").exists()
        # 决策 JSON 落盘内容正确
        saved = json.loads((work / "clip_decision.json").read_text(encoding="utf-8"))
        assert saved["summary"] == "测试"
        assert len(saved["segments"]) == 2
        assert out_path.name == "highlight.mp4"

    def test_decision_json_format(self, tmp_path):
        """decision.to_dict() 序列化后能被 json.loads 还原。"""
        decision = ClipDecision(
            segments=[HighlightSegment(1.5, 6.0, "关键段")],
            summary="摘要内容",
        )
        d = decision.to_dict()
        # 确保可序列化
        s = json.dumps(d, ensure_ascii=False)
        back = json.loads(s)
        assert back["segments"][0]["start"] == 1.5
        assert back["summary"] == "摘要内容"

    def test_empty_segments_raises(self, tmp_path):
        """决策无有效片段时抛错。"""
        video = tmp_path / "src.mp4"
        video.write_bytes(b"x")
        decision = ClipDecision(segments=[], summary="")

        def fake_extract(src, out_dir, **kw):
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "frame_000001.png").write_bytes(b"f")
            return [Path(out_dir) / "frame_000001.png"]

        with patch.object(ext.frames_mod, "extract_frames", side_effect=fake_extract), \
             patch.object(ext.frames_mod, "scale_frame", side_effect=lambda p, **kw: Path(p)), \
             patch.object(ext, "OUTPUT_ROOT", tmp_path):
            with pytest.raises(ext.ExtractorError, match="无有效片段"):
                ext.extract_highlights(video, task_id=1, decision=decision)


class TestCutSegments:
    def test_cut_segments_creates_files(self, tmp_path):
        """_cut_segments 为每个切点生成一段文件。"""
        segments_data = [
            HighlightSegment(0.0, 5.0),
            HighlightSegment(10.0, 15.0),
        ]
        segs_dir = tmp_path / "segs"

        video = tmp_path / "src.mp4"
        video.write_bytes(b"src")

        with patch.object(ext.subprocess, "run") as mock_run:
            def run_side_effect(cmd, **kw):
                # 剪切命令:建段文件
                out = Path(cmd[-1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"fake seg")
                return MagicMock(returncode=0, stderr="", stdout="")
            mock_run.side_effect = run_side_effect

            out_files = ext._cut_segments(video, segments_data, segs_dir)
            assert len(out_files) == 2
            assert all(f.exists() for f in out_files)
            assert out_files[0].name == "seg_000.mp4"
            assert out_files[1].name == "seg_001.mp4"

    def test_concat_list_format(self, tmp_path):
        """_concat_segments 写的 concat_list.txt 格式正确。"""
        segs_dir = tmp_path / "segs"
        segs_dir.mkdir()
        seg_files = []
        for i in range(2):
            f = segs_dir / f"seg_{i:03d}.mp4"
            f.write_bytes(b"seg")
            seg_files.append(f)
        output = tmp_path / "out.mp4"

        with patch.object(ext.subprocess, "run") as mock_run:
            def run_side_effect(cmd, **kw):
                # concat 命令:读 list,写成片
                Path(cmd[-1]).write_bytes(b"fake mp4")
                return MagicMock(returncode=0, stderr="", stdout="")
            mock_run.side_effect = run_side_effect

            ext._concat_segments(seg_files, output)
            assert output.exists()
            # concat_list 格式校验(写在 output_path.parent)
            concat_list = output.parent / "concat_list.txt"
            assert concat_list.exists()
            content = concat_list.read_text(encoding="utf-8")
            assert "file '" in content
            assert "seg_000.mp4" in content
            assert "seg_001.mp4" in content
