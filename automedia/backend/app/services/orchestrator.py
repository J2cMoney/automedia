"""全链路编排器 - Phase 6 FLOW-8(串联热点→文案→视频成片,到成片即停)。

架构(用户拍板方案 B:进程内协调器):
    orchestrator 作为 FastAPI 进程内的 async 协调器,逐个 submit() 子任务到
    Dramatiq,async 轮询 task_runs 状态推进到下一环。到"视频成片"就停(产出
    待发布 Content 列表),发布留给用户点按钮触发(复用 publish.py 线程池,
    Spec A-8 人机协同铁律)。

全链路(单账号串行):
    1. 热点采集(submit crawl_hotspot_task)→ 选最佳 topic(按 match_score)
    2. 文案生成(submit generate_copy_task)→ 产出 Content(pending_review)
    3. 视频成片(submit generate_video_task,场景 B 从零生成)→ Content 标 approved
    4. 【停】。返回待发布 Content,等用户触发发布
    发布成功后,用户/前端可单独触发回评(submit reply_comments_task,全自动)

关键约束:
    - A-8 铁律:编排器【绝不自动跑发布】,到视频成片就停
    - 失败隔离:单账号/单环节失败 → 记录 error,不影响其他账号
    - 并发控制:BrowserSemaphore(MAX_BROWSER_CONCURRENCY) 限同时跑全链路的账号数
    - 渲染串行:render.py::_render_lock 已保证(同时只跑 1 条渲染)
    - 任务记录持久化:每个 submit() 都建 task_run 记录,服务重启后 task_runs 表保留
      全部历史(审计源);但批次状态(_running_batches)是进程内字典,重启后丢失——
      即单个任务的 RUNNING 态会被 recover_on_startup() 转回 PENDING,
      但正在进行的编排批次不会自动续跑,需用户重新点「开始今日运营」
"""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.scheduler import BrowserSemaphore, poll_task, submit_and_poll

logger = logging.getLogger(__name__)

# 进程内批次状态(重启丢失,但 task_runs 表是真相源)
# key=batch_id, value={account_ids, started_at, status, results}
_running_batches: Dict[str, dict] = {}


def _make_batch_id() -> str:
    return uuid.uuid4().hex[:12]


async def run_daily_pipeline(
    account_ids: List[int],
    *,
    exclude_words: Optional[List[str]] = None,
    max_topics: int = 20,
    scene_count: int = 6,
    video_whisper_fallback: bool = False,
    poll_interval: float = 2.0,
    poll_timeout: Optional[float] = 1200.0,  # 单环节最多 20 分钟
    semaphore: Optional[BrowserSemaphore] = None,
) -> str:
    """启动"今日运营"全链路(后台 async 跑,立即返回 batch_id)。

    每账号串行:热点→文案→视频成片→【停】。多账号并行(受 Semaphore 限并发)。
    到视频成片就停,产出待发布 Content(状态 approved),发布由用户触发(A-8)。

    Args:
        account_ids: 要跑的账号 id 列表(已校验过 auth_status=valid + topic_theme)
        exclude_words: 热点排除词(Spec FLOW-1)
        max_topics: 热点采集上限
        scene_count: 视频脚本分镜数
        video_whisper_fallback: TTS 无字幕时是否用 Whisper 备选
        poll_interval / poll_timeout: 轮询参数
        semaphore: 并发信号量(默认新建,可注入测试)

    Returns:
        batch_id,用于查批次状态
    """
    batch_id = _make_batch_id()
    sem = semaphore or BrowserSemaphore()

    _running_batches[batch_id] = {
        "account_ids": list(account_ids),
        "started_at": datetime.utcnow().isoformat(),
        "status": "running",
        "results": {},  # account_id -> {status, content_id, error, step}
    }

    # 后台并行跑(不 await,立即返回 batch_id)
    asyncio.create_task(
        _drive_batch(
            batch_id, account_ids, exclude_words, max_topics,
            scene_count, video_whisper_fallback, poll_interval, poll_timeout, sem,
        )
    )
    logger.info("编排批次 %s 启动,账号=%s", batch_id, account_ids)
    return batch_id


