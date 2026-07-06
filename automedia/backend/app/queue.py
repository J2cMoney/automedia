"""任务队列 - Dramatiq + Redis,状态同步写 task_runs 表。

设计(DEV-PLAN Phase 1):
    - Dramatiq 做实时调度(Redis broker)
    - task_runs 表是单一真相源:每次状态变更同步写表
    - 重启恢复:启动时扫 task_runs 把 running 状态转回 pending 重新入队
    - 失败重试:Dramatiq Retry middleware + DB 记 retry_count

封装 API:
    submit(func, *args) -> task_id      # 提交任务,返回 task_runs.id
    status(task_id) -> dict              # 查状态 {status, started_at, ...}
    result(task_id) -> str|None          # 取结果
    retry(task_id) -> bool               # 手动重试失败任务

用法:
    from app.queue import submit, status, q_task
    tid = submit("test.sleep_task", 3)   # 提交
    info = status(tid)                   # 查状态
"""
import functools
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    Callbacks,
    Pipelines,
    Retries,
    TimeLimit,
)

from app.config import settings
from app.db import SyncSessionLocal
from app.models.task_run import TaskRun, TaskStatus

logger = logging.getLogger(__name__)

# ---------- broker 初始化 ----------

broker = RedisBroker(
    url=settings.redis_url,
    middleware=[
        AgeLimit(max_age=86400000),  # 消息最大存活 1 天(ms)
        TimeLimit(time_limit=1800000),  # 单任务最大 30 分钟(Phase4 视频渲染可能较长)
        Callbacks(),
        Pipelines(),
        Retries(),           # 失败重试(Dramatiq 内置)
    ],
)
dramatiq.set_broker(broker)


# ---------- task_runs 表同步工具 ----------

def _create_task_run(flow_type: str, message_id: Optional[str] = None,
                     account_id: Optional[int] = None,
                     content_id: Optional[int] = None) -> int:
    """提交时建一条 PENDING 的 task_run,返回 id。"""
    with SyncSessionLocal() as s:
        task = TaskRun(
            message_id=message_id,
            flow_type=flow_type,
            account_id=account_id,
            content_id=content_id,
            status=TaskStatus.PENDING,
        )
        s.add(task)
        s.commit()
        s.refresh(task)
        return task.id


def _update_status(task_id: int, status: TaskStatus,
                   error_log: Optional[str] = None,
                   result: Optional[str] = None) -> None:
    """状态变更同步写 task_runs 表(单一真相源)。"""
    with SyncSessionLocal() as s:
        task = s.get(TaskRun, task_id)
        if task is None:
            logger.warning("task_id=%s 不存在,跳过状态更新", task_id)
            return
        task.status = status
        now = datetime.utcnow()
        if status == TaskStatus.RUNNING and task.started_at is None:
            task.started_at = now
        elif status == TaskStatus.FINISHED:
            task.finished_at = now
        elif status == TaskStatus.FAILED:
            task.finished_at = now
            if error_log:
                task.error_log = error_log
        if result is not None:
            task.result = result
        s.commit()


def _set_message_id(task_id: int, message_id: str) -> None:
    """Dramatiq 入队后回填 message_id。"""
    with SyncSessionLocal() as s:
        task = s.get(TaskRun, task_id)
        if task is not None:
            task.message_id = message_id
            s.commit()


# ---------- 统一状态生命周期管理的 actor 工厂 ----------

def tracked_actor(fn: Callable) -> "dramatiq.Actor":
    """装饰器:把普通函数注册成 Dramatiq actor,并自动管理 task_runs 状态。

    流程:标 RUNNING -> 跑业务 -> 成功标 FINISHED(存 result) / 失败标 FAILED(存 err)。
    同步调 .fn 和异步 worker 跑行为一致。

    被装饰函数签名必须含 task_id 参数。
    """
    @functools.wraps(fn)
    def wrapper(task_id: int, *args, **kwargs):
        _update_status(task_id, TaskStatus.RUNNING)
        logger.info("任务 %s 开始: %s", task_id, fn.__name__)
        try:
            ret = fn(task_id=task_id, *args, **kwargs)
            result_str = json.dumps(ret, ensure_ascii=False, default=str) if ret is not None else None
            _update_status(task_id, TaskStatus.FINISHED, result=result_str)
            logger.info("任务 %s 完成: %s", task_id, fn.__name__)
            return ret
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            _update_status(task_id, TaskStatus.FAILED, error_log=err)
            logger.error("任务 %s 失败: %s - %s", task_id, fn.__name__, err)
            raise  # 让 Dramatiq Retries middleware 处理自动重试
    # 注册成 Dramatiq actor(保留原函数名作 actor_name)
    return dramatiq.actor(wrapper)


