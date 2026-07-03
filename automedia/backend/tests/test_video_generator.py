"""video/generator.py 场景B 从零生成单测 - Phase 4。

编排层测试,全 mock 子模块(agent/assets/tts/subtitle/render),验证:
    - 正常端到端流程:脚本 -> plan -> 找素材 -> TTS -> 渲染
    - 混合素材:Pexels 找到回填 path / 找不到保持 None 兜底
    - 中间产物落盘(scene_plan.json / subtitles.json)
    - 各环节失败抛 GeneratorError
    - get_missing_asset_scenes 识别缺素材的镜

真实端到端(真 LLM/TTS/Remotion)靠手动验收(步骤 9 / 四步走)。
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.video import generator as gen
from app.services.video.agent import ScenePlan
from app.services.video.generator import GeneratorError, generate_from_script, get_missing_asset_scenes
from app.services.video.tts import SubtitleCue, TTSResult


# ---------- mock 工具 ----------

def _mock_plans():
    return [
        ScenePlan(index=1, narration="镜一口播", visual="画面一", asset_keyword="office work", duration=3),
        ScenePlan(index=2, narration="镜二口播", visual="画面二", asset_keyword="city night", duration=5),
    ]


def _mock_tts_result(path):
    return TTSResult(
        audio_path=Path(path),
        cues=[SubtitleCue(0.0, 3.0, "镜一口播"), SubtitleCue(3.0, 8.0, "镜二口播")],
        voice="zh-CN-XiaoxiaoNeural",
        duration=8.0,
    )


# ---------- 正常流程 ----------

class TestGenerateFromScript:
    def test_empty_script_raises(self):
        with pytest.raises(GeneratorError, match="video_script 为空"):
            generate_from_script([], task_id=1)

    def test_full_pipeline_success(self, tmp_path, monkeypatch):
        """正常端到端:脚本 -> plan -> 找到素材 -> TTS -> 渲染。"""
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        plans = _mock_plans()

        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=plans) as mock_plan, \
             patch.object(gen.assets_mod, "find_or_fallback", return_value=tmp_path / "asset.mp4") as mock_find, \
             patch.object(gen.tts_mod, "synthesize", return_value=_mock_tts_result(tmp_path / "tts.mp3")) as mock_tts, \
             patch.object(gen.render_mod, "render_video", return_value=tmp_path / "1" / "video.mp4") as mock_render:
            # 让 asset 文件存在(find_or_fallback mock 返回路径)
            (tmp_path / "asset.mp4").write_bytes(b"x")

            video_script = [
                {"index": 1, "narration": "镜一口播", "visual": "画面一", "duration": 3},
                {"index": 2, "narration": "镜二口播", "visual": "画面二", "duration": 5},
            ]
            out, ret_plans, ret_cues = generate_from_script(video_script, task_id=1)

        # 各子模块被调
        assert mock_plan.called
        assert mock_find.call_count == 2  # 两镜都找素材
        assert mock_tts.called
        assert mock_render.called
        # 返回值
        assert len(ret_plans) == 2
        assert ret_plans[0].asset_path is not None  # 找到了
        assert len(ret_cues) == 2
        # 中间产物落盘
        work = tmp_path / "1"
        assert (work / "scene_plan.json").exists()
        assert (work / "subtitles.json").exists()
        # scene_plan.json 内容正确
        sp = json.loads((work / "scene_plan.json").read_text(encoding="utf-8"))
        assert len(sp) == 2
        assert sp[0]["asset_keyword"] == "office work"

    def test_tts_called_with_concatenated_narration(self, tmp_path, monkeypatch):
        """TTS 收到的是所有镜口播拼接(保持顺序)。"""
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        plans = _mock_plans()
        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=plans), \
             patch.object(gen.assets_mod, "find_or_fallback", return_value=None), \
             patch.object(gen.tts_mod, "synthesize", return_value=_mock_tts_result(tmp_path / "tts.mp3")) as mock_tts, \
             patch.object(gen.render_mod, "render_video", return_value=tmp_path / "video.mp4"):
            generate_from_script(
                [{"narration": "镜一口播"}, {"narration": "镜二口播"}], task_id=1,
            )
            # TTS 第一参数是拼接的口播
            tts_text = mock_tts.call_args[0][0]
            assert "镜一口播" in tts_text
            assert "镜二口播" in tts_text


# ---------- 混合素材兜底 ----------

class TestHybridAssets:
    def test_missing_asset_kept_none(self, tmp_path, monkeypatch):
        """Pexels 找不到的镜,asset_path 保持 None(Remotion 兜底渲染)。"""
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        plans = _mock_plans()

        def fake_find(keyword, dest, **kw):
            # 第一镜找到,第二镜找不到
            if "office" in keyword:
                p = tmp_path / "asset1.mp4"
                p.write_bytes(b"x")
                return p
            return None

        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=plans), \
             patch.object(gen.assets_mod, "find_or_fallback", side_effect=fake_find), \
             patch.object(gen.tts_mod, "synthesize", return_value=_mock_tts_result(tmp_path / "tts.mp3")), \
             patch.object(gen.render_mod, "render_video", return_value=tmp_path / "video.mp4") as mock_render:
            out, ret_plans, _ = generate_from_script(
                [{"narration": "a"}, {"narration": "b"}], task_id=1,
            )

        assert ret_plans[0].asset_path is not None  # 找到
        assert ret_plans[1].asset_path is None       # 没找到,兜底
        # 渲染仍被调(用兜底 plan)
        assert mock_render.called
        render_plans = mock_render.call_args[0][0]
        assert render_plans[1].asset_path is None

    def test_empty_keyword_skips_search(self, tmp_path, monkeypatch):
        """空 asset_keyword 的镜不调 Pexels(直接兜底)。"""
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        plans = [ScenePlan(index=1, narration="n", visual="v", asset_keyword="", duration=3)]
        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=plans), \
             patch.object(gen.assets_mod, "find_or_fallback") as mock_find, \
             patch.object(gen.tts_mod, "synthesize", return_value=_mock_tts_result(tmp_path / "tts.mp3")), \
             patch.object(gen.render_mod, "render_video", return_value=tmp_path / "video.mp4"):
            generate_from_script([{"narration": "n"}], task_id=1)
            mock_find.assert_not_called()  # 空 keyword 不搜


# ---------- 失败处理 ----------

class TestFailures:
    def test_plan_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        with patch.object(gen.agent_mod, "plan_scenes_from_script",
                          side_effect=Exception("LLM 挂了")):
            with pytest.raises(GeneratorError, match="scene plan 生成失败"):
                generate_from_script([{"narration": "n"}], task_id=1)

    def test_tts_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=_mock_plans()), \
             patch.object(gen.assets_mod, "find_or_fallback", return_value=None), \
             patch.object(gen.tts_mod, "synthesize", side_effect=Exception("TTS 离线")):
            with pytest.raises(GeneratorError, match="TTS 配音失败"):
                generate_from_script([{"narration": "n"}], task_id=1)

    def test_render_failure_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gen, "OUTPUT_ROOT", tmp_path)
        with patch.object(gen.agent_mod, "plan_scenes_from_script", return_value=_mock_plans()), \
             patch.object(gen.assets_mod, "find_or_fallback", return_value=None), \
             patch.object(gen.tts_mod, "synthesize", return_value=_mock_tts_result(tmp_path / "tts.mp3")), \
             patch.object(gen.render_mod, "render_video", side_effect=Exception("Chrome 崩了")):
            with pytest.raises(GeneratorError, match="Remotion 渲染失败"):
                generate_from_script([{"narration": "n"}], task_id=1)


# ---------- get_missing_asset_scenes ----------

class TestGetMissingScenes:
    def test_all_found(self):
        plans = [
            ScenePlan(index=1, narration="a", visual="v", asset_path="x.mp4"),
            ScenePlan(index=2, narration="b", visual="v", asset_path="y.mp4"),
        ]
        assert get_missing_asset_scenes(plans) == []

    def test_some_missing(self):
        plans = [
            ScenePlan(index=1, narration="a", visual="v", asset_path="x.mp4"),
            ScenePlan(index=2, narration="b", visual="v", asset_path=None),
            ScenePlan(index=3, narration="c", visual="v", asset_path=None),
        ]
        assert get_missing_asset_scenes(plans) == [2, 3]