async def _drive_batch(
    batch_id: str,
    account_ids: List[int],
    exclude_words: Optional[List[str]],
    max_topics: int,
    scene_count: int,
    video_whisper_fallback: bool,
    poll_interval: float,
    poll_timeout: Optional[float],
    semaphore: BrowserSemaphore,
) -> None:
    """驱动一个批次:并行跑每个账号的全链路,结果写回 _running_batches。"""
    coros = [
        _safe_run_account(
            semaphore, batch_id, aid, exclude_words, max_topics,
            scene_count, video_whisper_fallback, poll_interval, poll_timeout,
        )
        for aid in account_ids
    ]
    await asyncio.gather(*coros, return_exceptions=False)  # 异常已在 _safe_run_account 内吞

    _running_batches[batch_id]["status"] = "finished"
    _running_batches[batch_id]["finished_at"] = datetime.utcnow().isoformat()
    logger.info("编排批次 %s 全部完成", batch_id)


async def _safe_run_account(
    semaphore: BrowserSemaphore,
    batch_id: str,
    account_id: int,
    exclude_words: Optional[List[str]],
    max_topics: int,
    scene_count: int,
    video_whisper_fallback: bool,
    poll_interval: float,
    poll_timeout: Optional[float],
) -> None:
    """单账号全链路(失败隔离:异常写回 batch 结果,不抛)。"""
    try:
        async with semaphore:
            result = await _run_account_pipeline(
                account_id,
                exclude_words=exclude_words,
                max_topics=max_topics,
                scene_count=scene_count,
                video_whisper_fallback=video_whisper_fallback,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )
        _running_batches[batch_id]["results"][account_id] = result
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        logger.error("编排批次 %s 账号 %s 失败: %s", batch_id, account_id, err)
        _running_batches[batch_id]["results"][account_id] = {
            "account_id": account_id,
            "status": "failed",
            "step": "unknown",
            "error": err,
            "content_id": None,
        }


