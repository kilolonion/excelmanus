"""EffectiveToolCatalog：模型可见工具目录的唯一推导。

L2 执行目录 = SDK 绑定 = 桥许可 = introspect 源 = 策略可达集 = ``catalog_digest``。
``_turn_exposure`` **不进**本目录；披露收窄
只发生在 L4 ``envelope.tools``，不会改变执行权。
Registry 只持有注册快照；本模块做 mode / scope 投影，不改工具名与 schema 字段。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from excelmanus.tools.policy import (
    TOOL_CATEGORIES,
    TOOL_INTENT_ROUTES,
    READONLY_TOOL_ACTIONS,
    TOOL_SHORT_DESCRIPTIONS,
    is_catalog_visible,
    is_mutating_write_effect,
    normalize_write_effect,
)

CatalogMode = Literal["read", "plan", "write"]
OpenAISchemaMode = Literal["responses", "chat_completions"]

RUN_CODE_NAME = "run_code"
_VALID_MODES: frozenset[str] = frozenset({"read", "plan", "write"})
_READ_PLAN_MODES: frozenset[str] = frozenset({"read", "plan"})
_DELEGATE_META_NAMES: frozenset[str] = frozenset(
    {"delegate", "delegate_to_subagent", "parallel_delegate"}
)
_WORD_TOOLS: frozenset[str] = frozenset(
    {"read_word", "inspect_word", "search_word", "write_word"}
)
_PLAN_TOOLS: frozenset[str] = frozenset({"write_plan", "exit_plan_mode"})
_XLSX_SUFFIXES: frozenset[str] = frozenset(
    {".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb"}
)
_CSV_SUFFIXES: frozenset[str] = frozenset({".csv"})
_WORKBOOK_SUFFIXES: frozenset[str] = _XLSX_SUFFIXES | _CSV_SUFFIXES
_DOCX_SUFFIXES: frozenset[str] = frozenset({".docx"})
_DEFAULT_FAMILIES: frozenset[str] = frozenset({"xlsx"})
_CSV_ONLY_DISALLOWED: frozenset[str] = frozenset(
    {"trace_spreadsheet_formulas", "manage_spreadsheet_objects"}
)


def resolve_catalog_mode(
    *,
    chat_mode: str = "write",
    tool_access: str = "may_write",
) -> CatalogMode:
    """只按 chat_mode 与 read_only 权限推导目录；未知模式明确拒绝。"""
    chat = str(chat_mode).strip().lower()
    if chat not in _VALID_MODES:
        raise ValueError(f"unknown catalog mode: {chat_mode!r}")
    access = str(tool_access or "may_write").strip().lower() or "may_write"
    if chat == "read":
        return "read"
    if chat == "plan":
        return "plan"
    if access == "read_only":
        return "read"
    return "write"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "")


def _declared_effect(tool: Any) -> str:
    return normalize_write_effect(getattr(tool, "write_effect", "unknown"))


def _is_visible(
    tool: Any,
    mode: CatalogMode,
    *,
    allow_run_code: bool,
    families: frozenset[str] | None = None,
) -> bool:
    name = _tool_name(tool)
    if not name:
        return False
    families = _DEFAULT_FAMILIES if families is None else families
    visibility = str(getattr(tool, "visibility", "always") or "always")
    if name in _WORD_TOOLS and "docx" not in families:
        return False
    if (name.startswith("mcp_") or name == "parallel_search") and "mcp" not in families:
        return False
    if name in _PLAN_TOOLS:
        return mode == "plan"
    if mode == "read" and visibility == "hide_in_read":
        return False
    if mode in _READ_PLAN_MODES:
        if is_catalog_visible(name, _declared_effect(tool)):
            return True
        if mode == "plan" and visibility == "hide_in_read":
            return True
        return bool(allow_run_code and name == RUN_CODE_NAME)
    return True


def _description_for(tool: Any) -> str:
    name = _tool_name(tool)
    description = str(getattr(tool, "description", "") or "")
    if description:
        return _one_line(description)
    short = TOOL_SHORT_DESCRIPTIONS.get(name, "")
    if short:
        return _one_line(short)
    return _one_line(str(getattr(tool, "description", "") or ""))


def _prune_schema_node(node: Any, depth: int) -> tuple[Any, bool]:
    """出网投影裁剪：深度 >=3 的 description 剥离；字段名/type/enum/required 保留。"""
    if not isinstance(node, dict):
        return node, False
    out: dict[str, Any] = {}
    pruned = False
    for key, value in node.items():
        if key == "description" and depth >= 3:
            pruned = True
            continue
        if key in ("properties", "items") and isinstance(value, dict):
            if key == "items":
                sub, sub_pruned = _prune_schema_node(value, depth + 1)
            else:
                sub = {}
                sub_pruned = False
                for prop, pspec in value.items():
                    p, p_pruned = _prune_schema_node(pspec, depth + 1)
                    sub[prop] = p
                    sub_pruned = sub_pruned or p_pruned
            out[key] = sub
            pruned = pruned or sub_pruned
        else:
            out[key] = value
    return out, pruned


def _contains_ref(node: Any) -> bool:
    """子树是否含 $ref（pydantic $defs 引用）。"""
    if isinstance(node, dict):
        return any(k == "$ref" or _contains_ref(v) for k, v in node.items())
    if isinstance(node, list):
        return any(_contains_ref(v) for v in node)
    return False


def _prune_parameters_schema(parameters: dict[str, Any]) -> dict[str, Any]:
    """input_schema 的出网投影：剥离深层描述，补 tool_detail 指引。原 schema 不变。"""
    if not isinstance(parameters, dict):
        return parameters
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return parameters
    out = dict(parameters)
    new_props: dict[str, Any] = {}
    for name, pspec in properties.items():
        p, pruned = _prune_schema_node(pspec, 1)
        if isinstance(p, dict) and _contains_ref(p):
            desc = str(p.get("description") or "")
            p = {
                "type": "object",
                "description": (
                    f"{desc}（结构化规格；字段合同见 "
                    "introspect_capability query_type=tool_detail）"
                ).strip(),
            }
            pruned = True
        elif pruned and isinstance(p, dict):
            p = dict(p)
            p["description"] = (
                f"{p.get('description', '')}（字段级合同见 "
                "introspect_capability query_type=tool_detail）"
            ).strip()
        new_props[name] = p
    out["properties"] = new_props
    if not _contains_ref(new_props):
        out.pop("$defs", None)
    return out


def _prune_wire_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """对 to_openai_schema 返回的整包 schema 裁剪 parameters 部分。"""
    out = dict(schema)
    fn = out.get("function")
    if isinstance(fn, dict):
        fn = dict(fn)
        fn["parameters"] = _prune_parameters_schema(fn.get("parameters") or {})
        out["function"] = fn
    elif "parameters" in out:
        out["parameters"] = _prune_parameters_schema(out.get("parameters") or {})
    return out


@dataclass(frozen=True)
class EffectiveToolCatalog:
    """一次推导产出 schemas / 索引文本 / introspect 源 / digest。"""

    mode: CatalogMode
    tools: tuple[Any, ...]
    skill_names: tuple[str, ...] = ()

    def names(self) -> list[str]:
        return [_tool_name(tool) for tool in self.tools]

    def name_set(self) -> set[str]:
        return {name for name in self.names() if name}

    def contains(self, name: str) -> bool:
        return str(name) in self.name_set()

    def tool_schemas(self, schema_mode: OpenAISchemaMode = "chat_completions") -> list[dict[str, Any]]:
        """按工具名排序后的出网 tools 数组（本目录内的条目）。"""
        schemas: list[dict[str, Any]] = []
        for tool in self.tools:
            to_schema = getattr(tool, "to_openai_schema", None)
            if callable(to_schema):
                schema = to_schema(mode=schema_mode)
                if isinstance(schema, dict):
                    schemas.append(_prune_wire_parameters(schema))
                continue
            name = _tool_name(tool)
            if not name:
                continue
            parameters = _prune_parameters_schema(
                getattr(tool, "input_schema", None) or {},
            )
            if schema_mode == "chat_completions":
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": str(getattr(tool, "description", "") or ""),
                            "parameters": parameters,
                        },
                    }
                )
            else:
                schemas.append(
                    {
                        "type": "function",
                        "name": name,
                        "description": str(getattr(tool, "description", "") or ""),
                        "parameters": parameters,
                    }
                )
        return schemas

    def tool_index_text(self) -> str:
        """由当前目录生成的可用工具索引，不手写维护。"""
        if not self.tools:
            return ""
        visible = self.name_set()
        lines = ["## 可用工具"]
        emitted: set[str] = set()
        for _category, members in TOOL_CATEGORIES.items():
            present = [name for name in members if name in visible]
            if not present:
                continue
            for name in present:
                tool = self._tool_by_name(name)
                if tool is None:
                    continue
                lines.append(f"- {name} — {_description_for(tool)}")
                emitted.add(name)
        for tool in self.tools:
            name = _tool_name(tool)
            if not name or name in emitted:
                continue
            lines.append(f"- {name} — {_description_for(tool)}")
        routes = [
            f"{intent} → {', '.join(name for name in names if name in visible)}"
            + ("（本模式仅只读 action）" if self.mode in _READ_PLAN_MODES and any(name in READONLY_TOOL_ACTIONS for name in names) else "")
            for intent, names in TOOL_INTENT_ROUTES.items()
            if any(name in visible for name in names)
        ]
        if routes:
            lines.extend(["", "## 能力地图（当前目录）", *routes])
        return "\n".join(lines)

    def capability_map_text(self) -> str:
        """完整授权能力的短导航：意图/分类/名称，不注入参数或 SDK 声明。"""
        visible = self.name_set()
        if not visible:
            return ""
        routes = [
            f"{intent} → {', '.join(name for name in names if name in visible)}"
            for intent, names in TOOL_INTENT_ROUTES.items()
            if any(name in visible for name in names)
        ]
        covered = {
            name for names in TOOL_INTENT_ROUTES.values() for name in names if name in visible
        }
        for category, members in TOOL_CATEGORIES.items():
            remaining = [name for name in members if name in visible and name not in covered]
            if remaining:
                routes.append(f"{category} → {', '.join(remaining)}")
                covered.update(remaining)
        for tool in self.tools:
            name = _tool_name(tool)
            if not name or name in covered:
                continue
            # 未分类插件以名称 + 短描述提供发现线索；完整说明只按需查询。
            hint = _description_for(tool)
            if len(hint) > 80:
                hint = hint[:80] + "…"
            routes.append(f"{name}：{hint}" if hint else name)
        guidance = (
            "上列是当前授权能力；普通内置工具与 MCP 都可能尚未加载参数。"
            "用 introspect_capability 的 can_i_do/category_tools 查找能力，"
            "tool_detail 获取具体工具或字段详情后，下一步可直接调用；"
            "system_status 可列出完整目录。未展示 schema 不表示能力不可用。"
            if "introspect_capability" in visible else ""
        )
        return "\n".join(filter(None, ["## 能力地图（当前目录）", guidance, *routes]))

    def introspection_source(self) -> dict[str, Any]:
        """introspect / can_i_do 只扫当前目录。"""
        source: dict[str, Any] = {}
        for tool in self.tools:
            name = _tool_name(tool)
            if name:
                source[name] = tool
        return source

    def digest(self) -> str:
        """稳定内容摘要：排序 + 规范化 JSON。不含注册计数器。"""
        from excelmanus.tools.output_contracts import output_schema_for

        payload = {
            "mode": self.mode,
            "model_index": self.tool_index_text(),
            "skills": list(self.skill_names),
            "tools": [
                {
                    "description": str(getattr(tool, "description", "") or ""),
                    "name": _tool_name(tool),
                    "schema": getattr(tool, "input_schema", None) or {},
                    "output_schema": output_schema_for(_tool_name(tool), tool_def=tool),
                    "write_effect": _declared_effect(tool),
                }
                for tool in self.tools
            ],
        }
        encoded = _canonical_json(payload)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def _tool_by_name(self, name: str) -> Any | None:
        for tool in self.tools:
            if _tool_name(tool) == name:
                return tool
        return None


def derive_effective_catalog(
    *,
    tools: Sequence[Any] = (),
    mode: CatalogMode | str = "write",
    allowed: Sequence[str] | None = None,
    disallowed: Sequence[str] = (),
    extra_tools: Sequence[Any] = (),
    skill_names: Sequence[str] = (),
    allow_run_code: bool = False,
    families: frozenset[str] | None = None,
) -> EffectiveToolCatalog:
    """从 registry 快照 + mode + scope 推导目录，未知模式不得放宽到 write。"""
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown catalog mode: {mode!r}")
    resolved: CatalogMode = mode  # type: ignore[assignment]
    merged: dict[str, Any] = {}
    for tool in tools:
        name = _tool_name(tool)
        if name:
            merged[name] = tool
    for tool in extra_tools:
        name = _tool_name(tool)
        if name:
            merged.setdefault(name, tool)

    blocked = {str(name) for name in disallowed if str(name)}
    if allowed is not None:
        keep = {str(name) for name in allowed if str(name)} - blocked
        merged = {name: tool for name, tool in merged.items() if name in keep}
    elif blocked:
        merged = {name: tool for name, tool in merged.items() if name not in blocked}

    visible = tuple(
        merged[name]
        for name in sorted(merged)
        if _is_visible(
            merged[name],
            resolved,
            allow_run_code=allow_run_code,
            families=families,
        )
    )
    skills = tuple(sorted({str(name) for name in skill_names if str(name).strip()}))
    return EffectiveToolCatalog(mode=resolved, tools=visible, skill_names=skills)


def _skill_names_of(engine: Any) -> tuple[str, ...]:
    router = getattr(engine, "_skill_router", None)
    if router is None:
        return ()
    blocked = ()
    resolver = getattr(engine, "_skill_resolver", None)
    if resolver is not None:
        getter = getattr(resolver, "blocked_skillpacks", None)
        if callable(getter):
            try:
                raw_blocked = getter()
            except Exception:
                raw_blocked = None
            if isinstance(raw_blocked, (list, tuple, set, frozenset)):
                blocked = tuple(str(name) for name in raw_blocked)
    list_names = getattr(router, "list_skill_names", None)
    if not callable(list_names):
        return ()
    try:
        names = list_names(blocked_skillpacks=blocked)
    except TypeError:
        try:
            names = list_names()
        except Exception:
            return ()
    except Exception:
        return ()
    if not isinstance(names, (list, tuple)):
        return ()
    return tuple(str(name) for name in names if str(name).strip())


def _allow_run_code_of(engine: Any, mode: CatalogMode) -> bool:
    if mode != "read":
        return False
    sub = getattr(engine, "_subagent_config", None)
    if sub is None:
        return False
    return getattr(sub, "permission_mode", None) == "readOnly"


def inspect_workspace_catalog(root: str | None) -> dict[str, Any]:
    """扫描 uploads/outputs/顶层，得到附件族与是否已有表格。"""
    from pathlib import Path

    has_xlsx = False
    has_csv = False
    has_docx = False
    if root:
        base = Path(root)
        scan: list[Path] = []
        if base.is_dir():
            scan.append(base)
            uploads = base / "uploads"
            outputs = base / "outputs"
            if uploads.is_dir():
                scan.append(uploads)
            if outputs.is_dir():
                scan.append(outputs)
        for folder in scan:
            try:
                iterator = folder.rglob("*") if folder != base else folder.iterdir()
            except OSError:
                continue
            for path in iterator:
                try:
                    if not path.is_file():
                        continue
                except OSError:
                    continue
                suf = path.suffix.lower()
                if suf in _XLSX_SUFFIXES:
                    has_xlsx = True
                elif suf in _CSV_SUFFIXES:
                    has_csv = True
                elif suf in _DOCX_SUFFIXES:
                    has_docx = True
                if has_xlsx and has_csv and has_docx:
                    break
    families: set[str] = set()
    if has_xlsx:
        families.add("xlsx")
    if has_csv:
        families.add("csv")
    if has_docx:
        families.add("docx")
    if not families:
        families.add("xlsx")
    if has_csv and not has_xlsx:
        profile = "csv"
    elif has_docx and not has_xlsx:
        profile = "docx"
    else:
        profile = "xlsx"
    return {
        "families": frozenset(families),
        "new_workbook": not has_xlsx,
        "profile": profile,
    }


def _registered_mcp_present(registered: Sequence[Any]) -> bool:
    """合并后注册表内存在 MCP 工具（mcp_* / parallel_search）。"""
    return any(
        _tool_name(tool).startswith("mcp_") or _tool_name(tool) == "parallel_search"
        for tool in registered
    )


def _workspace_flags(engine: Any) -> dict[str, Any]:
    config = getattr(engine, "config", None)
    root = getattr(config, "workspace_root", None)
    return inspect_workspace_catalog(str(root) if root else None)


def _families_with_mcp(flags: dict[str, Any], registered: Sequence[Any]) -> frozenset[str]:
    """文件族 + 合并注册后的 MCP 标记。MCP 取合并后的注册表，不取缓存。"""
    families: frozenset[str] = flags["families"]
    if _registered_mcp_present(registered):
        return families | {"mcp"}
    return families


def execution_catalog_from_engine(
    engine: Any,
    *,
    tool_access: str = "may_write",
) -> EffectiveToolCatalog | None:
    """执行目录：SDK 绑定 / 桥分发 / introspect / 能力地图 / 策略段的共同数据源。

    与 ``catalog_from_engine`` 使用同一权限投影。无真实 registry 返回 None。
    """
    from excelmanus.tools.registry import ToolRegistry

    registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
    if not isinstance(registry, ToolRegistry):
        return None
    from excelmanus.tools.context import capability_from_engine
    from excelmanus.tools.meta_tool_defs import refresh_meta_tool_schemas

    refresh_meta_tool_schemas(engine)
    cap = getattr(engine, "_fixed_capability", None) or capability_from_engine(engine)
    access = "read_only" if cap.tool_access == "read_only" else tool_access
    mode = resolve_catalog_mode(
        chat_mode=cap.catalog_mode,
        tool_access=access,
    )
    flags = _workspace_flags(engine)
    profile = str(flags.get("profile") or "xlsx")
    registered = registry.get_all_tools()
    families = _families_with_mcp(flags, registered)
    disallowed: list[str] = list(cap.disallowed_tools)
    from excelmanus.self_management import disallowed_tools
    disallowed.extend(disallowed_tools(engine))
    if profile == "csv":
        disallowed.extend(_CSV_ONLY_DISALLOWED)
    return derive_effective_catalog(
        tools=registered,
        mode=mode,
        allowed=None if cap.allowed_tools is None else list(cap.allowed_tools),
        disallowed=disallowed,
        skill_names=_skill_names_of(engine),
        allow_run_code=_allow_run_code_of(engine, mode),
        families=families,
    )


def catalog_from_engine(
    engine: Any,
    *,
    tool_access: str = "may_write",
) -> EffectiveToolCatalog | None:
    """从引擎推导 L2 目录并绑到 registry / introspect。失败返回 None。

    ``_turn_exposure`` 不进入 mode：digest 只跟 chat_mode /
    工作区族 / 注册表走。披露收窄留给 L4 ``build_v5_tools_impl``。
    """
    from excelmanus.tools.registry import ToolRegistry

    registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
    if not isinstance(registry, ToolRegistry):
        return None

    from excelmanus.tools.context import capability_from_engine
    from excelmanus.tools.meta_tool_defs import refresh_meta_tool_schemas

    refresh_meta_tool_schemas(engine)
    cap = getattr(engine, "_fixed_capability", None) or capability_from_engine(engine)
    access = "read_only" if cap.tool_access == "read_only" else tool_access
    mode = resolve_catalog_mode(
        chat_mode=cap.catalog_mode,
        tool_access=access,
    )
    skill_names = _skill_names_of(engine)
    allow_run_code = _allow_run_code_of(engine, mode)
    flags = _workspace_flags(engine)
    profile = str(flags.get("profile") or "xlsx")
    engine._catalog_new_workbook = bool(flags["new_workbook"])
    engine._catalog_profile = profile
    disallowed = tuple(cap.disallowed_tools) + (tuple(_CSV_ONLY_DISALLOWED) if profile == "csv" else ())
    from excelmanus.self_management import disallowed_tools
    disallowed += tuple(disallowed_tools(engine))
    allowed = None if cap.allowed_tools is None else list(cap.allowed_tools)
    registered = registry.get_all_tools()
    families = _families_with_mcp(flags, registered)
    engine._catalog_families = families
    catalog = derive_effective_catalog(
        tools=registered,
        mode=mode,
        allowed=allowed,
        disallowed=disallowed,
        skill_names=skill_names,
        allow_run_code=allow_run_code,
        families=families,
    )
    registry.bind_catalog(
        mode=mode,
        allowed=allowed,
        disallowed=disallowed,
        skill_names=skill_names,
        allow_run_code=allow_run_code,
        families=families,
    )
    return catalog


def bind_engine_catalog(
    engine: Any,
    *,
    tool_access: str = "may_write",
) -> EffectiveToolCatalog | None:
    """plan/child 切 mode 后重绑有效目录。"""
    return catalog_from_engine(engine, tool_access=tool_access)


def schema_name(schema: Mapping[str, Any] | None) -> str:
    if not isinstance(schema, dict):
        return ""
    func = schema.get("function")
    if isinstance(func, dict) and func.get("name"):
        return str(func.get("name") or "")
    return str(schema.get("name") or "")


def filter_meta_schemas(
    schemas: Sequence[Mapping[str, Any]],
    *,
    mode: CatalogMode,
) -> list[dict[str, Any]]:
    """按 ToolDef.visibility 投影元工具 schema（兼容旧测试入口）。"""
    hide_in_read = _DELEGATE_META_NAMES | {"manage_skills", "list_subagents"}
    result: list[dict[str, Any]] = []
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        name = schema_name(schema)
        if mode == "read" and name in hide_in_read:
            continue
        result.append(dict(schema))
    return result
