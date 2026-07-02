"""LLM 客户端测试 - 全 mock,不真调 API(不烧钱、不依赖网络)。

验证:
    - chat() 正确构造 DeepSeek 请求并解析返回
    - vision() 正确构造 Anthropic 多模态请求
    - 异常时抛 LLMError 不裸露 SDK 错误
    - 配置从 settings 读,不硬编码
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def llm_client():
    """构造 LLMClient,内部两个 SDK client 独立 mock。"""
    with patch("app.llm.client.OpenAI") as mock_openai:
        # OpenAI 被调两次(DeepSeek + GLM),每次返回独立 mock 实例
        mock_ds = MagicMock()
        mock_glm = MagicMock()
        mock_openai.side_effect = [mock_ds, mock_glm]
        from app.llm.client import LLMClient
        real = LLMClient()
        assert real._ds is mock_ds
        assert real._glm is mock_glm
        yield real


class TestChat:
    """文本对话(DeepSeek)。"""

    def test_chat_returns_response_text(self, llm_client):
        """chat() 返回模型回复文本。"""
        # mock DeepSeek 返回
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="生成的文案内容"))]
        llm_client._ds.chat.completions.create.return_value = mock_resp

        result = llm_client.chat("写一条小红书文案")
        assert result == "生成的文案内容"
        # 验证调用参数
        call_kwargs = llm_client._ds.chat.completions.create.call_args
        assert call_kwargs.kwargs["messages"][-1] == {"role": "user", "content": "写一条小红书文案"}

    def test_chat_with_system_and_history(self, llm_client):
        """chat() 支持 system 提示词和历史对话。"""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="回复"))]
        llm_client._ds.chat.completions.create.return_value = mock_resp

        history = [{"role": "assistant", "content": "前一轮"}]
        llm_client.chat("继续", system="你是文案专家", history=history)

        call_kwargs = llm_client._ds.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert len(messages) == 2  # history + 当前
        assert messages[0] == {"role": "assistant", "content": "前一轮"}
        assert messages[1] == {"role": "user", "content": "继续"}

    def test_chat_raises_llm_error_on_failure(self, llm_client):
        """SDK 异常时抛 LLMError,不裸露 SDK 错误。"""
        llm_client._ds.chat.completions.create.side_effect = Exception("网络错误")
        from app.llm.client import LLMError
        with pytest.raises(LLMError, match="DeepSeek"):
            llm_client.chat("测试")

    def test_chat_returns_empty_string_on_none_content(self, llm_client):
        """返回 content 为 None 时返回空字符串。"""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=None))]
        llm_client._ds.chat.completions.create.return_value = mock_resp
        assert llm_client.chat("test") == ""


class TestVision:
    """视觉理解(GLM)。"""

    def test_vision_requires_frames(self, llm_client):
        """无图片时报错。"""
        from app.llm.client import LLMError
        with pytest.raises(LLMError, match="至少一张"):
            llm_client.vision([], "描述")

    def test_vision_constructs_openai_format(self, llm_client, tmp_path):
        """vision() 构造 OpenAI 多模态 content 格式(image_url base64)。"""
        # 建一张测试图
        img = tmp_path / "frame.png"
        img.write_bytes(b"\x89PNG fake image data")

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="画面里有个人在讲话"))]
        llm_client._glm.chat.completions.create.return_value = mock_resp

        result = llm_client.vision([img], "描述画面")
        assert result == "画面里有个人在讲话"

        # 验证 OpenAI content 格式
        call_kwargs = llm_client._glm.chat.completions.create.call_args
        content = call_kwargs.kwargs["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "描述画面"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_vision_raises_llm_error_on_failure(self, llm_client, tmp_path):
        """SDK 异常时抛 LLMError。"""
        img = tmp_path / "f.png"
        img.write_bytes(b"fake")
        llm_client._glm.chat.completions.create.side_effect = Exception("GLM 挂了")
        from app.llm.client import LLMError
        with pytest.raises(LLMError, match="GLM"):
            llm_client.vision([img], "test")


class TestConfig:
    """配置从 settings 读,不硬编码。"""

    def test_keys_from_settings_not_hardcoded(self):
        """API key 从 config 读,代码里无硬编码 key。"""
        from app.llm import client as client_mod
        import inspect
        src = inspect.getsource(client_mod)
        # 不应出现真实 key 格式(sk-xxx / ark-xxx 开头的具体值)
        assert "sk-c463" not in src, "代码里残留硬编码 DeepSeek key"
        assert "ark-32d4" not in src, "代码里残留硬编码 GLM key"
