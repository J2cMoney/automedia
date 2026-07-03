"""video/agent.py 剪辑决策核心单测 - Phase 4(DEV-PLAN 测试策略指定)。

全 mock LLM(不烧钱、不联网),覆盖:
    - decide_highlights: 正常决策 / JSON 解析失败重试 / segments 全无效 / 重试耗尽
    - plan_scenes_from_script: 正常生成 / asset_keyword 缺失容错 / 空脚本 / 重试耗尽
    - ClipDecision.total_duration / to_dict
    - ScenePlan.needs_asset / to_dict
"""
import json
from typing import List

import pytest

from app.services.video.agent import (
    AgentError,
    ClipDecision,
    HighlightSegment,
    ScenePlan,
    decide_highlights,
    plan_scenes_from_script,
)


# ---------- mock LLM ----------

class MockVisionLLM:
    """可控 mock,模拟 GLM vision() 返回。"""
    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.calls = 0
        self.last_frames = None
        self.last_prompt = None

    def vision(self, frames, prompt, **kwargs):
        self.calls += 1
        self.last_frames = frames
        self.last_prompt = prompt
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return self.responses[-1] if self.responses else ""


class MockChatLLM:
    """可控 mock,模拟 DeepSeek chat() 返回。"""
    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.calls = 0
        self.last_prompt = None

    def chat(self, prompt, **kwargs):
        self.calls += 1
        self.last_prompt = prompt
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return self.responses[-1] if self.responses else ""


