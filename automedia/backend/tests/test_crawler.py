"""热点采集服务单测 - Phase 3 关键服务(DEV-PLAN 测试策略指定)。

覆盖重点:
    - score_topic_by_theme / score_topics_by_theme 纯函数(主题过滤+排除词+排序)
    - crawl_hot_topics 流程(cookie 失效抛 AuthExpiredError,视频号跳过,extractor 注入)

Playwright 真实抓取不在单测范围(页面结构易变 + 需真账号 cookie + 风控),
用 mock extractor 验证流程编排,真实抓取靠 Phase 验收手动跑。
"""
from typing import List

import pytest

from app.models.account import Account, AuthState, Platform, AccountStatus
from app.services.crawler import (
    AuthExpiredError,
    RawTopic,
    ScoredTopic,
    crawl_hot_topics,
    score_topic_by_theme,
    score_topics_by_theme,
)


# ---------- score_topic_by_theme ----------

class TestScoreTopic:
    def test_empty_theme_returns_zero(self):
        """主题为空,匹配度 0(不过滤但不算命中)。"""
        assert score_topic_by_theme("AI Agent 开发", "") == 0.0
        assert score_topic_by_theme("AI Agent 开发", None) == 0.0

    def test_single_keyword_hit(self):
        """命中单关键词,基础分 0.5。"""
        # 关键词"编程"在标题中出现
        assert score_topic_by_theme("AI Agent 编程框架对比", "编程") == 0.5

    def test_multi_keyword_more_score(self):
        """命中多关键词,分数更高(封顶 1.0)。"""
        s1 = score_topic_by_theme("AI Agent 编程", "AI 编程")
        s2 = score_topic_by_theme("AI 工具", "AI 编程")
        assert s1 > s2  # 命中两个词 > 命中一个
        assert s1 == 0.75  # 0.5 + 0.25

    def test_score_cap_at_one(self):
        """命中很多词,封顶 1.0。"""
        s = score_topic_by_theme("AI Agent 编程 框架 工具", "AI 编程 框架 工具")
        assert s == 1.0

    def test_no_hit_returns_zero(self):
        """不命中,0 分。"""
        assert score_topic_by_theme("美食探店", "AI编程") == 0.0

    def test_exclude_word_returns_negative(self):
        """命中排除词,返回 -1 标记弃用。"""
        s = score_topic_by_theme("AI 编程 线下培训", "AI编程", exclude_words=["线下培训"])
        assert s == -1.0

    def test_exclude_priority_over_match(self):
        """排除词优先于主题命中。"""
        s = score_topic_by_theme("AI 编程 广告推广", "AI 编程", exclude_words=["广告"])
        assert s == -1.0

    def test_case_insensitive(self):
        """大小写不敏感。"""
        assert score_topic_by_theme("ai agent develop", "AI") == 0.5


# ---------- score_topics_by_theme ----------

class TestScoreTopics:
    def _raw(self, title, heat=10.0, platform=Platform.XHS):
        return RawTopic(source_platform=platform, title=title, heat_score=heat)

    def test_filters_excluded(self):
        """排除词命中的被剔除。"""
        topics = [
            self._raw("AI 编程好物"),
            self._raw("AI 线下培训"),  # 排除
            self._raw("美食探店"),
        ]
        result = score_topics_by_theme(topics, "AI 编程", exclude_words=["线下培训"])
        titles = [t.title for t in result]
        assert "AI 线下培训" not in titles
        assert "AI 编程好物" in titles

    def test_sorted_by_match_then_heat(self):
        """先按匹配度再按热度排序。"""
        topics = [
            self._raw("美食探店", heat=100),       # 匹配度 0
            self._raw("AI 工具", heat=5),          # 匹配度 0.5
            self._raw("AI 编程 框架", heat=8),     # 匹配度 0.75
        ]
        result = score_topics_by_theme(topics, "AI 编程 框架")
        # 匹配度最高排第一
        assert result[0].title == "AI 编程 框架"
        # 同匹配度的,匹配度 0 的两条里热度高的排前(0 分的美食 vs 0 分的... 这里只有一个 0 分)
        # AI 工具 匹配度 0.5 排在 0 分之前
        assert result[1].title == "AI 工具"

    def test_empty_input(self):
        assert score_topics_by_theme([], "AI") == []

    def test_matched_account_ids_default_empty(self):
        """score_topics 不填 matched_account_ids(crawl_hot_topics 才填)。"""
        topics = [self._raw("AI 编程")]
        result = score_topics_by_theme(topics, "AI 编程")
        assert result[0].matched_account_ids == []