# ---------- 内置测试 actor(Phase 1 验收用) ----------

@tracked_actor
def sleep_task(task_id: int, seconds: int = 3) -> Dict[str, Any]:
    """测试任务:sleep N 秒,用于验收队列状态流转 pending->running->finished。"""
    import time
    time.sleep(seconds)
    return {"slept": seconds, "done": True}


@tracked_actor
def fail_task(task_id: int, msg: str = "故意失败") -> None:
    """测试任务:必定失败,用于验收 FAILED 状态和重试。"""
    raise RuntimeError(f"测试失败: {msg}")


# ---------- Phase 3:热点爬取 actor ----------

@tracked_actor
def crawl_hotspot_task(
    task_id: int,
    account_id: int,
    exclude_words: Optional[list] = None,
    max_results: int = 20,
) -> Dict[str, Any]:
    """热点爬取任务(Phase 3 FLOW-1,长任务异步化)。

    拿账号 cookie 爬其所在平台热榜,按主题过滤排序后批量写入 topics 表。
    Playwright 爬取在线程池外(Dramatiq worker 本身是同步进程)。

    ⚠️ Windows 已知限制(Phase 6 端到端实测发现):
    Dramatiq worker 子进程在 Windows 上跑 Playwright 会触发
    OSError: [Errno 9] Bad file descriptor(worker fork 后 fd 表损坏,
    同 Phase 5 publish 的坑)。单测用 .fn 直接调能过(主进程 fd 干净)。
    生产全链路编排请走 orchestrator._crawl_hotspot_in_threadpool(后端线程池,
    不经 worker),见 services/orchestrator.py。

    Args:
        task_id: tracked_actor 自动注入
        account_id: 账号 id(决定平台 + 主题过滤)
        exclude_words: 排除词(Spec FLOW-1 MUST)
        max_results: 最多入库多少条
    """
    from sqlalchemy import select as sa_select

    from app.db import SyncSessionLocal
    from app.models.account import Account
    from app.models.topic import Topic, TopicStatus
    from app.services.crawler import crawl_hot_topics

    with SyncSessionLocal() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise RuntimeError(f"账号 {account_id} 不存在")

        # 爬取(同步 Playwright,worker 进程内跑)
        scored = crawl_hot_topics(
            acc, exclude_words=exclude_words, max_results=max_results
        )

        # 批量入库(去重:同平台同标题已存在的不重复插)
        existing_titles: set = set()
        if scored:
            existing = s.execute(
                sa_select(Topic.title).where(Topic.source_platform == acc.platform)
            )
            existing_titles = {row[0] for row in existing}

        inserted = 0
        for t in scored:
            if t.title in existing_titles:
                continue
            topic = Topic(
                source_platform=t.source_platform,
                title=t.title,
                heat_score=t.heat_score,
                source_url=t.source_url,
                matched_account_ids=t.matched_account_ids,
                match_score=t.match_score,
                status=TopicStatus.CANDIDATE,
            )
            s.add(topic)
            inserted += 1
        s.commit()

    logger.info("热点爬取完成 account=%s 入库 %d 条", account_id, inserted)
    return {"account_id": account_id, "inserted": inserted, "platform": acc.platform.value}


# ---------- Phase 6:文案+脚本生成 actor(编排器串链路用) ----------

