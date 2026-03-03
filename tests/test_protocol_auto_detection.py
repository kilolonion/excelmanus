"""协议自动检测多信号推断测试。

覆盖：
  - _infer_protocol_from_model：模型名称前缀 → 协议推断
  - _infer_protocol_from_api_key：API Key 前缀 → 协议推断
  - _normalize_base_url：多信号推断（URL + 模型名 + API Key）
  - create_client：auto 模式下的模型名推断
"""

from __future__ import annotations

import pytest

from excelmanus.config import (
    _infer_protocol_from_model,
    _infer_protocol_from_api_key,
    _normalize_base_url,
)


# ══════════════════════════════════════════════════════════
# _infer_protocol_from_model
# ══════════════════════════════════════════════════════════


class TestInferProtocolFromModel:
    """模型名称 → 协议推断。"""

    @pytest.mark.parametrize("model,expected", [
        # Anthropic 系列
        ("claude-3-opus-20240229", "anthropic"),
        ("claude-3-5-sonnet-latest", "anthropic"),
        ("claude-3.5-haiku-20241022", "anthropic"),
        ("claude-3-7-sonnet-20250219", "anthropic"),
        ("claude-4-sonnet-20250514", "anthropic"),
        ("claude-sonnet-4-20250514", "anthropic"),
        ("Claude-3-Opus", "anthropic"),  # 大小写不敏感
        ("claude3-opus", "anthropic"),
        ("claude4-sonnet", "anthropic"),
        ("claude_custom_fine_tuned", "anthropic"),
        # Gemini 系列
        ("gemini-2.5-pro-preview-05-06", "gemini"),
        ("gemini-2.0-flash", "gemini"),
        ("gemini-1.5-pro-002", "gemini"),
        ("Gemini-2.5-Flash", "gemini"),  # 大小写不敏感
        ("gemini2.5-pro", "gemini"),
        ("gemini1.5-flash", "gemini"),
        ("gemini_custom", "gemini"),
    ])
    def test_known_model_prefixes(self, model: str, expected: str) -> None:
        assert _infer_protocol_from_model(model) == expected

    @pytest.mark.parametrize("model", [
        # OpenAI 系列 — 不应推断
        "gpt-4o",
        "gpt-4-turbo",
        "o1-mini",
        "o3-pro",
        # 其他国产模型 — 不应推断
        "qwen-max",
        "deepseek-chat",
        "glm-4",
        "moonshot-v1-128k",
        # 空值 / 边界
        "",
        "   ",
    ])
    def test_unknown_model_returns_none(self, model: str) -> None:
        assert _infer_protocol_from_model(model) is None

    def test_none_input(self) -> None:
        assert _infer_protocol_from_model(None) is None  # type: ignore[arg-type]

    def test_whitespace_stripped(self) -> None:
        assert _infer_protocol_from_model("  claude-3-opus  ") == "anthropic"
        assert _infer_protocol_from_model("  gemini-2.0-flash  ") == "gemini"


# ══════════════════════════════════════════════════════════
# _infer_protocol_from_api_key
# ══════════════════════════════════════════════════════════


class TestInferProtocolFromApiKey:
    """API Key 前缀 → 协议推断。"""

    def test_anthropic_key_prefix(self) -> None:
        assert _infer_protocol_from_api_key("sk-ant-api03-xxxxxxxxxxxx") == "anthropic"

    def test_anthropic_key_short(self) -> None:
        assert _infer_protocol_from_api_key("sk-ant-") == "anthropic"

    @pytest.mark.parametrize("key", [
        "sk-xxxxxxxxxxxx",       # OpenAI 风格
        "AIzaSy-xxxxxxxxxxxx",   # Gemini 风格
        "some-random-key",
        "",
    ])
    def test_non_anthropic_returns_none(self, key: str) -> None:
        assert _infer_protocol_from_api_key(key) is None

    def test_none_input(self) -> None:
        assert _infer_protocol_from_api_key(None) is None  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════
