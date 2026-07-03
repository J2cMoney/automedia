"""api/videos.py 视频生成路由单测 - Phase 4。

覆盖:
    - POST /upload: 上传视频文件 / 非法格式拒绝 / 返回路径
    - POST /extract: 源视频不存在拒绝 / 提交返回 task_id
    - POST /generate: Content 不存在拒绝 / 无 script 拒绝 / 正常提交
    - GET /{content_id}: 状态查询

用 FastAPI TestClient,mock submit 避免真入队。
"""
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """TestClient(app),隔离 DB。"""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session():
    """同步 DB session(造测试数据)。每个测试独立 session,测完回滚。"""
    from app.db import SyncSessionLocal
    s = SyncSessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def content_with_script(db_session):
    """造一条有 video_script 的 Content。"""
    from app.models.content import Content, ContentStatus
    from app.models.account import Account, Platform

    acc = Account(platform=Platform.XHS, nickname="测试号", topic_theme="AI")
    db_session.add(acc)
    db_session.flush()

    content = Content(
        account_id=acc.id, status=ContentStatus.APPROVED,
        title="测试标题", body="测试正文", tags=["#test"],
        video_script=[{"index": 1, "narration": "口播", "visual": "画面", "duration": 5}],
    )
    db_session.add(content)
    db_session.commit()
    return content


# ---------- upload ----------

class TestUpload:
    def test_upload_mp4(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.api.videos.UPLOADS_DIR", tmp_path)
        resp = client.post(
            "/api/videos/upload",
            files={"file": ("test.mp4", b"fake mp4 content", "video/mp4")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["filename"].endswith("test.mp4")
        assert data["size"] > 0
        assert Path(data["path"]).exists()

    def test_reject_invalid_format(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr("app.api.videos.UPLOADS_DIR", tmp_path)
        resp = client.post(
            "/api/videos/upload",
            files={"file": ("test.txt", b"not a video", "text/plain")},
        )
        assert resp.status_code == 400
        assert "不支持" in resp.json()["detail"]


# ---------- extract(场景 A)----------

class TestExtract:
    def test_missing_source_video(self, client, tmp_path, monkeypatch):
        # 路径穿越防护:UPLOADS_DIR 指向 tmp_path,视频放里面但故意不存在
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr("app.api.videos.UPLOADS_DIR", uploads)
        resp = client.post("/api/videos/extract", json={
            "content_id": 1,
            "source_video_path": str(uploads / "nope.mp4"),
        })
        assert resp.status_code == 400
        assert "源视频不存在" in resp.json()["detail"]

    def test_path_traversal_rejected(self, client, tmp_path, monkeypatch):
        """路径穿越防护:不在 UPLOADS_DIR 下的路径被拒。"""
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr("app.api.videos.UPLOADS_DIR", uploads)
        # 故意传 UPLOADS_DIR 外的路径
        evil = tmp_path / "evil.mp4"
        evil.write_bytes(b"x")
        resp = client.post("/api/videos/extract", json={
            "content_id": 1,
            "source_video_path": str(evil),
        })
        assert resp.status_code == 400
        assert "路径非法" in resp.json()["detail"]

    def test_submit_returns_task_id(self, client, tmp_path, monkeypatch):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        monkeypatch.setattr("app.api.videos.UPLOADS_DIR", uploads)
        # 造假视频文件(放 UPLOADS_DIR 内,通过路径校验)
        video = uploads / "src.mp4"
        video.write_bytes(b"fake")
        with patch("app.api.videos.submit", return_value=42) as mock_submit:
            resp = client.post("/api/videos/extract", json={
                "content_id": 1,
                "source_video_path": str(video),
                "target_duration": 30,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == 42
        assert data["content_id"] == 1
        # submit 被调,参数含 content_id 和 source_video_path(resolve 后的绝对路径)
        kwargs = mock_submit.call_args[1]
        assert kwargs["content_id"] == 1
        assert kwargs["source_video_path"] == str(video.resolve())
        assert kwargs["target_duration"] == 30


# ---------- generate(场景 B)----------

class TestGenerate:
    def test_content_not_found(self, client):
        with patch("app.api.videos.submit", return_value=1):
            resp = client.post("/api/videos/generate", json={"content_id": 99999})
        assert resp.status_code == 404

    def test_no_video_script_rejected(self, client, db_session):
        """Content 无 video_script 拒绝。"""
        from app.models.content import Content, ContentStatus
        from app.models.account import Account, Platform

        acc = Account(platform=Platform.XHS, nickname="n", topic_theme="t")
        db_session.add(acc); db_session.flush()
        content = Content(account_id=acc.id, status=ContentStatus.APPROVED,
                          title="t", body="b", tags=[], video_script=[])
        db_session.add(content); db_session.commit()

        resp = client.post("/api/videos/generate", json={"content_id": content.id})
        assert resp.status_code == 400
        assert "video_script" in resp.json()["detail"]

    def test_submit_returns_task_id(self, client, content_with_script):
        with patch("app.api.videos.submit", return_value=7) as mock_submit:
            resp = client.post("/api/videos/generate", json={
                "content_id": content_with_script.id,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == 7
        kwargs = mock_submit.call_args[1]
        assert kwargs["content_id"] == content_with_script.id


# ---------- 状态查询 ----------

class TestVideoStatus:
    def test_get_status(self, client, content_with_script):
        resp = client.get(f"/api/videos/{content_with_script.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content_id"] == content_with_script.id
        assert data["status"] == "approved"

    def test_not_found(self, client):
        resp = client.get("/api/videos/99999")
        assert resp.status_code == 404
