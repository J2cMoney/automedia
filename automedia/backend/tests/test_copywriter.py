"""文案生成服务单测 - Phase 3 关键服务(DEV-PLAN 测试策略指定)。

覆盖重点(全 mock LLM,不烧钱不依赖网络):
    - parse_llm_json: markdown fence 剥离 / 截取 {} / 解析失败
    - generate_copy: 正常生成 / 平台调性传入 / 重试 / 失败兜底
    - generate_script: 分镜解析 / 空镜跳过 / 失败兜底
    - filter_hotspots: 精筛 / 失败降级返回空
    - prompts 模板: 三模板内容正确性
"""
import json
from typing import List

import pytest

from app.models.account import Platform
from app.prompts import (
    PLATFORM_TONE,
    build_copywriter_prompt,
    build_hotspot_filter_prompt,
    build_video_script_prompt,
)
from app.services.copywriter import (
    CopywriterError,
    ScriptScene,
    generate_copy,
    generate_script,
    filter_hotspots,
    parse_llm_json,
)


# ---------- mock LLM ----------

class MockLLM:
    """可控 mock LLM,按预设返回。"""
    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.calls: List[str] = []
        self.call_count = 0

    def chat(self, prompt, **kwargs):
        self.calls.append(prompt)
        self.call_count += 1
        if self.call_count <= len(self.responses):
            return self.responses[self.call_count - 1]
        # 超出预设,重复最后一个或抛错(看场景)
        return self.responses[-1] if self.responses else ""


class FailingLLM:
    """总是失败的 mock LLM。"""
    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.call_count = 0
        self.success_response = '{"title": "t", "body": "b", "tags": ["#a"]}'

    def chat(self, prompt, **kwargs):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError("模拟 LLM 失败")
        return self.success_response


# ---------- parse_llm_json ----------

class TestParseJson:
    def test_plain_json(self):
        data = parse_llm_json('{"a": 1, "b": "x"}')
        assert data == {"a": 1, "b": "x"}

    def test_markdown_fence(self):
        """LLM 把 JSON 包在 ```json 里,能剥离。"""
        text = '```json\n{"title": "标题", "tags": ["#a"]}\n```'
        data = parse_llm_json(text)
        assert data["title"] == "标题"

    def test_markdown_fence_plain(self):
        """无 json 标记的 fence。"""
        text = '```\n{"x": 1}\n```'
        assert parse_llm_json(text) == {"x": 1}

    def test_json_with_surrounding_text(self):
        """JSON 前后有解释文字,能截取最外层 {}。"""
        text = '好的,这是结果:\n{"title": "T", "body": "B"}\n希望满意。'
        data = parse_llm_json(text)
        assert data["title"] == "T"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_llm_json("这不是 json")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError):
            parse_llm_json("纯文字没有花括号")


# ---------- generate_copy ----------

class TestGenerateCopy:
    def test_normal_generation(self):
        llm = MockLLM(['{"title": "AI编程真香", "body": "正文内容...", "tags": ["#AI", "#编程"]}'])
        result = generate_copy("AI 框架对比", "AI 编程", Platform.XHS, llm=llm)
        assert result.title == "AI编程真香"
        assert "正文" in result.body
        assert result.tags == ["#AI", "#编程"]

    def test_platform_tone_in_prompt(self):
        """不同平台 prompt 包含对应调性。"""
        llm = MockLLM(['{"title": "t", "body": "b", "tags": []}'])
        generate_copy("选题", "主题", Platform.DOUYIN, llm=llm)
        # 抖音调性:短平快
        assert "抖音" in llm.calls[0]
        assert "50-150字" in llm.calls[0]

        llm2 = MockLLM(['{"title": "t", "body": "b", "tags": []}'])
        generate_copy("选题", "主题", Platform.XHS, llm=llm2)
        # 小红书调性:长文多标签
        assert "小红书" in llm2.calls[0]
        assert "300-800字" in llm2.calls[0]

    def test_retry_on_parse_failure(self):
        """JSON 解析失败时重试(Spec 5.3)。"""
        llm = MockLLM([
            "坏掉的输出",
            '{"title": "好标题", "body": "好正文", "tags": ["#ok"]}',
        ])
        result = generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert result.title == "好标题"
        assert llm.call_count == 2  # 第一次失败,第二次成功

    def test_retry_on_llm_error(self):
        """LLM 异常时重试。"""
        llm = FailingLLM(fail_times=2)  # 前 2 次失败,第 3 次成功
        result = generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert result.title == "t"
        assert llm.call_count == 3

    def test_all_retries_exhausted(self):
        """重试 MAX_RETRIES 次仍失败,抛 CopywriterError。"""
        from app.services.copywriter import MAX_RETRIES
        llm = FailingLLM(fail_times=MAX_RETRIES + 5)  # 一直失败
        with pytest.raises(CopywriterError):
            generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert llm.call_count == MAX_RETRIES

    def test_missing_title_raises_valueerror(self):
        """缺少 title 字段触发重试(校验失败)。"""
        llm = MockLLM([
            '{"body": "无标题", "tags": []}',  # 缺 title
            '{"title": "T", "body": "B", "tags": []}',
        ])
        result = generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert result.title == "T"

    def test_tags_not_list_coerced(self):
        """tags 不是列表时容错成列表。"""
        llm = MockLLM(['{"title": "T", "body": "B", "tags": "#单标签"}'])
        result = generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert result.tags == ["#单标签"]

    def test_tags_fallback_extract_from_body(self):
        """LLM 把标签写在 body 末尾而非 tags 字段时,兜底提取(实测高频问题)。

        场景:LLM 返回 tags=[] 但 body 末尾有 #标签,应提取出来并从 body 移除。
        """
        body_with_tags = "这是正文内容。\n\n#AI编程 #程序员 #干货分享"
        llm = MockLLM([json.dumps({
            "title": "T", "body": body_with_tags, "tags": []
        })])
        result = generate_copy("选题", "主题", Platform.XHS, llm=llm)
        assert "#AI编程" in result.tags
        assert "#程序员" in result.tags
        assert "#干货分享" in result.tags
        # body 里不再包含标签
        assert "#AI编程" not in result.body
        assert "这是正文内容" in result.body


