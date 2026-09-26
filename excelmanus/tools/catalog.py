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
# 文件存在性不是写权限：apply_spreadsheet_changes 可通过 workbook_spec 新建，
# 不要求预先存在 xlsx。mode / allowed / disallowed 仍统一决定真实授权。
_CSV_ONLY_DISALLOWED: frozenset[str] = frozenset({"trace_spreadsheet_formulas"})
# CSV 不包含公式，追踪工具仍需已有工作簿；以下迁移路线只提示当前可用工具。
# 它们可写出第一个 xlsx，让下一轮目录提供依赖已有工作簿的能力。
# 只列产出 xlsx 的仍可见工具，且调用方必须按当前目录过滤后才对外播报。
_CSV_BOOTSTRAP_TOOLS: tuple[str, ...] = (
    "convert_spreadsheet",
    "query_spreadsheet",
    "split_spreadsheet",
)
# 目录扫描只覆盖工作区顶层、uploads/**、outputs/**；写到这里才会被看到。
_CSV_BOOTSTRAP_LOCATION: str = "outputs/（或工作区顶层）"


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
        if key in ("oneOf", "anyOf", "allOf", "prefixItems") and isinstance(value, list):
            items = [_prune_schema_node(branch, depth) for branch in value]
            out[key] = [item for item, _ in items]
            pruned = pruned or any(flag for _, flag in items)
        elif key in ("additionalProperties", "if", "then", "else", "not") and isinstance(value, dict):
            sub, flag = _prune_schema_node(value, depth + 1)
            out[key] = sub
            pruned = pruned or flag
        elif key in ("properties", "items") and isinstance(value, dict):
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


_WIRE_SCHEMA_THRESHOLD = 7500


def _schema_ref_id(tool_name: str, parameters: dict[str, Any]) -> str:
    from excelmanus.tools.schema_registry import schema_id

    return schema_id(tool_name, parameters)


_JSON_SCHEMA_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _known_outer_types(
    node: Any,
    root: dict[str, Any],
    *,
    seen: frozenset[str] = frozenset(),
    depth: int = 0,
    budget: list[int] | None = None,
) -> tuple[str, ...] | None:
    """A conservative outer type bound; never expand property trees.

    Every allowed value must satisfy an explicit type or each allOf branch.
    A union is bounded only if all its branches have known types. Unknown,
    remote and recursive pure references stay untyped, rather than becoming
    objects. oneOf branches lose their inner constraints during disclosure,
    so expose a type union instead of falsely making broad shapes exclusive.
    """
    remaining = [128] if budget is None else budget
    if not isinstance(node, dict) or depth >= 16 or remaining[0] <= 0:
        return None
    remaining[0] -= 1
    declared = node.get("type")
    if isinstance(declared, str) and declared in _JSON_SCHEMA_TYPES:
        return (declared,)
    if (
        isinstance(declared, list) and declared
        and all(isinstance(item, str) and item in _JSON_SCHEMA_TYPES for item in declared)
    ):
        return tuple(dict.fromkeys(declared))
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
        from excelmanus.tools.schema_walk import resolve_local_ref

        target = resolve_local_ref(root, ref)
        types = _known_outer_types(target, root, seen=seen | {ref}, depth=depth + 1, budget=remaining)
        if types is not None:
            return types
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if not isinstance(branches, list) or not branches or len(branches) > 32:
            continue
        known = [_known_outer_types(branch, root, seen=seen, depth=depth + 1, budget=remaining) for branch in branches]
        if all(types is not None for types in known):
            return tuple(dict.fromkeys(kind for types in known if types is not None for kind in types))
    branches = node.get("allOf")
    if isinstance(branches, list) and len(branches) <= 32:
        # Keeping any proven conjunct is a safe (possibly wider) disclosure.
        # The full host schema still validates every actual call.
        for branch in branches:
            types = _known_outer_types(branch, root, seen=seen, depth=depth + 1, budget=remaining)
            if types is not None:
                return types
    return None