@tracked_actor
def generate_copy_task(
    task_id: int,
    account_id: int,
    topic_id: int,
    scene_count: int = 6,
) -> Dict[str, Any]:
    """文案+视频脚本生成任务(Phase 6 编排器串链路用,Spec FLOW-2)。

    编排器串联全链路时,文案生成作为独立环节走 Dramatiq,与热点/视频成片一样
    有 task_run 记录,断点续跑天然支持。行为对齐 topics.py::_generate_copy_and_script
    (同步 route 版本),但本 actor 自建 Content 并写库,不依赖 HTTP 上下文。

    Args:
        task_id: tracked_actor 自动注入
        account_id: 账号 id(决定主题 + 平台调性)
        topic_id: 选题 id(决定文案内容来源)
        scene_count: 视频脚本分镜数(默认 6)
    """
    from app.models.account import Account
    from app.models.content import Content, ContentStatus
    from app.models.topic import Topic, TopicStatus
    from app.services.copywriter import generate_copy, generate_script

    with SyncSessionLocal() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise RuntimeError(f"账号 {account_id} 不存在")
        if not acc.topic_theme:
            raise RuntimeError(f"账号 {account_id} 未配置 topic_theme,无法生成文案")
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise RuntimeError(f"选题 {topic_id} 不存在")

        # 建 Content(初始 GENERATING),失败也保留记录(Spec 5.3 兜底)
        content = Content(
            account_id=acc.id,
            topic_id=topic.id,
            status=ContentStatus.GENERATING,
        )
        s.add(content)
        s.commit()
        s.refresh(content)
        content_id = content.id
        topic_title = topic.title
        topic_theme = acc.topic_theme
        platform = acc.platform

    # 生成(同步,DeepSeek 文本快,15s 内;copywriter 内部已有 3 次重试)
    try:
        copy = generate_copy(topic_title, topic_theme, platform)
        script = generate_script(topic_title, topic_theme, copy.body, scene_count=scene_count)
    except Exception as e:
        # 标 FAILED 保留记录,不阻塞编排器其他账号
        with SyncSessionLocal() as s:
            c = s.get(Content, content_id)
            if c is not None:
                c.status = ContentStatus.FAILED
                c.error_log = f"{type(e).__name__}: {e}"
                s.commit()
        raise RuntimeError(f"文案生成失败: {e}") from e

    video_script = [
        {
            "index": sc.index,
            "narration": sc.narration,
            "visual": sc.visual,
            "duration": sc.duration,
        }
        for sc in script.scenes
    ]

    # 写回 Content
    with SyncSessionLocal() as s:
        c = s.get(Content, content_id)
        if c is not None:
            c.title = copy.title
            c.body = copy.body
            c.tags = copy.tags
            c.video_script = video_script
            c.status = ContentStatus.PENDING_REVIEW
            s.commit()
        # 选题标 ADOPTED(已采纳生成)
        t = s.get(Topic, topic_id)
        if t is not None and t.status == TopicStatus.CANDIDATE:
            t.status = TopicStatus.ADOPTED
            s.commit()

    logger.info(
        "文案生成完成 content_id=%s account=%s topic=%s scenes=%d",
        content_id, account_id, topic_id, len(video_script),
    )
    return {
        "content_id": content_id,
        "account_id": account_id,
        "topic_id": topic_id,
        "title": copy.title,
        "scenes": len(video_script),
    }


# ---------- Phase 4:视频智能剪辑 actor ----------

@tracked_actor
def extract_highlights_task(
    task_id: int,
    content_id: int,
    source_video_path: str,
    target_duration: int = 60,
) -> Dict[str, Any]:
    """场景 A:长视频高光提取(Phase 4 FLOW-3 场景 A)。

    用户上传长视频 -> 抽帧 -> GLM 看帧找高光 -> 剪切拼接成 60s 短片。
    成片路径写回 Content.video_path,clip_decision 写回 Content.clip_decision。

    Args:
        task_id: tracked_actor 自动注入
        content_id: 关联的 Content(成片写回)
        source_video_path: 源长视频本地路径(用户上传)
        target_duration: 目标成片秒数(默认 60)
    """
    from app.services.video.extractor import extract_highlights, ExtractorError

    try:
        output_path, decision = extract_highlights(
            source_video_path,
            task_id=task_id,
            target_duration=target_duration,
        )
    except ExtractorError as e:
        raise RuntimeError(f"高光提取失败: {e}") from e

    # 写回 Content 表
    with SyncSessionLocal() as s:
        from app.models.content import Content
        content = s.get(Content, content_id)
        if content is not None:
            content.video_path = str(output_path)
            content.clip_decision = decision.to_dict()
            s.commit()

    return {
        "content_id": content_id,
        "video_path": str(output_path),
        "segments": len(decision.segments),
        "summary": decision.summary,
    }


