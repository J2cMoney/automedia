"""任务队列测试 - 用真 Redis(Docker),验证状态流转和重启恢复。

DEV-PLAN Phase 1 验收:
    - 提交任务能查到 pending->running->finished
    - 失败任务能重试
    - 重启后 running 状态恢复成 pending

前置:Docker Redis 跑着(docker run -d --name automedia-redis -p 6379:6379 redis:7-alpine)
"""
from datetime import datetime

import pytest


@pytest.fixture
def clean_db():
    """每个测试前清空 task_runs 表,避免互相干扰。"""
    from app.db import SyncSessionLocal
    from app.models.task_run import TaskRun
    with SyncSessionLocal() as s:
        s.query(TaskRun).delete()
        s.commit()
    yield


class TestRedisConnection:
    """Redis 连通性。"""

    def test_redis_connected(self):
        """Redis 必须连通(前置:Docker 跑着)。"""
        from app.queue import get_redis_info
        info = get_redis_info()
        assert info["connected"] is True, f"Redis 未连通: {info}"


class TestSubmitAndStatus:
    """提交任务 + 查状态。"""

    def test_submit_creates_pending_task(self, clean_db):
        """提交后状态是 pending。"""
        from app.queue import submit, status
        tid = submit("test", "sleep_task", seconds=1)
        assert tid > 0
        info = status(tid)
        assert info["status"] == "pending"
        assert info["flow_type"] == "test"
        assert info["message_id"] is not None  # Dramatiq 入队后回填

    def test_status_returns_none_for_missing(self):
        """不存在的 task_id 返回 None。"""
        from app.queue import status
        assert status(99999) is None

    def test_task_lifecycle_via_fn(self, clean_db):
        """同步调 .fn 验证 pending->running->finished 状态流转。

        真 worker 异步执行在步骤4已手动验证,这里用 .fn 做单测(确定性)。
        """
        from app.queue import submit, status, sleep_task
        tid = submit("test", "sleep_task", seconds=0)  # 0 秒,快速
        sleep_task.fn(task_id=tid, seconds=0)
        info = status(tid)
        assert info["status"] == "finished"
        assert info["result"] is not None
        assert info["started_at"] is not None
        assert info["finished_at"] is not None


class TestFailureAndRetry:
    """失败 + 重试。"""

    def test_failed_task_status(self, clean_db):
        """失败任务标 FAILED + 记错误日志。"""
        from app.queue import submit, status, fail_task
        tid = submit("test", "fail_task")
        with pytest.raises(RuntimeError):
            fail_task.fn(task_id=tid, msg="测试失败")
        info = status(tid)
        assert info["status"] == "failed"
        assert info["error_log"] is not None
        assert "测试失败" in info["error_log"]

    def test_retry_failed_task(self, clean_db):
        """重试失败任务:状态回 pending,retry_count +1。"""
        from app.queue import submit, status, fail_task, retry
        tid = submit("test", "fail_task")
        with pytest.raises(RuntimeError):
            fail_task.fn(task_id=tid)
        assert status(tid)["status"] == "failed"

        ok = retry(tid)
        assert ok is True
        info = status(tid)
        assert info["status"] == "pending"
        assert info["retry_count"] == 1

    def test_retry_non_failed_returns_false(self, clean_db):
        """重试非失败任务返回 False。"""
        from app.queue import submit, retry
        tid = submit("test", "sleep_task", seconds=1)  # pending
        assert retry(tid) is False

    def test_retry_missing_returns_false(self):
        """重试不存在的任务返回 False。"""
        from app.queue import retry
        assert retry(99999) is False


class TestRecoverOnStartup:
    """重启恢复:routing 状态转回 pending。"""

    def test_recover_running_to_pending(self, clean_db):
        """重启时 running 状态转回 pending。"""
        from app.queue import submit, status, recover_on_startup
        from app.db import SyncSessionLocal
        from app.models.task_run import TaskRun, TaskStatus

        tid = submit("test", "sleep_task", seconds=10)
        # 手动设成 running 模拟中断
        with SyncSessionLocal() as s:
            t = s.get(TaskRun, tid)
            t.status = TaskStatus.RUNNING
            t.started_at = datetime.utcnow()
            s.commit()
        assert status(tid)["status"] == "running"

        # 触发恢复
        n = recover_on_startup()
        assert n >= 1
        info = status(tid)
        assert info["status"] == "pending"
        assert info["started_at"] is None  # 清空

    def test_recover_does_not_touch_finished(self, clean_db):
        """已完成的任务不被恢复。"""
        from app.queue import submit, status, recover_on_startup, sleep_task
        tid = submit("test", "sleep_task", seconds=0)
        sleep_task.fn(task_id=tid, seconds=0)
        assert status(tid)["status"] == "finished"

        recover_on_startup()
        assert status(tid)["status"] == "finished"  # 不变


