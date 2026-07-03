"""评论 API 单测 - Phase 5 FLOW-5。

覆盖重点:
    - GET /api/comments:列表筛选
    - POST /api/comments/{content_id}/reply:回评入队(校验/视频号拒绝)

照 test_publish_api.py 范式:TestClient + patch submit + 真实 DB 造数据。
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _make_published_content(platform="xhs"):
    """造一条已发布的 content(有 platform_post_url),返回 id。"""
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
            account_id=acc.id, title="标题", body="正文",
            tags=[], video_path="/tmp/v.mp4",
            status=ContentStatus.PUBLISHED,
            platform_post_url="https://example.com/post/123",
        )
        s.add(content)
        s.commit()
        s.refresh(content)
        return content.id


def _make_comment(content_id, text="评论内容", status="pending"):
    from app.db import SyncSessionLocal
    from app.models.comment import Comment, CommentStatus

    with SyncSessionLocal() as s:
        c = Comment(
            content_id=content_id, text=text,
            status=getattr(CommentStatus, status.upper()),
        )
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.id


class TestCommentApi:
    def test_list_comments_by_content(self, client):
        """按 content_id 筛选评论。"""
        cid = _make_published_content()
        _make_comment(cid, "第一条")
        _make_comment(cid, "第二条")
        r = client.get(f"/api/comments?content_id={cid}")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert data[0]["text"] in ("第一条", "第二条")

    def test_list_comments_empty(self, client):
        """无评论返回空列表。"""
        r = client.get("/api/comments")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_trigger_reply_submits_task(self, client):
        """已发布 content 触发回评,返回 task_id。"""
        cid = _make_published_content("xhs")
        with patch("app.api.comments.submit", return_value=88) as mock_submit:
            r = client.post(f"/api/comments/{cid}/reply", json={"max_replies": 3})
        assert r.status_code == 200
        assert r.json()["task_id"] == 88
        args, kwargs = mock_submit.call_args
        assert kwargs["content_id"] == cid
        assert kwargs["max_replies"] == 3

    def test_trigger_reply_unpublished_rejected(self, client):
        """未发布的 content(无 platform_post_url)拒绝回评。"""
        from app.db import SyncSessionLocal
        from app.models.account import Account, AuthState, Platform, AccountStatus
        from app.models.content import Content, ContentStatus

        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.XHS, nickname="x", topic_theme="",
                          auth_state="c", auth_status=AuthState.VALID,
                          status=AccountStatus.ACTIVE)
            s.add(acc); s.commit(); s.refresh(acc)
            c = Content(account_id=acc.id, title="t", body="", tags=[],
                        video_path="/tmp/v.mp4", status=ContentStatus.APPROVED)
            s.add(c); s.commit(); s.refresh(c)
            cid = c.id

        with patch("app.api.comments.submit") as mock_submit:
            r = client.post(f"/api/comments/{cid}/reply", json={})
        assert r.status_code == 400
        assert "未发布" in r.json()["detail"]
        mock_submit.assert_not_called()

    def test_trigger_reply_wechat_rejected(self, client):
        """视频号拒绝自动回评。"""
        cid = _make_published_content("wx")
        with patch("app.api.comments.submit") as mock_submit:
            r = client.post(f"/api/comments/{cid}/reply", json={})
        assert r.status_code == 400
        assert "视频号" in r.json()["detail"]
        mock_submit.assert_not_called()

    def test_trigger_reply_content_not_found(self, client):
        """不存在的 content 返回 404。"""
        r = client.post("/api/comments/99999/reply", json={})
        assert r.status_code == 404
