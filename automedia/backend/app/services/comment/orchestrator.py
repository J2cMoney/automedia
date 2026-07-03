"""回评编排服务 - Phase 5 FLOW-5 自动回评论。

职责:
    编排完整回评批次:fetch 评论 → 逐条 generate reply → reply → 限速 → 落库。
    被 queue actor(调度轮询)调用,单次处理一条 Content 的评论。

设计要点:
    1. 三个子步骤(fetcher/replier/commenter)均可注入(测试用),默认用真模块。
    2. 限速(Spec FLOW-5):每条回复之间 sleep config.REPLY_INTERVAL_SECONDS 防风控。
    3. 单次最多回 config.REPLY_MAX_PER_POLL 条(防一次刷屏触发风控)。
    4. 容错(Spec 5.3):任何子步骤失败都跳过该条不阻塞其余,记入 result.errors/skipped。
    5. fetch 失败直接返回(没评论没法回),其余失败累计不中断。
    6. 落库(Spec FLOW-5 产出"评论回复记录"+ 5.3 人工抽检护栏):每条评论写入 Comment 表,
       发送成功标 REPLIED,失败标 MANUAL,供前端评论中心展示 + 人工抽检。

返回 ReplyBatchResult(fetched/replied/skipped/errors),供上层记录与告警。
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from app.config import settings
from app.models.account import Account
from app.models.comment import Comment, CommentStatus
from app.models.content import Content
from app.services.comment.commenter import CommenterError, reply_comment
from app.services.comment.fetcher import FetcherError, RawComment, fetch_comments
from app.services.comment.replier import ReplyError, generate_reply

logger = logging.getLogger(__name__)


@dataclass
class ReplyBatchResult:
    """单次回评批次结果。"""
    fetched: int = 0           # 抓到多少条评论
    replied: int = 0           # 成功回复多少条
    skipped: int = 0           # 跳过多少条(生成失败/发送失败)
    errors: List[str] = field(default_factory=list)


def _persist_comment(
    content_id: int,
    raw: RawComment,
    reply_text: Optional[str],
    status: CommentStatus,
    error_log: Optional[str] = None,
    *,
    session_factory=None,
) -> None:
    """把一条评论 + AI 回复落库(Comment 表)。

    供人工抽检(Spec 5.3)和前端评论中心(SCREEN-4)展示。
    session_factory 可注入(测试用),默认 SyncSessionLocal。
    """
    if session_factory is None:
        from app.db import SyncSessionLocal as session_factory

    with session_factory() as s:
        c = Comment(
            content_id=content_id,
            platform_comment_id=raw.platform_comment_id,
            author=raw.author,
            text=raw.text,
            ai_reply=reply_text,
            status=status,
            error_log=error_log,
            replied_at=datetime.utcnow() if status == CommentStatus.REPLIED else None,
        )
        s.add(c)
        s.commit()


def process_comments(
    account: Account,
    content: Content,
    *,
    fetcher: Optional[Callable] = None,
    replier: Optional[Callable] = None,
    commenter: Optional[Callable] = None,
    max_replies: Optional[int] = None,
    sleep_func=time.sleep,
    session_factory=None,
) -> ReplyBatchResult:
    """编排:fetch 评论 → 逐条 generate reply → reply → 限速 → 落库。

    每条回复之间 sleep config.REPLY_INTERVAL_SECONDS(默认 60s)防风控。
    单次最多回 config.REPLY_MAX_PER_POLL 条(默认 10)。
    fetcher/replier/commenter/session_factory 可注入(测试用),默认用真模块。

    Args:
        account: 账号
        content: 内容(取 platform_post_url + title)
        fetcher: 抓评论函数(注入用),签名 (account, content) -> List[RawComment]
        replier: 生成回复函数(注入用),签名 (comment_text, video_title, platform, ...) -> str
        commenter: 发送回复函数(注入用),签名 (account, content, comment_text, reply_text) -> bool
        max_replies: 单次最多回多少条,None 则用 settings.REPLY_MAX_PER_POLL
        sleep_func: 限速 sleep 函数(注入用,测试传 fake 加快)
        session_factory: DB session 工厂(注入用,测试传 mock)

    Returns:
        ReplyBatchResult。任何子步骤失败都跳过该条不阻塞其余。
    """
    limit = max_replies if max_replies is not None else settings.REPLY_MAX_PER_POLL
    result = ReplyBatchResult()
    do_fetch = fetcher or fetch_comments
    do_reply_gen = replier or generate_reply
    do_reply_send = commenter or reply_comment

    # 1. 抓评论
    try:
        comments: List[RawComment] = do_fetch(account, content)
    except FetcherError as e:
        result.errors.append(f"抓评论失败: {e}")
        logger.warning("回评批次抓评论失败 content=%s: %s", content.id, e)
        return result
    result.fetched = len(comments)
    if not comments:
        logger.info("无评论可回 content=%s", content.id)
        return result

    # 2. 逐条:生成回复 → 发送 → 落库 → 限速
    replied_count = 0
    for i, c in enumerate(comments):
        if replied_count >= limit:
            break
        # 2a. 生成回复(失败跳过该条,但落库标 MANUAL 留底供人工)
        try:
            reply_text = do_reply_gen(
                c.text,
                content.title or "",
                account.platform,
                account_persona=account.topic_theme or None,
                comment_author=c.author,
            )
        except ReplyError as e:
            result.errors.append(f"评论{i}回复生成失败: {e}")
            result.skipped += 1
            logger.warning("评论%d回复生成失败 content=%s: %s", i, content.id, e)
            # 落库:生成失败标 MANUAL,留 ai_reply 空供人工补
            _persist_comment(content.id, c, None, CommentStatus.MANUAL,
                             error_log=f"回复生成失败: {e}",
                             session_factory=session_factory)
            continue

        # 2b. 发送回复(失败跳过该条,但落库留底)
        send_ok = False
        send_err = None
        try:
            send_ok = do_reply_send(account, content, c.text, reply_text)
            if not send_ok:
                send_err = "发送返回 False(平台侧可能未成功)"
        except CommenterError as e:
            send_err = f"发送失败: {e}"
            result.errors.append(f"评论{i}发送失败: {e}")
            logger.warning("评论%d发送失败 content=%s: %s", i, content.id, e)

        # 2c. 落库(无论成功失败都记,供人工抽检 + 评论中心展示)
        if send_ok:
            result.replied += 1
            replied_count += 1
            _persist_comment(content.id, c, reply_text, CommentStatus.REPLIED,
                             session_factory=session_factory)
        else:
            result.skipped += 1
            _persist_comment(content.id, c, reply_text, CommentStatus.MANUAL,
                             error_log=send_err, session_factory=session_factory)

        # 2d. 限速:每条之间等 X 秒防风控(最后一条不等)
        if replied_count < limit and i < len(comments) - 1:
            sleep_func(settings.REPLY_INTERVAL_SECONDS)

    logger.info("回评批次完成 content=%s fetched=%d replied=%d skipped=%d",
                content.id, result.fetched, result.replied, result.skipped)
    return result


__all__ = ["ReplyBatchResult", "process_comments"]