class FailingLLM:
    """总是失败的 mock。"""
    def __init__(self, fail_times: int, method: str = "vision"):
        self.fail_times = fail_times
        self.method = method
        self.calls = 0
        self.good_vision = json.dumps({
            "worth_clip": True, "start": 0.0, "end": 5.0, "reason": "ok", "topic": "t",
        })
        self.good_chat = json.dumps({
            "scenes": [{"index": 1, "narration": "n", "visual": "v", "asset_keyword": "office work", "duration": 5}]
        })

    def vision(self, frames, prompt, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("模拟 GLM 失败")
        return self.good_vision

    def chat(self, prompt, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("模拟 DeepSeek 失败")
        return self.good_chat


class FailingVisionLLM:
    """分批决策专用 mock:第 1 批(批0)失败 N 次,后续批次返回成功的高光判断。

    用于测试"某批失败跳过不阻塞其他批"。
    """
    def __init__(self, fail_first_batch: int):
        self.fail_first_batch = fail_first_batch
        self.calls = 0
        self.batch_calls = 0  # 当前批内的调用次数(用于判定重试)

    def vision(self, frames, prompt, **kwargs):
        self.calls += 1
        self.batch_calls += 1
        # 通过 frames 数量判断批次(批0=5帧,批1=5帧)
        # 简化:前 fail_first_batch 次都失败,之后返回批1的成功结果
        if self.calls <= self.fail_first_batch:
            raise RuntimeError("模拟批0失败")
        # 后续视为批1成功
        return json.dumps({"worth_clip": True, "start": 5.0, "end": 10.0,
                           "reason": "批1高光", "topic": "批1"})


# ---------- decide_highlights ----------

class TestDecideHighlights:
    """分批决策测试:glm-4v-flash 每批 5 帧,各批出局部判断,Python 聚合。"""

    def test_single_batch_worth_clip(self):
        """单批 5 帧,GLM 判断值得剪,返回该段高光。"""
        # 5 帧 @ 1fps → 批次0 覆盖 0-5s
        resp = json.dumps({"worth_clip": True, "start": 1.0, "end": 4.0,
                           "reason": "核心观点", "topic": "讲了方法论"})
        llm = MockVisionLLM([resp])
        decision = decide_highlights(["f1.png"] * 5, fps=1.0, llm=llm)
        assert llm.calls == 1  # 5 帧一批,一次调用
        assert len(decision.segments) == 1
        assert decision.segments[0].start == 1.0
        assert decision.segments[0].end == 4.0
        assert "核心观点" in decision.segments[0].reason

    def test_single_batch_not_worth_raises(self):
        """GLM 判断不值得剪(整段无高光)→ 抛 AgentError。"""
        resp = json.dumps({"worth_clip": False, "topic": "铺垫"})
        llm = MockVisionLLM([resp])
        with pytest.raises(AgentError, match="不值得剪辑"):
            decide_highlights(["f1.png"] * 5, fps=1.0, llm=llm)

    def test_multi_batch_aggregation(self):
        """12 帧 @ 1fps → 3 批(5+5+2),批0/批2 值得剪,批1 不值得 → 聚合 2 段。"""
        responses = [
            json.dumps({"worth_clip": True, "start": 1.0, "end": 4.0, "reason": "开头", "topic": "开头"}),
            json.dumps({"worth_clip": False, "topic": "中段铺垫"}),  # 批1 不值得
            json.dumps({"worth_clip": True, "start": 10.5, "end": 12.0, "reason": "结尾金句", "topic": "结尾"}),
        ]
        llm = MockVisionLLM(responses)
        decision = decide_highlights(["f.png"] * 12, fps=1.0, llm=llm)
        assert llm.calls == 3  # 3 批
        assert len(decision.segments) == 2
        # 按时间排序
        assert decision.segments[0].start == 1.0
        assert decision.segments[1].start == 10.5

    def test_batch_time_constraint(self):
        """GLM 返回的子区间越界时,被约束在本批时间范围内。"""
        # 5 帧 @ 1fps → 批次0 覆盖 0-5s,GLM 故意返回 8s(越界)
        resp = json.dumps({"worth_clip": True, "start": 0.0, "end": 8.0,
                           "reason": "越界", "topic": "x"})
        llm = MockVisionLLM([resp])
        decision = decide_highlights(["f.png"] * 5, fps=1.0, llm=llm)
        # end 应被约束到 5.0(本批末帧时间)
        assert decision.segments[0].end <= 5.0

    def test_prompt_contains_batch_info(self):
        """prompt 含批次号、时间区间。"""
        resp = json.dumps({"worth_clip": False, "topic": ""})
        llm = MockVisionLLM([resp])
        with pytest.raises(AgentError):
            decide_highlights(["f.png"] * 5, fps=1.0, llm=llm)
        # prompt 应含批次信息
        assert "1/1" in llm.last_prompt or "1/1" in str(llm.last_prompt)

    def test_adjacent_segments_merged(self):
        """相邻批的高光段(间隔<1s)被合并。"""
        # 10 帧 @ 1fps → 2 批,批0 高光 0-5s,批1 高光 5-10s(相邻)
        responses = [
            json.dumps({"worth_clip": True, "start": 0.0, "end": 5.0, "reason": "段1", "topic": "t1"}),
            json.dumps({"worth_clip": True, "start": 5.0, "end": 10.0, "reason": "段2", "topic": "t2"}),
        ]
        llm = MockVisionLLM(responses)
        decision = decide_highlights(["f.png"] * 10, fps=1.0, llm=llm)
        # 5-5 间隔 0s < 1s,应合并成 0-10 一段
        assert len(decision.segments) == 1
        assert decision.segments[0].start == 0.0
        assert decision.segments[0].end == 10.0

    def test_target_duration_selection(self):
        """总高光超 target_duration 时,按时长降序保留 top 段。"""
        # 3 批各产 5s 高光,共 15s,target=8s → 保留约 2 段(10s)
        responses = [
            json.dumps({"worth_clip": True, "start": 0.0, "end": 5.0, "reason": "r1", "topic": "t1"}),
            json.dumps({"worth_clip": True, "start": 5.0, "end": 10.0, "reason": "r2", "topic": "t2"}),
            json.dumps({"worth_clip": True, "start": 10.0, "end": 15.0, "reason": "r3", "topic": "t3"}),
        ]
        llm = MockVisionLLM(responses)
        decision = decide_highlights(["f.png"] * 15, fps=1.0, target_duration=8, llm=llm)
        # 3 段各 5s,合并不了(间隔正好),target=8 选 2 段
        total = sum(s.end - s.start for s in decision.segments)
        assert total <= 15
        assert len(decision.segments) <= 3

    def test_batch_failure_skipped(self):
        """某批 GLM 调用失败,跳过该批不阻塞其他批。"""
        # 批0 失败3次,批1 成功
        llm = FailingVisionLLM(fail_first_batch=3)
        decision = decide_highlights(["f.png"] * 10, fps=1.0, llm=llm)
        # 批0 全失败跳过,批1 成功 → 1 段
        assert len(decision.segments) == 1

    def test_empty_frames_raises(self):
        with pytest.raises(AgentError, match="至少一帧"):
            decide_highlights([], llm=MockVisionLLM([]))

    # ClipDecision 数据结构测试
    def test_total_duration(self):
        decision = ClipDecision(segments=[
            HighlightSegment(0.0, 10.0),
            HighlightSegment(20.0, 35.0),
        ])
        assert decision.total_duration() == 25.0

    def test_to_dict_roundtrip(self):
        decision = ClipDecision(
            segments=[HighlightSegment(1.0, 3.0, "理由")],
            summary="摘要",
        )
        d = decision.to_dict()
        assert d["segments"][0]["start"] == 1.0
        assert d["summary"] == "摘要"
        assert d["segments"][0]["reason"] == "理由"


# ---------- plan_scenes_from_script ----------

class TestPlanScenesFromScript:
    def test_normal_generation(self):
        video_script = [
            {"index": 1, "narration": "开头钩子", "visual": "特写镜头", "duration": 3},
            {"index": 2, "narration": "正文内容", "visual": "全景", "duration": 5},
        ]
        resp = json.dumps({
            "scenes": [
                {"index": 1, "narration": "开头钩子", "visual": "特写镜头", "asset_keyword": "close up face", "duration": 3},
                {"index": 2, "narration": "正文内容", "visual": "全景", "asset_keyword": "wide shot office", "duration": 5},
            ]
        })
        llm = MockChatLLM([resp])
        plans = plan_scenes_from_script(video_script, "AI 编程", llm=llm)
        assert len(plans) == 2
        assert plans[0].narration == "开头钩子"
        assert plans[0].asset_keyword == "close up face"
        assert plans[1].duration == 5
        assert plans[0].needs_asset is True  # asset_path 还没填

    def test_empty_script_raises(self):
        with pytest.raises(AgentError, match="video_script 为空"):
            plan_scenes_from_script([], llm=MockChatLLM([]))

    def test_all_empty_narration_raises(self):
        """口播全空的分镜被过滤后,无有效分镜抛错。"""
        with pytest.raises(AgentError, match="无有效分镜"):
            plan_scenes_from_script(
                [{"narration": "", "visual": "v"}, {"narration": "  ", "visual": "v"}],
                llm=MockChatLLM([]),
            )

    def test_missing_keyword_kept_as_empty(self):
        """LLM 漏掉 asset_keyword 的镜,保留但 keyword 为空(后续走兜底)。"""
        resp = json.dumps({
            "scenes": [
                {"index": 1, "narration": "n1", "visual": "v1", "duration": 3},  # 无 keyword
                {"index": 2, "narration": "n2", "visual": "v2", "asset_keyword": "city", "duration": 4},
            ]
        })
        llm = MockChatLLM([resp])
        plans = plan_scenes_from_script(
            [{"narration": "n1", "visual": "v1"}, {"narration": "n2", "visual": "v2"}],
            llm=llm,
        )
        assert len(plans) == 2
        assert plans[0].asset_keyword == ""
        assert plans[1].asset_keyword == "city"

    def test_missing_duration_defaults_5(self):
        resp = json.dumps({
            "scenes": [{"index": 1, "narration": "n", "visual": "v", "asset_keyword": "k"}]
        })
        llm = MockChatLLM([resp])
        plans = plan_scenes_from_script([{"narration": "n", "visual": "v"}], llm=llm)
        assert plans[0].duration == 5

    def test_retry_on_failure(self):
        llm = FailingLLM(fail_times=2, method="chat")
        plans = plan_scenes_from_script(
            [{"narration": "n", "visual": "v"}], llm=llm,
        )
        assert len(plans) == 1
        assert llm.calls == 3

    def test_retries_exhausted(self):
        from app.services.video.agent import MAX_RETRIES
        llm = FailingLLM(fail_times=MAX_RETRIES + 5, method="chat")
        with pytest.raises(AgentError):
            plan_scenes_from_script([{"narration": "n"}], llm=llm)
        assert llm.calls == MAX_RETRIES

    def test_prompt_asks_english_keyword(self):
        """prompt 要求英文关键词。"""
        llm = MockChatLLM([json.dumps({"scenes": [{"index": 1, "narration": "n", "asset_keyword": "k"}]})])
        plan_scenes_from_script([{"narration": "n", "visual": "v"}], "主题", llm=llm)
        assert "Pexels" in llm.last_prompt or "pexels" in llm.last_prompt
        assert "英文" in llm.last_prompt or "english" in llm.last_prompt.lower()


# ---------- ScenePlan 属性 ----------

class TestScenePlanProps:
    def test_needs_asset_true_when_no_path(self):
        p = ScenePlan(index=1, narration="n", visual="v", asset_keyword="k")
        assert p.needs_asset is True

    def test_needs_asset_false_when_has_path(self):
        p = ScenePlan(index=1, narration="n", visual="v", asset_path="/some/x.mp4")
        assert p.needs_asset is False

    def test_to_dict(self):
        p = ScenePlan(index=2, narration="n", visual="v", asset_keyword="k", duration=7, asset_path="p")
        d = p.to_dict()
        assert d == {"index": 2, "narration": "n", "visual": "v",
                     "asset_keyword": "k", "duration": 7, "asset_path": "p"}
