"""属性测试：未授权工具调用和错误响应格式。

# Feature: v3-post-refactor-cleanup, Property 1: 未授权工具调用抛出 ToolNotAllowedError
# Feature: v3-post-refactor-cleanup, Property 2: 未授权工具错误响应格式正确

使用 hypothesis 生成随机工具名和 scope，验证：
- ToolRegistry.call_tool() 在工具不在 scope 中时抛出 ToolNotAllowedError
- AgentEngine._execute_tool_call() 返回 success=False 且 error 包含正确 JSON 结构

**Validates: Requirements 2.2, 2.3, 2.4**
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from excelmanus.tools.registry import (
    ToolDef,
    ToolNotAllowedError,
    ToolRegistry,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ToolCallResult


# ── 辅助策略 ──────────────────────────────────────────────

# 生成合法的工具名称（小写字母开头，包含字母数字下划线）
tool_name_st = st.from_regex(r"[a-z][a-z0-9_]{2,20}", fullmatch=True)

# 生成非空的 tool_scope 列表（每个元素都是合法工具名）
tool_scope_st = st.lists(
    tool_name_st,
    min_size=1,
    max_size=10,
    unique=True,
)


# ── 辅助工厂 ──────────────────────────────────────────────


def _make_config(**overrides) -> ExcelManusConfig:
    """创建测试用配置。"""
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "max_consecutive_failures": 3,
        "workspace_root": ".",
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_tool_def(name: str) -> ToolDef:
    """创建一个简单的测试工具定义。"""
    return ToolDef(
        name=name,
        description=f"测试工具 {name}",
        input_schema={"type": "object", "properties": {}},
        func=lambda: "ok",
    )


def _make_tool_call_object(tool_name: str, arguments: str = "{}") -> SimpleNamespace:
    """模拟 OpenAI tool_call 对象。"""
    return SimpleNamespace(
        id=f"call_test_{tool_name}",
        function=SimpleNamespace(name=tool_name, arguments=arguments),
    )


# ---------------------------------------------------------------------------
# Property 1：未授权工具调用抛出 ToolNotAllowedError
# Feature: v3-post-refactor-cleanup, Property 1: 未授权工具调用抛出 ToolNotAllowedError
# **Validates: Requirements 2.2**
# ---------------------------------------------------------------------------


@given(
    tool_name=tool_name_st,
    tool_scope=tool_scope_st,
)
@settings(max_examples=100)
def test_property_1_unauthorized_tool_raises_not_allowed_error(
    tool_name: str,
    tool_scope: list[str],
) -> None:
    """Property 1：未授权工具调用抛出 ToolNotAllowedError。

    对于任意工具名 tool_name 和授权范围 tool_scope，
    如果 tool_name 不在 tool_scope 中，
    调用 ToolRegistry.call_tool(tool_name, {}, tool_scope=tool_scope) 应抛出 ToolNotAllowedError。

    **Validates: Requirements 2.2**
    """
    # 确保 tool_name 不在 tool_scope 中
    assume(tool_name not in tool_scope)

    # 注册工具到 registry（确保工具存在，排除 ToolNotFoundError 干扰）
    registry = ToolRegistry()
    registry.register_tool(_make_tool_def(tool_name))

    # 调用时传入不包含该工具的 scope，应抛出 ToolNotAllowedError
    with pytest.raises(ToolNotAllowedError):
        registry.call_tool(tool_name, {}, tool_scope=tool_scope)


# ---------------------------------------------------------------------------
# Property 2：未授权工具错误响应格式正确
# Feature: v3-post-refactor-cleanup, Property 2: 未授权工具错误响应格式正确
# **Validates: Requirements 2.3, 2.4**
# ---------------------------------------------------------------------------


@given(
    tool_name=tool_name_st,
    tool_scope=tool_scope_st,
)
@settings(max_examples=100)
@pytest.mark.asyncio
async def test_property_2_unauthorized_tool_error_response_format(
    tool_name: str,
    tool_scope: list[str],
) -> None:
    """Property 2：未授权工具错误响应格式正确。

    对于任意未授权的工具调用，_execute_tool_call() 返回的 ToolCallResult 应满足：
    - success == False
    - error 字段包含 JSON 字符串
    - JSON 中包含 error_code、tool、allowed_tools、message 四个键

    **Validates: Requirements 2.3, 2.4**
    """
    # 确保 tool_name 不在 tool_scope 中
    assume(tool_name not in tool_scope)

    # 创建真实的 ToolRegistry 并注册工具
    from excelmanus.tools.registry import ToolRegistry as RealToolRegistry

    real_registry = RealToolRegistry()
    real_registry.register_tool(_make_tool_def(tool_name))

    # 创建 AgentEngine（mock LLM 客户端和路由器，只测试 _execute_tool_call）
    config = _make_config()
    with patch("openai.AsyncOpenAI"):
        engine = AgentEngine(config=config, registry=real_registry)

    # 构造 tool_call 对象
    tc = _make_tool_call_object(tool_name)

    # 调用 _execute_tool_call，传入不包含该工具的 scope
    result: ToolCallResult = await engine._execute_tool_call(
        tc=tc,
        tool_scope=tool_scope,
        on_event=None,
        iteration=1,
    )

    # 验证 success == False
    assert result.success is False, (
        f"未授权工具调用应返回 success=False，实际: {result.success}"
    )

    # 验证 error 字段非空
    assert result.error is not None, "未授权工具调用的 error 字段不应为 None"

    # 验证 error 是合法 JSON
    try:
        error_data = json.loads(result.error)
    except (json.JSONDecodeError, TypeError) as exc:
        pytest.fail(f"error 字段不是合法 JSON: {result.error!r}, 异常: {exc}")

    # 验证 JSON 包含必需的四个键
    required_keys = {"error_code", "tool", "allowed_tools", "message"}
    missing_keys = required_keys - set(error_data.keys())
    assert not missing_keys, (
        f"错误响应 JSON 缺少必需键: {missing_keys}，实际键: {set(error_data.keys())}"
    )

    # 验证 error_code 值
    assert error_data["error_code"] == "TOOL_NOT_ALLOWED", (
        f"error_code 应为 'TOOL_NOT_ALLOWED'，实际: {error_data['error_code']!r}"
    )

    # 验证 tool 值与请求的工具名一致
    assert error_data["tool"] == tool_name, (
        f"tool 应为 {tool_name!r}，实际: {error_data['tool']!r}"
    )

    # 验证 allowed_tools 与传入的 tool_scope 一致
    assert error_data["allowed_tools"] == list(tool_scope), (
        f"allowed_tools 应为 {tool_scope!r}，实际: {error_data['allowed_tools']!r}"
    )