@tracked_actor
def generate_video_task(
    task_id: int,
    content_id: int,
    whisper_fallback: bool = False,
) -> Dict[str, Any]:
    """场景 B:从零生成视频(Phase 4 FLOW-3 场景 B)。

    Flow-2 脚本 -> scene plan -> Pexels 素材 + TTS + 字幕 -> Remotion 渲染成片。
    成片路径写回 Content.video_path,scene plan 写回 Content.script_scenes。

    Args:
        task_id: tracked_actor 自动注入
        content_id: 关联的 Content(读取 video_script,写回 video_path)
        whisper_fallback: TTS 无字幕时是否用 Whisper 备选
    """
    from sqlalchemy import select as sa_select

    from app.models.account import Account
    from app.models.content import Content
    from app.services.video.generator import (
        generate_from_script, get_missing_asset_scenes, GeneratorError,
    )

    with SyncSessionLocal() as s:
        content = s.get(Content, content_id)
        if content is None:
            raise RuntimeError(f"Content {content_id} 不存在")
        video_script = content.video_script or []
        if not video_script:
            raise RuntimeError(f"Content {content_id} 无 video_script,场景 B 需要先有 Flow-2 脚本")

        # 取账号主题(辅助 LLM 理解素材方向)
        acc = s.get(Account, content.account_id) if content.account_id else None
        topic_theme = acc.topic_theme if acc else ""

    try:
        output_path, plans, cues = generate_from_script(
            video_script,
            task_id=task_id,
            topic_theme=topic_theme,
            whisper_fallback=whisper_fallback,
        )
    except GeneratorError as e:
        raise RuntimeError(f"场景 B 生成失败: {e}") from e

    missing = get_missing_asset_scenes(plans)

    # 写回 Content 表
    with SyncSessionLocal() as s:
        content = s.get(Content, content_id)
        if content is not None:
            content.video_path = str(output_path)
            content.script_scenes = {"scenes": [p.to_dict() for p in plans]}
            s.commit()

    return {
        "content_id": content_id,
        "video_path": str(output_path),
        "scenes": len(plans),
        "missing_asset_scenes": missing,  # 缺素材的镜(前端提示手动上传)
        "cues": len(cues),
    }


# ---------- Phase 5:发布 + 视频号打包 + 回评 actor ----------

@tracked_actor
def publish_task(
    task_id: int,
    content_id: int,
    timeout_minutes: int = 5,
) -> Dict[str, Any]:
    """人机协同辅助发布任务(Phase 5 v1.6 修订,Spec A-8)。

    读 Content + Account -> 调 assist_publish(有头浏览器自动上传+填文案,
    停住等用户手动点发布,抓 URL 回填) -> 写回 Content 状态。

    worker 在用户机器上跑,有头 Chrome 会弹出让用户操作。
    超时(默认 5 分钟)用户未点发布视为放弃。

    Args:
        task_id: tracked_actor 自动注入
        content_id: 要发布的 Content
        timeout_minutes: 等用户点发布的超时(默认 5 分钟)
    """
    from datetime import datetime

    from app.models.account import Account
    from app.models.content import Content, ContentStatus
    from app.services.publish.assist import assist_publish

    with SyncSessionLocal() as s:
        content = s.get(Content, content_id)
        if content is None:
            raise RuntimeError(f"Content {content_id} 不存在")
        if content.account_id is None:
            raise RuntimeError(f"Content {content_id} 无关联账号")
        account = s.get(Account, content.account_id)
        if account is None:
            raise RuntimeError(f"账号 {content.account_id} 不存在")
        # 标记发布中
        content.status = ContentStatus.PUBLISHING
        s.commit()

    # 执行辅助发布(有头浏览器,长任务,等用户点发布)
    result = assist_publish(account, content, timeout_minutes=timeout_minutes)

    # 写回结果
    with SyncSessionLocal() as s:
        content = s.get(Content, content_id)
        if content is not None:
            if result.success:
                content.status = ContentStatus.PUBLISHED
                content.platform_post_url = result.post_url
                content.published_at = datetime.utcnow()
                content.error_log = None
            else:
                # 用户超时未点发布不算生成失败,回到已审核态供下次再发
                if "超时" in (result.error or ""):
                    content.status = ContentStatus.APPROVED
                else:
                    content.status = ContentStatus.FAILED
                content.error_log = result.error
            s.commit()

    if not result.success:
        raise RuntimeError(f"辅助发布未完成: {result.error}")
    return {
        "content_id": content_id,
        "platform": result.platform,
        "post_url": result.post_url,
    }


@tracked_actor
def reply_comments_task(
    task_id: int,
    content_id: int,
    max_replies: Optional[int] = None,
) -> Dict[str, Any]:
    """自动回评任务(Phase 5 FLOW-5)。

    读 Content + Account -> 调 process_comments(fetch 评论 -> 生成回复 -> 模拟回复)
    -> 抓到的新评论入 comments 表,成功回复的标 REPLIED。

    Args:
        task_id: tracked_actor 自动注入
        content_id: 要回评的 Content(已发布的)
        max_replies: 本次最多回几条(默认 config.REPLY_MAX_PER_POLL)
    """
    from app.models.account import Account
    from app.models.content import Content
    from app.models.comment import Comment, CommentStatus
    from app.services.comment.orchestrator import process_comments

    with SyncSessionLocal() as s:
        content = s.get(Content, content_id)
        if content is None:
            raise RuntimeError(f"Content {content_id} 不存在")
        account = s.get(Account, content.account_id) if content.account_id else None
        if account is None:
            raise RuntimeError(f"Content {content_id} 无有效关联账号")

    # 执行回评(含限速 sleep,长任务;评论记录在 process_comments 内落库 Comment 表)
    batch = process_comments(account, content, max_replies=max_replies)

    # 批次统计写 task_runs.result,详细评论记录查 /api/comments(已落库)
    return {
        "content_id": content_id,
        "fetched": batch.fetched,
        "replied": batch.replied,
        "skipped": batch.skipped,
        "errors": batch.errors[:5],  # 只存前 5 条错误,避免过长
    }


