"""调度器 - Phase 6 FLOW-8(并发控制 + 渲染限流声明 + 任务轮询)。

Spec FLOW-8 调度策略的落地:
    - 并发控制:同时最多 N 个账号跑全链路(N=MAX_BROWSER_CONCURRENCY,每账号
      独占一个 Chrome profile)。用 asyncio.Semaphore 限制编排器同时推进的账号数。
    - 渲染限流:Remotion 渲染是 CPU 密集,实际串行由 render.py::_render_lock
      (threading.Lock)保证,同时只跑 1 条。本模块只做声明文档,不改 render.py。
    - 发布风控限速:复用 services/publish/base.py::check_publish_rate_limit
      (同账号同平台 ≥30 分钟间隔),编排器到"视频成片"就停,发布由用户触发,
      限速在 publish 流程内部生效,本模块不重复实现。
    - 失败隔离:由 orchestrator._safe_run_account 负责(每账号 try/except 写回结果),
      不抛到 asyncio.gather 外,单账号失败不阻塞其他。
    - 任务记录持久化:每个环节 submit() 都建 task_run 记录,recover_on_startup()
      在启动时把 RUNNING 转回 PENDING;任务轮询只读 status()。

任务轮询设计:
    poll_task 是 async 的(asyncio.sleep 不阻塞 FastAPI 事件循环)。
    submit() / status() 本身是同步的(操作 SQLite,毫秒级),在 async 上下文里
    用 run_in_threadpool 包装 submit 避免阻塞,status 很轻直接调。
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


class BrowserSemaphore:
    """账号级并发信号量(Spec FLOW-8:同时最多 N 个 Playwright/Chrome)。

    每个账号独占一个 Chrome profile(不串 cookie),Semaphore 限制同时跑
    全链路的账号数,防内存爆。N 由 config.MAX_BROWSER_CONCURRENCY 控制(默认 3)。

    用法:
        sem = BrowserSemaphore()
        async with sem:
            await run_account_pipeline(account_id)
    """

    def __init__(self, max_concurrency: Optional[int] = None) -> None:
        limit = max_concurrency if max_concurrency is not None else settings.MAX_BROWSER_CONCURRENCY
        self._max = max(1, limit)
        self._sem = asyncio.Semaphore(self._max)

    @property
    def max_concurrency(self) -> int:
        return self._max

    async def __aenter__(self) -> "BrowserSemaphore":
        await self._sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._sem.release()


async def poll_task(
    task_id: int,
    *,
    interval: float = 2.0,
    timeout: Optional[float] = None,
    status_fn: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
    sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Dict[str, Any]:
    """轮询任务状态直到终态(finished/failed),返回最终 status dict。

    Spec FLOW-8:编排器 submit() 子任务后,异步轮询 task_runs 状态推进到下一环。
    用 async sleep 不阻塞 FastAPI 事件循环。

    Args:
        task_id: task_runs.id
        interval: 轮询间隔秒(默认 2)
        timeout: 超时秒(None 等到终态);超时抛 asyncio.TimeoutError
        status_fn: 状态查询函数(默认 queue.status,可注入测试 mock)
        sleep_fn: sleep 函数(默认 asyncio.sleep,可注入测试 mock)

    Returns:
        最终 status dict,{status: "finished"|"failed", ...}

    Raises:
        asyncio.TimeoutError: 超时
        RuntimeError: 任务不存在(status_fn 返回 None)
        RuntimeError: 任务最终 failed(调用方决定是否视为失败)
    """
    if status_fn is None:
        from app.queue import status as _status
        status_fn = _status

    elapsed = 0.0
    while True:
        info = status_fn(task_id)
        if info is None:
            raise RuntimeError(f"任务 {task_id} 不存在")
        st = info.get("status")
        if st in ("finished", "failed"):
            return info
        # 还在 pending/running,继续等
        await sleep_fn(interval)
        elapsed += interval
        if timeout is not None and elapsed >= timeout:
            raise asyncio.TimeoutError(
                f"任务 {task_id} 轮询超时({timeout}s),当前状态={st}"
            )


async def submit_and_poll(
    flow_type: str,
    actor_name: str,
    *,
    run_account_id: Optional[int] = None,
    run_content_id: Optional[int] = None,
    submit_fn: Optional[Callable[..., int]] = None,
    poll_kwargs: Optional[Dict[str, Any]] = None,
    **actor_kwargs: Any,
) -> Tuple[int, Dict[str, Any]]:
    """提交任务 + 轮询到终态,返回 (task_id, final_status)。

    封装编排器最常见的"submit 后轮询"模式。submit 是同步 DB 操作,
    用 run_in_threadpool 包装避免阻塞事件循环。

    Args:
        flow_type / actor_name: 见 queue.submit
        run_account_id / run_content_id: 写 task_runs 关联
        submit_fn: submit 函数(默认 queue.submit,可注入测试 mock)
        poll_kwargs: 传给 poll_task 的参数(interval/timeout 等)
        **actor_kwargs: actor 业务参数

    Returns:
        (task_id, final_status_dict)

    Raises:
        RuntimeError: 任务终态 failed
        asyncio.TimeoutError: 轮询超时
    """
    from starlette.concurrency import run_in_threadpool

    if submit_fn is None:
        from app.queue import submit as _submit
        submit_fn = _submit

    # submit 是同步 DB 操作,放线程池避免阻塞
    task_id = await run_in_threadpool(
        submit_fn, flow_type, actor_name,
        run_account_id=run_account_id,
        run_content_id=run_content_id,
        **actor_kwargs,
    )
    poll_kwargs = poll_kwargs or {}
    final = await poll_task(task_id, **poll_kwargs)

    if final.get("status") == "failed":
        raise RuntimeError(
            f"任务 {flow_type}/{actor_name} (task_id={task_id}) 失败: "
            f"{final.get('error_log', '未知错误')}"
        )
    return task_id, final