# ---------- generate_script ----------

class TestGenerateScript:
    def test_normal_generation(self):
        resp = json.dumps({
            "scenes": [
                {"index": 1, "narration": "开头钩子", "visual": "特写", "duration": 3},
                {"index": 2, "narration": "正文", "visual": "全景", "duration": 5},
            ]
        })
        llm = MockLLM([resp])
        result = generate_script("选题", "主题", "文案正文", llm=llm)
        assert len(result.scenes) == 2
        assert result.scenes[0].narration == "开头钩子"
        assert result.scenes[0].duration == 3
        assert result.scenes[1].visual == "全景"

    def test_empty_scene_skipped(self):
        """空口播的分镜被跳过。"""
        resp = json.dumps({
            "scenes": [
                {"index": 1, "narration": "有效", "visual": "v", "duration": 3},
                {"index": 2, "narration": "", "visual": "空口播", "duration": 2},
                {"index": 3, "narration": "也有效", "visual": "v2", "duration": 4},
            ]
        })
        llm = MockLLM([resp])
        result = generate_script("选题", "主题", "正文", llm=llm)
        assert len(result.scenes) == 2
        assert result.scenes[1].narration == "也有效"

    def test_missing_duration_defaults(self):
        """缺 duration 字段默认 5。"""
        resp = json.dumps({
            "scenes": [{"index": 1, "narration": "n", "visual": "v"}]
        })
        llm = MockLLM([resp])
        result = generate_script("选题", "主题", "正文", llm=llm)
        assert result.scenes[0].duration == 5

    def test_empty_scenes_raises(self):
        """空 scenes 数组触发重试。"""
        llm = MockLLM([
            '{"scenes": []}',
            '{"scenes": [{"index": 1, "narration": "n", "visual": "v", "duration": 3}]}',
        ])
        result = generate_script("选题", "主题", "正文", llm=llm)
        assert len(result.scenes) == 1


# ---------- filter_hotspots ----------

class TestFilterHotspots:
    def test_normal_filter(self):
        llm = MockLLM(['{"selected": ["标题A", "标题B"]}'])
        result = filter_hotspots("主题", ["标题A", "标题B", "标题C"], llm=llm)
        assert result == ["标题A", "标题B"]

    def test_failure_returns_empty(self):
        """LLM 失败时降级返回空列表(不抛错,不阻塞)。"""
        llm = FailingLLM(fail_times=100)
        result = filter_hotspots("主题", ["标题"], llm=llm)
        assert result == []

    def test_empty_input(self):
        result = filter_hotspots("主题", [], llm=MockLLM([]))
        assert result == []


# ---------- prompts 模板 ----------

class TestPrompts:
    def test_hotspot_filter_prompt_contains_theme_and_titles(self):
        p = build_hotspot_filter_prompt("AI 编程", ["标题一", "标题二"], top_n=3)
        assert "AI 编程" in p
        assert "标题一" in p
        assert "标题二" in p
        assert "3" in p  # top_n

    def test_copywriter_prompt_contains_all_platforms_tone(self):
        """每个平台的 prompt 都包含其调性参数。"""
        for plat, tone in PLATFORM_TONE.items():
            p = build_copywriter_prompt("选题", "主题", plat)
            assert tone["name"] in p
            assert tone["body_len"] in p

    def test_video_script_prompt_contains_scene_count(self):
        p = build_video_script_prompt("选题", "主题", "正文", scene_count=8)
        assert "8" in p
        assert "选题" in p
        assert "正文" in p
