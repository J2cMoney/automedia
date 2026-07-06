"""调度器单测 - Phase 6 scheduler.py。

覆盖重点(Spec FLOW-8):
    - poll_task 三态:finished / failed / 超时
    - BrowserSemaphore 并发上限
    - submit_and_poll 集成(注入 mock submit + status)

失败隔离由 orchestrator._safe_run_account 负责,在 test_orchestrator.py 测。
不依赖真 Redis(轮询函数注入 mock,不真 submit)。
"""
import asyncio

import pytest


class FakeSleep:
    """记录 sleep 调用的假 sleep(不真睡,推进虚拟时间)。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, seconds: float):
        self.calls.append(seconds)


class TestPollTask:
    """poll_task 轮询三态。"""

    def test_poll_finished(self):
        from app.services.scheduler import poll_task

        async def run():
            statuses = [
                {"status": "pending"},
                {"status": "running"},
                {"status": "finished", "result": '{"ok": true}'},
            ]
            idx = {"i": 0}

            def status_fn(_tid):
                s = statuses[idx["i"]]
                idx["i"] = min(idx["i"] + 1, len(statuses) - 1)
                return s

            sleep = FakeSleep()
            info = await poll_task(1, interval=0.01, status_fn=status_fn, sleep_fn=sleep)
            assert info["status"] == "finished"
            assert info["result"] == '{"ok": true}'
            # 至少 sleep 2 次(pending→running→finished)
            assert len(sleep.calls) >= 2

        asyncio.run(run())

    def test_poll_failed_returns_dict(self):
        """poll_task 对 failed 终态返回 dict(不抛,抛是 submit_and_poll 的职责)。"""
        from app.services.scheduler import poll_task

        async def run():
            def status_fn(_tid):
                return {"status": "failed", "error_log": "模拟失败"}

            info = await poll_task(1, interval=0.01, status_fn=status_fn, sleep_fn=FakeSleep())
            assert info["status"] == "failed"
            assert info["error_log"] == "模拟失败"

        asyncio.run(run())

    def test_poll_timeout(self):
        from app.services.scheduler import poll_task

        async def run():
            def status_fn(_tid):
                return {"status": "running"}  # 永远 running

            with pytest.raises(asyncio.TimeoutError):
                await poll_task(
                    1, interval=0.01, timeout=0.05,
                    status_fn=status_fn, sleep_fn=FakeSleep(),
                )

        asyncio.run(run())

    def test_poll_missing_task_raises(self):
        from app.services.scheduler import poll_task

        async def run():
            with pytest.raises(RuntimeError, match="不存在"):
                await poll_task(999, status_fn=lambda _t: None, sleep_fn=FakeSleep())

        asyncio.run(run())


class TestBrowserSemaphore:
    """BrowserSemaphore 并发上限。"""

    def test_limits_concurrency(self):
        """同时进入的协程数不超过 max。"""
        from app.services.scheduler import BrowserSemaphore

        async def run():
            sem = BrowserSemaphore(max_concurrency=2)
            current = {"n": 0}
            peak = {"n": 0}

            async def worker():
                async with sem:
                    current["n"] += 1
                    peak["n"] = max(peak["n"], current["n"])
                    await asyncio.sleep(0.02)
                    current["n"] -= 1

            # 起 5 个,但信号量限 2
            await asyncio.gather(*[worker() for _ in range(5)])
            assert peak["n"] <= 2
            assert sem.max_concurrency == 2

        asyncio.run(run())

    def test_uses_config_default(self):
        """不传 max_concurrency 时读 config.MAX_BROWSER_CONCURRENCY。"""
        from app.services.scheduler import BrowserSemaphore
        from app.config import settings

        sem = BrowserSemaphore()
        assert sem.max_concurrency == settings.MAX_BROWSER_CONCURRENCY


class TestSubmitAndPoll:
    """submit_and_poll 集成(注入 mock submit + status)。"""

    def test_success_path(self):
        from app.services.scheduler import submit_and_poll, poll_task

        async def run():
            # mock submit 返回固定 task_id
            call_count = {"n": 0}

            def fake_submit(flow, actor, **kw):
                call_count["n"] += 1
                return 777

            # mock status:第一次 running,第二次 finished
            statuses = [{"status": "running"}, {"status": "finished", "result": "{}"}]
            idx = {"i": 0}

            def fake_status(_tid):
                s = statuses[idx["i"]]
                idx["i"] = min(idx["i"] + 1, len(statuses) - 1)
                return s

            # monkeypatch poll_task 用的 status_fn 通过 poll_kwargs 注入
            tid, final = await submit_and_poll(
                "hotspot", "crawl_hotspot_task",
                account_id=1,
                submit_fn=fake_submit,
                poll_kwargs={
                    "interval": 0.01,
                    "status_fn": fake_status,
                    "sleep_fn": FakeSleep(),
                },
            )
            assert tid == 777
            assert final["status"] == "finished"
            assert call_count["n"] == 1

        asyncio.run(run())

    def test_failed_task_raises(self):
        from app.services.scheduler import submit_and_poll

        async def run():
            def fake_submit(flow, actor, **kw):
                return 888

            def fake_status(_tid):
                return {"status": "failed", "error_log": "worker 崩了"}

            with pytest.raises(RuntimeError, match="worker 崩了"):
                await submit_and_poll(
                    "video", "generate_video_task",
                    content_id=1,
                    submit_fn=fake_submit,
                    poll_kwargs={
                        "interval": 0.01,
                        "status_fn": fake_status,
                        "sleep_fn": FakeSleep(),
                    },
                )

        asyncio.run(run())