class TestGenerateCopyTask:
    """Phase 6 generate_copy_task actor 单测。

    范式:submit() 后用 .fn(task_id=...) 同步直接调底层函数(不依赖 worker),
    做确定性测试,mock copywriter 不真调 LLM。
    """

    def test_generate_copy_success(self, clean_db, monkeypatch):
        """生成成功:Content 建出 + 状态 pending_review + topic 标 adopted。"""
        from app.db import SyncSessionLocal
        from app.models import Account, Platform
        from app.models.topic import Topic, TopicStatus
        from app.queue import submit, status, generate_copy_task
        from app.services import copywriter as cw

        # 造账号 + 选题
        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.XHS, nickname="测试号", topic_theme="AI 编程")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            topic = Topic(
                source_platform=Platform.XHS, title="AI 框架对比",
                heat_score=80.0, match_score=0.9, matched_account_ids=[acc.id],
                status=TopicStatus.CANDIDATE,
            )
            s.add(topic)
            s.commit()
            s.refresh(topic)
            acc_id, topic_id = acc.id, topic.id

        # mock copywriter(不真调 LLM)
        monkeypatch.setattr(
            cw, "generate_copy",
            lambda title, theme, platform, **kw: cw.CopyResult(
                title="mock 标题", body="mock 正文", tags=["#AI", "#测试"]
            )
        )
        monkeypatch.setattr(
            cw, "generate_script",
            lambda title, theme, body, **kw: cw.ScriptResult(
                scenes=[cw.ScriptScene(index=1, narration="口播", visual="画面", duration=4)]
            )
        )

        # submit + .fn 同步执行
        tid = submit("copy", "generate_copy_task", account_id=acc_id, topic_id=topic_id,
                     run_account_id=acc_id)
        ret = generate_copy_task.fn(task_id=tid, account_id=acc_id, topic_id=topic_id)

        assert ret["content_id"] > 0
        assert ret["title"] == "mock 标题"
        assert ret["scenes"] == 1
        assert status(tid)["status"] == "finished"

        # 验证 Content 入库
        from app.models.content import Content, ContentStatus
        with SyncSessionLocal() as s:
            c = s.get(Content, ret["content_id"])
            assert c.title == "mock 标题"
            assert c.body == "mock 正文"
            assert c.tags == ["#AI", "#测试"]
            assert len(c.video_script) == 1
            assert c.status == ContentStatus.PENDING_REVIEW
            # 选题标 adopted
            t = s.get(Topic, topic_id)
            assert t.status == TopicStatus.ADOPTED

    def test_generate_copy_no_theme_fails(self, clean_db, monkeypatch):
        """账号没配主题 → Content 标 FAILED。"""
        from app.db import SyncSessionLocal
        from app.models import Account, Platform
        from app.models.topic import Topic, TopicStatus
        from app.queue import submit, status, generate_copy_task

        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.XHS, nickname="无主题号", topic_theme="")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            topic = Topic(
                source_platform=Platform.XHS, title="随便",
                heat_score=10.0, match_score=0.5, matched_account_ids=[acc.id],
                status=TopicStatus.CANDIDATE,
            )
            s.add(topic)
            s.commit()
            s.refresh(topic)
            acc_id, topic_id = acc.id, topic.id

        tid = submit("copy", "generate_copy_task", account_id=acc_id, topic_id=topic_id,
                     run_account_id=acc_id)
        with pytest.raises(RuntimeError):
            generate_copy_task.fn(task_id=tid, account_id=acc_id, topic_id=topic_id)

        assert status(tid)["status"] == "failed"
        assert "topic_theme" in status(tid)["error_log"]

    def test_generate_copy_service_fails_marks_content_failed(self, clean_db, monkeypatch):
        """copywriter 服务抛异常 → Content 保留且标 FAILED(Spec 5.3 兜底)。"""
        from app.db import SyncSessionLocal
        from app.models import Account, Platform
        from app.models.topic import Topic, TopicStatus
        from app.queue import submit, status, generate_copy_task
        from app.services import copywriter as cw

        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.DOUYIN, nickname="抖音号", topic_theme="科技")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            topic = Topic(
                source_platform=Platform.DOUYIN, title="失败测试",
                heat_score=5.0, match_score=0.3, matched_account_ids=[acc.id],
                status=TopicStatus.CANDIDATE,
            )
            s.add(topic)
            s.commit()
            s.refresh(topic)
            acc_id, topic_id = acc.id, topic.id

        def boom(*a, **kw):
            raise cw.CopywriterError("模拟 LLM 失败")
        monkeypatch.setattr(cw, "generate_copy", boom)

        tid = submit("copy", "generate_copy_task", account_id=acc_id, topic_id=topic_id,
                     run_account_id=acc_id)
        with pytest.raises(RuntimeError):
            generate_copy_task.fn(task_id=tid, account_id=acc_id, topic_id=topic_id)

        assert status(tid)["status"] == "failed"
        # Content 保留且标 FAILED
        from app.models.content import Content, ContentStatus
        with SyncSessionLocal() as s:
            c = s.query(Content).filter(Content.account_id == acc_id).one()
            assert c.status == ContentStatus.FAILED
            assert c.error_log is not None


class TestDBModels:
    """DB 模型基础(配合 DEV-PLAN 验收:CRUD 测试数据能存能取)。"""

    def test_account_crud(self, clean_db):
        """Account 表 CRUD。"""
        from app.db import SyncSessionLocal
        from app.models import Account, Platform, AccountStatus
        with SyncSessionLocal() as s:
            acc = Account(platform=Platform.DOUYIN, nickname="测试抖音号", topic_theme="搞笑")
            s.add(acc)
            s.commit()
            s.refresh(acc)
            aid = acc.id
            assert acc.platform == Platform.DOUYIN

            acc2 = s.get(Account, aid)
            assert acc2.nickname == "测试抖音号"
            assert acc2.status == AccountStatus.ACTIVE  # 默认值

            s.delete(acc2)
            s.commit()
            assert s.get(Account, aid) is None
