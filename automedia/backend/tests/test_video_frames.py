"""video/frames.py 抽帧工具单测 - Phase 4。

全 mock subprocess(不真跑 ffmpeg),覆盖:
    - get_video_duration: 正常解析 / 解析失败抛错
    - extract_frames: 命令构造 / 输出帧排序 / 视频不存在 / max_frames 限制
    - frames_to_timestamp / timestamp_to_frame_index 互转
    - scale_frame: 真实小图缩放(不 mock,轻量)
"""
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from app.services.video import frames as frames_mod


# ---------- get_video_duration ----------

class TestGetDuration:
    def test_parse_normal(self):
        """ffmpeg stderr 含 Duration 行,能解析成秒。"""
        fake_stderr = (
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'test.mp4':\n"
            "  Duration: 00:02:30.50, start: 0.000000, bitrate: 2000 kb/s\n"
            "At least one output file must be specified\n"
        )
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr=fake_stderr, stdout="")
            dur = frames_mod.get_video_duration("test.mp4")
        # 2分30.5秒 = 150.5
        assert dur == pytest.approx(150.5)

    def test_parse_zero_padding(self):
        """时分秒零填充格式。"""
        fake_stderr = "  Duration: 00:00:05.00, start: 0.000000\n"
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr=fake_stderr, stdout="")
            assert frames_mod.get_video_duration("x") == pytest.approx(5.0)

    def test_parse_failure_raises(self):
        """stderr 没 Duration 行,抛 RuntimeError。"""
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="garbage", stdout="")
            with pytest.raises(RuntimeError, match="无法解析视频时长"):
                frames_mod.get_video_duration("x.mp4")


# ---------- extract_frames ----------

