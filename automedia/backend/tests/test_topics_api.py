"""选题 + 内容 API 单测 - Phase 3。

风格对齐 Phase 2 test_accounts_api:用同步 TestClient,不引 async 配置复杂度。

覆盖重点:
    - /api/topics GET 列表/状态筛选
    - /api/topics/{id}/adopt|discard 状态流转
    - /api/topics/{id}/generate 文案生成(mock copywriter)
    - /api/contents GET 列表

热点爬取异步任务的真实执行靠 Phase 验收手动跑(需 worker + Redis + 真账号)。
"""
import pytest
from fastapi.testclient import TestClient

from app.models.account import Platform
from app.models.topic import Topic, TopicStatus


@pytest.fixture(scope="module")
def client():
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


# ---------- 造数据 ----------

def _create_account(client, platform="xhs", theme="AI 编程"):
    r = client.post("/api/accounts", json={
        "platform": platform, "nickname": f"测试-{platform}", "topic_theme": theme
    })
    assert r.status_code == 201, r.text
    return r.json()


def _create_topic(db_session, platform=Platform.XHS, title="AI 编程新框架",
                  match_score=0.8, status=TopicStatus.CANDIDATE):
    t = Topic(
        source_platform=platform,
        title=title,
        heat_score=50.0,
        match_score=match_score,
        matched_account_ids=[1],
        status=status,
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


# ---------- 选题 CRUD ----------

class TestTopicsApi:
    def test_list_and_filter(self, client, db_session):
        """列表 + 状态筛选。"""
        _create_topic(db_session, title="候选1")
        _create_topic(db_session, title="候选2")
        _create_topic(db_session, title="已采纳", status=TopicStatus.ADOPTED)

        # 全部
        resp = client.get("/api/topics")
        assert resp.status_code == 200
        assert len(resp.json()) >= 3

        # 按状态筛
        resp = client.get("/api/topics?status=adopted")
        data = resp.json()
        assert len(data) >= 1
        assert all(t["status"] == "adopted" for t in data)

    def test_adopt_topic(self, client, db_session):
        t = _create_topic(db_session, title="采纳测试")
        resp = client.post(f"/api/topics/{t.id}/adopt")
        assert resp.status_code == 200
        assert resp.json()["status"] == "adopted"

    def test_adopt_wrong_status_fails(self, client, db_session):
        t = _create_topic(db_session, title="已采纳", status=TopicStatus.ADOPTED)
        resp = client.post(f"/api/topics/{t.id}/adopt")
        assert resp.status_code == 400

    def test_discard_topic(self, client, db_session):
        t = _create_topic(db_session, title="弃用测试")
        resp = client.post(f"/api/topics/{t.id}/discard")
        assert resp.status_code == 200
        assert resp.json()["status"] == "discarded"

    def test_get_not_found(self, client):
        resp = client.get("/api/topics/99999")
        assert resp.status_code == 404


# ---------- 文案生成 ----------

class TestGenerateApi:
    def test_generate_success(self, client, db_session, monkeypatch):
        """选题采纳后生成文案+脚本(mock copywriter)。"""
        acc = _create_account(client, theme="AI 编程")
        t = _create_topic(db_session, title="AI 框架对比")

        from app.services import copywriter as cw
        monkeypatch.setattr(
            cw, "generate_copy",
            lambda title, theme, platform, **kw: cw.CopyResult(
                title="mock 标题", body="mock 正文", tags=["#mock"]
            )
        )
        monkeypatch.setattr(
            cw, "generate_script",
            lambda title, theme, body, **kw: cw.ScriptResult(
                scenes=[cw.ScriptScene(index=1, narration="口播", visual="画面", duration=3)]
            )
        )

        resp = client.post(f"/api/topics/{t.id}/generate", json={
            "account_id": acc["id"], "scene_count": 6
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "pending_review"

        # 验证 content 入库
        cresp = client.get("/api/contents")
        contents = cresp.json()
        match = [c for c in contents if c["title"] == "mock 标题"]
        assert match
        assert match[0]["tags"] == ["#mock"]
        assert len(match[0]["video_script"]) == 1

    def test_generate_no_theme_fails(self, client, db_session):
        """账号没配主题,生成失败。"""
        acc = _create_account(client, theme="")
        t = _create_topic(db_session, title="无主题测试")

        resp = client.post(f"/api/topics/{t.id}/generate", json={
            "account_id": acc["id"]
        })
        assert resp.status_code == 400

    def test_generate_copywriter_fails_marks_failed(self, client, db_session, monkeypatch):
        """文案生成失败 -> Content 标 FAILED(Spec 5.3 兜底)。"""
        acc = _create_account(client, theme="AI")
        t = _create_topic(db_session, title="失败测试")

        from app.services import copywriter as cw
        def boom(*a, **kw):
            raise cw.CopywriterError("模拟失败")
        monkeypatch.setattr(cw, "generate_copy", boom)

        resp = client.post(f"/api/topics/{t.id}/generate", json={
            "account_id": acc["id"]
        })
        assert resp.status_code == 500

        # Content 记录保留且标 FAILED
        cresp = client.get("/api/contents?status=failed")
        contents = cresp.json()
        match = [c for c in contents if c.get("error_log")]
        assert match
        assert match[0]["status"] == "failed"


# ---------- 热点爬取提交 ----------

class TestCrawlSubmit:
    def test_crawl_submit(self, client, monkeypatch):
        """提交热点爬取任务(返回 task_id,不实际执行)。"""
        acc = _create_account(client)
        monkeypatch.setattr("app.api.topics.submit_task", lambda flow, actor, **kw: 12345)
        resp = client.post("/api/topics/crawl", json={"account_id": acc["id"]})
        assert resp.status_code == 200
        assert resp.json()["task_id"] == 12345

    def test_crawl_account_not_found(self, client):
        resp = client.post("/api/topics/crawl", json={"account_id": 99999})
        assert resp.status_code == 404


# ---------- 内容列表 ----------

class TestContentsApi:
    def test_list_works(self, client):
        resp = client.get("/api/contents")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
