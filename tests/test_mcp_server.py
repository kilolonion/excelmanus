"""MCP Server 测试：Property 9、10、11 属性测试 + 单元测试。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st

from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    ListToolsRequest,
)

from excelmanus.skills import (
    SkillRegistry,
    ToolDef,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.mcp_server import (
    _init_skill_guards,
    _run_stdio_server_async,
    create_mcp_server,
)


# ── 辅助函数 ──────────────────────────────────────────────


def _make_tool(
    name: str,
    description: str = "测试工具",
    func=None,
    input_schema: dict | None = None,
) -> ToolDef:
    """创建测试用 ToolDef。"""
    return ToolDef(
        name=name,
        description=description,
        input_schema=input_schema or {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
        func=func or (lambda x="": f"result:{x}"),
    )


def _make_registry(*tool_names: str) -> SkillRegistry:
    """创建包含指定工具名的 SkillRegistry。"""
    registry = SkillRegistry()
    if tool_names:
        tools = [_make_tool(name) for name in tool_names]
        registry.register("test_skill", "测试 Skill", tools)
    return registry


async def _list_tools(server) -> list:
    """调用 MCP Server 的 list_tools handler。"""
    handler = server.request_handlers[ListToolsRequest]
    result = await handler(ListToolsRequest(method="tools/list"))
    return result.root.tools


async def _call_tool(server, name: str, arguments: dict):
    """调用 MCP Server 的 call_tool handler，返回 CallToolResult。"""
    handler = server.request_handlers[CallToolRequest]
    result = await handler(
        CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
    )
    return result.root


# ── 策略定义 ──────────────────────────────────────────────

# 生成合法的工具名称（非空 ASCII 字母 + 下划线，避免冲突）
_tool_name_char = st.characters(whitelist_categories=("Ll",), whitelist_characters="_")
_tool_name_st = st.text(alphabet=_tool_name_char, min_size=1, max_size=20)

# 生成工具参数值（字符串类型）
_arg_value_st = st.text(min_size=0, max_size=100)


# ── Property 9：MCP 工具映射数量一致 ─────────────────────


class TestProperty9MCPToolMapping:
    """Property 9：MCP 暴露工具数量必须等于 Registry 工具数量，且名称一致。

    **Validates: Requirements 3.1**
    """

    @settings(max_examples=100)
    @given(tool_count=st.integers(min_value=1, max_value=15))
    def test_mcp_tool_count_matches_registry(self, tool_count: int) -> None:
        """MCP list_tools 返回的工具数量必须等于 Registry 中的工具数量。"""
        registry = SkillRegistry()
        tools = [_make_tool(f"tool_{i}") for i in range(tool_count)]
        registry.register("test_skill", "测试", tools)
        server = create_mcp_server(registry)

        mcp_tools = asyncio.run(_list_tools(server))
        assert len(mcp_tools) == tool_count

    @settings(max_examples=100)
    @given(tool_count=st.integers(min_value=1, max_value=15))
    def test_mcp_tool_names_match_registry(self, tool_count: int) -> None:
        """MCP list_tools 返回的工具名称集合必须与 Registry 一致。"""
        registry = SkillRegistry()
        tools = [_make_tool(f"tool_{i}") for i in range(tool_count)]
        registry.register("test_skill", "测试", tools)
        server = create_mcp_server(registry)

        mcp_tools = asyncio.run(_list_tools(server))
        mcp_names = {t.name for t in mcp_tools}
        registry_names = {t.name for t in registry.get_all_tools()}
        assert mcp_names == registry_names

    def test_empty_registry_returns_no_tools(self) -> None:
        """空 Registry 时 MCP 应返回空工具列表。"""
        registry = SkillRegistry()
        server = create_mcp_server(registry)
        mcp_tools = asyncio.run(_list_tools(server))
        assert len(mcp_tools) == 0

    def test_mcp_tool_description_matches(self) -> None:
        """MCP 工具的描述应与 Registry 中的描述一致。"""
        registry = _make_registry("echo_tool")
        server = create_mcp_server(registry)
        mcp_tools = asyncio.run(_list_tools(server))
        assert mcp_tools[0].description == "测试工具"

    def test_mcp_tool_input_schema_matches(self) -> None:
        """MCP 工具的 inputSchema 应与 Registry 中的 input_schema 一致。"""
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name"],
        }
        registry = SkillRegistry()
        registry.register("s", "d", [_make_tool("custom", input_schema=schema)])
        server = create_mcp_server(registry)

        mcp_tools = asyncio.run(_list_tools(server))
        assert mcp_tools[0].inputSchema == schema


# ── Property 10：MCP 调用往返正确性 ──────────────────────


class TestProperty10MCPCallRoundTrip:
    """Property 10：MCP 调用参数必须无损传递到工具函数，返回值必须正确封装为 MCP 响应。

    **Validates: Requirements 3.2, 3.3**
    """

    @settings(max_examples=100)
    @given(value=_arg_value_st)
    def test_string_arg_round_trip(self, value: str) -> None:
        """字符串参数必须无损传递到工具函数并正确返回。"""
        registry = _make_registry("echo")
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "echo", {"x": value})
        )
        assert result.isError is False
        assert len(result.content) == 1
        assert result.content[0].type == "text"
        assert result.content[0].text == f"result:{value}"

    @settings(max_examples=100)
    @given(
        a=st.integers(min_value=-1000, max_value=1000),
        b=st.integers(min_value=-1000, max_value=1000),
    )
    def test_numeric_args_round_trip(self, a: int, b: int) -> None:
        """数值参数必须无损传递，返回值正确封装为 JSON 文本。"""
        def add_func(a: int = 0, b: int = 0) -> dict:
            return {"sum": a + b}

        registry = SkillRegistry()
        tool = ToolDef(
            name="add",
            description="加法",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
            func=add_func,
        )
        registry.register("math", "数学工具", [tool])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "add", {"a": a, "b": b})
        )
        assert result.isError is False
        parsed = json.loads(result.content[0].text)
        assert parsed["sum"] == a + b

    def test_dict_return_serialized_as_json(self) -> None:
        """工具返回字典时应序列化为 JSON 字符串。"""
        def info_func(x: str = "") -> dict:
            return {"input": x, "length": len(x)}

        registry = SkillRegistry()
        registry.register("s", "d", [_make_tool("info", func=info_func)])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "info", {"x": "hello"})
        )
        assert result.isError is False
        parsed = json.loads(result.content[0].text)
        assert parsed["input"] == "hello"
        assert parsed["length"] == 5

    def test_string_return_not_double_serialized(self) -> None:
        """工具返回字符串时应直接作为 text，不做二次 JSON 序列化。"""
        registry = _make_registry("echo")
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "echo", {"x": "test"})
        )
        assert result.content[0].text == "result:test"
        # 不应是 '"result:test"'（带引号的 JSON 字符串）
        assert not result.content[0].text.startswith('"')

    def test_no_args_tool_call(self) -> None:
        """无参数工具调用应正常工作。"""
        def no_arg_func() -> str:
            return "无参数结果"

        registry = SkillRegistry()
        tool = ToolDef(
            name="noop",
            description="无参数工具",
            input_schema={"type": "object", "properties": {}},
            func=no_arg_func,
        )
        registry.register("s", "d", [tool])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "noop", {})
        )
        assert result.isError is False
        assert result.content[0].text == "无参数结果"


# ── Property 11：MCP 错误响应 ────────────────────────────


class TestProperty11MCPErrorResponse:
    """Property 11：工具异常必须转换为 MCP 标准错误响应。

    **Validates: Requirements 3.4**
    """

    @settings(max_examples=100)
    @given(error_msg=st.text(min_size=1, max_size=100))
    def test_tool_exception_returns_error_response(self, error_msg: str) -> None:
        """工具执行异常必须返回 isError=True 的 MCP 响应，而非抛出异常。"""
        def failing_func(**kwargs):
            raise ValueError(error_msg)

        registry = SkillRegistry()
        tool = ToolDef(
            name="fail",
            description="会失败的工具",
            input_schema={"type": "object", "properties": {}},
            func=failing_func,
        )
        registry.register("s", "d", [tool])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "fail", {})
        )
        # 必须标记为错误
        assert result.isError is True
        # 必须包含错误内容
        assert len(result.content) >= 1
        assert result.content[0].type == "text"
        # 错误文本应包含原始错误信息
        assert error_msg in result.content[0].text
        assert "ToolExecutionError:" in result.content[0].text

    def test_tool_not_found_returns_error(self) -> None:
        """调用不存在的工具应返回 isError=True 的 MCP 错误响应。"""
        registry = _make_registry("existing")
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "nonexistent", {})
        )
        assert result.isError is True
        assert len(result.content) >= 1
        assert result.content[0].text.startswith("ToolNotFoundError:")

    def test_runtime_error_returns_error(self) -> None:
        """工具抛出 RuntimeError 时应返回 MCP 错误响应。"""
        def runtime_fail(**kwargs):
            raise RuntimeError("运行时错误")

        registry = SkillRegistry()
        tool = ToolDef(
            name="runtime_fail",
            description="运行时错误工具",
            input_schema={"type": "object", "properties": {}},
            func=runtime_fail,
        )
        registry.register("s", "d", [tool])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "runtime_fail", {})
        )
        assert result.isError is True
        assert "ToolExecutionError:" in result.content[0].text
        assert "运行时错误" in result.content[0].text

    def test_error_response_has_text_content(self) -> None:
        """错误响应的 content 必须是 TextContent 类型。"""
        def fail(**kwargs):
            raise Exception("通用异常")

        registry = SkillRegistry()
        tool = ToolDef(
            name="generic_fail",
            description="通用异常工具",
            input_schema={"type": "object", "properties": {}},
            func=fail,
        )
        registry.register("s", "d", [tool])
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "generic_fail", {})
        )
        assert result.isError is True
        for content_item in result.content:
            assert content_item.type == "text"
            assert isinstance(content_item.text, str)

    def test_success_response_is_not_error(self) -> None:
        """成功调用时 isError 应为 False。"""
        registry = _make_registry("ok_tool")
        server = create_mcp_server(registry)

        result = asyncio.run(
            _call_tool(server, "ok_tool", {"x": "test"})
        )
        assert result.isError is False


# ── MCP Server 创建与 stdio 入口单元测试 ─────────────────


class TestMCPServerCreation:
    """MCP Server 创建与基本功能验证。

    **Validates: Requirements 3.5**
    """

    def test_create_mcp_server_returns_server(self) -> None:
        """create_mcp_server 应返回 MCP Server 实例。"""
        from mcp.server.lowlevel.server import Server
        registry = _make_registry("tool_a")
        server = create_mcp_server(registry)
        assert isinstance(server, Server)

    def test_server_has_list_tools_handler(self) -> None:
        """Server 应注册 list_tools handler。"""
        registry = _make_registry("tool_a")
        server = create_mcp_server(registry)
        assert ListToolsRequest in server.request_handlers

    def test_server_has_call_tool_handler(self) -> None:
        """Server 应注册 call_tool handler。"""
        registry = _make_registry("tool_a")
        server = create_mcp_server(registry)
        assert CallToolRequest in server.request_handlers

    def test_run_stdio_server_is_callable(self) -> None:
        """run_stdio_server 应调用 asyncio.run。"""
        from excelmanus.mcp_server import run_stdio_server
        with patch("excelmanus.mcp_server.asyncio.run") as mock_run:
            run_stdio_server()
            assert mock_run.call_count == 1
            # asyncio.run 被 mock 后，传入协程不会自动消费；手动关闭避免告警。
            coro = mock_run.call_args[0][0]
            coro.close()

    def test_multiple_skills_all_exposed(self) -> None:
        """多个 Skill 注册后，所有工具都应通过 MCP 暴露。"""
        registry = SkillRegistry()
        registry.register("skill_a", "A", [_make_tool("tool_a")])
        registry.register("skill_b", "B", [_make_tool("tool_b")])
        registry.register("skill_c", "C", [_make_tool("tool_c")])
        server = create_mcp_server(registry)

        mcp_tools = asyncio.run(_list_tools(server))
        names = {t.name for t in mcp_tools}
        assert names == {"tool_a", "tool_b", "tool_c"}


class TestMCPWorkspaceAndBootstrap:
    """MCP 启动流程与 workspace_root 边界测试。"""

    def test_init_skill_guards_applies_workspace_root(self, tmp_path: Path) -> None:
        """初始化 guard 后，文件类工具应写入 workspace_root。"""
        from excelmanus.skills.data_skill import get_tools as get_data_tools

        target_file = tmp_path / "guarded.xlsx"
        cwd_file = Path.cwd() / "guarded.xlsx"
        if cwd_file.exists():
            cwd_file.unlink()

        _init_skill_guards(str(tmp_path))
        try:
            registry = SkillRegistry()
            registry.register("data", "数据工具", get_data_tools())
            server = create_mcp_server(registry)

            result = asyncio.run(
                _call_tool(
                    server,
                    "write_excel",
                    {"file_path": "guarded.xlsx", "data": [{"a": 1}]},
                )
            )
            assert result.isError is False
            assert target_file.exists()
            assert not cwd_file.exists()
        finally:
            # 恢复默认 guard，避免影响其他测试
            _init_skill_guards(".")

    def test_run_stdio_bootstrap_inits_guard_and_logs(self) -> None:
        """_run_stdio_server_async 应初始化 guard、输出工具数量并启动 server.run。"""
        cfg = ExcelManusConfig(
            api_key="test-key",
            workspace_root="/tmp/mcp-root",
            log_level="DEBUG",
        )

        class _FakeStdioCtx:
            async def __aenter__(self):
                return ("read_stream", "write_stream")

            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_server = MagicMock()
        fake_server.create_initialization_options.return_value = {"init": True}
        fake_server.run = AsyncMock(return_value=None)

        with (
            patch("excelmanus.mcp_server.load_config", return_value=cfg),
            patch("excelmanus.mcp_server.setup_logging") as mock_setup_logging,
            patch.object(SkillRegistry, "auto_discover", return_value=None),
            patch.object(SkillRegistry, "get_all_tools", return_value=[_make_tool("t1")]),
            patch("excelmanus.mcp_server._init_skill_guards") as mock_init_guards,
            patch("excelmanus.mcp_server.create_mcp_server", return_value=fake_server) as mock_create_server,
            patch("excelmanus.mcp_server.stdio_server", return_value=_FakeStdioCtx()),
            patch("excelmanus.mcp_server.logger") as mock_logger,
        ):
            asyncio.run(_run_stdio_server_async())

        mock_setup_logging.assert_called_once_with("DEBUG")
        mock_init_guards.assert_called_once_with("/tmp/mcp-root")
        mock_create_server.assert_called_once()
        fake_server.run.assert_awaited_once_with(
            "read_stream",
            "write_stream",
            {"init": True},
        )
        mock_logger.info.assert_called_once_with("MCP Server 启动，已注册 %d 个工具", 1)
