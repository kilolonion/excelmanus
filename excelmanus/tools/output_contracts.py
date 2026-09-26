"""Code Mode SDK 返回合同：声明与运行时校验同源。

设计约束（code-mode-repair-plan P1-A）：
- 内置表格合同只声明**顶层键**；不展开单元格矩阵/嵌套规格。
  自定义/MCP output_schema 可声明嵌套结构，按需自检，不全量塞入 prompt。
- ``required`` 是所有成功返回必须有的键；``optional`` 列出模型可依赖但
  不保证出现的键；``branches`` 记录模式分支（如 analyze 的 mode）下
  额外必有的键——``value["mode"]`` 命中分支时才校验。
- ``required_types`` 对必有键做 str/dict/list/bool 等类型检查。
- 文本工具保持 str；交互工具用 schema 明确对象/数组/文本分支。
- 自定义工具的 output_schema 优先；未声明的外部工具保持未知。
- 合同声明必须有运行时校验配套：Native/SDK 共用 ``validate_output``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from itertools import islice
from typing import Any


@dataclass(frozen=True)
class OutputContract:
    """单个工具的返回顶层键合同。"""

    required: frozenset[str] = frozenset()
    optional: frozenset[str] = frozenset()
    required_types: dict[str, str] = field(default_factory=dict)
    # 可选键出现时也做类型检查（如 applied 仅编辑分支有，但有就必须是 list）。
    optional_types: dict[str, str] = field(default_factory=dict)
    # 模式分支：value[mode_key] 等于分支名时，额外要求这些键。
    mode_key: str | None = None
    branches: dict[str, frozenset[str]] = field(default_factory=dict)
    # 参数分支：arguments[arg_mode_key] 非空时查 ``"present"`` 分支，
    # 为空/缺失时查 ``"absent"`` 分支；未传 arguments 时跳过该校验。
    # 例：apply_spreadsheet_changes 返回统一 receipt/files/observation；
    # 不为 WorkbookSpec 和 operations 维护两套旧返回合同。
    arg_mode_key: str | None = None
    arg_branches: dict[str, frozenset[str]] = field(default_factory=dict)
    # object = 成功 value 必须是 dict；str = 整段字符串；any = 不校验。
    value_kind: str = "object"
    schema: dict[str, Any] | None = None

    def as_schema(self) -> dict[str, Any]:
        if self.schema is not None:
            return deepcopy(self.schema)
        if self.value_kind == "str":
            return {"type": "string"}
        if self.value_kind == "any":
            return {}
        types = {"str": "string", "dict": "object", "list": "array", "int": "integer", "bool": "boolean", "float": "number"}
        declared = {**self.required_types, **self.optional_types}
        schema: dict[str, Any] = {
            "type": "object", "required": sorted(self.required),
            "properties": {key: {"type": types[declared[key]]} if key in declared else {}
                           for key in sorted(self.required | self.optional)},
        }
        if self.mode_key:
            branches = []
            for branch, keys in self.branches.items():
                condition: dict[str, Any] = {"const": branch}
                for key in reversed(self.mode_key.split(".")):
                    condition = {"type": "object", "required": [key], "properties": {key: condition}}
                branches.append({"if": condition, "then": {"required": sorted(keys)}})
            if branches:
                schema["allOf"] = branches
        if self.arg_mode_key:
            schema["x-argument-branches"] = {
                "key": self.arg_mode_key,
                "required": {branch: sorted(keys) for branch, keys in self.arg_branches.items()},
            }
        return schema

    def render_return_hint(self) -> str:
        """SDK 段里的一行返回形状提示，如 ``dict{status, file_path, applied?…}``。

        必有键在前、可选键带 ``?`` 后缀——脚本据此判断哪些键可直接读、
        哪些要先 ``in`` 检查（如 apply_spreadsheet_changes 的 observation 为可选字段）。
        """
        if self.schema is not None:
            return _schema_return_hint(self.schema)
        if self.value_kind == "str":
            return "str"
        if self.value_kind == "any":
            return "Any"
        req = sorted(self.required)
        parts = req + [f"{key}?" for key in sorted(self.optional)]
        if not parts:
            return "dict"
        text = "{" + ", ".join(parts) + "}"
        if len(text) > 150:
            head = ", ".join(req)
            preferred_names = (
                "applied", "observation", "warnings",
                "selection", "coverage", "content_version",
            )
            preferred = [
                f"{name}?" for name in preferred_names if name in self.optional
            ][:3]
            visible = ", ".join([head, *preferred]) if head else ", ".join(preferred)
            text = "{" + (visible + ", …" if visible else "…") + "}"
        return f"dict{text}"


def _obj(
    required: dict[str, str],
    optional: set[str] | frozenset[str] | None = None,
    *,
    mode_key: str | None = None,
    branches: dict[str, frozenset[str]] | None = None,
    arg_mode_key: str | None = None,
    arg_branches: dict[str, frozenset[str]] | None = None,
    optional_types: dict[str, str] | None = None,
) -> OutputContract:
    return OutputContract(
        required=frozenset(required),
        optional=frozenset(optional or ()),
        required_types=dict(required),
        optional_types=dict(optional_types or {}),
        mode_key=mode_key,
        branches=dict(branches or {}),
        arg_mode_key=arg_mode_key,
        arg_branches=dict(arg_branches or {}),
        value_kind="object",
    )


# ── 已核实的成功返回顶层键（真实工具输出驱动，非猜测） ──
OUTPUT_CONTRACTS: dict[str, OutputContract] = {
    "observe_spreadsheet": _obj({"status": "str", "schema_version": "str", "file_path": "str", "content_version": "str"}, {"observation_id", "snapshot_id", "request", "sheets", "regions", "coverage", "file", "active_sheet", "matches"}),
    "preview_spreadsheet": _obj({"status": "str", "schema_version": "str", "file_path": "str", "content_version": "str", "attachment_id": "str", "render_id": "str"}, {"snapshot_id", "observation_id", "surface", "sheet", "range", "renderer_digest", "measured", "geometry", "coverage", "limitations", "objects", "source_pixel_size", "attachment_pixel_size", "request_pixel_size", "source_to_attachment", "cell_to_pixel_map", "renderer_version", "font_fingerprint", "locale", "dpi", "zoom", "device_scale"}),
    "apply_spreadsheet_changes": _obj({"status": "str", "schema_version": "str", "files": "list", "receipt": "dict", "committed": "bool", "applied": "list"}, {"file_path", "content_version", "previous_version", "observation", "document", "operation_id", "dry_run", "planned"}, optional_types={"observation":"dict", "document":"dict", "dry_run":"bool"}),
    "calculate_spreadsheet": _obj(
        {"status": "str"}, {"file_path", "content_version", "source_version", "formula_recalculation", "receipt", "committed"}
    ),
    "render_spreadsheet": _obj(
        {"status": "str"}, {"files", "source_version", "engine", "format", "page_count", "sheet", "range", "receipt"}
    ),
    "convert_spreadsheet": _obj(
        {"status": "str"}, {"file_path", "content_version", "source_version", "engine", "mode", "loss_report", "receipt"}
    ),
    "validate_spreadsheet": _obj(
        {"status": "str"}, {"file_path", "content_version", "valid", "validation_status", "uncalculated_cells", "rules", "failure_count", "failures", "truncated", "source_versions"}
    ),
    "query_spreadsheet": _obj(
        {"status": "str"}, {"columns", "values", "total_rows", "truncated", "sources", "engine", "storage", "file_path", "content_version", "receipt"}
    ),
    "analyze_spreadsheet": _obj(
        {"status": "str"},
        {
            "result_kind", "file", "file_path", "content_version", "sheet",
            "columns", "rows", "data", "values", "records", "selection",
            "spill", "files", "file_list", "directory", "aggregations",
            "pivot", "relationships", "quality_signals", "profile",
            "coverage", "truncated", "meta", "sheets",
            "source_cols", "source_columns", "source_rows", "selection_spill", "result_spill", "warnings",
            "missing_columns", "sheet_disambiguation", "matrix", "mode", "index",
            "total", "original_rows", "matched_rows", "join",
        },
        mode_key="meta.kind",
        branches={
            "files": frozenset({"files"}),
            "filter": frozenset({"selection"}),
        },
    ),
    "split_spreadsheet": _obj(
        {"status": "str", "file_path": "str", "files": "list"},
        {
            "sheet_name", "by_column", "groups",
            "total_rows", "blank_key_rows", "output_dir",
            "warnings", "source_content_version", "copy_semantics",
        },
    ),
    "compare_spreadsheets": _obj(
        {"status": "str", "file_a": "str", "file_b": "str"},
        {
            "content_version", "content_version_a", "content_version_b",
            "sheet_a", "sheet_b", "summary", "sample_diffs", "truncated",
            "hint", "coverage", "diff_mode",
            "duplicate_keys_a", "duplicate_keys_b",
            "unmatched_in_a", "unmatched_in_b",
            "alignment", "key_columns", "scope", "compared_sheets", "formula_status",
            "appearance", "appearance_versions",
        },
    ),
    "trace_spreadsheet_formulas": _obj(
        {"status": "str", "file_path": "str"},
        {
            "sheets", "cross_sheet_edges", "named_ranges", "summary_text",
            "target", "formula", "precedents", "dependents",
            "direct_impact", "total_affected_cells", "affected_sheets", "scope",
            "content_version", "coverage", "resolved_sheet",
        },
    ),
    "manage_spreadsheet_versions": _obj(
        {"status": "str", "file_path": "str"},
        {
            "content_version", "seen_version", "revisions", "revision",
            "restored", "restored_revision", "deleted_revision", "lineage_id", "exists_after", "summary",
        },
    ),
    "list_directory": _obj(
        {"status": "str", "directory": "str"},
        {
            "absolute_path", "mode", "depth", "tree", "entries", "total",
            "offset", "limit", "returned", "has_more", "next_cursor",
            "truncated", "omitted", "summary", "exclude_patterns",
            "returned_count", "scanned_count",
        },
    ),
    "read_text_file": _obj(
        {
            "status": "str",
            "file_path": "str",
            "content": "str",
            "content_version": "str",
        },
        {"encoding", "lines_read", "truncated"},
    ),
    "write_text_file": _obj(
        {"status": "str", "file_path": "str", "content_version": "str"},
        {"bytes", "encoding", "overwritten"},
    ),
    "edit_text_file": _obj(
        {"status": "str", "file_path": "str", "content_version": "str"},
        {"replacements", "bytes"},
    ),
    "copy_file": _obj(
        {
            "status": "str",
            "source": "str",
            "destination": "str",
            "content_version": "str",
        },
        {"size"},
    ),
    "rename_file": _obj(
        {
            "status": "str",
            "source": "str",
            "destination": "str",
            "content_version": "str",
        },
        set(),
    ),
    "delete_file": _obj(
        {"status": "str"},
        {"deleted", "file_path", "size", "previous_version", "message", "modified"},
    ),
    "offer_download": _obj(
        {"status": "str", "file_path": "str", "filename": "str"},
        {"size", "description", "content_version"},
    ),
    "run_shell": _obj(
        {"status": "str", "command": "str", "return_code": "int"},
        {
            "timed_out", "duration_seconds", "workdir",
            "stdout_tail", "stderr_tail",
        },
    ),
    "run_code": _obj(
        {"status": "str", "return_code": "int", "stdout_tail": "str", "stderr_tail": "str"},
        {"mode", "script", "workdir", "duration_seconds", "timed_out", "published", "sdk_calls",
         "stdout_file", "stderr_file", "save_versions", "pending_discarded", "sandbox_tier",
         "readonly_note", "truncation_warning", "sdk_unavailable", "sdk_unavailable_reason", "empty_output_diagnostic"},
        optional_types={"published": "list", "timed_out": "bool", "sdk_calls": "dict"},
    ),
    "read_word": _obj(
        {"status": "str", "file_path": "str", "total_paragraphs": "int", "offset": "int",
         "returned": "int", "truncated": "bool", "paragraphs": "list"},
        {"tables", "total_tables"}, optional_types={"tables": "list", "total_tables": "int"},
    ),
    "write_word": _obj(
        {"status": "str", "file_path": "str", "content_version": "str", "applied": "list", "applied_count": "int"},
        {"source_file", "source_version", "operation_id", "output_file", "filled_keys", "unfilled_keys",
         "bookmarks_filled", "target_file", "target_sheet", "created_sheet", "target_content_version", "uncertainties", "formulas_uncached"},
    ),
    "inspect_word": OutputContract(schema={
        "type": "object", "required": ["status"],
        "properties": {"status": {"type": "string"}, "files": {"type": "array"},
                       "file_path": {"type": "string"}, "headings": {"type": "array"}, "sections": {"type": "array"}},
        "oneOf": [{"required": ["files"]}, {"required": ["file_path", "headings", "sections", "total_paragraphs", "total_tables", "total_sections", "size_bytes"]}],
    }),
    "search_word": _obj(
        {"status": "str", "query": "str", "match_mode": "str", "total_matches": "int", "matches": "list"},
        {"errors"}, optional_types={"errors": "list"},
    ),
    "read_image": _obj(
        {"status": "str", "mime_type": "str", "attachment_id": "str"},
        {
            "width", "height", "crop", "crop_zoom", "layout", "source_digest",
            "source_dimensions", "source_media_type", "source_bytes", "animated",
            "source_orientation", "frame_count", "parent_attachment_id",
        },
    ),
    "convert_image": _obj({"status": "str", "file_path": "str", "source_path": "str", "format": "str", "content_version": "str"}, {"source_version", "source_size", "output_size", "size_bytes", "receipt"}),
    "parallel_search": _obj(
        {"status": "str", "query": "str", "variants_used": "list", "total_results": "int", "query_summaries": "list", "results": "list"},
    ),
    "introspect_capability": OutputContract(schema={
        "type": ["string", "object"],
        "description": "旧式详情/批量查询返回文本；JSON Schema 节点与统一认知门户返回对象。",
        "properties": {
            "portal_version": {"type": "integer"},
            "product_version": {"type": "string"},
            "catalog_digest": {"type": "string"},
            "revision": {"type": "string"},
            "status": {"type": "string", "description": "门户状态：ok/unavailable/not_found/stale/invalid_query。"},
            "ref": {"type": "string"},
            "content": {"type": "string"},
            "data": {"type": "object"},
            "items": {"type": ["array", "object", "boolean"], "description": "门户列表为对象数组；工具详情中的 JSON Schema 节点也可能带 items schema。"},
            "links": {"type": "array", "items": {"type": "object"}},
            "page": {"type": "integer"},
            "pages": {"type": "integer"},
            "next_call": {"type": "object", "description": "还有后续页面时返回；原样调用。"},
            "index_call": {"type": "object", "description": "返回认知目录的可执行调用。"},
            "restart_call": {"type": "object", "description": "引用过期或页码错误时重新读取第一页。"},
            "content_revision": {"type": "string"},
            "citation": {"type": "object", "description": "可重取的资源 ref、正文哈希、行号和列号。内部引用不冒充公开 URL。"},
            "coverage": {"type": "object"},
        },
        "allOf": [{"if": {"type": "object", "required": ["portal_version"]},
                   "then": {"properties": {"items": {"type": "array", "items": {"type": "object"}}}}}],
        "additionalProperties": True,
    }),
    **{name: OutputContract(value_kind="str") for name in (
        "skill", "manage_skills", "list_subagents", "memory_read_topic", "memory_save", "sleep",
        "task_create", "task_update", "write_plan", "exit_plan_mode",
    )},
    "ask_user": OutputContract(schema={"oneOf": [
        {"type": "object", "required": ["raw_input"]},
        {"type": "array", "items": {"type": "object", "required": ["raw_input"]}},
    ]}),
    "show_workbook": OutputContract(schema={"type": "object", "required": ["kind", "target", "stage", "summary"]}),
    "delegate": OutputContract(schema={"oneOf": [
        {"type": "string"},
        {"type": "object", "required": ["status"],
         "properties": {"status": {"type": "string"}, "run": {"type": "object"}, "runs": {"type": "array"}},
         "oneOf": [{"required": ["run"]}, {"required": ["runs"]}]},
    ]}),
}


def contract_for(tool_name: str) -> OutputContract | None:
    return OUTPUT_CONTRACTS.get(tool_name)


def return_hint_for(tool_name: str, *, tool_def: Any = None) -> str:
    """SDK 返回提示，未声明时返回 Any。"""
    if isinstance(getattr(tool_def, "output_schema", None), dict):
        return _schema_return_hint(tool_def.output_schema)
    contract = contract_for(str(getattr(tool_def, "name", None) or tool_name))
    if contract is None:
        return "Any"
    hint = contract.render_return_hint()
    schema = output_schema_for(tool_name, tool_def=tool_def)
    if schema is not None and schema.get("x-spill-result-types"):
        hint += " | list | str（spill 句柄）"
    return hint


def contract_summary(tool_name: str, *, tool_def: Any = None) -> str | None:
    """introspect ``工具名.output`` 用的合同摘要；未声明返回 None（不编造）。"""
    contract = contract_for(str(getattr(tool_def, "name", None) or tool_name))
    if isinstance(getattr(tool_def, "output_schema", None), dict) or (contract is not None and contract.schema is not None):
        import json
        from excelmanus.tools.schema_walk import compact_node

        schema = output_schema_for(tool_name, tool_def=tool_def)
        assert schema is not None
        return "返回: " + return_hint_for(tool_name, tool_def=tool_def) + "\nJSON Schema:\n" + json.dumps(compact_node(schema), ensure_ascii=False)
    if contract is None:
        return None
    if contract.value_kind == "str":
        return "value_kind: str（无对象键）"
    if contract.value_kind == "any":
        return "value_kind: any（不校验）"
    lines = [
        "value_kind: object",
        f"required: {', '.join(sorted(contract.required)) or '（无）'}",
        f"optional: {', '.join(sorted(contract.optional)) or '（无）'}",
    ]
    if contract.required_types:
        types = ", ".join(
            f"{key}:{declared}"
            for key, declared in sorted(contract.required_types.items())
        )
        lines.append(f"types: {types}")
    if contract.optional_types:
        types = ", ".join(
            f"{key}:{declared}"
            for key, declared in sorted(contract.optional_types.items())
        )
        lines.append(f"optional types (when present): {types}")
    for branch, keys in contract.branches.items():
        lines.append(f"{contract.mode_key}={branch} required: {', '.join(sorted(keys))}")
    for branch, keys in contract.arg_branches.items():
        lines.append(f"argument {contract.arg_mode_key} {branch} required: {', '.join(sorted(keys))}")
    if tool_name in {"read_text_file", "observe_spreadsheet"}:
        lines.append(
            "file_path 为 spill:… 结果句柄时，返回原始完整 JSON 或文本，不套普通文件 content 包装；"
            "将 spill/result_spill/selection_spill 字段的完整值原样传入，或照返回的 next_call 调用。"
        )
    return "\n".join(lines)


def output_schema_for(tool_name: str, *, tool_def: Any = None) -> dict[str, Any] | None:
    """The current ToolDef wins over the built-in name fallback."""
    declared = getattr(tool_def, "output_schema", None)
    if isinstance(declared, dict):
        return deepcopy(declared)
    name = str(getattr(tool_def, "name", None) or tool_name)
    contract = contract_for(name)
    if contract is None:
        return None
    schema = contract.as_schema()
    if name in {"read_text_file", "observe_spreadsheet"}:
        schema["x-spill-result-types"] = ["object", "array", "string"]
    return schema


def _schema_return_hint(
    schema: dict[str, Any], *, root: dict[str, Any] | None = None, seen: frozenset[str] = frozenset(),
) -> str:
    root = schema if root is None else root
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref not in seen:
        from excelmanus.tools.schema_walk import resolve_local_ref

        resolved = resolve_local_ref(root, ref)
        if resolved:
            return _schema_return_hint(
                {**resolved, **{key: value for key, value in schema.items() if key != "$ref"}},
                root=root, seen=seen | {ref},
            )
    kinds = {"object": "dict", "array": "list", "string": "str", "integer": "int",
             "number": "float", "boolean": "bool", "null": "None"}
    kind = schema.get("type")
    if isinstance(kind, list):
        return " | ".join(kinds.get(item, "Any") for item in kind)
    if kind == "object":
        required = sorted(schema.get("required") or [])
        properties = schema.get("properties") or {}
        parts = required + [key + "?" for key in sorted(properties) if key not in required]
        return "dict{" + ", ".join(parts[:12]) + (", …" if len(parts) > 12 else "") + "}" if parts else "dict"
    if isinstance(kind, str):
        return kinds.get(kind, "Any")
    options = schema.get("oneOf") or schema.get("anyOf")
    if isinstance(options, list):
        return " | ".join(dict.fromkeys(_schema_return_hint(option, root=root, seen=seen) for option in options if isinstance(option, dict)))
    return "Any"


def validate_output(
    tool_name: str,
    value: Any,
    arguments: dict[str, Any] | None = None,
    *,
    tool_def: Any = None,
) -> list[str]:
    """Validate the same declaration used by SDK, introspection and Native."""
    schema = output_schema_for(tool_name, tool_def=tool_def)
    if schema is None:
        return []
    violations = validate_declared_output_schema(value, schema)
    argument_branches = schema.get("x-argument-branches")
    if isinstance(argument_branches, dict) and isinstance(arguments, dict) and isinstance(value, dict):
        key = argument_branches["key"]
        branch = "present" if arguments.get(key) else "absent"
        for required in argument_branches.get("required", {}).get(branch, []):
            if required not in value:
                violations.append(f"{key} {branch} 分支缺少必有字段: {required}")
    return violations


def validate_declared_output_schema(value: Any, schema: dict[str, Any]) -> list[str]:
    """JSON Schema 2020-12, including complete arrays; references never fetch remote data."""
    from jsonschema import Draft202012Validator
    from referencing import Registry

    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=Registry())
        errors = islice(validator.iter_errors(value), 20)
        return [f"{error.json_path}: {error.message[:500]}" for error in errors]
    except Exception as exc:
        # Invalid/unresolvable declarations must not silently become successful results.
        return [f"输出 schema 无效或无法解析 ({type(exc).__name__})"]


def enforce_output_contract(result: Any, tool_name: str, arguments: dict[str, Any], *, tool_def: Any = None) -> Any:
    """Keep committed UI facts when an executed tool violates its success contract."""
    from dataclasses import replace
    from excelmanus.engine_core.tool_result import error_result, result_value

    if not result.success or (isinstance(result.coverage, dict) and result.coverage.get("spill_retrieve")):
        return result
    violations = validate_output(tool_name, result_value(result), arguments, tool_def=tool_def)
    if not violations:
        return result
    fields: dict[str, Any] = {"tool": tool_name, "violations": violations, "execution_completed": True}
    if isinstance(result.value, dict):
        for key in ("operation_id", "content_version"):
            if key in result.value:
                fields[key] = result.value[key]
    failed = error_result(
        f"{tool_name} 返回值不符合声明合同；调用已返回，不能据此认定写入未发生。",
        code="SDK_CONTRACT_VIOLATION", fields=fields,
        remediation="先核对已提交操作和文件版本；不要重放写入。通过 tool_detail 查询工具输出合同，修复返回格式后再继续。",
    )
    return replace(failed, ui_meta=result.ui_meta, coverage=result.coverage)
