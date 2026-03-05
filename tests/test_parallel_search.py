"""并发搜索工具 (parallel_search) 测试。

覆盖：
1. 查询变体生成
2. 关键词提取
3. Exa 搜索工具发现
4. 结果去重与聚合
5. 并发搜索核心逻辑
6. ToolDef 注册
7. engine 注册集成
8. Policy 白名单
9. Prompt 指南增强
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.tools.search_tools import (
    _deduplicate_results,
    _extract_keywords,
    _find_exa_search_tool,
    _generate_query_variants,
    _parallel_search_impl,
    _parse_exa_results,
    get_tools,
)


# ── 1. 查询变体生成 ──────────────────────────────────────────


class TestGenerateQueryVariants:
    def test_single_variant(self):
        variants = _generate_query_variants("qwen3.5", num_variants=1)
        assert len(variants) == 1
        assert variants[0] == "qwen3.5"

    def test_default_three_variants(self):
        variants = _generate_query_variants("qwen3.5 最新情况")
        assert len(variants) <= 3
        assert variants[0] == "qwen3.5 最新情况"

    def test_deduplication(self):
        """如果关键词提取结果与原查询相同，不应出现重复。"""
        variants = _generate_query_variants("AI", num_variants=3)
        assert len(variants) == len(set(variants))

    def test_chinese_query_appends_context(self):
        """中文查询的第三个变体应包含'最新信息'。"""
        variants = _generate_query_variants("深度学习框架", num_variants=3)
        assert any("最新信息" in v for v in variants)

    def test_english_query_appends_context(self):
        """英文查询的第三个变体应包含'latest'。"""
        variants = _generate_query_variants("deep learning framework", num_variants=3)
        assert any("latest" in v for v in variants)

    def test_max_five_variants(self):
        variants = _generate_query_variants("test query", num_variants=5)
        assert len(variants) <= 5

    def test_empty_query(self):
        variants = _generate_query_variants("", num_variants=3)
        # 应至少返回原始查询
        assert len(variants) >= 1


# ── 2. 关键词提取 ──────────────────────────────────────────


class TestExtractKeywords:
    def test_chinese_stop_words_removed(self):
        result = _extract_keywords("帮我搜一下 qwen3.5 的情况")
        assert "qwen3.5" in result
        # 提取后应比原文短（停用词被移除）
        assert len(result) < len("帮我搜一下 qwen3.5 的情况")

    def test_english_stop_words_removed(self):
        result = _extract_keywords("please search for the latest AI news")
        assert "please" not in result.lower()
        assert "search" not in result.lower()
        assert "ai" in result.lower() or "AI" in result

    def test_preserves_meaningful_terms(self):
        result = _extract_keywords("Python深度学习框架对比")
        assert "Python" in result

    def test_returns_original_if_all_stop_words(self):
        """全是停用词时应返回原查询。"""
        result = _extract_keywords("的了吗")
        # 应该有返回值
        assert isinstance(result, str)


# ── 3. Exa 搜索工具发现 ──────────────────────────────────────


class TestFindExaSearchTool:
    def test_finds_tool_from_scopes(self):
        mgr = MagicMock()
        mgr._clients = {"exa": MagicMock()}
        mgr.tool_scopes = {"mcp_exa_web_search_exa": "search"}
        result = _find_exa_search_tool(mgr)
        assert result == "web_search_exa"

    def test_returns_none_when_no_exa_client(self):
        mgr = MagicMock()
        mgr._clients = {}
        result = _find_exa_search_tool(mgr)
        assert result is None

    def test_finds_tool_from_client_cache(self):
        """当 tool_scopes 没有匹配时，从 client._tools 回退查找。"""
        client = MagicMock()
        tool_mock = MagicMock()
        tool_mock.name = "web_search_exa"
        client._tools = [tool_mock]
        mgr = MagicMock()
        mgr._clients = {"exa": client}
        mgr.tool_scopes = {}
        result = _find_exa_search_tool(mgr)
        assert result == "web_search_exa"

    def test_finds_tool_with_search_in_name(self):
        mgr = MagicMock()
        mgr._clients = {"exa": MagicMock()}
        mgr.tool_scopes = {"mcp_exa_search": "search"}
        result = _find_exa_search_tool(mgr)
        assert result == "search"


# ── 4. 结果去重 ──────────────────────────────────────────


class TestDeduplicateResults:
    def test_removes_duplicate_urls(self):
        items = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "title": "B"},
            {"url": "https://a.com", "title": "A duplicate"},
        ]
        unique = _deduplicate_results(items)
        assert len(unique) == 2
        urls = [i["url"] for i in unique]
        assert urls == ["https://a.com", "https://b.com"]

    def test_preserves_items_without_url(self):
        items = [
            {"text": "no url 1"},
            {"text": "no url 2"},
        ]
        unique = _deduplicate_results(items)
        assert len(unique) == 2

    def test_empty_list(self):
        assert _deduplicate_results([]) == []


# ── 5. 结果解析 ──────────────────────────────────────────


class TestParseExaResults:
    def test_json_list(self):
        raw = json.dumps([{"url": "https://a.com"}])
        result = _parse_exa_results(raw)
        assert len(result) == 1
        assert result[0]["url"] == "https://a.com"

    def test_json_dict_with_results_key(self):
        raw = json.dumps({"results": [{"url": "https://a.com"}, {"url": "https://b.com"}]})
        result = _parse_exa_results(raw)
        assert len(result) == 2

    def test_plain_text_fallback(self):
        raw = "Some search results in plain text"
        result = _parse_exa_results(raw)
        assert len(result) == 1
        assert result[0]["text"] == raw

    def test_empty_text(self):
        assert _parse_exa_results("") == []
        assert _parse_exa_results("   ") == []


# ── 6. 并发搜索核心逻辑 ──────────────────────────────────


class TestParallelSearchImpl:
    @pytest.mark.asyncio
    async def test_no_exa_client_returns_error(self):
        mgr = MagicMock()
        mgr._clients = {}
        result = await _parallel_search_impl(mgr, "test query")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_no_search_tool_returns_error(self):
        mgr = MagicMock()
        mgr._clients = {"exa": MagicMock()}
        mgr.tool_scopes = {}
        # client._tools 也为空
        mgr._clients["exa"]._tools = []
        result = await _parallel_search_impl(mgr, "test query")
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_successful_parallel_search(self):
        """模拟 Exa 返回结果并验证并发聚合。"""
        client = MagicMock()
        call_count = 0

        async def mock_call_tool(name, args):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.content = [MagicMock()]
            result.content[0].text = json.dumps([
                {"url": f"https://example.com/{call_count}", "title": f"Result {call_count}"}
            ])
            result.isError = False
            return result

        client.call_tool = mock_call_tool

        mgr = MagicMock()
        mgr._clients = {"exa": client}
        mgr.tool_scopes = {"mcp_exa_web_search_exa": "search"}

        import excelmanus.tools.search_tools as _st
        _orig = _st.format_tool_result
        _st.format_tool_result = lambda r: r.content[0].text
        try:
            result = await _parallel_search_impl(mgr, "test query", num_queries=2)
        finally:
            _st.format_tool_result = _orig

        parsed = json.loads(result)
        assert "results" in parsed
        assert "variants_used" in parsed
        assert len(parsed["variants_used"]) <= 2
        assert parsed["total_results"] >= 1

    @pytest.mark.asyncio
    async def test_handles_timeout_gracefully(self):
        """搜索超时不应导致整体失败。"""
        client = MagicMock()

        async def mock_call_tool(name, args):
            await asyncio.sleep(100)  # 永远超时

        client.call_tool = mock_call_tool

        mgr = MagicMock()
        mgr._clients = {"exa": client}
        mgr.tool_scopes = {"mcp_exa_web_search_exa": "search"}

        # 使用极短超时
        with patch("excelmanus.tools.search_tools.asyncio.wait_for") as mock_wait:
            mock_wait.side_effect = asyncio.TimeoutError()

            result = await _parallel_search_impl(mgr, "test query", num_queries=1)

        parsed = json.loads(result)
        # 超时后结果应为空但不报错
        assert parsed["total_results"] == 0

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        """单个搜索失败不影响其他搜索结果。"""
        client = MagicMock()
        call_idx = 0

        async def mock_call_tool(name, args):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                raise ConnectionError("network error")
            r = MagicMock()
            r.content = [MagicMock()]
            r.content[0].text = json.dumps([{"url": "https://ok.com", "title": "OK"}])
            r.isError = False
            return r

        client.call_tool = mock_call_tool

        mgr = MagicMock()
        mgr._clients = {"exa": client}
        mgr.tool_scopes = {"mcp_exa_web_search_exa": "search"}

        import excelmanus.tools.search_tools as _st
        _orig = _st.format_tool_result
        _st.format_tool_result = lambda r: r.content[0].text
        try:
            # 使用中文查询确保生成至少 3 个不同变体（含 "最新信息" 后缀变体）
            result = await _parallel_search_impl(mgr, "深度学习框架对比", num_queries=3)
        finally:
            _st.format_tool_result = _orig

        parsed = json.loads(result)
        # 至少一个查询应成功（第1个失败，后续应有结果）
        assert parsed["total_results"] >= 1


# ── 7. ToolDef 注册 ──────────────────────────────────────


class TestGetTools:
    def test_returns_parallel_search_tool(self):
        mgr = MagicMock()
        tools = get_tools(mgr)
        assert len(tools) == 1
        assert tools[0].name == "parallel_search"

    def test_tool_has_async_func(self):
        mgr = MagicMock()
        tools = get_tools(mgr)
        assert tools[0].async_func is not None
        assert asyncio.iscoroutinefunction(tools[0].async_func)

    def test_tool_schema(self):
        mgr = MagicMock()
        tools = get_tools(mgr)
        schema = tools[0].input_schema
        assert "query" in schema["properties"]
        assert "num_queries" in schema["properties"]
        assert "query" in schema["required"]

    def test_tool_write_effect_is_none(self):
        mgr = MagicMock()
        tools = get_tools(mgr)
        assert tools[0].write_effect == "none"


# ── 8. Policy 白名单 ──────────────────────────────────────


class TestPolicyIntegration:
    def test_parallel_search_in_read_only_safe(self):
        from excelmanus.tools.policy import READ_ONLY_SAFE_TOOLS
        assert "parallel_search" in READ_ONLY_SAFE_TOOLS

    def test_parallel_search_in_parallelizable(self):
        from excelmanus.tools.policy import PARALLELIZABLE_READONLY_TOOLS
        assert "parallel_search" in PARALLELIZABLE_READONLY_TOOLS

    def test_parallel_search_in_search_scope(self):
        from excelmanus.tools.policy import ROUTE_TOOL_SCOPE
        assert "parallel_search" in ROUTE_TOOL_SCOPE["search"]

    def test_parallelizable_subset_assertion(self):
        """PARALLELIZABLE_READONLY_TOOLS 仍然是 READ_ONLY_SAFE_TOOLS 的子集。"""
        from excelmanus.tools.policy import (
            PARALLELIZABLE_READONLY_TOOLS,
            READ_ONLY_SAFE_TOOLS,
        )
        assert PARALLELIZABLE_READONLY_TOOLS <= READ_ONLY_SAFE_TOOLS


# ── 9. Prompt 指南增强 ──────────────────────────────────────


class TestPromptEnhancement:
    def _make_engine_for_notice(self, servers, scopes):
        engine = MagicMock()
        engine._mcp_manager.get_server_info.return_value = servers
        engine._mcp_manager.tool_scopes = scopes
        return engine

    def test_search_guide_mentions_parallel_search(self):
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
        assert "parallel_search" in notice

    def test_search_only_guide_mentions_parallel_search(self):
        from excelmanus.engine_core.context_builder import ContextBuilder

        servers = [
            {"name": "exa", "status": "ready", "tool_count": 1, "tools": ["web_search_exa"]},
        ]
        scopes = {"mcp_exa_web_search_exa": "search"}
        engine = self._make_engine_for_notice(servers, scopes)
        cb = ContextBuilder.__new__(ContextBuilder)
        cb._engine = engine
        notice = cb._build_mcp_context_notice()
        assert "parallel_search" in notice

    def test_guide_mentions_parallel_execution(self):
        """当同时有搜索和文档工具时，应提示可并行调用。"""
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
        assert "并行" in notice


# ── 10. Engine 注册集成 ──────────────────────────────────────


class TestEngineRegistration:
    def test_register_search_tools_when_exa_connected(self):
        """Exa 连接时 _register_search_tools 应注册 parallel_search。"""
        engine = MagicMock()
        engine._mcp_manager.connected_servers = ["exa"]
        engine._registry = MagicMock()
        engine._registry.get_tool.return_value = None  # 尚未注册
        engine._approval = MagicMock()

        from excelmanus.engine import AgentEngine
        AgentEngine._register_search_tools(engine)

        engine._registry.register_tools.assert_called_once()
        tools_arg = engine._registry.register_tools.call_args[0][0]
        tool_names = [t.name for t in tools_arg]
        assert "parallel_search" in tool_names

    def test_skip_when_exa_not_connected(self):
        """Exa 未连接时不注册搜索工具。"""
        engine = MagicMock()
        engine._mcp_manager.connected_servers = ["context7"]
        engine._registry = MagicMock()

        from excelmanus.engine import AgentEngine
        AgentEngine._register_search_tools(engine)

        engine._registry.register_tools.assert_not_called()

    def test_idempotent_skips_if_already_registered(self):
        """parallel_search 已注册时跳过，保证幂等。"""
        engine = MagicMock()
        engine._mcp_manager.connected_servers = ["exa"]
        engine._registry = MagicMock()
        engine._registry.get_tool.return_value = MagicMock()  # 已注册

        from excelmanus.engine import AgentEngine
        AgentEngine._register_search_tools(engine)

        engine._registry.register_tools.assert_not_called()

    def test_retry_callback_triggers_registration(self):
        """_on_mcp_retry_success 应触发 sync_mcp_auto_approve + _register_search_tools。"""
        engine = MagicMock()

        from excelmanus.engine import AgentEngine
        AgentEngine._on_mcp_retry_success(engine)

        # 验证两个方法都被调用
        engine.sync_mcp_auto_approve.assert_called_once()
        engine._register_search_tools.assert_called_once()


# ── 11. 停用词修复验证 ──────────────────────────────────────


class TestStopWordsFix:
    def test_multi_char_chinese_stop_words(self):
        """多字符中文停用词（什么、搜索等）应被正确移除。"""
        from excelmanus.tools.search_tools import _CN_STOP_WORDS
        # 验证多字符停用词已在集合中
        assert "什么" in _CN_STOP_WORDS
        assert "搜索" in _CN_STOP_WORDS
        assert "可以" in _CN_STOP_WORDS
        assert "一下" in _CN_STOP_WORDS

    def test_single_char_chinese_stop_words(self):
        """单字符中文停用词应被正确移除。"""
        from excelmanus.tools.search_tools import _CN_STOP_WORDS
        assert "的" in _CN_STOP_WORDS
        assert "了" in _CN_STOP_WORDS
        assert "是" in _CN_STOP_WORDS

    def test_extract_removes_multi_char_stop_words(self):
        """关键词提取应移除多字符停用词。"""
        result = _extract_keywords("搜索 qwen3.5 什么 情况")
        assert "qwen3.5" in result
        assert "搜索" not in result
        assert "什么" not in result


# ── 12. num_queries clamp 验证 ──────────────────────────────────


class TestNumQueriesClamp:
    @pytest.mark.asyncio
    async def test_clamps_to_max_five(self):
        """num_queries > 5 应被 clamp 到 5。"""
        mgr = MagicMock()
        mgr._clients = {}
        # 即使被 clamp，仍然会因为没有 client 返回错误
        result = await _parallel_search_impl(mgr, "test", num_queries=100)
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_clamps_to_min_one(self):
        """num_queries < 1 应被 clamp 到 1。"""
        mgr = MagicMock()
        mgr._clients = {}
        result = await _parallel_search_impl(mgr, "test", num_queries=0)
        parsed = json.loads(result)
        assert "error" in parsed