def _deferred_field_shape(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Keep honest outer shapes and annotations while deferring nested refs."""
    projected: dict[str, Any] = {}
    types = _known_outer_types(node, root)
    if types:
        projected["type"] = types[0] if len(types) == 1 else list(types)
        if "array" in types:
            projected["items"] = {}
    for key in ("examples", "default"):
        if key in node:
            projected[key] = node[key]
    return projected


def _prune_parameters_schema(
    parameters: dict[str, Any],
    *,
    schema_name: str = "",
) -> dict[str, Any]:
    """input_schema 的出网投影：剥离深层描述，补 tool_detail 指引。原 schema 不变。"""
    if not isinstance(parameters, dict):
        return parameters
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return parameters
    out = dict(parameters)
    new_props: dict[str, Any] = {}
    pruned_any = False
    for name, pspec in properties.items():
        p, pruned = _prune_schema_node(pspec, 1)
        if isinstance(p, dict) and _contains_ref(p):
            desc = str(p.get("description") or "")
            p = _deferred_field_shape(p, parameters)
            p["description"] = (
                f"{desc}（完整字段合同见 "
                "introspect_capability query_type=tool_detail）"
            ).strip()
            pruned = True
        elif pruned and isinstance(p, dict):
            p = dict(p)
            p["description"] = (
                f"{p.get('description', '')}（字段级合同见 "
                "introspect_capability query_type=tool_detail）"
            ).strip()
        new_props[name] = p
        pruned_any = pruned_any or pruned
    out["properties"] = new_props
    if not _contains_ref(new_props):
        out.pop("$defs", None)
    try:
        wire_size = len(_canonical_json(out))
    except Exception:
        wire_size = 0
    if pruned_any or wire_size > _WIRE_SCHEMA_THRESHOLD:
        from excelmanus.tools.schema_registry import register_schema

        # Keep the handle resolvable even for direct callers that do not pass a
        # tool name.  Returning a digest without registering it would expose a
        # dead introspection reference.
        handle = register_schema(schema_name, parameters)
        out["x-excelmanus-schema-ref"] = {
            "id": handle,
            "version": 1,
            "tool": schema_name or None,
            "query": f"tool_detail {schema_name}" if schema_name else "tool_detail <tool>",
            "note": "完整字段合同按需从 introspect_capability 获取；执行仍使用宿主完整 schema。",
        }
    return out


def _prune_wire_parameters(schema: dict[str, Any], *, schema_name: str = "") -> dict[str, Any]:
    """对 to_openai_schema 返回的整包 schema 裁剪 parameters 部分。"""
    out = dict(schema)
    fn = out.get("function")
    if isinstance(fn, dict):
        fn = dict(fn)
        fn["parameters"] = _prune_parameters_schema(fn.get("parameters") or {}, schema_name=schema_name)
        out["function"] = fn
    elif "parameters" in out:
        out["parameters"] = _prune_parameters_schema(out.get("parameters") or {}, schema_name=schema_name)
    return out


def _pruned_schema_for_digest(tool: Any) -> dict[str, Any]:
    """Schema material included in the catalog digest.

    The digest must change when structured-reference fields are enabled, while
    remaining independent of long prose descriptions that are intentionally
    removed from the model wire projection.
    """
    from excelmanus.tools.reference_contract import augment_reference_schema

    return augment_reference_schema(getattr(tool, "input_schema", None) or {})


@dataclass(frozen=True)
class EffectiveToolCatalog:
    """一次推导产出 schemas / 索引文本 / introspect 源 / digest。

    ``gate_notes`` 只描述“已注册但被当前 mode/profile 门控”的写工具及其
    解锁路径；它不扩大可见集，也不进入 ``introspection_source``。
    """

    mode: CatalogMode
    tools: tuple[Any, ...]
    skill_names: tuple[str, ...] = ()
    gate_notes: tuple[str, ...] = ()

    def names(self) -> list[str]:
        return [_tool_name(tool) for tool in self.tools]

    def name_set(self) -> set[str]:
        return {name for name in self.names() if name}

    def contains(self, name: str) -> bool:
        return str(name) in self.name_set()

    def gated_reason(self, name: str) -> str:
        """工具不在本目录时的门控说明；可见或未知返回空串。"""
        tool = str(name or "").strip()
        if not tool or tool in self.name_set():
            return ""
        for note in self.gate_notes:
            if tool in note:
                return note
        return ""

    def tool_schemas(self, schema_mode: OpenAISchemaMode = "chat_completions") -> list[dict[str, Any]]:
        """按工具名排序后的出网 tools 数组（本目录内的条目）。"""
        schemas: list[dict[str, Any]] = []
        for tool in self.tools:
            to_schema = getattr(tool, "to_openai_schema", None)
            if callable(to_schema):
                schema = to_schema(mode=schema_mode)
                if isinstance(schema, dict):
                    schemas.append(_prune_wire_parameters(schema, schema_name=_tool_name(tool)))
                continue
            name = _tool_name(tool)
            if not name:
                continue
            from excelmanus.tools.reference_contract import augment_reference_schema

            parameters = _prune_parameters_schema(
                augment_reference_schema(getattr(tool, "input_schema", None) or {}),
                schema_name=name,
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
            "需要 ExcelManus 的流程、设计、配置或限制时，用 knowledge_index 浏览、knowledge_search 定位后 knowledge_read 取正文；knowledge_spec 查工具规范与示例。"
            if "introspect_capability" in visible else ""
        )
        sections = ["## 能力地图（当前目录）", guidance, *routes]
        if self.gate_notes:
            # 门控不是不存在：把解锁路径写进目录本身，避免模型靠猜或绕过守卫。
            sections.extend(["", "## 被门控（工具仍在，可解锁）", *self.gate_notes])
        return "\n".join(filter(None, sections))

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

        # gate_notes 不进 digest：它只影响能力地图文案（由 system_digest 覆盖），
        # 而 digest 必须与 registry.catalog_digest() 保持一致（绑定投影无 gates）。
        payload = {
            "mode": self.mode,
            "model_index": self.tool_index_text(),
            "skills": list(self.skill_names),
            "tools": [
                {
                    "description": str(getattr(tool, "description", "") or ""),
                    "name": _tool_name(tool),
                    "schema": _pruned_schema_for_digest(tool),
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
    gate_notes: Sequence[str] = (),
) -> EffectiveToolCatalog:
    """从 registry 快照 + mode + scope 推导目录，未知模式不得放宽到 write。

    ``gate_notes`` 是给发现层的“被门控 + 解锁”说明，不参与可见集判定。
    """
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
    gates = tuple(str(note) for note in gate_notes if str(note).strip())
    return EffectiveToolCatalog(
        mode=resolved, tools=visible, skill_names=skills, gate_notes=gates
    )


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
                if suf in _XLSX_SUFFIXES and not has_xlsx:
                    from excelmanus.workbook.file_format import is_workbook_file

                    has_xlsx = is_workbook_file(path)
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


def workspace_catalog_profile(engine: Any) -> str:
    """当前工作区的目录 profile（csv / docx / xlsx）。"""
    return str(_workspace_flags(engine).get("profile") or "xlsx")


def csv_profile_bootstrap_hint(visible_names: Any = None) -> str:
    """csv-only 工作区产出第一个 xlsx 的可执行细节（按需查询用，不进 prompt）。

    ``visible_names`` 传入当前有效目录名集合时，只播报该集合里真实存在的
    工具，绝不指向当前不可调用的 API；``None`` 表示调用方已知目录。
    """
    available = None if visible_names is None else {str(name) for name in visible_names}
    calls: list[str] = []
    if available is None or "convert_spreadsheet" in available:
        calls.append(
            "convert_spreadsheet(file_path='uploads/<数据>.csv', "
            "output_path='outputs/<结果>.xlsx', mode='preserve')"
        )
    if available is None or "query_spreadsheet" in available:
        calls.append(
            "query_spreadsheet(sources=[{'file_path': 'uploads/<数据>.csv'}], "
            "sql='SELECT * FROM data1', output_path='outputs/<结果>.xlsx')"
        )
    named = "；".join(calls)
    head = (
        f"可执行解锁步骤：{named}。"
        if named
        else "当前目录没有可确认的转换调用；可由用户上传有效 xlsx。"
    )
    return (
        head
        + f"xlsx 必须落在 {_CSV_BOOTSTRAP_LOCATION}才会被目录扫描到；"
        "下一轮目录仅重新评估文件族门控，仍受模式和会话授权限制。"
    )


def _csv_gate_notes(
    *,
    profile: str,
    mode: CatalogMode | str,
    registered: Sequence[Any],
    disallowed: Sequence[str],
    allowed: Sequence[str] | None,
) -> tuple[str, ...]:
    """csv-only 工作区“依赖已有工作簿的工具被 profile 门控”的单行指路。

    只保留模型最需要的事实（工具仍注册、被 profile 门控、解锁靠 outputs 下
    的第一个 xlsx），不展开调用示例：system prompt 里已有
    ``14_csv_bootstrap`` 策略段承担完整路线，避免同一段 prompt 重复。
    解锁指针只在工具真实可见时才点名（``bootstrap`` 已按目录过滤）。

    注意：工作簿写工具不再按 CSV profile 门控（新建不依赖已有 xlsx），
    当前 ``_CSV_ONLY_DISALLOWED`` 只含依赖已有工作簿的追踪类工具。
    """
    if str(profile) != "csv" or str(mode) != "write":
        return ()
    names = {_tool_name(tool) for tool in registered}
    gated = sorted(name for name in _CSV_ONLY_DISALLOWED if name in names)
    if not gated:
        return ()
    blocked = {str(name) for name in disallowed}
    keep = None if allowed is None else {str(name) for name in allowed}
    bootstrap = {
        name
        for name in _CSV_BOOTSTRAP_TOOLS
        if name in names and name not in blocked and (keep is None or name in keep)
    }
    if "convert_spreadsheet" in bootstrap:
        unlock = ("解锁：先用 apply_spreadsheet_changes(workbook_spec=...) 新建，"
                  "或用 convert_spreadsheet 写出 outputs/ 下的第一个 xlsx")
    elif bootstrap:
        unlock = "解锁：可用 " + "、".join(sorted(bootstrap)) + " 写出 outputs/ 下的第一个 xlsx"
    else:
        unlock = "解锁：由用户上传有效 xlsx；当前没有已确认可用的转换工具"
    return (
        "被 profile 门控（工具仍注册，只是不进当前目录）："
        + "、".join(gated)
        + "——工作区只有 CSV、尚无 xlsx；"
        + unlock
        + "，下一轮重评估文件族门控（仍受模式和会话授权限制）。",
    )


def gated_tool_reason(
    engine: Any,
    name: str,
    *,
    catalog: EffectiveToolCatalog | None = None,
) -> str:
    """工具在当前有效目录中不可见时的原因与解锁路径；可见/未知返回空串。

    只依据当前有效目录与工作区事实作答，供 introspect_capability 与报错
    文案消费：把“工具不存在”换成“被什么门控、怎么解锁”。``catalog`` 可
    由调用方传入已绑定的目录（``catalog.gated_reason``），避免重复推导。
    """
    tool = str(name or "").strip()
    if not tool:
        return ""
    if catalog is None and engine is not None:
        catalog = execution_catalog_from_engine(engine)
    if catalog is not None and tool in catalog.name_set():
        return ""
    cap = getattr(engine, "_fixed_capability", None)
    allowed = getattr(cap, "allowed_tools", None)
    fixed_blocked = set(getattr(cap, "disallowed_tools", ()) or ())
    if tool in fixed_blocked or (allowed is not None and tool not in allowed):
        gate = "disallowed_tools" if tool in fixed_blocked else "allowed_tools"
        return (f"{tool} 被当前会话授权限制（{gate}）；需要授权方调整权限。"
                "创建 xlsx、查询详情或 configure_agent 均不能扩大这项授权。")
    if catalog is not None:
        note = catalog.gated_reason(tool)
        if note:
            # 目录里的 gate note 是给 prompt 的单行指路；按需查询补可执行细节。
            if tool in _CSV_ONLY_DISALLOWED:
                return note + " " + csv_profile_bootstrap_hint(catalog.name_set())
            return note
    if engine is None:
        return ""
    registry = getattr(engine, "_registry", None) or getattr(engine, "registry", None)
    getter = getattr(registry, "get_all_tools", None)
    registered = {_tool_name(t) for t in getter()} if callable(getter) else set()
    if registered and tool not in registered:
        return f"{tool} 未注册在当前宿主注册表：不是目录门控，本会话无法调用。"

    mode = catalog.mode if catalog is not None else ""
    if mode in _READ_PLAN_MODES:
        return (
            f"{tool} 是写效应工具，当前目录模式为 {mode}（只读/计划），故不进目录；"
            "切到 write 模式后即可调用。"
        )
    cap = getattr(engine, "_fixed_capability", None)
    blocked = {str(n) for n in (getattr(cap, "disallowed_tools", ()) or ())}
    try:
        from excelmanus.self_management import disallowed_tools

        blocked |= {str(n) for n in (disallowed_tools(engine) or ())}
    except Exception:
        pass
    if tool in blocked:
        return (
            f"{tool} 被当前会话授权禁用（工具仍注册）：若它只是被暂停，"
            "可用 configure_agent enable_tools 恢复。"
        )
    if workspace_catalog_profile(engine) == "csv" and tool in _CSV_ONLY_DISALLOWED:
        return (
            f"{tool} 被 csv-only profile 门控（工作区只有 CSV、尚无 xlsx），不是被删除。"
            + csv_profile_bootstrap_hint(catalog.name_set() if catalog is not None else None)
        )
    return (
        f"{tool} 不在当前有效目录中：可能被 mode / 文件族 / 会话授权过滤。"
        "用 category_tools 查询当前可用能力。"
    )


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
    allowed = None if cap.allowed_tools is None else list(cap.allowed_tools)
    return derive_effective_catalog(
        tools=registered,
        mode=mode,
        allowed=allowed,
        disallowed=disallowed,
        skill_names=_skill_names_of(engine),
        allow_run_code=_allow_run_code_of(engine, mode),
        families=families,
        gate_notes=_csv_gate_notes(
            profile=profile,
            mode=mode,
            registered=registered,
            disallowed=disallowed,
            allowed=allowed,
        ),
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
    gate_notes = _csv_gate_notes(
        profile=profile,
        mode=mode,
        registered=registered,
        disallowed=disallowed,
        allowed=allowed,
    )
    engine._catalog_gate_notes = gate_notes
    catalog = derive_effective_catalog(
        tools=registered,
        mode=mode,
        allowed=allowed,
        disallowed=disallowed,
        skill_names=skill_names,
        allow_run_code=allow_run_code,
        families=families,
        gate_notes=gate_notes,
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
