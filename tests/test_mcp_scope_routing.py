"""MCP 工具分级暴露与搜索意图路由测试。

覆盖：
1. MCPServerConfig.scope 字段解析
2. MCPManager._tool_scopes 追踪
3. MCP_SCOPE_ACTIVATION 映射完整性
4. ROUTE_TOOL_SCOPE["search"] 域工具白名单
5. build_v5_tools_impl MCP scope 过滤（替代旧的无差别绕过）
6. _classify_tool_route_llm 识别 search 标签
7. _build_mcp_context_notice 场景注解与搜索指南
8. Exa 内置 scope="search" 配置
9. mcp.json scope 字段解析
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.mcp.builtin import get_builtin_mcp_configs, _EXA_SERVER_NAME
from excelmanus.mcp.config import MCPConfigLoader, MCPServerConfig
from excelmanus.mcp.manager import MCPManager
from excelmanus.tools.policy import (
    MCP_SCOPE_ACTIVATION,
    ROUTE_TOOL_SCOPE,
)
from excelmanus.skillpacks.router import _VALID_ROUTE_TAGS


# ── 辅助 ────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    exa_search_enabled: bool = True
    search_default_provider: str = "exa"
    exa_api_key: str | None = None
    tavily_api_key: str | None = None
    brave_api_key: str | None = None


def _extract_tool_names(schemas: list[dict]) -> set[str]:
    names = set()
    for s in schemas:
        func = s.get("function", {})
        name = func.get("name", "")
        if not name:
            name = s.get("name", "")
        if name:
            names.add(name)
    return names


def _make_engine_with_mcp_tools(mcp_tools=None, mcp_scopes=None):
    """构建 mock engine，含 MCP 工具和 scope 映射。"""
    from excelmanus.tools.registry import ToolDef, ToolRegistry

    registry = ToolRegistry()

    # 内置域工具
    _DOMAIN_TOOLS = [
        ("read_excel", "none"),
        ("filter_data", "none"),
        ("inspect_excel_files", "none"),
        ("compare_excel", "none"),
        ("scan_excel_snapshot", "none"),
        ("search_excel_values", "none"),
        ("list_sheets", "none"),
        ("focus_window", "none"),
        ("discover_file_relationships", "none"),
        ("memory_read_topic", "none"),
        ("list_directory", "none"),
        ("read_text_file", "none"),
        ("run_code", "dynamic"),
        ("run_shell", "dynamic"),
        ("write_text_file", "workspace_write"),
        ("edit_text_file", "workspace_write"),
        ("copy_file", "workspace_write"),
        ("rename_file", "workspace_write"),
        ("delete_file", "workspace_write"),
        ("create_excel_chart", "workspace_write"),
        ("read_image", "none"),
        ("rebuild_excel_from_spec", "workspace_write"),
        ("verify_excel_replica", "workspace_write"),
        ("extract_table_spec", "none"),
        ("introspect_capability", "none"),
    ]
    for name, effect in _DOMAIN_TOOLS:
        registry.register_tool(ToolDef(
            name=name,
            description=f"test tool {name}",
            input_schema={"type": "object", "properties": {}},
            func=lambda: None,
            write_effect=effect,
        ))

    # MCP 工具
    if mcp_tools is None:
        mcp_tools = [
            ("mcp_exa_web_search_exa", "search"),
            ("mcp_context7_resolve_library_id", "dev_docs"),
            ("mcp_context7_query_docs", "dev_docs"),
            ("mcp_excel_read_sheet", "always"),
        ]
    for name, _scope in mcp_tools:
        registry.register_tool(ToolDef(
            name=name,
            description=f"[MCP] test tool {name}",
            input_schema={"type": "object", "properties": {}},
            func=lambda: None,
            write_effect="unknown",
        ))

    engine = MagicMock()
    engine._registry = registry
    engine.registry = registry
    engine._active_skills = []
    engine._bench_mode = False
    engine._tools_cache = None
    engine._tools_cache_key = None
    engine._current_write_hint = "unknown"
    engine._current_chat_mode = "write"
    engine.active_model = "test-model"

    # MCP Manager mock
    if mcp_scopes is None:
        mcp_scopes = {name: scope for name, scope in mcp_tools}
    engine._mcp_manager = MagicMock()
    engine._mcp_manager.tool_scopes = mcp_scopes

    return engine


# ── 1. MCPServerConfig.scope 字段 ────────────────────────────


class TestMCPServerConfigScope:
    """MCPServerConfig.scope 字段默认值和赋值。"""

    def test_default_scope_is_always(self):
        cfg = MCPServerConfig(name="test", transport="sse", url="http://x")
        assert cfg.scope == "always"

    def test_custom_scope(self):
        cfg = MCPServerConfig(name="test", transport="sse", url="http://x", scope="search")
        assert cfg.scope == "search"

    def test_scope_preserved_in_dataclass(self):
        cfg = MCPServerConfig(
            name="test", transport="stdio", command="node",
            scope="dev_docs",
        )
        assert cfg.scope == "dev_docs"


# ── 2. MCPConfigLoader._parse_scope ─────────────────────────


class TestParseScope:
    """mcp.json scope 字段解析。"""

    def test_default_when_absent(self):
        result = MCPConfigLoader._parse_scope("test", {})
        assert result == "always"

    def test_parses_valid_scope(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "search"})
        assert result == "search"

    def test_lowercases_scope(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "Dev_Docs"})
        assert result == "dev_docs"

    def test_strips_whitespace(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": "  search  "})
        assert result == "search"

    def test_invalid_type_fallback(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": 123})
        assert result == "always"

    def test_empty_string_fallback(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": ""})
        assert result == "always"

    def test_none_value_default(self):
        result = MCPConfigLoader._parse_scope("test", {"scope": None})
        assert result == "always"


class TestMcpJsonScopeIntegration:
    """通过 _parse_config 解析 scope 字段集成测试。"""

    def test_scope_parsed_for_sse_server(self):
        data = {
            "mcpServers": {
                "exa": {
                    "transport": "sse",
                    "url": "https://mcp.exa.ai/sse",
                    "scope": "search",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "search"

    def test_scope_default_when_absent(self):
        data = {
            "mcpServers": {
                "test": {
                    "transport": "sse",
                    "url": "https://example.com/sse",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "always"

    def test_scope_parsed_for_stdio_server(self):
        data = {
            "mcpServers": {
                "context7": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["server.js"],
                    "scope": "dev_docs",
                }
            }
        }
        configs = MCPConfigLoader._parse_config(data)
        assert len(configs) == 1
        assert configs[0].scope == "dev_docs"


# ── 3. Exa 内置 scope="search" ──────────────────────────────


class TestExaBuiltinScope:
    """Exa 内置 MCP Server 的 scope 配置。"""

    def test_exa_scope_is_search(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        assert len(configs) == 1
        assert configs[0].scope == "search"

    def test_exa_name_unchanged(self):
        configs = get_builtin_mcp_configs(_FakeConfig(exa_search_enabled=True))
        assert configs[0].name == _EXA_SERVER_NAME


# ── 4. MCPManager._tool_scopes 追踪 ─────────────────────────


class TestMCPManagerToolScopes:
    """MCPManager tool_scopes 属性测试。"""

    def test_initial_empty(self):
        mgr = MCPManager(workspace_root=".")
        assert mgr.tool_scopes == {}

    def test_tool_scopes_returns_copy(self):
        mgr = MCPManager(workspace_root=".")
        mgr._tool_scopes["mcp_exa_search"] = "search"
        scopes = mgr.tool_scopes
        scopes["mcp_extra"] = "always"
        assert "mcp_extra" not in mgr._tool_scopes


# ── 5. MCP_SCOPE_ACTIVATION 映射完整性 ──────────────────────


class TestMCPScopeActivation:
    """MCP_SCOPE_ACTIVATION 映射表测试。"""

    def test_always_in_all_route_tags(self):
        """所有路由标签都激活 'always' scope。"""
        for tag, scopes in MCP_SCOPE_ACTIVATION.items():
            assert "always" in scopes, f"tag={tag} 缺少 'always' scope"

    def test_search_activates_search_scope(self):
        assert "search" in MCP_SCOPE_ACTIVATION["search"]

    def test_code_activates_dev_docs_scope(self):
        assert "dev_docs" in MCP_SCOPE_ACTIVATION["code"]

    def test_data_read_only_always(self):
        assert MCP_SCOPE_ACTIVATION["data_read"] == frozenset({"always"})

    def test_data_write_only_always(self):
        assert MCP_SCOPE_ACTIVATION["data_write"] == frozenset({"always"})

    def test_all_tools_not_in_mapping(self):
        """all_tools 不在映射中 → 所有 MCP scope 可见。"""
        assert "all_tools" not in MCP_SCOPE_ACTIVATION

    def test_covers_all_route_tags_except_all_tools(self):
        """除 all_tools 外，所有 ROUTE_TOOL_SCOPE 的 key 都应在 MCP_SCOPE_ACTIVATION 中。"""
        for tag in ROUTE_TOOL_SCOPE:
            assert tag in MCP_SCOPE_ACTIVATION, (
                f"ROUTE_TOOL_SCOPE['{tag}'] 缺少 MCP_SCOPE_ACTIVATION 映射"
            )


# ── 6. ROUTE_TOOL_SCOPE["search"] 域工具白名单 ───────────────


class TestSearchScope:
    """search 标签的域工具白名单测试。"""

    def test_search_scope_exists(self):
        assert "search" in ROUTE_TOOL_SCOPE

    def test_search_has_introspect(self):
        assert "introspect_capability" in ROUTE_TOOL_SCOPE["search"]

    def test_search_has_memory_read(self):
        assert "memory_read_topic" in ROUTE_TOOL_SCOPE["search"]

    def test_search_has_basic_read_tools(self):
        assert "read_text_file" in ROUTE_TOOL_SCOPE["search"]
        assert "list_directory" in ROUTE_TOOL_SCOPE["search"]

    def test_search_does_not_have_excel_tools(self):
        assert "read_excel" not in ROUTE_TOOL_SCOPE["search"]
        assert "filter_data" not in ROUTE_TOOL_SCOPE["search"]

    def test_search_does_not_have_write_tools(self):
        assert "write_text_file" not in ROUTE_TOOL_SCOPE["search"]
        assert "run_code" not in ROUTE_TOOL_SCOPE["search"]


# ── 7. _VALID_ROUTE_TAGS 包含 search ─────────────────────────


class TestValidRouteTags:
    """路由分类器标签验证。"""

    def test_search_in_valid_tags(self):
        assert "search" in _VALID_ROUTE_TAGS

    def test_all_route_scope_tags_are_valid(self):
        for tag in ROUTE_TOOL_SCOPE:
            assert tag in _VALID_ROUTE_TAGS, f"ROUTE_TOOL_SCOPE['{tag}'] 不在 _VALID_ROUTE_TAGS 中"


# ── 8. build_v5_tools_impl MCP scope 过滤 ────────────────────


class TestBuildV5ToolsMCPScopeFiltering:
    """build_v5_tools_impl 中 MCP 工具按 scope 过滤测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        self.engine = _make_engine_with_mcp_tools()
        self.builder = MetaToolBuilder(self.engine)
        self.builder.build_meta_tools = MagicMock(return_value=[])

    def test_no_route_tags_exposes_all_mcp(self):
        """无路由标签时所有 MCP 工具可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=())
        names = _extract_tool_names(schemas)
        assert "mcp_exa_web_search_exa" in names
        assert "mcp_context7_resolve_library_id" in names
        assert "mcp_context7_query_docs" in names
        assert "mcp_excel_read_sheet" in names

    def test_all_tools_exposes_all_mcp(self):
        """all_tools 标签时所有 MCP 工具可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("all_tools",))
        names = _extract_tool_names(schemas)
        assert "mcp_exa_web_search_exa" in names
        assert "mcp_context7_resolve_library_id" in names
        assert "mcp_excel_read_sheet" in names

    def test_search_tag_shows_exa_hides_context7(self):
        """search 标签：Exa(scope=search) 可见，Context7(scope=dev_docs) 隐藏。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("search",))
        names = _extract_tool_names(schemas)
        assert "mcp_exa_web_search_exa" in names
        assert "mcp_context7_resolve_library_id" not in names
        assert "mcp_context7_query_docs" not in names
        # scope=always 的 MCP 工具仍可见
        assert "mcp_excel_read_sheet" in names

    def test_data_read_hides_search_and_docs_mcp(self):
        """data_read 标签：仅 scope=always 的 MCP 工具可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("data_read",))
        names = _extract_tool_names(schemas)
        assert "mcp_excel_read_sheet" in names
        assert "mcp_exa_web_search_exa" not in names
        assert "mcp_context7_resolve_library_id" not in names

    def test_code_tag_shows_dev_docs_mcp(self):
        """code 标签：Context7(scope=dev_docs) 可见，Exa(scope=search) 隐藏。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("code",))
        names = _extract_tool_names(schemas)
        assert "mcp_context7_resolve_library_id" in names
        assert "mcp_context7_query_docs" in names
        assert "mcp_exa_web_search_exa" not in names
        assert "mcp_excel_read_sheet" in names

    def test_data_write_only_always_mcp(self):
        """data_write 标签：仅 scope=always。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("data_write",))
        names = _extract_tool_names(schemas)
        assert "mcp_excel_read_sheet" in names
        assert "mcp_exa_web_search_exa" not in names
        assert "mcp_context7_resolve_library_id" not in names

    def test_chart_only_always_mcp(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("chart",))
        names = _extract_tool_names(schemas)
        assert "mcp_excel_read_sheet" in names
        assert "mcp_exa_web_search_exa" not in names

    def test_vision_only_always_mcp(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("vision",))
        names = _extract_tool_names(schemas)
        assert "mcp_excel_read_sheet" in names
        assert "mcp_exa_web_search_exa" not in names

    def test_unknown_scope_defaults_to_always(self):
        """MCP 工具 scope 不在 _tool_scopes 中时，默认 'always'（向后兼容）。"""
        engine = _make_engine_with_mcp_tools(
            mcp_tools=[("mcp_unknown_tool", "always")],
            mcp_scopes={},  # 空映射 → 默认 always
        )
        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        builder = MetaToolBuilder(engine)
        builder.build_meta_tools = MagicMock(return_value=[])

        schemas = builder.build_v5_tools_impl(route_tool_tags=("data_read",))
        names = _extract_tool_names(schemas)
        assert "mcp_unknown_tool" in names

    def test_no_mcp_manager_graceful(self):
        """engine 没有 _mcp_manager 时不崩溃。"""
        engine = _make_engine_with_mcp_tools()
        del engine._mcp_manager
        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        builder = MetaToolBuilder(engine)
        builder.build_meta_tools = MagicMock(return_value=[])

        # 无 _mcp_manager → 所有 MCP 工具默认 always → 仅 always scope 可见
        schemas = builder.build_v5_tools_impl(route_tool_tags=("data_read",))
        names = _extract_tool_names(schemas)
        # 所有 MCP 工具的 scope 默认 always，data_read 仅激活 always → 全部可见
        assert "mcp_exa_web_search_exa" in names