# _normalize_base_url 多信号推断
# ══════════════════════════════════════════════════════════


class TestNormalizeBaseUrlMultiSignal:
    """多信号自动推断：URL 特征 + 模型名 + API Key。"""

    # ── 用户场景：自定义代理 + claude 模型 ──

    def test_proxy_url_with_claude_model_no_v1_append(self) -> None:
        """用户场景复现：自定义代理 + claude 模型，不应追加 /v1。"""
        result = _normalize_base_url(
            "http://ai.tachira.cn/api",
            protocol="auto",
            model="claude-3-5-sonnet-latest",
        )
        assert result == "http://ai.tachira.cn/api"
        assert not result.endswith("/v1")

    def test_proxy_url_with_gemini_model_no_v1_append(self) -> None:
        """自定义代理 + gemini 模型，不应追加 /v1。"""
        result = _normalize_base_url(
            "https://my-proxy.com/api",
            protocol="auto",
            model="gemini-2.5-pro-preview-05-06",
        )
        assert result == "https://my-proxy.com/api"

    def test_proxy_url_with_anthropic_api_key_no_v1_append(self) -> None:
        """自定义代理 + Anthropic API Key，不应追加 /v1。"""
        result = _normalize_base_url(
            "https://custom-proxy.example.com/api",
            protocol="auto",
            api_key="sk-ant-api03-xxxxxxxxxxxx",
        )
        assert result == "https://custom-proxy.example.com/api"

    def test_model_signal_takes_priority_over_api_key(self) -> None:
        """模型名优先于 API Key（模型名信号更可靠）。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="auto",
            model="claude-3-5-sonnet",
            api_key="sk-xxxx",  # 非 Anthropic key，但模型名是 claude
        )
        assert result == "https://proxy.example.com/api"

    # ── OpenAI 模型仍然正常追加 /v1 ──

    def test_proxy_url_with_openai_model_appends_v1(self) -> None:
        """OpenAI 模型 + 自定义代理，应正常追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="auto",
            model="gpt-4o",
        )
        assert result == "https://proxy.example.com/api/v1"

    def test_proxy_url_with_qwen_model_appends_v1(self) -> None:
        """Qwen 模型 + 自定义代理，应正常追加 /v1。"""
        result = _normalize_base_url(
            "https://dashscope.aliyuncs.com/compatible-mode",
            protocol="auto",
            model="qwen-max",
        )
        assert result == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_unknown_model_appends_v1(self) -> None:
        """未知模型 + 自定义代理，默认追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="auto",
            model="some-custom-model",
        )
        assert result == "https://proxy.example.com/api/v1"

    # ── 显式协议覆盖推断 ──

    def test_explicit_anthropic_protocol_no_v1(self) -> None:
        """显式 anthropic 协议，不管模型名，不追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="anthropic",
            model="gpt-4o",  # 模型名和协议不一致也尊重显式设置
        )
        assert result == "https://proxy.example.com/api"

    def test_explicit_openai_protocol_appends_v1(self) -> None:
        """显式 openai 协议，即使模型名是 claude 也追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="openai",
            model="claude-3-5-sonnet",  # 显式 openai 覆盖模型名推断
        )
        assert result == "https://proxy.example.com/api/v1"

    def test_explicit_gemini_protocol_no_v1(self) -> None:
        """显式 gemini 协议，不追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="gemini",
        )
        assert result == "https://proxy.example.com/api"

    # ── URL 特征匹配仍然生效 ──

    def test_anthropic_url_still_detected(self) -> None:
        """Anthropic 官方 URL 不管其他信号，都不追加 /v1。"""
        result = _normalize_base_url(
            "https://api.anthropic.com",
            protocol="auto",
        )
        assert result == "https://api.anthropic.com"

    def test_gemini_url_still_detected(self) -> None:
        """Gemini 官方 URL 不管其他信号，都不追加 /v1。"""
        result = _normalize_base_url(
            "https://generativelanguage.googleapis.com",
            protocol="auto",
        )
        assert result == "https://generativelanguage.googleapis.com"

    # ── 已有 /v1 的 URL 不重复追加 ──

    def test_already_has_v1_no_double_append(self) -> None:
        """已有 /v1 的 URL，不重复追加。"""
        result = _normalize_base_url(
            "https://api.openai.com/v1",
            protocol="auto",
            model="gpt-4o",
        )
        assert result == "https://api.openai.com/v1"

    # ── 尾部斜杠去除 ──

    def test_trailing_slash_stripped(self) -> None:
        """尾部斜杠被去除。"""
        result = _normalize_base_url(
            "https://api.anthropic.com/",
            protocol="auto",
        )
        assert result == "https://api.anthropic.com"

    # ── 空模型/空 key 不影响原有行为 ──

    def test_empty_model_empty_key_appends_v1(self) -> None:
        """无模型无 key 信号时，回退到旧行为追加 /v1。"""
        result = _normalize_base_url(
            "https://proxy.example.com/api",
            protocol="auto",
            model="",
            api_key="",
        )
        assert result == "https://proxy.example.com/api/v1"


