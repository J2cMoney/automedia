"""编排 API + 统计 API 单测 - Phase 6。

覆盖:
    - POST /api/orchestrator/daily 前置校验(账号不存在/登录态无效/无主题)
    - POST /api/orchestrator/daily 成功启动(mock orchestrator.run_daily_pipeline)
    - GET /api/orchestrator/batches/{id} 查状态
    - GET /api/orchestrator/pending 待发布列表
    - GET /api/stats 聚合统计
    - GET /api/tasks 任务列表
    - GET /api/config 配置(无密钥泄漏)

风格对齐 test_topics_api:同步 TestClient + mock。
"""
import pytest
from fastapi.testclient import TestClient

from app.models.account import AuthState, Platform
from app.models.comment import Comment, CommentStatus
from app.models.content import Content, ContentStatus


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前清业务表。"""
    from app.db import SyncSessionLocal
    from app.models.account import Account
    with SyncSessionLocal() as s:
        s.query(Comment).delete()
        s.query(Content).delete()
        s.query(Account).delete()
        s.commit()
    yield


def _create_account(client, platform="xhs", theme="AI 编程", auth_status="valid",
                    nickname=None):
    r = client.post("/api/accounts", json={
        "platform": platform,
        "nickname": nickname or f"测试-{platform}",
        "topic_theme": theme,
    })
    assert r.status_code == 201, r.text
    acc = r.json()
    # 手动设 auth_status(valid 默认是 unknown,需更新)
    from app.db import SyncSessionLocal
    from app.models.account import Account
    with SyncSessionLocal() as s:
        a = s.get(Account, acc["id"])
        a.auth_status = AuthState(auth_status)
        s.commit()
    return acc


# ---------- 编排 API ----------

class TestDailyStart:
    def test_account_not_found(self, client):
        resp = client.post("/api/orchestrator/daily", json={"account_ids": [99999]})
        assert resp.status_code == 404

    def test_invalid_auth_status(self, client):
        acc = _create_account(client, auth_status="invalid")
        resp = client.post("/api/orchestrator/daily", json={"account_ids": [acc["id"]]})
        assert resp.status_code == 400
        assert "登录态无效" in resp.json()["detail"]

    def test_no_theme(self, client):
        acc = _create_account(client, theme="", auth_status="valid")
        # theme 空,但 auth_status 设成 valid 了
        resp = client.post("/api/orchestrator/daily", json={"account_ids": [acc["id"]]})
        assert resp.status_code == 400
        assert "主题" in resp.json()["detail"]

    def test_start_success_returns_batch_id(self, client, monkeypatch):
        """校验通过 → 调 orchestrator.run_daily_pipeline → 返回 batch_id。"""
        acc = _create_account(client, auth_status="valid")

        async def fake_run(account_ids, **kw):
            return "mock-batch-123"

        monkeypatch.setattr("app.api.orchestrator.orch.run_daily_pipeline", fake_run)
        resp = client.post("/api/orchestrator/daily", json={"account_ids": [acc["id"]]})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["batch_id"] == "mock-batch-123"
        assert data["account_ids"] == [acc["id"]]

    def test_empty_account_ids_rejected(self, client):
        resp = client.post("/api/orchestrator/daily", json={"account_ids": []})
        assert resp.status_code == 422  # pydantic min_length=1


class TestBatchStatus:
    def test_not_found(self, client):
        resp = client.get("/api/orchestrator/batches/nonexistent")
        assert resp.status_code == 404

    def test_returns_status(self, client, monkeypatch):
        from app.services import orchestrator as orch
        monkeypatch.setattr(orch, "get_batch_status", lambda bid: {
            "batch_id": bid, "status": "running", "started_at": "t",
            "finished_at": None, "account_ids": [1],
            "results": {}, "summary": {"total": 1, "pending_publish": 0, "failed": 0, "running": True},
        })
        resp = client.get("/api/orchestrator/batches/b1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"


class TestPendingContents:
    def test_lists_approved(self, client):
        """待发布列表只含 approved 态。"""
        from app.db import SyncSessionLocal
        from app.models.account import Account
        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.XHS, nickname="x", topic_theme="t")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            s.add(Content(account_id=acc.id, title="待发", status=ContentStatus.APPROVED,
                          video_path="/tmp/1.mp4"))
            s.add(Content(account_id=acc.id, title="已发", status=ContentStatus.PUBLISHED))
            s.commit()

        resp = client.get("/api/orchestrator/pending")
        assert resp.status_code == 200
        titles = [p["title"] for p in resp.json()]
        assert "待发" in titles
        assert "已发" not in titles


# ---------- 统计 API ----------

class TestStats:
    def test_empty_stats(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["contents_total"] == 0
        assert data["published"] == 0
        assert data["replied_rate"] == 0.0

    def test_aggregation(self, client):
        """聚合计数正确。"""
        from app.db import SyncSessionLocal
        from app.models.account import Account
        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.XHS, nickname="x", topic_theme="t")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            # 2 已发布 + 1 待发布 + 1 失败
            for _ in range(2):
                s.add(Content(account_id=acc.id, status=ContentStatus.PUBLISHED))
            s.add(Content(account_id=acc.id, status=ContentStatus.APPROVED))
            s.add(Content(account_id=acc.id, status=ContentStatus.FAILED))
            # 评论:2 已回 + 1 待回
            c1 = Content(account_id=acc.id, status=ContentStatus.PUBLISHED)
            s.add(c1)
            s.commit()
            s.refresh(c1)
            s.add(Comment(content_id=c1.id, text="赞", status=CommentStatus.REPLIED))
            s.add(Comment(content_id=c1.id, text="问", status=CommentStatus.REPLIED))
            s.add(Comment(content_id=c1.id, text="喷", status=CommentStatus.PENDING))
            s.commit()

        resp = client.get("/api/stats")
        data = resp.json()
        assert data["published"] == 3  # 2 个 + c1
        assert data["pending_publish"] == 1
        assert data["failed"] == 1
        assert data["comments_total"] == 3
        assert data["replied"] == 2
        # replied_rate = 2/3
        assert abs(data["replied_rate"] - 2 / 3) < 0.01


class TestTasksList:
    def test_list_returns_array(self, client):
        resp = client.get("/api/tasks?limit=5")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_filter_by_flow_type(self, client):
        from app.db import SyncSessionLocal
        from app.models.task_run import TaskRun, TaskStatus
        with SyncSessionLocal() as s:
            s.add(TaskRun(flow_type="hotspot", status=TaskStatus.FINISHED))
            s.add(TaskRun(flow_type="copy", status=TaskStatus.FINISHED))
            s.commit()

        resp = client.get("/api/tasks?flow_type=hotspot")
        assert resp.status_code == 200
        for t in resp.json():
            assert t["flow_type"] == "hotspot"


class TestConfig:
    def test_config_no_secret_leak(self, client):
        """配置端点绝不返回密钥。"""
        resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.text
        # 密钥字段不应出现在响应里
        assert "API_KEY" not in body.upper()
        assert "ENCRYPT_KEY" not in body.upper()
        data = resp.json()
        assert "deepseek_model" in data
        assert "max_browser_concurrency" in data
        assert "publish_interval_minutes" in data