async def _run_account_pipeline(
    account_id: int,
    *,
    exclude_words: Optional[List[str]] = None,
    max_topics: int = 20,
    scene_count: int = 6,
    video_whisper_fallback: bool = False,
    poll_interval: float = 2.0,
    poll_timeout: Optional[float] = 1200.0,
    submit_poll: Optional[Any] = None,
    crawl_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """单账号全链路:热点→文案→视频成片→【停】。

    可注入 submit_poll / crawl_fn(测试用),默认热点走 _crawl_hotspot_in_threadpool,
    文案/视频走 scheduler.submit_and_poll。
    返回 {account_id, status, content_id, topic_id, step, error}。
    """
    if submit_poll is None:
        submit_poll = submit_and_poll
    if crawl_fn is None:
        crawl_fn = _crawl_hotspot_in_threadpool

    poll_kwargs = {"interval": poll_interval, "timeout": poll_timeout}

    # ---- 环节 1:热点采集(后端线程池,不经 worker)----
    # Windows 上 Dramatiq worker 子进程跑 Playwright 会 Errno 9(同 Phase 5 publish 的坑),
    # 热点采集走后端线程池绕开 worker fork,保留 task_run 记录用于断点续跑/审计。
    try:
        await crawl_fn(
            account_id=account_id,
            exclude_words=exclude_words,
            max_results=max_topics,
        )
    except Exception as e:
        return _fail(account_id, "hotspot", e)

    # 选最佳 topic(按 match_score 降序,候选态)
    topic_id = _pick_best_topic(account_id)
    if topic_id is None:
        return _fail(account_id, "hotspot",
                     RuntimeError(f"账号 {account_id} 采集后无候选选题"))

    # ---- 环节 2:文案生成 ----
    try:
        _tid, final = await submit_poll(
            "copy", "generate_copy_task",
            account_id=account_id,
            topic_id=topic_id,
            scene_count=scene_count,
            run_account_id=account_id,
            poll_kwargs=poll_kwargs,
        )
    except Exception as e:
        return _fail(account_id, "copy", e)

    content_id = _extract_content_id(final)
    if content_id is None:
        return _fail(account_id, "copy",
                     RuntimeError("文案生成结果无 content_id"))

    # ---- 环节 3:视频成片(场景 B 从零生成)----
    try:
        _tid, _final = await submit_poll(
            "video", "generate_video_task",
            content_id=content_id,
            whisper_fallback=video_whisper_fallback,
            run_account_id=account_id,
            run_content_id=content_id,
            poll_kwargs=poll_kwargs,
        )
    except Exception as e:
        return _fail(account_id, "video", e, content_id=content_id)

    # 成片 → Content 标 approved(待发布,A-8:到此停)
    _mark_content_approved(content_id)

    return {
        "account_id": account_id,
        "status": "pending_publish",  # 待发布,等用户触发
        "step": "done",
        "content_id": content_id,
        "topic_id": topic_id,
        "error": None,
    }


def _fail(account_id: int, step: str, exc: Exception,
          content_id: Optional[int] = None) -> Dict[str, Any]:
    err = f"{type(exc).__name__}: {exc}"
    return {
        "account_id": account_id,
        "status": "failed",
        "step": step,
        "error": err,
        "content_id": content_id,
    }


async def _crawl_hotspot_in_threadpool(
    account_id: int,
    exclude_words: Optional[List[str]] = None,
    max_results: int = 20,
) -> None:
    """热点采集:在后端线程池跑 Playwright(不经 Dramatiq worker)。

    Windows 上 Dramatiq worker 子进程跑 Playwright 会触发 Errno 9
    (worker fork 后 fd 表损坏,同 Phase 5 publish 的坑)。本函数在后端
    进程的线程池里跑 sync_playwright,绕开 worker fork,fd 干净。

    保留 task_run 记录(flow_type=hotspot)用于断点续跑/审计/前端日志展示。
    行为对齐 queue.crawl_hotspot_task,但执行环境不同。

    Raises:
        抓取或入库失败的原始异常(由调用方 _run_account_pipeline 捕获记 failed)。
    """
    from datetime import datetime
    from sqlalchemy import select as sa_select

    from app.db import SyncSessionLocal
    from app.models.account import Account
    from app.models.task_run import TaskRun, TaskStatus
    from app.models.topic import Topic, TopicStatus
    from app.services.crawler import crawl_hot_topics
    from starlette.concurrency import run_in_threadpool

    # 建 task_run 记录(RUNNING,审计/断点续跑)
    with SyncSessionLocal() as s:
        task = TaskRun(
            flow_type="hotspot", account_id=account_id,
            status=TaskStatus.RUNNING, started_at=datetime.utcnow(),
        )
        s.add(task); s.commit(); s.refresh(task)
        task_id = task.id

    def _do_crawl() -> int:
        # 线程内独立 session,跑 sync_playwright(后端进程 fd 干净)
        with SyncSessionLocal() as ts:
            acc = ts.get(Account, account_id)
            if acc is None:
                raise RuntimeError(f"账号 {account_id} 不存在")
            scored = list(crawl_hot_topics(
                acc, exclude_words=exclude_words, max_results=max_results
            ))
            # 去重入库(同平台同标题已存在的不重复插)
            existing_titles: set = set()
            if scored:
                existing = ts.execute(
                    sa_select(Topic.title).where(Topic.source_platform == acc.platform)
                )
                existing_titles = {row[0] for row in existing}
            inserted = 0
            for t in scored:
                if t.title in existing_titles:
                    continue
                ts.add(Topic(
                    source_platform=t.source_platform, title=t.title,
                    heat_score=t.heat_score, source_url=t.source_url,
                    matched_account_ids=t.matched_account_ids,
                    match_score=t.match_score, status=TopicStatus.CANDIDATE,
                ))
                inserted += 1
            ts.commit()
            return inserted

    try:
        inserted = await run_in_threadpool(_do_crawl)
        # 成功:更新 task_run
        with SyncSessionLocal() as s:
            t = s.get(TaskRun, task_id)
            if t is not None:
                t.status = TaskStatus.FINISHED
                t.finished_at = datetime.utcnow()
                import json
                t.result = json.dumps({"account_id": account_id, "inserted": inserted})
                s.commit()
        logger.info("热点采集(线程池)完成 account=%s 入库 %d 条", account_id, inserted)
    except Exception as e:
        # 失败:记录错误
        with SyncSessionLocal() as s:
            t = s.get(TaskRun, task_id)
            if t is not None:
                t.status = TaskStatus.FAILED
                t.finished_at = datetime.utcnow()
                t.error_log = f"{type(e).__name__}: {e}"
                s.commit()
        raise


def _pick_best_topic(account_id: int) -> Optional[int]:
    """选该账号匹配度最高的候选 topic(按 match_score 降序)。"""
    from sqlalchemy import select as sa_select

    from app.db import SyncSessionLocal
    from app.models.account import Account
    from app.models.topic import Topic, TopicStatus

    with SyncSessionLocal() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            return None
        stmt = (
            sa_select(Topic)
            .where(
                Topic.source_platform == acc.platform,
                Topic.status == TopicStatus.CANDIDATE,
            )
            .order_by(Topic.match_score.desc(), Topic.heat_score.desc())
            .limit(1)
        )
        topic = s.execute(stmt).scalar_one_or_none()
        return topic.id if topic else None


def _extract_content_id(final_status: Dict[str, Any]) -> Optional[int]:
    """从 generate_copy_task 的 final status.result 解析 content_id。"""
    result_str = final_status.get("result")
    if not result_str:
        return None
    try:
        import json
        parsed = json.loads(result_str)
        return parsed.get("content_id")
    except (ValueError, TypeError):
        return None


def _mark_content_approved(content_id: int) -> None:
    """成片后标 approved(待发布状态,A-8:到此停等用户)。"""
    from app.db import SyncSessionLocal
    from app.models.content import Content, ContentStatus

    with SyncSessionLocal() as s:
        c = s.get(Content, content_id)
        if c is not None and c.status == ContentStatus.PENDING_REVIEW:
            c.status = ContentStatus.APPROVED
            s.commit()
            logger.info("Content %s 标 approved(待发布)", content_id)


def get_batch_status(batch_id: str) -> Optional[Dict[str, Any]]:
    """查批次状态(各账号进度)。批次不存在返回 None。"""
    batch = _running_batches.get(batch_id)
    if batch is None:
        return None
    results = batch["results"]
    summary = {
        "total": len(batch["account_ids"]),
        "pending_publish": sum(1 for r in results.values() if r.get("status") == "pending_publish"),
        "failed": sum(1 for r in results.values() if r.get("status") == "failed"),
        "running": batch["status"] == "running",
    }
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "started_at": batch["started_at"],
        "finished_at": batch.get("finished_at"),
        "account_ids": batch["account_ids"],
        "results": results,
        "summary": summary,
    }


def list_pending_publish_contents() -> List[Dict[str, Any]]:
    """列出所有待发布 Content(状态 approved,供前端"待发布"区展示)。"""
    from sqlalchemy import select as sa_select

    from app.db import SyncSessionLocal
    from app.models.account import Account
    from app.models.content import Content, ContentStatus

    with SyncSessionLocal() as s:
        stmt = (
            sa_select(Content)
            .where(Content.status == ContentStatus.APPROVED)
            .order_by(Content.updated_at.desc())
        )
        contents = s.execute(stmt).scalars().all()
        out = []
        for c in contents:
            acc = s.get(Account, c.account_id) if c.account_id else None
            out.append({
                "content_id": c.id,
                "account_id": c.account_id,
                "account_nickname": acc.nickname if acc else None,
                "platform": acc.platform.value if acc else None,
                "title": c.title,
                "video_path": c.video_path,
                "has_video": bool(c.video_path),
            })
        return out