# ---------- crawl_hot_topics 流程(mock Playwright)----------

def _make_account(platform=Platform.XHS, theme="AI 编程", auth_state="encrypted"):
    """构造测试账号。"""
    return Account(
        id=1,
        platform=platform,
        nickname="测试号",
        topic_theme=theme,
        auth_state=auth_state,
        auth_status=AuthState.VALID,
        status=AccountStatus.ACTIVE,
    )


class TestCrawlFlow:
    def test_invalid_cookie_raises_auth_expired(self, monkeypatch):
        """cookie 失效 -> AuthExpiredError(FLOW-6 爬取前校验)。"""
        acc = _make_account(auth_state="", )  # 空密文

        with pytest.raises(AuthExpiredError):
            crawl_hot_topics(acc)

    def test_wechat_skipped(self, monkeypatch):
        """视频号无公开热榜,返回空列表不报错。"""
        # 视频号但 cookie 健康(需要 mock health check)
        acc = _make_account(platform=Platform.WECHAT)
        monkeypatch.setattr(
            "app.services.crawler.check_account_health",
            lambda a: type("H", (), {"healthy": True, "message": "ok"})()
        )
        result = crawl_hot_topics(acc)
        assert result == []

    def test_flow_with_mock_extractor(self, monkeypatch):
        """完整流程:cookie 健康 -> 解密 -> 注入 mock extractor -> 打分。

        验证编排正确性,不验证真实页面抓取。
        """
        acc = _make_account(theme="AI 编程")

        # mock 健康检查通过
        monkeypatch.setattr(
            "app.services.crawler.check_account_health",
            lambda a: type("H", (), {"healthy": True, "message": "ok"})()
        )
        # mock 解密返回空 cookie 列表
        monkeypatch.setattr(
            "app.services.crawler.decrypt_cookie", lambda c: [{"name": "x", "value": "y"}]
        )

        # mock extractor:返回固定词条
        def fake_extractor(page):
            return [
                RawTopic(Platform.XHS, "AI 编程新框架发布", heat_score=90),
                RawTopic(Platform.XHS, "美食探店合集", heat_score=80),
                RawTopic(Platform.XHS, "AI 工具盘点", heat_score=70),
            ]

        # mock Playwright 抓取,直接调 extractor 传 None
        def fake_fetch(platform, cookies, extractor):
            return extractor(None)
        monkeypatch.setattr("app.services.crawler._fetch_with_playwright", fake_fetch)

        result = crawl_hot_topics(acc, extractor_map={Platform.XHS: fake_extractor})

        # AI 编程新框架(0.5 分) 和 AI 工具(0.5 分) 应该排在前面,美食被排后
        titles = [t.title for t in result]
        assert "AI 编程新框架发布" in titles
        assert "AI 工具盘点" in titles
        # matched_account_ids 被填入
        assert all(t.matched_account_ids == [1] for t in result)
        # 排序:匹配度高的在前
        ai_topics = [t for t in result if "AI" in t.title]
        food_topics = [t for t in result if "美食" in t.title]
        assert result.index(ai_topics[0]) < result.index(food_topics[0])

    def test_max_results_limit(self, monkeypatch):
        """max_results 截断结果数量。"""
        acc = _make_account(theme="")
        monkeypatch.setattr(
            "app.services.crawler.check_account_health",
            lambda a: type("H", (), {"healthy": True, "message": "ok"})()
        )
        monkeypatch.setattr(
            "app.services.crawler.decrypt_cookie", lambda c: [{"name": "x"}]
        )
        monkeypatch.setattr(
            "app.services.crawler._fetch_with_playwright",
            lambda platform, cookies, extractor: extractor(None)
        )
        result = crawl_hot_topics(
            acc,
            extractor_map={Platform.XHS: lambda page: [
                RawTopic(Platform.XHS, f"词条{i}", heat_score=50 - i) for i in range(30)
            ]},
            max_results=5,
        )
        assert len(result) == 5