# ---------- 公开 API ----------

def submit(flow_type: str, actor_name: str, *args,
           run_account_id: Optional[int] = None,
           run_content_id: Optional[int] = None,
           **kwargs) -> int:
    """提交任务到队列。

    Args:
        flow_type: 任务类型(如 test/hotspot/copywrite)
        actor_name: dramatiq actor 名(如 sleep_task)
        run_account_id: 写入 task_runs.account_id(便于按账号查任务历史)。
            注意:用 run_ 前缀避免和 actor 业务参数名 account_id 冲突。
        run_content_id: 写入 task_runs.content_id。
        *args, **kwargs: actor 的位置/关键字参数(不含 task_id,自动注入)

    Returns:
        task_id: task_runs 表的记录 id,用于后续查状态
    """
    # 1. 先建 task_run(PENDING)
    task_id = _create_task_run(
        flow_type=flow_type, account_id=run_account_id, content_id=run_content_id
    )

    # 2. 入队(message_id 入队后才知道,先空)
    actor = getattr(__import__("app.queue", fromlist=[actor_name]), actor_name)
    message = actor.send(task_id=task_id, *args, **kwargs)

    # 3. 回填 message_id
    if message is not None and hasattr(message, "message_id"):
        _set_message_id(task_id, message.message_id)

    logger.info("提交任务 task_id=%s flow=%s actor=%s", task_id, flow_type, actor_name)
    return task_id


def status(task_id: int) -> Optional[Dict[str, Any]]:
    """查任务状态。返回 task_run 的关键字段。"""
    with SyncSessionLocal() as s:
        task = s.get(TaskRun, task_id)
        if task is None:
            return None
        return {
            "id": task.id,
            "flow_type": task.flow_type,
            "status": task.status.value,
            "message_id": task.message_id,
            "retry_count": task.retry_count,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "error_log": task.error_log,
            "result": task.result,
            "created_at": task.created_at.isoformat() if task.created_at else None,
        }


def retry(task_id: int) -> bool:
    """手动重试失败任务。重新入队同一 actor。

    Returns:
        True 表示重试已提交,False 表示任务不存在或非失败状态。
    """
    with SyncSessionLocal() as s:
        task = s.get(TaskRun, task_id)
        if task is None:
            return False
        if task.status != TaskStatus.FAILED:
            logger.warning("任务 %s 状态=%s,非 FAILED 不重试", task_id, task.status.value)
            return False
        flow_type = task.flow_type
        # message_id 不复用,新入队
        task.status = TaskStatus.PENDING
        task.retry_count += 1
        task.error_log = None
        task.finished_at = None
        s.commit()

    # 注意:这里无法知道原 actor 名,需要业务层自行重新提交
    # Phase 1 测试场景由调用方重新 submit
    logger.info("任务 %s 标记重试(flow=%s),等待重新提交", task_id, flow_type)
    return True


def recover_on_startup() -> int:
    """服务重启恢复:把 RUNNING 状态(中断未完成)的转回 PENDING。

    返回恢复的任务数。实际重新入队需业务层根据 flow_type 重新 submit,
    Phase 1 只保证状态不丢(DB 里有记录)。

    调用时机:FastAPI 启动事件。
    """
    with SyncSessionLocal() as s:
        running = s.query(TaskRun).filter(TaskRun.status == TaskStatus.RUNNING).all()
        count = 0
        for task in running:
            task.status = TaskStatus.PENDING
            task.started_at = None
            count += 1
        s.commit()
        if count:
            logger.warning("重启恢复: %d 个 RUNNING 任务转回 PENDING", count)
        return count


def get_redis_info() -> Dict[str, Any]:
    """健康检查用:返回 Redis 连通状态。"""
    import redis as redis_lib
    try:
        r = redis_lib.from_url(settings.redis_url)
        pong = r.ping()
        info = r.info("server")
        return {
            "connected": bool(pong),
            "version": info.get("redis_version", "unknown"),
            "mode": info.get("redis_mode", "unknown"),
        }
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {e}"}