# ══════════════════════════════════════════════════════════
# create_client 模型推断
# ══════════════════════════════════════════════════════════


class TestCreateClientModelInference:
    """create_client auto 模式下的模型名推断。"""

    def test_auto_claude_model_returns_claude_client(self) -> None:
        """auto + claude 模型名 → ClaudeClient。"""
        from excelmanus.providers import create_client
        from excelmanus.providers.claude import ClaudeClient
        client = create_client(
            api_key="test-key",
            base_url="https://custom-proxy.example.com/api",
            protocol="auto",
            model="claude-3-5-sonnet-latest",
        )
        assert isinstance(client, ClaudeClient)

    def test_auto_gemini_model_returns_gemini_client(self) -> None:
        """auto + gemini 模型名 → GeminiClient。"""
        from excelmanus.providers import create_client
        from excelmanus.providers.gemini import GeminiClient
        client = create_client(
            api_key="test-key",
            base_url="https://custom-proxy.example.com/api",
            protocol="auto",
            model="gemini-2.5-pro",
        )
        assert isinstance(client, GeminiClient)

    def test_auto_openai_model_returns_openai_client(self) -> None:
        """auto + OpenAI 模型名 → AsyncOpenAI。"""
        import openai
        from excelmanus.providers import create_client
        client = create_client(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            protocol="auto",
            model="gpt-4o",
        )
        assert isinstance(client, openai.AsyncOpenAI)

    def test_auto_no_model_returns_openai_client(self) -> None:
        """auto + 无模型名 → 默认 AsyncOpenAI。"""
        import openai
        from excelmanus.providers import create_client
        client = create_client(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            protocol="auto",
        )
        assert isinstance(client, openai.AsyncOpenAI)

    def test_explicit_anthropic_protocol_ignores_model(self) -> None:
        """显式 anthropic 协议 + OpenAI 模型名 → 仍用 ClaudeClient。"""
        from excelmanus.providers import create_client
        from excelmanus.providers.claude import ClaudeClient
        client = create_client(
            api_key="test-key",
            base_url="https://custom-proxy.example.com/api",
            protocol="anthropic",
            model="gpt-4o",
        )
        assert isinstance(client, ClaudeClient)

    def test_url_detection_takes_priority_over_model(self) -> None:
        """URL 特征匹配优先于模型名推断（向后兼容）。"""
        from excelmanus.providers import create_client
        from excelmanus.providers.claude import ClaudeClient
        client = create_client(
            api_key="test-key",
            base_url="https://api.anthropic.com",
            protocol="auto",
            model="some-unknown-model",
        )
        assert isinstance(client, ClaudeClient)
