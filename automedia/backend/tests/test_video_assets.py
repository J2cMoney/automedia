"""video/assets.py Pexels 素材获取单测 - Phase 4。

全 mock requests(不联网、不烧配额),覆盖:
    - search_videos: 正常 / key 未配 / 429 配额 / HTTP 错误 / 响应解析
    - download_video: 直链选择 / 流式下载 / 无 mp4 直链 / 下载失败
    - find_or_fallback: 找到 / 无结果返 None / key 未配返 None(兜底)
    - PexelsVideo.best_mp4_link: 多清晰度选最高清 / 过滤 hls
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.video import assets as assets_mod
from app.services.video.assets import AssetsError, PexelsVideo, find_or_fallback


# ---------- PexelsVideo.best_mp4_link ----------

class TestBestMp4Link:
    def test_pick_highest_resolution(self):
        v = PexelsVideo(
            id=1, duration=10, width=1080, height=1920,
            video_files=[
                {"quality": "sd", "width": 720, "link": "http://sd.mp4", "file_type": "video/mp4"},
                {"quality": "hd", "width": 1080, "link": "http://hd.mp4", "file_type": "video/mp4"},
                {"quality": "hls", "link": "http://hls.m3u8", "file_type": "video/hls"},
            ],
        )
        assert v.best_mp4_link == "http://hd.mp4"

    def test_filter_out_hls(self):
        v = PexelsVideo(
            id=2, duration=10, width=0, height=0,
            video_files=[
                {"quality": "hls", "link": "http://x.m3u8", "file_type": "video/hls"},
            ],
        )
        assert v.best_mp4_link is None

    def test_no_files(self):
        v = PexelsVideo(id=3, duration=10, width=0, height=0, video_files=[])
        assert v.best_mp4_link is None


# ---------- search_videos ----------

class TestSearchVideos:
    def _make_response(self, status=200, json_data=None, headers=None, text=""):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_data or {}
        resp.headers = headers or {}
        resp.text = text
        return resp

    def test_normal_search(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "fake-key")
        data = {
            "videos": [
                {"id": 1, "duration": 15, "width": 1080, "height": 1920,
                 "video_files": [{"file_type": "video/mp4", "width": 1080, "link": "http://a.mp4"}],
                 "image": "http://img.jpg"},
                {"id": 2, "duration": 20, "width": 720, "height": 1280,
                 "video_files": [], "image": ""},
            ]
        }
        with patch.object(assets_mod.requests, "get", return_value=self._make_response(json_data=data)):
            videos = assets_mod.search_videos("office work", per_page=5)
        assert len(videos) == 2
        assert videos[0].id == 1
        assert videos[0].best_mp4_link == "http://a.mp4"

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "")
        with pytest.raises(AssetsError, match="PEXELS_API_KEY 未配置"):
            assets_mod.search_videos("test")

    def test_empty_query(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        with pytest.raises(AssetsError, match="搜索词为空"):
            assets_mod.search_videos("   ")

    def test_rate_limit_429(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        resp = self._make_response(status=429, text="Too Many Requests")
        with patch.object(assets_mod.requests, "get", return_value=resp):
            with pytest.raises(AssetsError, match="配额超限"):
                assets_mod.search_videos("test")

    def test_http_error(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        resp = self._make_response(status=500, text="server error")
        with patch.object(assets_mod.requests, "get", return_value=resp):
            with pytest.raises(AssetsError, match="HTTP 500"):
                assets_mod.search_videos("test")

    def test_request_exception(self, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        import requests as req
        with patch.object(assets_mod.requests, "get", side_effect=req.ConnectionError("net down")):
            with pytest.raises(AssetsError, match="请求失败"):
                assets_mod.search_videos("test")

    def test_orientation_portrait_default(self, monkeypatch):
        """默认竖屏(9:16 场景B)。"""
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        with patch.object(assets_mod.requests, "get", return_value=self._make_response(json_data={"videos": []})) as mock_get:
            assets_mod.search_videos("city")
            params = mock_get.call_args[1]["params"]
            assert params["orientation"] == "portrait"


# ---------- download_video ----------

class TestDownloadVideo:
    def test_stream_download(self, tmp_path):
        v = PexelsVideo(
            id=42, duration=10, width=1080, height=1920,
            video_files=[{"file_type": "video/mp4", "width": 1080, "link": "http://x.mp4"}],
        )
        # mock 流式响应
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[b"chunk1", b"chunk2"])

        with patch.object(assets_mod.requests, "get", return_value=mock_resp):
            out = assets_mod.download_video(v, tmp_path)
        assert out.exists()
        assert out.read_bytes() == b"chunk1chunk2"
        assert out.name == "pexels_42.mp4"

    def test_custom_filename(self, tmp_path):
        v = PexelsVideo(
            id=1, duration=10, width=0, height=0,
            video_files=[{"file_type": "video/mp4", "width": 0, "link": "http://x.mp4"}],
        )
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[b"x"])

        with patch.object(assets_mod.requests, "get", return_value=mock_resp):
            out = assets_mod.download_video(v, tmp_path, filename="scene1.mp4")
        assert out.name == "scene1.mp4"

    def test_no_mp4_link_raises(self, tmp_path):
        v = PexelsVideo(id=1, duration=10, width=0, height=0, video_files=[])
        with pytest.raises(AssetsError, match="无可用 mp4 直链"):
            assets_mod.download_video(v, tmp_path)

    def test_download_failure_raises(self, tmp_path):
        v = PexelsVideo(
            id=1, duration=10, width=0, height=0,
            video_files=[{"file_type": "video/mp4", "width": 0, "link": "http://x.mp4"}],
        )
        import requests as req
        with patch.object(assets_mod.requests, "get", side_effect=req.ConnectionError("fail")):
            with pytest.raises(AssetsError, match="下载.*失败"):
                assets_mod.download_video(v, tmp_path)


# ---------- find_or_fallback ----------

class TestFindOrFallback:
    def test_empty_keyword_returns_none(self, tmp_path):
        """空关键词直接返 None(兜底),不调 API。"""
        result = find_or_fallback("", tmp_path)
        assert result is None

    def test_found_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        v = PexelsVideo(
            id=1, duration=10, width=0, height=0,
            video_files=[{"file_type": "video/mp4", "width": 0, "link": "http://x.mp4"}],
        )
        with patch.object(assets_mod, "search_videos", return_value=[v]), \
             patch.object(assets_mod, "download_video", return_value=tmp_path / "scene.mp4") as mock_dl:
            # download_video mock 要让文件存在
            (tmp_path / "scene.mp4").write_bytes(b"x")
            result = find_or_fallback("office", tmp_path, filename="scene.mp4")
        assert result == tmp_path / "scene.mp4"

    def test_no_results_returns_none(self, tmp_path, monkeypatch):
        """Pexels 搜索无结果返 None(需兜底)。"""
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        with patch.object(assets_mod, "search_videos", return_value=[]):
            result = find_or_fallback("rare keyword", tmp_path)
        assert result is None

    def test_search_failure_returns_none(self, tmp_path, monkeypatch):
        """Pexels 搜索失败(key 未配/网络)返 None(需兜底),不抛错。"""
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "")
        result = find_or_fallback("office", tmp_path)
        assert result is None

    def test_download_failure_returns_none(self, tmp_path, monkeypatch):
        """下载失败返 None(需兜底),不抛错。"""
        monkeypatch.setattr(assets_mod.settings, "PEXELS_API_KEY", "k")
        v = PexelsVideo(id=1, duration=10, width=0, height=0, video_files=[])
        with patch.object(assets_mod, "search_videos", return_value=[v]), \
             patch.object(assets_mod, "download_video", side_effect=AssetsError("dl fail")):
            result = find_or_fallback("office", tmp_path)
        assert result is None