# ── 9. _classify_tool_route_llm 识别 search ──────────────────


class TestClassifySearchTag:
    """_classify_tool_route_llm 正确返回 search 标签。"""

    @pytest.mark.asyncio
    async def test_returns_search(self):
        from excelmanus.skillpacks.router import SkillRouter

        config = MagicMock()
        config.aux_enabled = True
        config.aux_api_key = "test"
        config.aux_base_url = "https://test.example.com/v1"
        config.aux_model = "test-model"
        config.aux_protocol = "openai"
        config.api_key = "main"
        config.base_url = "https://main.example.com/v1"
        config.protocol = "openai"
        config.skills_context_char_budget = 4000
        config.large_excel_threshold_bytes = 0

        loader = MagicMock()
        loader.get_skillpacks.return_value = {}
        router = SkillRouter(config, loader)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="search"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我搜一下qwen3.5的情况")
        assert result == ("search",)


# ── 10. _build_mcp_context_notice 场景注解 ────────────────────


class TestMCPContextNotice:
    """_build_mcp_context_notice 场景注解与搜索指南。"""

    def _make_engine_for_notice(self, servers, tool_scopes=None):
        engine = MagicMock()
        engine._mcp_manager.get_server_info.return_value = servers
        engine._mcp_manager.tool_scopes = tool_scopes or {}
        return engine

    def test_exa_usage_hint_in_notice(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
        ]
        engine = self._make_engine_for_notice(servers, {"mcp_exa_web_search_exa": "search"})
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "通用网页搜索" in notice

    def test_context7_usage_hint_in_notice(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "context7", "status": "ready", "tool_count": 2, "tools": ["resolve_library_id", "query_docs"]},
        ]
        engine = self._make_engine_for_notice(servers, {"mcp_context7_resolve_library_id": "dev_docs"})
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "编程库/框架" in notice

    def test_search_guide_when_both_exa_and_context7(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
            {"name": "context7", "status": "ready", "tool_count": 2, "tools": ["resolve_library_id"]},
        ]
        scopes = {
            "mcp_exa_web_search_exa": "search",
            "mcp_context7_resolve_library_id": "dev_docs",
        }
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索工具选择指南" in notice
        assert "exa" in notice.lower()
        assert "context7" in notice.lower()

    def test_search_guide_only_exa(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
        ]
        scopes = {"mcp_exa_web_search_exa": "search"}
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索指南" in notice
        # 不应出现 "搜索工具选择指南"（无 context7 无需对比）
        assert "搜索工具选择指南" not in notice

    def test_no_guide_without_search_tools(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [
            {"name": "excel", "status": "ready", "tool_count": 3, "tools": ["read_sheet"]},
        ]
        scopes = {"mcp_excel_read_sheet": "always"}
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "搜索指南" not in notice
        assert "搜索工具选择指南" not in notice

    def test_empty_servers_returns_empty(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        engine = self._make_engine_for_notice([])
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        assert cb._build_mcp_context_notice() == ""

    def test_no_ready_servers_returns_empty(self):
        from excelmanus.engine_core.context_builder import ContextBuilder
        servers = [{"name": "exa", "status": "connect_failed", "tool_count": 0, "tools": []}]
        engine = self._make_engine_for_notice(servers)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        assert cb._build_mcp_context_notice() == ""


# ── 11. 端到端场景回归测试 ────────────────────────────────────


class TestEndToEndScenarios:
    """模拟真实场景验证路由+过滤联动。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        self.engine = _make_engine_with_mcp_tools()
        self.builder = MetaToolBuilder(self.engine)
        self.builder.build_meta_tools = MagicMock(return_value=[])

    def test_scenario_search_qwen35(self):
        """场景：'帮我搜一下qwen3.5' → search 标签 → Exa 可见, Context7 不可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("search",))
        names = _extract_tool_names(schemas)
        assert "mcp_exa_web_search_exa" in names
        assert "mcp_context7_resolve_library_id" not in names
        # 基本域工具也可用
        assert "memory_read_topic" in names
        assert "introspect_capability" in names

    def test_scenario_code_query(self):
        """场景：'React useEffect 怎么用' → code 标签 → Context7 可见, Exa 不可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("code",))
        names = _extract_tool_names(schemas)
        assert "mcp_context7_resolve_library_id" in names
        assert "mcp_context7_query_docs" in names
        assert "mcp_exa_web_search_exa" not in names

    def test_scenario_read_excel(self):
        """场景：'帮我看一下sales.xlsx' → data_read → 仅 always MCP。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("data_read",))
        names = _extract_tool_names(schemas)
        assert "read_excel" in names
        assert "mcp_excel_read_sheet" in names
        assert "mcp_exa_web_search_exa" not in names
        assert "mcp_context7_resolve_library_id" not in names

    def test_scenario_complex_task_all_tools(self):
        """场景：复杂任务 → all_tools → 所有 MCP 工具可见。"""
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("all_tools",))
        names = _extract_tool_names(schemas)
        assert "mcp_exa_web_search_exa" in names
        assert "mcp_context7_resolve_library_id" in names
        assert "mcp_excel_read_sheet" in names
        assert "read_excel" in names

    def test_backward_compat_no_scope_field(self):
        """向后兼容：旧 MCP 配置无 scope 字段 → 默认 always，行为不变。"""
        engine = _make_engine_with_mcp_tools(
            mcp_tools=[("mcp_legacy_tool", "always")],
            mcp_scopes={"mcp_legacy_tool": "always"},
        )
        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        builder = MetaToolBuilder(engine)
        builder.build_meta_tools = MagicMock(return_value=[])

        # 在任何路由标签下都可见（因为 scope=always）
        for tag in ("data_read", "search", "code", "chart"):
            schemas = builder.build_v5_tools_impl(route_tool_tags=(tag,))
            names = _extract_tool_names(schemas)
            assert "mcp_legacy_tool" in names, f"tag={tag} 丢失 legacy tool"