class TestExtractFrames:
    def test_command_structure(self, tmp_path):
        """抽帧命令包含 fps 滤镜和输出 pattern。"""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")  # 让 exists() 过
        with patch.object(frames_mod.subprocess, "run") as mock_run, \
             patch.object(frames_mod, "sorted", side_effect=lambda x: sorted(x)):
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            # 模拟 ffmpeg 生成了几帧文件
            for i in range(1, 4):
                (tmp_path / f"frame_{i:06d}.png").write_bytes(b"")
            frames_mod.extract_frames(video, tmp_path, fps=1.0)
        cmd = mock_run.call_args[0][0]
        # 命令含 -vf fps=1.0
        assert any("fps=1.0" in c for c in cmd)
        # 含输出 pattern
        assert any("frame_%06d.png" in c for c in cmd)
        # 含 -y 覆盖
        assert "-y" in cmd

    def test_max_frames_adds_vframes(self, tmp_path):
        """max_frames>0 时命令带 -frames:v N。"""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            frames_mod.extract_frames(video, tmp_path, max_frames=30)
        cmd = mock_run.call_args[0][0]
        assert "-frames:v" in cmd
        idx = cmd.index("-frames:v")
        assert cmd[idx + 1] == "30"

    def test_missing_video_raises(self, tmp_path):
        """视频不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            frames_mod.extract_frames(tmp_path / "nope.mp4", tmp_path)

    def test_ffmpeg_failure_raises(self, tmp_path):
        """ffmpeg 返回非 0 抛 RuntimeError。"""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="broken", stdout="")
            with pytest.raises(RuntimeError, match="ffmpeg 抽帧失败"):
                frames_mod.extract_frames(video, tmp_path)

    def test_frames_returned_sorted(self, tmp_path):
        """返回的帧列表按文件名升序。"""
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        with patch.object(frames_mod.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            # 故意乱序创建
            for i in [3, 1, 2]:
                (tmp_path / f"frame_{i:06d}.png").write_bytes(b"")
            result = frames_mod.extract_frames(video, tmp_path, fps=1.0)
        names = [p.name for p in result]
        assert names == ["frame_000001.png", "frame_000002.png", "frame_000003.png"]


# ---------- 时间戳互转 ----------

class TestTimestampConvert:
    def test_index_to_time(self):
        # frame_000001.png (index 1) @ 1fps = 0s
        assert frames_mod.frames_to_timestamp(1, fps=1.0) == 0.0
        # index 11 @ 1fps = 10s
        assert frames_mod.frames_to_timestamp(11, fps=1.0) == 10.0
        # index 21 @ 2fps = 10s
        assert frames_mod.frames_to_timestamp(21, fps=2.0) == 10.0

    def test_time_to_index(self):
        assert frames_mod.timestamp_to_frame_index(0.0, fps=1.0) == 1
        assert frames_mod.timestamp_to_frame_index(10.0, fps=1.0) == 11
        assert frames_mod.timestamp_to_frame_index(10.0, fps=2.0) == 21

    def test_roundtrip(self):
        for ts in [0.0, 5.0, 30.5, 99.9]:
            idx = frames_mod.timestamp_to_frame_index(ts, fps=1.0)
            back = frames_mod.frames_to_timestamp(idx, fps=1.0)
            assert abs(back - ts) < 1.0  # 帧率精度内


# ---------- scale_frame(轻量真实测试) ----------

class TestScaleFrame:
    def test_downscale_and_jpeg(self, tmp_path):
        """大图缩到 max_size 内 + 转 jpeg(高信息帧压缩必要)。"""
        src = tmp_path / "big.png"
        Image.new("RGB", (1920, 1080), (255, 0, 0)).save(src)
        out = frames_mod.scale_frame(src, max_size=1024)
        img = Image.open(out)
        w, h = img.size
        assert max(w, h) <= 1024
        assert w == 1024 and h == 576
        # 默认 force_jpeg=True,输出是 .jpg
        assert out.suffix == ".jpg"

    def test_small_still_converted_to_jpeg(self, tmp_path):
        """小图也会转 jpeg(保持体积一致策略)。"""
        src = tmp_path / "small.png"
        Image.new("RGB", (100, 100), (0, 255, 0)).save(src)
        out = frames_mod.scale_frame(src, max_size=1024)
        img = Image.open(out)
        assert out.suffix == ".jpg"
        # 尺寸不放大
        assert img.size == (100, 100)

    def test_force_jpeg_off_keeps_format(self, tmp_path):
        """force_jpeg=False 时不转格式。"""
        src = tmp_path / "src.png"
        Image.new("RGB", (2000, 2000), (0, 0, 255)).save(src)
        out = frames_mod.scale_frame(src, max_size=512, force_jpeg=False)
        assert out.suffix == ".png"

    def test_jpeg_compresses_size(self, tmp_path):
        """高信息 PNG 转 JPEG 后体积大幅下降(解决 GLM 1210 总请求体超限)。"""
        src = tmp_path / "noisy.png"
        # 造高信息噪点图(PNG 压不下,用小尺寸加速测试)
        import random
        img = Image.new("RGB", (200, 120))
        px = img.load()
        for x in range(200):
            for y in range(120):
                px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        img.save(src)
        png_size = src.stat().st_size
        out = frames_mod.scale_frame(src, max_size=1024, jpeg_quality=75)
        jpg_size = out.stat().st_size
        # JPEG 应比 PNG 小
        assert jpg_size < png_size
        assert out.suffix == ".jpg"

    def test_output_to_new_path_jpg(self, tmp_path):
        """指定 output_path 缩放到新文件(force_jpeg 转 .jpg)。"""
        src = tmp_path / "src.png"
        Image.new("RGB", (2000, 2000), (0, 0, 255)).save(src)
        dest = tmp_path / "out.png"  # 故意给 .png,force_jpeg 会改成 .jpg
        result = frames_mod.scale_frame(src, max_size=512, output_path=dest)
        # 输出实际是 .jpg(force_jpeg 改了后缀)
        assert result.suffix == ".jpg"
        assert result.exists()
        img = Image.open(result)
        assert max(img.size) <= 512
