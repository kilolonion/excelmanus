"""元工具构建 — 从 AgentEngine 提取的 LLM-Native 工具 schema 构建逻辑。

包括：
- _build_meta_tools: 构建 skill / manage_skills / delegate / list_subagents / ask_user
- _build_v5_tools: 带脏标记缓存的工具 schema 构建
- _build_v5_tools_impl: 实际构建逻辑（EffectiveToolCatalog 投影）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from excelmanus.logger import get_logger

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

logger = get_logger("meta_tools")


class MetaToolBuilder:
    """元工具 schema 构建器。

    通过 ``self._engine`` 引用访问 AgentEngine 的路由器、注册表和技能状态。
    """

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    def build_meta_tools(self) -> list[dict[str, Any]]:
        """从 registry 已注册的元工具 ToolDef 投影 schema，不另造第二套定义。"""
        from excelmanus.tools.meta_tool_defs import (
            apply_meta_schema_enums,
            get_meta_tools,
            is_meta_tool,
            refresh_meta_tool_schemas,
        )

        e = self._engine
        refresh_meta_tool_schemas(e)
        registry = getattr(e, "_registry", None) or getattr(e, "registry", None)
        tools: list[dict[str, Any]] = []
        seen: set[str] = set()
        candidates = list(get_meta_tools())
        if registry is not None:
            getter = getattr(registry, "get_all_tools", None)
            if callable(getter):
                for tool in getter() or []:
                    name = str(getattr(tool, "name", "") or "")
                    if name and is_meta_tool(name):
                        candidates.append(tool)
        for tool in candidates:
            name = str(getattr(tool, "name", "") or "")
            if not name or name in seen:
                continue
            from_registry = False
            if registry is not None:
                registered = registry.get_tool(name)
                if (
                    registered is not None
                    and str(getattr(registered, "name", "") or "") == name
                    and isinstance(getattr(registered, "input_schema", None), dict)
                ):
                    tool = registered
                    from_registry = True
            if not from_registry:
                apply_meta_schema_enums(e, tool)
            to_schema = getattr(tool, "to_openai_schema", None)
            if callable(to_schema):
                schema = to_schema(mode="chat_completions")
                if isinstance(schema, dict):
                    tools.append(schema)
                    seen.add(name)
                    continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(getattr(tool, "description", "") or ""),
                        "parameters": getattr(tool, "input_schema", None) or {},
                    },
                }
            )
            seen.add(name)
        return tools

    def build_v5_tools(
        self,
        *,
        tool_access: str = "unknown",
    ) -> list[dict[str, Any]]:
        """构建工具 schema + 元工具（带脏标记缓存）。"""
        e = self._engine
        from excelmanus.prompt.envelope import catalog_fingerprint, sort_tool_schemas
        from excelmanus.system_one.host import turn_wire_profile
        from excelmanus.tools.catalog import catalog_from_engine

        catalog = catalog_from_engine(e, tool_access=tool_access)
        catalog_mode = getattr(catalog, "mode", None) or "write"
        from excelmanus.tools.meta_tool_defs import (
            _session_skill_names,
            _session_subagent_names,
        )
        cache_key = (
            tool_access,
            catalog_mode,
            frozenset(s.name for s in e._active_skills),
            frozenset(_session_skill_names(e)),
            frozenset(_session_subagent_names(e)),
            catalog.digest() if catalog is not None else catalog_fingerprint(e),
            turn_wire_profile(e),
            frozenset(getattr(e, "_loaded_tool_names", ()) or ()),
        )
        if e._tools_cache is not None and e._tools_cache_key == cache_key:
            return e._tools_cache
        tools = sort_tool_schemas(self.build_v5_tools_impl(
            tool_access=tool_access,
            catalog=catalog,
        ))
        from excelmanus.tools.policy import DEFAULT_DISCLOSURE_CORE_TOOLS
        from excelmanus.tools.runtime import schema_tool_name

        loaded = getattr(e, "_loaded_tool_names", None)
        if not isinstance(loaded, set):
            loaded = e._loaded_tool_names = set()
        loaded.update(schema_tool_name(item) for item in tools
                      if schema_tool_name(item) not in DEFAULT_DISCLOSURE_CORE_TOOLS)
        cache_key = (*cache_key[:-1], frozenset(loaded))
        e._tools_cache = tools
        e._tools_cache_key = cache_key
        return tools

    def build_v5_tools_impl(
        self,
        *,
        tool_access: str = "unknown",
        catalog: Any = None,
    ) -> list[dict[str, Any]]:
        """构建工具 schema + 元工具。

        可见集来自 EffectiveToolCatalog：read/plan 不把写效应工具交给模型。
        ``tool_access == "read_only"`` 把 write 会话压成 read 投影。
        默认核心 + 初始 profile + 本会话已披露的工具，与有效目录求交。
        SDK 和实际执行仍使用完整有效目录，不使用这里的展示投影。
        """
        from excelmanus.system_one.host import turn_wire_profile
        from excelmanus.system_one.packs import resolve_profile_tools
        from excelmanus.tools.catalog import catalog_from_engine
        from excelmanus.tools.meta_tool_defs import refresh_meta_tool_schemas
        from excelmanus.tools.runtime import schema_tool_name
        from excelmanus.tools.policy import DEFAULT_DISCLOSURE_CORE_TOOLS

        e = self._engine
        refresh_meta_tool_schemas(e)
        if catalog is None:
            catalog = catalog_from_engine(e, tool_access=tool_access)
        if catalog is not None:
            schemas = catalog.tool_schemas(schema_mode="chat_completions")
        else:
            schemas = e._registry.get_tiered_schemas(mode="chat_completions")
        names = {schema_tool_name(item) for item in schemas}
        # 无发现入口时保留全部授权 schema，否则隐藏能力无法再次获得。
        if "introspect_capability" not in names:
            return schemas
        loaded = frozenset(getattr(e, "_loaded_tool_names", ()) or ())
        profile = turn_wire_profile(e)
        initial = (
            resolve_profile_tools(profile, names)
            if profile != "full" else frozenset()
        )
        visible = DEFAULT_DISCLOSURE_CORE_TOOLS | initial | loaded
        return [item for item in schemas if schema_tool_name(item) in visible]
