"""编排器单测 - Phase 6 orchestrator.py。

覆盖重点(Spec FLOW-8 + A-8):
    - 单账号跑完 热点→文案→视频成片 → 停(pending_publish,A-8 铁律:绝不自动发布)
    - 单环节失败 → 标 failed,不继续
    - 多账号失败隔离:一个账号失败不影响其他
    - get_batch_status 摘要正确
    - _pick_best_topic 按 match_score 选最佳

注入 mock submit_and_poll(不真 submit,不依赖 worker/Redis)。
"""
import asyncio
import json

import pytest


@pytest.fixture
def db_session():
    """同步 DB session(造测试数据)。每个测试独立 session,测完关闭。"""
    from app.db import SyncSessionLocal
    s = SyncSessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前清业务表,避免数据累积干扰编排器查询。"""
    from app.db import SyncSessionLocal
    from app.models.account import Account
    from app.models.content import Content
    from app.models.topic import Topic
    with SyncSessionLocal() as s:
        s.query(Content).delete()
        s.query(Topic).delete()
        s.query(Account).delete()
        s.commit()
    yield


def _make_account(db_session, platform="xhs", theme="AI 编程", nickname="测试号"):
    """造测试账号。"""
    from app.models import Account, Platform
    acc = Account(platform=getattr(Platform, {"xhs": "XHS", "dy": "DOUYIN",
                                              "ks": "KUAISHOU", "wx": "WECHAT"}[platform]),
                  nickname=nickname, topic_theme=theme)
    db_session.add(acc)
    db_session.commit()
    db_session.refresh(acc)
    return acc


def _make_topic(db_session, platform, title, match_score=0.8, account_id=1):
    from app.models.topic import Topic, TopicStatus
    t = Topic(
        source_platform=platform, title=title, heat_score=50.0,
        match_score=match_score, matched_account_ids=[account_id],
        status=TopicStatus.CANDIDATE,
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


class TestRunAccountPipeline:
    """_run_account_pipeline 单账号全链路(注入 mock submit_poll)。"""

    def test_full_pipeline_stops_at_pending_publish(self, db_session):
        """完整链路:热点→文案→视频成片 → 停在 pending_publish(A-8)。"""
        from app.services.orchestrator import _run_account_pipeline
        from app.models.content import Content, ContentStatus

        acc = _make_account(db_session, theme="AI")
        # 热点采集后预置候选 topic(编排器 _pick_best_topic 会选它)
        topic = _make_topic(db_session, acc.platform, "AI 框架", match_score=0.9, account_id=acc.id)

        # hotspot 走 crawl_fn(mock,空操作),copy/video 走 submit_poll(mock)
        call_log = []

        async def fake_crawl(**kw):
            call_log.append("hotspot")

        async def fake_submit_poll(flow, actor, **kw):
            call_log.append(flow)
            if flow == "copy":
                c = Content(account_id=acc.id, topic_id=topic.id, title="mock",
                            body="b", tags=["#t"], video_script=[{"index": 1, "narration": "n", "visual": "v", "duration": 5}],
                            status=ContentStatus.PENDING_REVIEW)
                db_session.add(c); db_session.commit(); db_session.refresh(c)
                return 102, {"status": "finished",
                             "result": json.dumps({"content_id": c.id, "title": "mock"})}
            if flow == "video":
                cid = kw.get("content_id")
                c = db_session.get(Content, cid)
                c.video_path = "/tmp/out.mp4"; db_session.commit()
                return 103, {"status": "finished", "result": '{"video_path": "/tmp/out.mp4"}'}
            raise AssertionError(f"意外 flow: {flow}")

        async def run():
            return await _run_account_pipeline(
                acc.id, exclude_words=[], scene_count=6,
                submit_poll=fake_submit_poll, crawl_fn=fake_crawl,
                poll_interval=0.01,
            )

        result = asyncio.run(run())

        # 三个环节都跑了(hotspot 来自 crawl_fn)
        assert call_log == ["hotspot", "copy", "video"]
        # 停在 pending_publish,绝不自动发布(A-8 铁律)
        assert result["status"] == "pending_publish"
        assert result["content_id"] > 0
        c = db_session.get(Content, result["content_id"])
        assert c.status == ContentStatus.APPROVED
        assert c.video_path == "/tmp/out.mp4"

    def test_hotspot_failure_marks_failed(self, db_session):
        """热点环节失败 → 标 failed,不继续后续环节。"""
        from app.services.orchestrator import _run_account_pipeline
        acc = _make_account(db_session, nickname="失败号")

        async def fake_crawl(**kw):
            raise RuntimeError("Playwright 启动失败")

        async def fake_submit_poll(flow, actor, **kw):
            raise AssertionError("hotspot 失败后不应调到后续环节")

        async def run():
            return await _run_account_pipeline(
                acc.id, submit_poll=fake_submit_poll, crawl_fn=fake_crawl, poll_interval=0.01,
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"
        assert result["step"] == "hotspot"
        assert "Playwright 启动失败" in result["error"]

    def test_copy_failure_marks_failed(self, db_session):
        """文案环节失败 → 标 failed。"""
        from app.services.orchestrator import _run_account_pipeline
        acc = _make_account(db_session, nickname="文案失败")
        _make_topic(db_session, acc.platform, "选题", match_score=0.9, account_id=acc.id)

        async def fake_crawl(**kw):
            pass  # 热点成功

        async def fake_submit_poll(flow, actor, **kw):
            if flow == "copy":
                raise RuntimeError("DeepSeek 挂了")
            raise AssertionError(f"copy 失败后不应调到 {flow}")

        async def run():
            return await _run_account_pipeline(
                acc.id, submit_poll=fake_submit_poll, crawl_fn=fake_crawl, poll_interval=0.01,
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"
        assert result["step"] == "copy"

    def test_no_candidate_topic_fails(self, db_session):
        """热点跑完但无候选 topic → 失败。"""
        from app.services.orchestrator import _run_account_pipeline
        acc = _make_account(db_session, nickname="无选题")
        # 不建 topic,_pick_best_topic 返回 None

        async def fake_crawl(**kw):
            pass  # 热点"成功"但没入库任何 topic

        async def fake_submit_poll(flow, actor, **kw):
            raise AssertionError("无选题不应调到后续环节")

        async def run():
            return await _run_account_pipeline(
                acc.id, submit_poll=fake_submit_poll, crawl_fn=fake_crawl, poll_interval=0.01,
            )

        result = asyncio.run(run())
        assert result["status"] == "failed"
        assert "无候选选题" in result["error"]


class TestPickBestTopic:
    """_pick_best_topic 按 match_score 选最佳。"""

    def test_picks_highest_match_score(self, db_session):
        from app.services.orchestrator import _pick_best_topic
        from app.models import Platform
        acc = _make_account(db_session, platform="xhs")

        _make_topic(db_session, acc.platform, "低分", match_score=0.3, account_id=acc.id)
        t_high = _make_topic(db_session, acc.platform, "高分", match_score=0.95, account_id=acc.id)
        _make_topic(db_session, acc.platform, "中分", match_score=0.6, account_id=acc.id)

        best = _pick_best_topic(acc.id)
        assert best == t_high.id

    def test_no_candidate_returns_none(self, db_session):
        from app.services.orchestrator import _pick_best_topic
        acc = _make_account(db_session, platform="dy", nickname="空")
        assert _pick_best_topic(acc.id) is None


class TestBatchStatus:
    """get_batch_status 摘要。"""

    def test_batch_not_found(self):
        from app.services.orchestrator import get_batch_status
        assert get_batch_status("nonexistent") is None

    def test_batch_summary_counts(self):
        """摘要正确统计 pending_publish / failed / running。"""
        from app.services import orchestrator as orch

        batch_id = "test-batch-1"
        orch._running_batches[batch_id] = {
            "account_ids": [1, 2, 3],
            "started_at": "2026-07-01T00:00:00",
            "status": "running",
            "results": {
                1: {"account_id": 1, "status": "pending_publish"},
                2: {"account_id": 2, "status": "failed"},
                # 3 还在跑
            },
        }
        try:
            st = orch.get_batch_status(batch_id)
            assert st["summary"]["total"] == 3
            assert st["summary"]["pending_publish"] == 1
            assert st["summary"]["failed"] == 1
            assert st["summary"]["running"] is True
        finally:
            orch._running_batches.pop(batch_id, None)


class TestDriveBatchIsolation:
    """多账号失败隔离集成测试(Spec FLOW-8:单账号失败不阻塞其他)。

    审查 M3 补充:验证 _drive_batch 真跑多账号时,一个账号失败不影响其他账号
    和整个批次完成。注入 mock _run_account_pipeline 控制成功/失败。
    """

    def test_one_failure_does_not_block_others(self, monkeypatch):
        """3 个账号:1 个失败,2 个成功 → 批次 finished,3 个都有结果。"""
        from app.services import orchestrator as orch
        from app.services.scheduler import BrowserSemaphore

        async def fake_run(account_id, **kw):
            if account_id == 2:
                return {
                    "account_id": account_id, "status": "failed",
                    "step": "hotspot", "error": "模拟失败", "content_id": None,
                }
            return {
                "account_id": account_id, "status": "pending_publish",
                "step": "done", "content_id": 100 + account_id, "error": None,
            }

        monkeypatch.setattr(orch, "_run_account_pipeline", fake_run)

        sem = BrowserSemaphore(max_concurrency=3)
        batch_id = "iso-test-1"
        orch._running_batches[batch_id] = {
            "account_ids": [1, 2, 3],
            "started_at": "t",
            "status": "running",
            "results": {},
        }

        async def run():
            await orch._drive_batch(
                batch_id, [1, 2, 3], None, 20, 6, False, 0.01, None, sem,
            )

        asyncio.run(run())

        st = orch.get_batch_status(batch_id)
        assert st["status"] == "finished"  # 批次整体完成
        assert st["summary"]["pending_publish"] == 2  # 账号 1, 3 成功
        assert st["summary"]["failed"] == 1  # 账号 2 失败
        # 三个账号都有结果,没被失败账号阻塞
        assert len(st["results"]) == 3
        assert st["results"][1]["status"] == "pending_publish"
        assert st["results"][2]["status"] == "failed"
        assert st["results"][3]["status"] == "pending_publish"

    def test_all_fail_still_finishes_batch(self, monkeypatch):
        """全部失败批次也 finished(失败隔离,不卡死)。"""
        from app.services import orchestrator as orch
        from app.services.scheduler import BrowserSemaphore

        async def fake_run(account_id, **kw):
            return {
                "account_id": account_id, "status": "failed",
                "step": "video", "error": "全挂", "content_id": None,
            }

        monkeypatch.setattr(orch, "_run_account_pipeline", fake_run)

        sem = BrowserSemaphore(max_concurrency=2)
        batch_id = "iso-test-2"
        orch._running_batches[batch_id] = {
            "account_ids": [10, 11],
            "started_at": "t",
            "status": "running",
            "results": {},
        }

        async def run():
            await orch._drive_batch(
                batch_id, [10, 11], None, 20, 6, False, 0.01, None, sem,
            )

        asyncio.run(run())

        st = orch.get_batch_status(batch_id)
        assert st["status"] == "finished"
        assert st["summary"]["failed"] == 2
        assert st["summary"]["pending_publish"] == 0


class TestListPendingPublish:
    """list_pending_publish_contents 列待发布。"""

    def test_lists_approved_contents(self, db_session):
        from app.services.orchestrator import list_pending_publish_contents
        from app.models.content import Content, ContentStatus
        acc = _make_account(db_session)

        c1 = Content(account_id=acc.id, title="待发1", status=ContentStatus.APPROVED,
                     video_path="/tmp/1.mp4")
        c2 = Content(account_id=acc.id, title="已发", status=ContentStatus.PUBLISHED)
        c3 = Content(account_id=acc.id, title="待发2", status=ContentStatus.APPROVED,
                     video_path="/tmp/2.mp4")
        for c in (c1, c2, c3):
            db_session.add(c)
        db_session.commit()

        result = list_pending_publish_contents()
        titles = [r["title"] for r in result]
        assert "待发1" in titles
        assert "待发2" in titles
        assert "已发" not in titles  # published 不在内
