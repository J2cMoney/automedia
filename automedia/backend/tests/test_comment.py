"""回评服务单测 - Phase 5 FLOW-5 关键服务(DEV-PLAN 测试策略指定)。

覆盖重点(全 mock,不依赖网络/真账号/真浏览器):
    - TestReplyGenerator: MockLLM 注入,测成功/prompt 内容/重试/空回复/失败兜底
    - TestFetcher: mock PublishContext + page,测 extractor 回调/无链接报错/平台分派
    - TestCommenter: mock PublishContext + page,测 selector 序列/找不到评论报错
    - TestOrchestrator: 三个注入函数,测限速/最多回 N 条/单条失败不阻塞/fetch 失败直接返回

真实 Playwright 抓评论/回评不在单测范围(需真账号 cookie + 平台风控 + DOM 易变),
用 mock 验证流程编排,真实回评靠 Phase 验收手动跑(照 test_publish_base 注释范式)。
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models.account import Account, AccountStatus, AuthState, Platform
from app.models.content import Content, ContentStatus
from app.services.comment.commenter import CommenterError, reply_comment
from app.services.comment.fetcher import (
    EXTRACTORS,
    FetcherError,
    RawComment,
    fetch_comments,
)
from app.services.comment.orchestrator import (
    ReplyBatchResult,
    process_comments,
)
from app.services.comment.replier import MAX_RETRIES, ReplyError, generate_reply


# ---------- 测试工具 ----------

def _make_account(platform=Platform.XHS) -> Account:
    """造一个测试账号(auth_state 非空绕过空检查)。"""
    return Account(
        id=1, platform=platform, nickname="测试号",
        topic_theme="AI编程", auth_state="cipher", auth_status=AuthState.VALID,
        status=AccountStatus.ACTIVE,
    )


def _make_content(post_url="https://xhs.com/note/123") -> Content:
    return Content(
        id=10, account_id=1, title="测试视频标题", body="测试正文",
        tags=["#测试"], video_path="/tmp/test.mp4", status=ContentStatus.PUBLISHED,
        platform_post_url=post_url,
    )


class MockLLM:
    """可控 mock LLM,按预设返回(照 test_copywriter.py:34 MockLLM 范式)。"""

    def __init__(self, responses):
        # responses 可以是单值或列表;列表元素可为 Exception(模拟失败)
        self.responses = responses
        self.calls = []
        self.call_count = 0

    def chat(self, prompt, **kwargs):
        self.calls.append(prompt)
        self.call_count += 1
        if isinstance(self.responses, list):
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return self.responses


class FailingLLM:
    """总是失败的 mock LLM(照 test_copywriter.py FailingLLM 范式)。"""

    def __init__(self, fail_times: int = MAX_RETRIES):
        self.fail_times = fail_times
        self.call_count = 0

    def chat(self, prompt, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError("模拟 LLM 失败")
        return "兜底回复"


# ---------- TestReplyGenerator ----------

class TestReplyGenerator:
    def test_success_returns_reply(self):
        """正常返回回复文本(strip)。"""
        llm = MockLLM("  谢谢支持,会继续加油!  ")
        reply = generate_reply("写得真好", "AI编程入门", Platform.XHS, llm=llm)
        assert reply == "谢谢支持,会继续加油!"
        assert llm.call_count == 1

    def test_prompt_contains_comment_and_title(self):
        """prompt 包含评论内容和视频标题(检查传给 chat 的 prompt)。"""
        llm = MockLLM("回复内容")
        generate_reply("请问哪里能学?", "AI 编程实战教程", Platform.DOUYIN, llm=llm)
        prompt = llm.calls[0]
        assert "请问哪里能学?" in prompt
        assert "AI 编程实战教程" in prompt

    def test_temperature_passed(self):
        """回评温度 0.7(比文案 0.8 略低,语气更稳)。"""
        captured = {}

        class _LLM:
            def chat(self_inner, prompt, **kw):
                captured.update(kw)
                return "回复"

        generate_reply("评论", "标题", Platform.XHS, llm=_LLM())
        assert captured.get("temperature") == 0.7

    def test_retry_then_success(self):
        """前 N 次失败,第 N+1 次成功 → 不抛错,返回成功回复。"""
        llm = MockLLM([RuntimeError("网络"), RuntimeError("超时"), "第三次成功"])
        reply = generate_reply("评论", "标题", Platform.XHS, llm=llm)
        assert reply == "第三次成功"
        assert llm.call_count == 3

    def test_all_failures_raise_reply_error(self):
        """3 次全失败 → 抛 ReplyError(不抛底层 RuntimeError)。"""
        llm = FailingLLM(fail_times=MAX_RETRIES)
        with pytest.raises(ReplyError):
            generate_reply("评论", "标题", Platform.XHS, llm=llm)
        assert llm.call_count == MAX_RETRIES

    def test_empty_reply_triggers_retry(self):
        """LLM 返回空字符串 → 触发重试(空回复视为失败)。"""
        llm = MockLLM(["", "", ""])  # 三次都空
        with pytest.raises(ReplyError):
            generate_reply("评论", "标题", Platform.XHS, llm=llm)
        assert llm.call_count == MAX_RETRIES

    def test_reply_error_message_mentions_retries(self):
        """ReplyError 信息提及重试次数(便于排查)。"""
        llm = FailingLLM(fail_times=MAX_RETRIES)
        with pytest.raises(ReplyError, match=str(MAX_RETRIES)):
            generate_reply("评论", "标题", Platform.XHS, llm=llm)


# ---------- TestFetcher ----------

class _MockPage:
    """模拟 Playwright page(query_selector_all + mouse + goto + wait)。"""

    def __init__(self, nodes=None):
        self._nodes = nodes or []
        self.goto = MagicMock()
        self.wait_for_timeout = MagicMock()
        self.mouse = MagicMock()

    def query_selector_all(self, selector):
        return list(self._nodes)


class _MockNode:
    """模拟评论 DOM 节点。"""

    def __init__(self, text):
        self._text = text

    def inner_text(self):
        return self._text


class TestFetcher:
    def test_no_post_url_raises(self):
        """无 platform_post_url → 抛 FetcherError(不进浏览器)。"""
        acc = _make_account()
        content = _make_content(post_url=None)
        with pytest.raises(FetcherError, match="发布链接"):
            fetch_comments(acc, content)

    def test_no_extractor_for_platform_raises(self):
        """平台无 extractor → 抛 FetcherError。"""
        acc = _make_account(platform=Platform.WECHAT)
        content = _make_content()
        with pytest.raises(FetcherError, match="无评论抽取器"):
            fetch_comments(acc, content)

    def test_extractor_callback_called(self):
        """extractor 被调用,返回其结果(注入 extractor)。"""
        acc = _make_account()
        content = _make_content()
        mock_page = _MockPage()

        called = {"n": 0}

        def fake_extractor(page):
            called["n"] += 1
            assert page is mock_page  # 确认传的是 PublishContext 开的 page
            return [RawComment(None, None, "评论A"), RawComment(None, None, "评论B")]

        with patch("app.services.comment.fetcher.PublishContext") as ctx_mock:
            ctx_mock.return_value.__enter__ = MagicMock(return_value=(mock_page, MagicMock()))
            ctx_mock.return_value.__exit__ = MagicMock(return_value=False)
            result = fetch_comments(acc, content, extractor=fake_extractor)

        assert called["n"] == 1
        assert len(result) == 2
        assert result[0].text == "评论A"

    def test_max_count_truncates(self):
        """max_count 截断返回。"""
        acc = _make_account()
        content = _make_content()
        mock_page = _MockPage()

        def fake_extractor(page):
            return [RawComment(None, None, f"c{i}") for i in range(50)]

        with patch("app.services.comment.fetcher.PublishContext") as ctx_mock:
            ctx_mock.return_value.__enter__ = MagicMock(return_value=(mock_page, MagicMock()))
            ctx_mock.return_value.__exit__ = MagicMock(return_value=False)
            result = fetch_comments(acc, content, extractor=fake_extractor, max_count=5)

        assert len(result) == 5

    def test_xhs_extractor_extracts_nodes(self):
        """小红书 extractor 从 query_selector_all 抽评论,过滤过短文本。"""
        from app.services.comment.fetcher import _extract_xhs_comments
        nodes = [
            _MockNode("这条视频讲得真清楚"),
            _MockNode("x"),          # 过短(len<=1)过滤
            _MockNode(""),           # 空过滤
            _MockNode("求更多教程!"),
        ]
        page = _MockPage(nodes=nodes)
        result = _extract_xhs_comments(page)
        assert len(result) == 2
        assert result[0].text == "这条视频讲得真清楚"

    def test_extractors_cover_three_platforms(self):
        """EXTRACTORS 覆盖三平台(XHS/DOUYIN/KUAISHOU)。"""
        assert Platform.XHS in EXTRACTORS
        assert Platform.DOUYIN in EXTRACTORS
        assert Platform.KUAISHOU in EXTRACTORS


# ---------- TestCommenter ----------

class TestCommenter:
    def test_no_post_url_raises(self):
        """无发布链接 → 抛 CommenterError(不进浏览器)。"""
        acc = _make_account()
        content = _make_content(post_url=None)
        with pytest.raises(CommenterError, match="发布链接"):
            reply_comment(acc, content, "评论", "回复")

    def test_no_replier_for_platform_raises(self):
        """平台无回评实现 → 抛 CommenterError。"""
        acc = _make_account(platform=Platform.WECHAT)
        content = _make_content()
        with pytest.raises(CommenterError, match="无回评实现"):
            reply_comment(acc, content, "评论", "回复")

    def test_replier_func_injected_and_returns(self):
        """replier_func 注入,返回其 bool 结果。"""
        acc = _make_account()
        content = _make_content()
        mock_page = _MockPage()
        called = {"n": 0}

        def fake_replier(page, comment_text, reply_text):
            called["n"] += 1
            assert page is mock_page
            assert comment_text == "评论"
            assert reply_text == "回复"
            return True

        with patch("app.services.comment.commenter.PublishContext") as ctx_mock:
            ctx_mock.return_value.__enter__ = MagicMock(return_value=(mock_page, MagicMock()))
            ctx_mock.return_value.__exit__ = MagicMock(return_value=False)
            ok = reply_comment(acc, content, "评论", "回复", replier_func=fake_replier)

        assert ok is True
        assert called["n"] == 1

    def test_replier_func_false_propagates(self):
        """replier_func 返回 False → 调用方拿到 False(不抛错)。"""
        acc = _make_account()
        content = _make_content()

        def fake_replier(page, comment_text, reply_text):
            return False

        with patch("app.services.comment.commenter.PublishContext") as ctx_mock:
            ctx_mock.return_value.__enter__ = MagicMock(return_value=(_MockPage(), MagicMock()))
            ctx_mock.return_value.__exit__ = MagicMock(return_value=False)
            ok = reply_comment(acc, content, "评论", "回复", replier_func=fake_replier)

        assert ok is False

    def test_comment_not_found_raises(self):
        """小红书回评:找不到评论 → 抛 CommenterError。"""
        from app.services.comment.commenter import _reply_xhs
        page = _MockPage(nodes=[])  # 无评论节点
        with pytest.raises(CommenterError, match="未找到评论"):
            _reply_xhs(page, "不存在的评论", "回复")

    def test_xhs_finds_comment_and_replies(self):
        """小红书回评:找到评论行 → 点回复 → 填文本 → 点发送 → 返回 True。

        验证完整 selector 序列被调用(reply/input/send)。
        """
        from app.services.comment.commenter import _reply_xhs

        # 造一个能命中文本的评论行节点 + 可见按钮
        visible_btn = MagicMock()
        visible_btn.is_visible.return_value = True
        visible_btn.click = MagicMock()

        visible_input = MagicMock()
        visible_input.is_visible.return_value = True
        visible_input.click = MagicMock()
        visible_input.type = MagicMock()

        send_btn = MagicMock()
        send_btn.is_visible.return_value = True
        send_btn.click = MagicMock()

        row_node = MagicMock()
        row_node.inner_text.return_value = "这条视频讲得真清楚,求更多"
        # 行内能找到回复按钮
        row_node.query_selector.return_value = visible_btn

        page = MagicMock()
        page.query_selector_all.return_value = [row_node]
        # 页面级 query_selector:输入框和发送按钮命中
        page.query_selector.side_effect = lambda sel: visible_input if "content" in sel or "textarea" in sel else send_btn
        page.wait_for_timeout = MagicMock()

        ok = _reply_xhs(page, "这条视频讲得真清楚", "谢谢支持!")
        assert ok is True
        visible_btn.click.assert_called()       # 点了回复
        visible_input.type.assert_called()      # 填了文本
        send_btn.click.assert_called()          # 点了发送


# ---------- TestOrchestrator ----------

class TestOrchestrator:
    @pytest.fixture(autouse=True)
    def _stub_persist(self, monkeypatch):
        """类级默认:patch _persist_comment 为 no-op,避免其他测试悄悄写真库污染数据。
        需要验证落库的测试(test_comments_persisted_to_db 等)用 monkeypatch.undo 或
        直接传 session_factory 走真库。"""
        monkeypatch.setattr(
            "app.services.comment.orchestrator._persist_comment",
            lambda *a, **kw: None,
        )
        self._persist_stubbed = True

    def test_fetch_failure_returns_early(self):
        """fetch 失败 → 直接返回,errors 记录,replied=0。"""
        acc = _make_account()
        content = _make_content()

        def fail_fetch(account, content):
            raise FetcherError("抓取失败")

        result = process_comments(acc, content, fetcher=fail_fetch)
        assert result.fetched == 0
        assert result.replied == 0
        assert any("抓评论失败" in e for e in result.errors)

    def test_no_comments_returns_empty(self):
        """抓到 0 条评论 → fetched=0,其余不动。"""
        acc = _make_account()
        content = _make_content()
        result = process_comments(acc, content, fetcher=lambda a, c: [])
        assert result.fetched == 0
        assert result.replied == 0
        assert result.skipped == 0

    def test_happy_path_all_replied(self):
        """3 条评论全成功 → replied=3,skipped=0。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, f"评论{i}") for i in range(3)]

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=lambda *a, **kw: True,
            sleep_func=lambda s: None,
        )
        assert result.fetched == 3
        assert result.replied == 3
        assert result.skipped == 0

    def test_max_replies_limits_count(self):
        """max_replies 限制回复数(抓 5 条只回 2 条)。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, f"评论{i}") for i in range(5)]

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=lambda *a, **kw: True,
            max_replies=2,
            sleep_func=lambda s: None,
        )
        assert result.replied == 2
        assert result.skipped == 0

    def test_reply_gen_failure_skips_not_blocks(self):
        """单条生成失败 → skipped+1,不阻塞其余。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, "评论0"), RawComment(None, None, "评论1")]

        calls = {"n": 0}

        def gen(comment_text, *a, **kw):
            calls["n"] += 1
            if comment_text == "评论0":
                raise ReplyError("生成失败")
            return "回复"

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=gen,
            commenter=lambda *a, **kw: True,
            sleep_func=lambda s: None,
        )
        assert result.replied == 1
        assert result.skipped == 1
        assert any("回复生成失败" in e for e in result.errors)

    def test_send_failure_skips_not_blocks(self):
        """单条发送失败 → skipped+1,不阻塞其余。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, "评论0"), RawComment(None, None, "评论1")]

        def send(account, content, comment_text, reply_text):
            if comment_text == "评论0":
                raise CommenterError("发送失败")
            return True

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=send,
            sleep_func=lambda s: None,
        )
        assert result.replied == 1
        assert result.skipped == 1
        assert any("发送失败" in e for e in result.errors)

    def test_send_returns_false_counts_as_skipped(self):
        """发送返回 False → skipped+1(不抛错但视为跳过)。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, "评论0")]

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=lambda *a, **kw: False,
            sleep_func=lambda s: None,
        )
        assert result.replied == 0
        assert result.skipped == 1

    def test_rate_limit_sleep_between_replies(self):
        """限速:每条之间 sleep 被调(3 条评论 → sleep 至少 2 次)。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, f"评论{i}") for i in range(3)]
        sleeps = []

        process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=lambda *a, **kw: True,
            sleep_func=lambda s: sleeps.append(s),
        )
        # 3 条成功回复,中间 sleep 2 次(最后一条不 sleep)
        assert len(sleeps) == 2
        # sleep 的秒数来自 settings.REPLY_INTERVAL_SECONDS
        from app.config import settings
        assert all(s == settings.REPLY_INTERVAL_SECONDS for s in sleeps)

    def test_no_sleep_after_last_reply(self):
        """最后一条回复后不 sleep(避免无谓等待)。"""
        acc = _make_account()
        content = _make_content()
        comments = [RawComment(None, None, "唯一评论")]
        sleeps = []

        process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "回复",
            commenter=lambda *a, **kw: True,
            sleep_func=lambda s: sleeps.append(s),
        )
        assert len(sleeps) == 0

    def test_result_dataclass_defaults(self):
        """ReplyBatchResult 默认值正确。"""
        r = ReplyBatchResult()
        assert r.fetched == 0
        assert r.replied == 0
        assert r.skipped == 0
        assert r.errors == []

    def test_comments_persisted_to_db(self, monkeypatch):
        """回评成功的评论落库 Comment 表(REPLIED 态),供评论中心 + 人工抽检。"""
        # 撤销类级 no-op patch,用真库验证落库闭环
        monkeypatch.undo()
        from app.db import SyncSessionLocal
        from app.models.comment import Comment, CommentStatus
        from sqlalchemy import select

        # 用独立 id 避免与其他测试的固定 id=1 account 冲突
        acc = _make_account()
        acc.id = 50
        content = _make_content()
        content.id = 50
        # content 需要先进库才能被外键引用
        with SyncSessionLocal() as s:
            s.add(acc)
            s.commit()
            s.refresh(acc)
            content.account_id = acc.id
            s.add(content)
            s.commit()
            s.refresh(content)
            content_id = content.id

        comments = [RawComment(None, "用户A", "夸赞评论")]
        result = process_comments(
            acc, content,
            fetcher=lambda a, c: comments,
            replier=lambda *a, **kw: "谢谢支持!",
            commenter=lambda *a, **kw: True,
            sleep_func=lambda s: None,
        )
        assert result.replied == 1

        # 验证落库
        with SyncSessionLocal() as s:
            rows = s.execute(
                select(Comment).where(Comment.content_id == content_id)
            ).scalars().all()
            assert len(rows) == 1
            c = rows[0]
            assert c.text == "夸赞评论"
            assert c.author == "用户A"
            assert c.ai_reply == "谢谢支持!"
            assert c.status == CommentStatus.REPLIED
            assert c.replied_at is not None

    def test_failed_reply_persisted_as_manual(self, monkeypatch):
        """发送失败的评论落库标 MANUAL + error_log,留底供人工处理。"""
        monkeypatch.undo()
        from app.db import SyncSessionLocal
        from app.models.comment import Comment, CommentStatus
        from sqlalchemy import select

        # 用新 id 避免与前一个落库测试的 account id=1 冲突
        acc = _make_account()
        acc.id = 99
        content = _make_content()
        content.id = 99
        with SyncSessionLocal() as s:
            s.add(acc); s.commit(); s.refresh(acc)
            content.account_id = acc.id
            s.add(content); s.commit(); s.refresh(content)
            content_id = content.id

        result = process_comments(
            acc, content,
            fetcher=lambda a, c: [RawComment(None, None, "提问")],
            replier=lambda *a, **kw: "生成的回复",
            commenter=lambda *a, **kw: False,  # 发送失败
            sleep_func=lambda s: None,
        )
        assert result.skipped == 1

        with SyncSessionLocal() as s:
            rows = s.execute(
                select(Comment).where(Comment.content_id == content_id)
            ).scalars().all()
            assert len(rows) == 1
            c = rows[0]
            assert c.status == CommentStatus.MANUAL
            assert c.ai_reply == "生成的回复"  # 生成的回复留底
            assert c.error_log is not None
            assert c.replied_at is None
