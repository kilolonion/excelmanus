"""LLM 工具路由统一管线测试。

覆盖：
- ROUTE_TOOL_SCOPE 映射完整性
- _classify_tool_route_llm 分类方法（正常/无效/超时/异常）
- build_v5_tools_impl 按 route_tool_tags 白名单过滤
- route() 并行 LLM 分类集成
- 图片附件强制 vision
- AUX 未配置时安全 fallback
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.tools.policy import (
    ROUTE_TOOL_SCOPE,
    _DATA_READ_TOOLS,
    READ_ONLY_SAFE_TOOLS,
)
from excelmanus.skillpacks.models import SkillMatchResult


# ── ROUTE_TOOL_SCOPE 映射表测试 ──────────────────────────────


class TestRouteToolScope:
    """ROUTE_TOOL_SCOPE 白名单映射的静态约束测试。"""

    def test_data_read_is_base(self):
        assert ROUTE_TOOL_SCOPE["data_read"] == _DATA_READ_TOOLS

    def test_data_write_superset_of_data_read(self):
        assert _DATA_READ_TOOLS <= ROUTE_TOOL_SCOPE["data_write"]
        assert "run_code" in ROUTE_TOOL_SCOPE["data_write"]
        assert "write_text_file" in ROUTE_TOOL_SCOPE["data_write"]

    def test_chart_includes_chart_tool(self):
        scope = ROUTE_TOOL_SCOPE["chart"]
        assert "create_excel_chart" in scope
        assert "run_code" in scope
        assert _DATA_READ_TOOLS <= scope

    def test_vision_includes_vision_tools(self):
        scope = ROUTE_TOOL_SCOPE["vision"]
        assert "read_image" in scope
        assert "rebuild_excel_from_spec" in scope
        assert "verify_excel_replica" in scope
        assert "extract_table_spec" in scope

    def test_code_has_code_tools(self):
        scope = ROUTE_TOOL_SCOPE["code"]
        assert "run_code" in scope
        assert "run_shell" in scope
        assert "write_text_file" in scope
        assert "list_directory" in scope

    def test_all_tools_not_in_mapping(self):
        """all_tools 不应在映射中，表示不做过滤。"""
        assert "all_tools" not in ROUTE_TOOL_SCOPE

    def test_introspect_in_all_scopes(self):
        """introspect_capability 作为安全阀，在所有路由标签中保留。"""
        for tag, scope in ROUTE_TOOL_SCOPE.items():
            assert "introspect_capability" in scope, (
                f"tag={tag} 缺少 introspect_capability 安全阀"
            )

    def test_data_read_tools_are_readonly(self):
        """_DATA_READ_TOOLS 中的工具应全部为只读安全工具（introspect 除外）。"""
        non_readonly = _DATA_READ_TOOLS - READ_ONLY_SAFE_TOOLS - {"introspect_capability"}
        assert not non_readonly, f"_DATA_READ_TOOLS 包含非只读工具: {non_readonly}"


# ── _classify_tool_route_llm 测试 ────────────────────────────


def _make_config(aux_enabled=True):
    """构建最小化 config mock。"""
    config = MagicMock()
    config.aux_enabled = aux_enabled
    config.aux_api_key = "test-key" if aux_enabled else None
    config.aux_base_url = "https://test.example.com/v1" if aux_enabled else None
    config.aux_model = "qwen3.5-flash" if aux_enabled else None
    config.aux_protocol = "openai"
    config.api_key = "main-key"
    config.base_url = "https://main.example.com/v1"
    config.protocol = "openai"
    config.skills_context_char_budget = 4000
    config.large_excel_threshold_bytes = 0
    return config


def _make_loader_with_dummy_skill():
    """构建包含一个 dummy skillpack 的 loader，让 route() 不走 no_skillpack 分支。"""
    dummy_skill = MagicMock()
    dummy_skill.name = "dummy"
    dummy_skill.user_invocable = True
    dummy_skill.triggers = []
    dummy_skill.render_context_instructions_only.return_value = ""
    loader = MagicMock()
    loader.get_skillpacks.return_value = {"dummy": dummy_skill}
    return loader


def _make_router_with_aux():
    """构建启用了 AUX LLM 的最小化 SkillRouter。"""
    from excelmanus.skillpacks.router import SkillRouter
    router = SkillRouter(_make_config(aux_enabled=True), _make_loader_with_dummy_skill())
    return router


def _make_router_without_aux():
    """构建未配置 AUX 的最小化 SkillRouter。"""
    from excelmanus.skillpacks.router import SkillRouter
    router = SkillRouter(_make_config(aux_enabled=False), _make_loader_with_dummy_skill())
    return router


class TestClassifyToolRouteLlm:
    """_classify_tool_route_llm 方法测试。"""

    @pytest.mark.asyncio
    async def test_returns_valid_label(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_read"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我看一下这个表格")
        assert result == ("data_read",)

    @pytest.mark.asyncio
    async def test_returns_data_write(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_write"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("把A列的数据填充到B列")
        assert result == ("data_write",)

    @pytest.mark.asyncio
    async def test_returns_chart(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="chart"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("画一个柱状图")
        assert result == ("chart",)

    @pytest.mark.asyncio
    async def test_returns_vision(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="vision"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("把这张截图还原成表格")
        assert result == ("vision",)

    @pytest.mark.asyncio
    async def test_returns_code(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="code"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("写个Python脚本处理数据")
        assert result == ("code",)

    @pytest.mark.asyncio
    async def test_invalid_label_fallback(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="invalid_tag_xyz"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我看一下表格")
        assert result == ("all_tools",)

    @pytest.mark.asyncio
    async def test_timeout_fallback(self):
        router = _make_router_with_aux()
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        result = await router._classify_tool_route_llm("帮我看一下表格", timeout=0.01)
        assert result == ("all_tools",)

    @pytest.mark.asyncio
    async def test_exception_fallback(self):
        router = _make_router_with_aux()
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(
            side_effect=ConnectionError("网络错误")
        )

        result = await router._classify_tool_route_llm("帮我看一下表格")
        assert result == ("all_tools",)

    @pytest.mark.asyncio
    async def test_no_client_fallback(self):
        router = _make_router_without_aux()
        result = await router._classify_tool_route_llm("帮我看一下表格")
        assert result == ("all_tools",)

    @pytest.mark.asyncio
    async def test_strips_backticks_and_quotes(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="`data_read`"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我看一下表格")
        assert result == ("data_read",)

    @pytest.mark.asyncio
    async def test_handles_multiline_response(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_write\n这是解释"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("修改A列")
        assert result == ("data_write",)

    @pytest.mark.asyncio
    async def test_empty_response_fallback(self):
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=""))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router._classify_tool_route_llm("帮我看一下表格")
        assert result == ("all_tools",)

    @pytest.mark.asyncio
    async def test_message_truncation(self):
        """超长消息应被截断到 500 字符。"""
        router = _make_router_with_aux()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_read"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        long_message = "x" * 1000
        await router._classify_tool_route_llm(long_message)

        call_args = router._router_client.chat.completions.create.call_args
        prompt_content = call_args.kwargs["messages"][0]["content"]
        # 验证消息被截断（prompt 模板中包含截断后的消息）
        assert "x" * 500 in prompt_content
        assert "x" * 501 not in prompt_content


# ── build_v5_tools_impl 路由过滤测试 ─────────────────────────


def _extract_tool_names(schemas: list[dict]) -> set[str]:
    """从 OpenAI tool schema 列表中提取工具名集合。"""
    names = set()
    for s in schemas:
        func = s.get("function", {})
        name = func.get("name", "")
        if not name:
            name = s.get("name", "")
        if name:
            names.add(name)
    return names


class TestBuildV5ToolsRouteFiltering:
    """build_v5_tools_impl 按 route_tool_tags 白名单过滤测试。"""

    @pytest.fixture(autouse=True)
    def _setup_engine(self):
        """构建最小化 mock engine。"""
        from excelmanus.tools.registry import ToolDef, ToolRegistry

        registry = ToolRegistry()
        _TOOLS = [
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
        for name, effect in _TOOLS:
            registry.register_tool(ToolDef(
                name=name,
                description=f"test tool {name}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect=effect,
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

        from excelmanus.engine_core.meta_tools import MetaToolBuilder
        self.builder = MetaToolBuilder(engine)
        # Mock build_meta_tools to return empty (meta tools tested separately)
        self.builder.build_meta_tools = MagicMock(return_value=[])
        self.engine = engine
        self.registry = registry

    def test_no_route_tags_exposes_all(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=())
        names = _extract_tool_names(schemas)
        assert "read_excel" in names
        assert "run_code" in names
        assert "create_excel_chart" in names
        assert "read_image" in names
        assert "delete_file" in names

    def test_data_read_whitelist(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("data_read",))
        names = _extract_tool_names(schemas)
        # 应包含 data_read 工具
        assert "read_excel" in names
        assert "filter_data" in names
        assert "introspect_capability" in names
        # 不应包含写工具
        assert "run_code" not in names
        assert "write_text_file" not in names
        assert "create_excel_chart" not in names
        assert "read_image" not in names

    def test_data_write_whitelist(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("data_write",))
        names = _extract_tool_names(schemas)
        assert "read_excel" in names
        assert "run_code" in names
        assert "write_text_file" in names
        # 不含 vision / chart
        assert "read_image" not in names
        assert "create_excel_chart" not in names

    def test_chart_whitelist(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("chart",))
        names = _extract_tool_names(schemas)
        assert "create_excel_chart" in names
        assert "run_code" in names
        assert "read_excel" in names
        assert "read_image" not in names

    def test_vision_whitelist(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("vision",))
        names = _extract_tool_names(schemas)
        assert "read_image" in names
        assert "rebuild_excel_from_spec" in names
        assert "extract_table_spec" in names
        assert "read_excel" in names
        assert "create_excel_chart" not in names

    def test_code_whitelist(self):
        schemas = self.builder.build_v5_tools_impl(route_tool_tags=("code",))
        names = _extract_tool_names(schemas)
        assert "run_code" in names
        assert "run_shell" in names
        assert "write_text_file" in names
        assert "list_directory" in names
        assert "introspect_capability" in names
        # Excel 专用工具不应暴露
        assert "read_excel" not in names
        assert "create_excel_chart" not in names

    def test_all_tools_no_filtering(self):
        all_schemas = self.builder.build_v5_tools_impl(route_tool_tags=())
        tagged_schemas = self.builder.build_v5_tools_impl(route_tool_tags=("all_tools",))
        assert _extract_tool_names(all_schemas) == _extract_tool_names(tagged_schemas)

    def test_unknown_tag_no_filtering(self):
        """未知标签视为 all_tools，不做过滤。"""
        all_schemas = self.builder.build_v5_tools_impl(route_tool_tags=())
        unknown_schemas = self.builder.build_v5_tools_impl(route_tool_tags=("unknown_xyz",))
        assert _extract_tool_names(all_schemas) == _extract_tool_names(unknown_schemas)

    def test_combined_read_only_and_route_tags(self):
        """write_hint=read_only + route_tool_tags 双重过滤。"""
        schemas = self.builder.build_v5_tools_impl(
            write_hint="read_only",
            route_tool_tags=("data_read",),
        )
        names = _extract_tool_names(schemas)
        # read_only 已排除写工具，data_read 进一步限制域
        assert "read_excel" in names
        assert "write_text_file" not in names
        assert "create_excel_chart" not in names
        assert "delete_file" not in names

    def test_introspect_always_available(self):
        """introspect_capability 在所有路由标签下都可用。"""
        for tag in ("data_read", "data_write", "chart", "vision", "code"):
            schemas = self.builder.build_v5_tools_impl(route_tool_tags=(tag,))
            names = _extract_tool_names(schemas)
            assert "introspect_capability" in names, f"tag={tag} 丢失 introspect"

    def test_cache_key_includes_route_tool_tags(self):
        """缓存键应包含 route_tool_tags，不同标签应产生不同缓存。"""
        self.builder.build_meta_tools = MagicMock(return_value=[])
        tools1 = self.builder.build_v5_tools(route_tool_tags=("data_read",))
        key1 = self.engine._tools_cache_key

        self.engine._tools_cache = None
        self.engine._tools_cache_key = None
        tools2 = self.builder.build_v5_tools(route_tool_tags=("data_write",))
        key2 = self.engine._tools_cache_key

        assert key1 != key2
        assert _extract_tool_names(tools1) != _extract_tool_names(tools2)


# ── SkillMatchResult 字段测试 ─────────────────────────────────


class TestSkillMatchResultRouteToolTags:
    """SkillMatchResult.route_tool_tags 字段测试。"""

    def test_default_empty(self):
        result = SkillMatchResult(skills_used=[], route_mode="all_tools")
        assert result.route_tool_tags == ()

    def test_preserves_value(self):
        result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            route_tool_tags=("data_read",),
        )
        assert result.route_tool_tags == ("data_read",)

    def test_frozen(self):
        result = SkillMatchResult(
            skills_used=[],
            route_mode="all_tools",
            route_tool_tags=("chart",),
        )
        with pytest.raises(AttributeError):
            result.route_tool_tags = ("data_write",)


# ── 路由集成测试 ──────────────────────────────────────────────


def _mock_build_all_tools_result(router):
    """Mock _build_all_tools_result 避免需要文件扫描基础设施。"""
    async def _fake_build(user_message="", candidate_file_paths=None, write_hint="unknown", task_tags=()):
        return SkillMatchResult(
            skills_used=[], route_mode="all_tools",
            write_hint=write_hint, task_tags=task_tags,
        )
    router._build_all_tools_result = _fake_build


class TestRouterIntegration:
    """route() 方法中 LLM 分类器集成测试。"""

    @pytest.mark.asyncio
    async def test_route_passes_route_tool_tags(self):
        """非 chitchat 消息应通过 LLM 分类器获得 route_tool_tags。"""
        router = _make_router_with_aux()
        _mock_build_all_tools_result(router)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_read"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router.route("分析这个表格的数据趋势", chat_mode="read")
        assert result.route_tool_tags == ("data_read",)

    @pytest.mark.asyncio
    async def test_route_chitchat_no_route_tags(self):
        """chitchat 消息不经过 LLM 分类器。"""
        router = _make_router_with_aux()
        result = await router.route("你好", chat_mode="write")
        assert result.route_mode == "chitchat"
        assert result.route_tool_tags == ()

    @pytest.mark.asyncio
    async def test_image_forces_vision(self):
        """图片附件强制 route_tool_tags=vision。"""
        router = _make_router_with_aux()
        _mock_build_all_tools_result(router)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="data_read"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router.route(
            "把这个表格做出来",
            images=[{"url": "data:image/png;base64,abc"}],
            chat_mode="write",
        )
        assert result.route_tool_tags == ("vision",)

    @pytest.mark.asyncio
    async def test_image_keeps_vision_if_already_vision(self):
        """LLM 已返回 vision 时，图片附件不改变结果。"""
        router = _make_router_with_aux()
        _mock_build_all_tools_result(router)

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="vision"))]
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await router.route(
            "还原这张截图",
            images=[{"url": "data:image/png;base64,abc"}],
            chat_mode="write",
        )
        assert result.route_tool_tags == ("vision",)


class TestFallbackSafety:
    """AUX 未配置时的安全 fallback 测试。"""

    @pytest.mark.asyncio
    async def test_no_aux_no_route_tags(self):
        """AUX 未配置时 route_tool_tags 为空（不做过滤）。"""
        router = _make_router_without_aux()
        _mock_build_all_tools_result(router)
        result = await router.route("帮我分析这个表格", chat_mode="read")
        assert result.route_tool_tags == ()

    @pytest.mark.asyncio
    async def test_aux_disabled_no_route_tags(self):
        """AUX 已配置但 disabled 时 route_tool_tags 为空。"""
        router = _make_router_without_aux()
        _mock_build_all_tools_result(router)
        assert not router._route_llm_enabled
        result = await router.route("修改A列数据", chat_mode="write")
        assert result.route_tool_tags == ()

    @pytest.mark.asyncio
    async def test_timeout_produces_all_tools(self):
        """LLM 超时时 route_tool_tags=("all_tools",)，即不过滤。"""
        router = _make_router_with_aux()
        _mock_build_all_tools_result(router)
        router._router_client = MagicMock()
        router._router_client.chat.completions.create = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )

        result = await router.route("修改A列数据", chat_mode="write")
        assert "all_tools" in result.route_tool_tags
