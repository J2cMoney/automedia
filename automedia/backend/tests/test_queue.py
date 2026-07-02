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
