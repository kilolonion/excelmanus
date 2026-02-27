"""SubagentResult token 用量追踪测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.subagent.models import SubagentConfig, SubagentResult


class TestSubagentResultTokenFields:
    """SubagentResult 数据类包含 token 字段。"""

    def test_default_zero(self):
        r = SubagentResult(
            success=True,
            summary="ok",
            subagent_name="sub",
            permission_mode="default",
            conversation_id="c1",
        )
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0

    def test_explicit_values(self):
        r = SubagentResult(
            success=True,
            summary="ok",
            subagent_name="sub",
            permission_mode="default",
            conversation_id="c1",
            prompt_tokens=1234,
            completion_tokens=567,
        )
        assert r.prompt_tokens == 1234
        assert r.completion_tokens == 567


class TestExecutorTokenAccumulation:
    """SubagentExecutor.run() 累积 response.usage token。"""

    @pytest.fixture
    def _mock_config(self):
        return SubagentConfig(
            name="test_sub",
            description="test",
            max_iterations=3,
            max_consecutive_failures=2,
        )

    @pytest.fixture
    def _parent_config(self):
        cfg = MagicMock()
        cfg.api_key = "sk-test"
        cfg.base_url = "http://localhost"
        cfg.model = "test-model"
        cfg.aux_model = None
        cfg.workspace_root = "/tmp/test"
        cfg.max_memory_messages = 20
        cfg.max_memory_tokens = 8000
        cfg.embedding_model = None
        cfg.embedding_dimensions = None
        cfg.embedding_api_key = None
        cfg.embedding_base_url = None
        return cfg

    @pytest.mark.asyncio
    async def test_accumulates_usage_across_iterations(self, _mock_config, _parent_config):
        """多轮 LLM 调用的 token 应被累积。"""
        from excelmanus.subagent.executor import SubagentExecutor

        # 构建两轮 response：第一轮有 tool_call，第二轮纯文本结束
        usage_1 = SimpleNamespace(prompt_tokens=100, completion_tokens=50)
        usage_2 = SimpleNamespace(prompt_tokens=200, completion_tokens=80)

        tc_func = SimpleNamespace(name="read_excel", arguments='{"sheet": "Sheet1"}')
        tc = SimpleNamespace(id="tc1", function=tc_func)
        msg_with_tools = SimpleNamespace(content="", tool_calls=[tc])
        msg_text_only = SimpleNamespace(content="done", tool_calls=None)

        resp_1 = SimpleNamespace(
            choices=[SimpleNamespace(message=msg_with_tools)],
            usage=usage_1,
        )
        resp_2 = SimpleNamespace(
            choices=[SimpleNamespace(message=msg_text_only)],
            usage=usage_2,
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[resp_1, resp_2])
        mock_client.close = AsyncMock()

        registry = MagicMock()
        registry.get_tool_names.return_value = ["read_excel"]
        registry.get_openai_schemas.return_value = [{"type": "function", "function": {"name": "read_excel"}}]
        registry.call_tool.return_value = '{"data": "ok"}'
        registry.is_tool_available.return_value = True
        registry.get_tool.return_value = None

        approval = MagicMock()
        approval.is_read_only_safe_tool.return_value = True
        approval.is_confirm_required_tool.return_value = False
        approval.is_audit_only_tool.return_value = False
        approval.is_mcp_tool.return_value = False

        executor = SubagentExecutor(
            parent_config=_parent_config,
            parent_registry=registry,
            approval_manager=approval,
        )

        with patch("excelmanus.subagent.executor.create_client", return_value=mock_client):
            with patch("excelmanus.subagent.executor.FilteredToolRegistry", return_value=registry):
                result = await executor.run(
                    config=_mock_config,
                    prompt="test task",
                )

        assert result.prompt_tokens == 300  # 100 + 200
        assert result.completion_tokens == 130  # 50 + 80

    @pytest.mark.asyncio
    async def test_handles_missing_usage(self, _mock_config, _parent_config):
        """response.usage 为 None 时不崩溃，token 保持 0。"""
        from excelmanus.subagent.executor import SubagentExecutor

        msg = SimpleNamespace(content="done", tool_calls=None)
        resp = SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=None,
        )

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=resp)
        mock_client.close = AsyncMock()

        registry = MagicMock()
        registry.get_tool_names.return_value = []
        registry.get_openai_schemas.return_value = []

        approval = MagicMock()

        executor = SubagentExecutor(
            parent_config=_parent_config,
            parent_registry=registry,
            approval_manager=approval,
        )

        with patch("excelmanus.subagent.executor.create_client", return_value=mock_client):
            with patch("excelmanus.subagent.executor.FilteredToolRegistry", return_value=registry):
                result = await executor.run(
                    config=_mock_config,
                    prompt="test task",
                )

        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
