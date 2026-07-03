"""发布 API 单测 - Phase 5 FLOW-4(v1.6 人机协同辅助发布)。

覆盖重点:
    - POST /api/publish/{content_id}:启动辅助发布(返回 token,线程池跑)
    - GET /api/publish/assist/{token}/status:轮询状态
    - POST /api/publish/wx/{content_id}/package:视频号打包(字段齐全)
    - 校验:无视频/视频号拒绝自动发布/不存在 404

辅助发布走线程池(不走 Dramatiq),patch assist_publish 避免真开浏览器。
"""
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _make_content(platform="xhs", video_path="/tmp/v.mp4", status="approved"):
    """造一条 Content 进库,返回 id。"""
    from app.db import SyncSessionLocal
    from app.models.account import Account, AuthState, Platform, AccountStatus
    from app.models.content import Content, ContentStatus

    with SyncSessionLocal() as s:
        acc = Account(
            platform=Platform(platform), nickname="测试号",
            topic_theme="AI", auth_state="cipher", auth_status=AuthState.VALID,
            status=AccountStatus.ACTIVE,
        )
        s.add(acc)
        s.commit()
        s.refresh(acc)
        content = Content(
            account_id=acc.id, title="测试标题", body="正文",
            tags=["#A"], video_path=video_path,
            status=getattr(ContentStatus, status.upper()),
        )
        s.add(content)
        s.commit()
        s.refresh(content)
        return content.id


class TestPublishApi:
    def test_assist_publish_returns_token(self, client):
        """小红书 content 启动辅助发布,返回 token(v1.6 新架构)。"""
        cid = _make_content("xhs")
        # mock 线程池里真正执行的函数,避免真开浏览器
        with patch("app.api.publish._run_assist_in_thread") as mock_run:
            r = client.post(f"/api/publish/{cid}", json={"timeout_minutes": 5})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert len(data["token"]) > 0
        assert data["content_id"] == cid
        # 线程函数被调度(传了 content_id)
        args = mock_run.call_args.args
        assert args[2] == cid  # content_id(参数顺序:token, account_id, content_id, ...)

    def test_assist_status_query(self, client):
        """GET status 能查到任务状态。"""
        cid = _make_content("xhs")
        with patch("app.api.publish._run_assist_in_thread"):
            r1 = client.post(f"/api/publish/{cid}", json={})
            token = r1.json()["token"]
        # 手动设个状态(模拟线程跑完)
        from app.api.publish import _assist_tasks
        _assist_tasks[token]["status"] = "success"
        _assist_tasks[token]["post_url"] = "https://xhs.com/note/abc"
        r2 = client.get(f"/api/publish/assist/{token}/status")
        assert r2.status_code == 200
        assert r2.json()["status"] == "success"
        assert r2.json()["post_url"] == "https://xhs.com/note/abc"

    def test_assist_status_not_found(self, client):
        """不存在的 token 返回 404。"""
        r = client.get("/api/publish/assist/nonexistent/status")
        assert r.status_code == 404

    def test_publish_no_video_path_rejected(self, client):
        """无视频文件的 content 拒绝发布。"""
        cid = _make_content("xhs", video_path="")
        r = client.post(f"/api/publish/{cid}", json={})
        assert r.status_code == 400
        assert "视频文件" in r.json()["detail"]

    def test_publish_wechat_rejected(self, client):
        """视频号拒绝辅助发布,引导走打包。"""
        cid = _make_content("wx")
        r = client.post(f"/api/publish/{cid}", json={})
        assert r.status_code == 400
        assert "package" in r.json()["detail"]

    def test_publish_content_not_found(self, client):
        """不存在的 content 返回 404。"""
        r = client.post("/api/publish/99999", json={})
        assert r.status_code == 404

    def test_wx_package_returns_fields(self, client):
        """视频号打包返回完整字段(对照 CMP-007)。"""
        cid = _make_content("wx", video_path="/tmp/wx.mp4")
        r = client.post(f"/api/publish/wx/{cid}/package")
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "测试标题"
        assert data["body"] == "正文"
        assert data["tags"] == ["#A"]
        assert data["video_path"] == "/tmp/wx.mp4"
        assert "测试标题" in data["copy_text"]
        assert data["channels_url"].startswith("https://")

    def test_wx_package_no_video_rejected(self, client):
        """无视频文件拒绝打包。"""
        cid = _make_content("wx", video_path="")
        r = client.post(f"/api/publish/wx/{cid}/package")
        assert r.status_code == 400
