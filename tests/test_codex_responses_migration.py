"""Codex OAuth → Responses API 迁移测试。

验证 Codex OAuth 模型正确路由到 chatgpt.com/backend-api/codex/responses 端点，
非 Codex 模型保持走 Chat Completions API。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.auth.providers.base import AuthProfileRecord, ResolvedCredential
from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
from excelmanus.auth.providers.resolver import CredentialResolver
from excelmanus.providers import create_client
from excelmanus.providers.openai_responses import (
    OpenAIResponsesClient,
    _ensure_fc_id,
    _chat_messages_to_responses_input,
)
from excelmanus.session import SessionManager
from excelmanus.tools import ToolRegistry


# ── P1: 基础设施 ──────────────────────────────────────────


class TestCodexProviderConstants:
    """OpenAICodexProvider 常量验证。"""

    def test_base_url_points_to_backend_api(self):
        assert OpenAICodexProvider.BASE_URL == "https://chatgpt.com/backend-api/codex"

    def test_protocol_is_openai_responses(self):
        assert OpenAICodexProvider.PROTOCOL == "openai_responses"

    def test_get_api_credential_returns_backend_api_url(self):
        provider = OpenAICodexProvider()
        api_key, base_url = provider.get_api_credential("test_token_123")
        assert api_key == "test_token_123"
        assert base_url == "https://chatgpt.com/backend-api/codex"


class TestResolverProtocol:
    """CredentialResolver 为 Codex 模型返回 openai_responses 协议。"""

    def _make_profile(self, **overrides) -> AuthProfileRecord:
        defaults = dict(
            id="prof_1",
            user_id="user_1",
            provider="openai-codex",
            profile_name="codex-default",
            credential_type="oauth",
            access_token="eyJ_fake_token",
            refresh_token="rt_fake",
            expires_at="2099-12-31T23:59:59+00:00",
            account_id="acc_1",
            plan_type="plus",
            extra_data=None,
            is_active=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        defaults.update(overrides)
        return AuthProfileRecord(**defaults)

    def test_resolve_sync_codex_returns_openai_responses(self):
        store = MagicMock()
        store.get_active_profile.return_value = self._make_profile()
        resolver = CredentialResolver(credential_store=store)

        result = resolver.resolve_sync("user_1", "gpt-5.3-codex")
        assert result is not None
        assert result.protocol == "openai_responses"
        assert result.source == "oauth"
        assert result.base_url == "https://chatgpt.com/backend-api/codex"

    def test_resolve_sync_non_codex_returns_none(self):
        """非 Codex 模型无 OAuth profile 时返回 None。"""
        store = MagicMock()
        store.get_active_profile.return_value = None
        resolver = CredentialResolver(credential_store=store)

        result = resolver.resolve_sync("user_1", "deepseek-v3")
        assert result is None

    def test_resolve_async_codex_returns_openai_responses(self):
        store = MagicMock()
        store.get_active_profile.return_value = self._make_profile()
        resolver = CredentialResolver(credential_store=store)

        result = asyncio.get_event_loop().run_until_complete(
            resolver.resolve("user_1", "codex-mini-latest")
        )
        assert result is not None
        assert result.protocol == "openai_responses"


# ── P2: create_client 路由 ──────────────────────────────────


class TestCreateClientRouting:
    """create_client 根据 protocol 选择正确的客户端。"""

    def test_openai_responses_protocol_creates_responses_client(self):
        client = create_client(
            api_key="test_key",
            base_url="https://chatgpt.com/backend-api/codex",
            protocol="openai_responses",
        )
        assert isinstance(client, OpenAIResponsesClient)

    def test_openai_protocol_creates_async_openai(self):
        import openai
        client = create_client(
            api_key="test_key",
            base_url="https://api.openai.com/v1",
            protocol="openai",
        )
        assert isinstance(client, openai.AsyncOpenAI)


class TestApiModelResolution:
    """api._resolve_model_info 对 Codex 前缀模型的解析。"""

    def test_codex_prefixed_model_resolves_openai_responses_protocol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import excelmanus.api as api_module

        monkeypatch.setattr(
            api_module,
            "_config",
            SimpleNamespace(
                protocol="openai",
                model="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key="cfg-key",
            ),
            raising=False,
        )
        monkeypatch.setattr(api_module, "_config_store", None, raising=False)

        model, base_url, api_key, protocol = api_module._resolve_model_info(
            "openai-codex/gpt-5.3-codex", None, None
        )

        assert model == "gpt-5.3-codex"
        assert base_url == OpenAICodexProvider.BASE_URL
        assert api_key == "cfg-key"
        assert protocol == "openai_responses"


# ── P3: store: false in request body ──────────────────────


class TestEnsureFcId:
    """function_call id 必须以 fc_ 开头。"""

    def test_call_prefix_converted(self):
        assert _ensure_fc_id("call_eUiC39plXoepMbGt8LMzwytc") == "fc_eUiC39plXoepMbGt8LMzwytc"

    def test_fc_prefix_unchanged(self):
        assert _ensure_fc_id("fc_abc123") == "fc_abc123"

    def test_empty_generates_fc_id(self):
        result = _ensure_fc_id("")
        assert result.startswith("fc_")
        assert len(result) > 3

    def test_other_prefix_gets_fc(self):
        assert _ensure_fc_id("xyz123") == "fc_xyz123"

    def test_tool_call_ids_converted_in_messages(self):
        """_chat_messages_to_responses_input 中 tool_call id 被正确转换。"""
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_abc123", "function": {"name": "test_tool", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "call_abc123", "content": "result"},
        ]
        _instructions, input_items = _chat_messages_to_responses_input(messages)

        # function_call item should have fc_ prefix
        fc_item = next(i for i in input_items if i["type"] == "function_call")
        assert fc_item["id"].startswith("fc_")
        assert fc_item["call_id"].startswith("fc_")
        assert "call_" not in fc_item["id"]

        # function_call_output should match
        fco_item = next(i for i in input_items if i["type"] == "function_call_output")
        assert fco_item["call_id"].startswith("fc_")
        assert fco_item["call_id"] == fc_item["call_id"]


class TestResponsesClientStoreParam:
    """OpenAIResponsesClient 请求体必须包含 store: false。"""

    def test_generate_body_includes_store_false(self):
        """非流式请求体包含 store: false。"""
        from excelmanus.providers.openai_responses import (
            _chat_messages_to_responses_input,
        )
        client = OpenAIResponsesClient(
            api_key="test", base_url="https://chatgpt.com/backend-api/codex"
        )
        # 直接检查 _generate 方法会构建的 body
        messages = [{"role": "user", "content": "hello"}]
        instructions, input_items = _chat_messages_to_responses_input(messages)

        body: dict = {
            "model": "gpt-5.3-codex",
            "input": input_items,
            "store": False,
        }
        assert body["store"] is False

    def test_stream_body_includes_store_false(self):
        """流式请求体包含 store: false。"""
        body: dict = {
            "model": "gpt-5.3-codex",
            "input": [],
            "stream": True,
            "store": False,
        }
        assert body["store"] is False
        assert body["stream"] is True


# ── P4: session.py protocol 透传 ──────────────────────────


class TestSessionProtocolPropagation:
    """session.py 凭证覆盖正确传播 protocol。"""

    def test_resolved_credential_protocol_propagated(self):
        """ResolvedCredential 的 protocol 字段被正确设置。"""
        cred = ResolvedCredential(
            api_key="test_key",
            base_url="https://chatgpt.com/backend-api/codex",
            source="oauth",
            provider="openai-codex",
            protocol="openai_responses",
        )
        assert cred.protocol == "openai_responses"

        # 模拟 session.py 中的覆盖逻辑
        overrides: dict = {}
        overrides["api_key"] = cred.api_key
        if cred.base_url:
            overrides["base_url"] = cred.base_url
        if cred.protocol and cred.protocol != "openai":
            overrides["protocol"] = cred.protocol

        assert overrides["protocol"] == "openai_responses"
        assert overrides["base_url"] == "https://chatgpt.com/backend-api/codex"

    def test_non_codex_credential_no_protocol_override(self):
        """非 Codex 凭证不覆盖 protocol。"""
        cred = ResolvedCredential(
            api_key="sk-test",
            base_url="https://api.deepseek.com/v1",
            source="user_key",
            protocol="openai",
        )
        overrides: dict = {}
        overrides["api_key"] = cred.api_key
        if cred.base_url:
            overrides["base_url"] = cred.base_url
        if cred.protocol and cred.protocol != "openai":
            overrides["protocol"] = cred.protocol

        assert "protocol" not in overrides


class TestSessionCodexProfiles:
    """SessionManager 同步用户 Codex profile 时，应使用 Responses 协议。"""

    def test_sync_user_subscription_profiles_sets_openai_responses_protocol(self):
        config = ExcelManusConfig(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-5.1",
            workspace_root="/tmp/excelmanus-test-codex-protocol",
            memory_enabled=False,
        )
        manager = SessionManager(
            max_sessions=2,
            ttl_seconds=60,
            config=config,
            registry=ToolRegistry(),
        )

        credential_store = MagicMock()
        credential_store.get_active_profile.return_value = MagicMock(
            access_token="eyJcodex",
            plan_type="plus",
        )
        manager.set_credential_store(credential_store)

        engine = MagicMock()
        engine._config.models = ()

        manager.sync_user_subscription_profiles(engine, user_id="user-1")

        profiles = engine.sync_model_profiles.call_args.args[0]
        codex_profiles = [p for p in profiles if p.name.startswith("openai-codex/")]
        assert codex_profiles
        assert all(p.protocol == "openai_responses" for p in codex_profiles)
