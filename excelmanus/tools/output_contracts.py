"""Code Mode SDK 返回合同：声明与运行时校验同源。

设计约束（code-mode-repair-plan P1-A）：
- 只声明**顶层键**；不展开单元格矩阵/嵌套规格——那会把 schema 病搬进 SDK。
- ``required`` 是所有成功返回必须有的键；``optional`` 列出模型可依赖但
  不保证出现的键；``branches`` 记录模式分支（如 analyze 的 mode）下
  额外必有的键——``value["mode"]`` 命中分支时才校验。
- ``required_types`` 对必有键做 str/dict/list/bool 等类型检查。
- 元工具（skill/ask_user/delegate/list_subagents）返回形状由交互层决定
  （ask_user 实为回答 payload dict），用 ``value_kind="any"`` 不校验。
- MCP 不登记，故不校验。
- 合同声明必须有运行时校验配套：Native/SDK 共用 ``validate_output``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # 例：edit_spreadsheet(workbook_spec=...) → present 分支要求
    # build_summary；无 spec 的 operations 编辑 → absent 分支要求 applied。
    arg_mode_key: str | None = None
    arg_branches: dict[str, frozenset[str]] = field(default_factory=dict)
    # object = 成功 value 必须是 dict；str = 整段字符串；any = 不校验。
    value_kind: str = "object"

    def render_return_hint(self) -> str:
        """SDK 段里的一行返回形状提示，如 ``dict{status, file_path, applied?…}``。

        必有键在前、可选键带 ``?`` 后缀——脚本据此判断哪些键可直接读、
        哪些要先 ``in`` 检查（如 edit_spreadsheet 的 applied 仅编辑分支有）。
        """
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
            text = "{" + (head + ", …" if head else "…") + "}"
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
    "inspect_spreadsheet": _obj(
        {"status": "str"},
        {
            "result_kind", "file", "file_path", "content_version", "sheets",
            "sheet", "resolved_sheet", "data", "values", "columns",
            "coverage", "selection", "spill", "matches", "has_more",
            "truncated", "capabilities", "directory", "files", "meta",
            "snapshot_id", "shape", "formulas", "range",
        },
        mode_key="result_kind",
        branches={
            "overview": frozenset({"coverage", "values"}),
            "range": frozenset({"data", "selection"}),
        },
    ),
    "analyze_spreadsheet": _obj(
        {"status": "str"},
        {
            "result_kind", "file", "file_path", "content_version", "sheet",
            "columns", "rows", "data", "values", "records", "selection",
            "spill", "files", "file_list", "directory", "aggregations",
            "pivot", "relationships", "quality_signals", "profile",
            "coverage", "truncated", "meta", "sheets",
        },
        mode_key="result_kind",
        branches={
            "files": frozenset({"files"}),
            "filter": frozenset({"selection"}),
        },
    ),
    "edit_spreadsheet": _obj(
        {
            "status": "str",
            "file_path": "str",
            "content_version": "str",
        },
        # applied 仅 operations 编辑分支必有；workbook_spec 创建分支
        # 返回 build_summary/verification/uncertainties，不带 applied。
        {
            "applied", "operations", "warnings", "skipped", "summary",
            "build_summary", "verification", "uncertainties",
        },
        arg_mode_key="workbook_spec",
        arg_branches={
            "present": frozenset({"build_summary"}),
            "absent": frozenset({"applied"}),
        },
        optional_types={"applied": "list"},
    ),
    "format_spreadsheet": _obj(
        {
            "status": "str",
            "file_path": "str",
            "content_version": "str",
            "applied": "list",
        },
        {"appearance", "skipped_merged_non_anchors"},
    ),
    "split_spreadsheet": _obj(
        {"status": "str", "file_path": "str", "files": "list"},
        {
            "sheet_name", "by_column", "groups",
            "total_rows", "blank_key_rows", "output_dir",
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
        },
    ),
    "trace_spreadsheet_formulas": _obj(
        {"status": "str", "file_path": "str"},
        {
            "sheets", "cross_sheet_edges", "named_ranges", "summary_text",
            "target", "formula", "precedents", "dependents",
            "direct_impact", "total_affected_cells", "affected_sheets",
        },
    ),
    "manage_spreadsheet_objects": _obj(
        {
            "status": "str",
            "file_path": "str",
            "content_version": "str",
            "applied": "list",
        },
        {
            "chart_type", "data_range", "target_sheet", "target_cell",
            "chart_info", "total_charts_on_sheet",
        },
    ),
    "manage_spreadsheet_versions": _obj(
        {"status": "str", "file_path": "str"},
        {
            "content_version", "seen_version", "revisions", "revision",
            "restored",
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
        {"size", "description"},
    ),
    "run_shell": _obj(
        {"status": "str", "command": "str", "return_code": "int"},
        {
            "timed_out", "duration_seconds", "workdir",
            "stdout_tail", "stderr_tail",
        },
    ),
    # 元工具的返回形状由交互层决定：ask_user 返回回答 payload（JSON
    # 对象经 coerce 成 dict，多问题为 JSON 数组），skill/delegate 的文本
    # 也可能是 JSON 形状——固定 str 合同会误伤，按 any 登记不校验。
    "skill": OutputContract(value_kind="any"),
    "ask_user": OutputContract(value_kind="any"),
    "delegate": OutputContract(value_kind="any"),
    "list_subagents": OutputContract(value_kind="any"),
}


def contract_for(tool_name: str) -> OutputContract | None:
    return OUTPUT_CONTRACTS.get(tool_name)


def return_hint_for(tool_name: str) -> str:
    """SDK 段/生成源的返回注解文本；未登记工具返回 ``dict``。"""
    contract = OUTPUT_CONTRACTS.get(tool_name)
    if contract is None:
        return "dict"
    return contract.render_return_hint()


def contract_summary(tool_name: str) -> str | None:
    """introspect ``工具名.output`` 用的合同摘要；未声明返回 None（不编造）。"""
    contract = OUTPUT_CONTRACTS.get(tool_name)
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
    return "\n".join(lines)


def validate_output(
    tool_name: str,
    value: Any,
    arguments: dict[str, Any] | None = None,
) -> list[str]:
    """校验成功结果的顶层键与声明类型；返回违约描述列表（空 = 通过）。

    只检查已登记的合同；MCP / 未登记工具不校验。``arguments`` 提供时
    启用参数分支校验（如 edit_spreadsheet 的 workbook_spec 创建路径）。
    """
    contract = OUTPUT_CONTRACTS.get(tool_name)
    if contract is None or contract.value_kind == "any":
        return []
    if contract.value_kind == "str":
        if not isinstance(value, str):
            return [f"{tool_name} 返回非字符串类型: {type(value).__name__}"]
        return []
    if not isinstance(value, dict):
        return [f"{tool_name} 返回非对象类型: {type(value).__name__}"]
    missing = sorted(k for k in contract.required if k not in value)
    violations = [f"缺少必有字段: {k}" for k in missing]
    merged_types = {**contract.required_types, **contract.optional_types}
    for key, declared in merged_types.items():
        if key not in value:
            continue
        actual = value[key]
        if declared == "int":
            ok = isinstance(actual, int) and not isinstance(actual, bool)
        elif declared == "bool":
            ok = isinstance(actual, bool)
        elif declared == "str":
            ok = isinstance(actual, str)
        elif declared == "dict":
            ok = isinstance(actual, dict)
        elif declared == "list":
            ok = isinstance(actual, list)
        else:
            ok = True
        if not ok:
            violations.append(
                f"字段 {key} 类型错误: 期望 {declared}，实际 {type(actual).__name__}"
            )
    if contract.mode_key:
        branch = value.get(contract.mode_key)
        branch_required = contract.branches.get(str(branch))
        if branch_required:
            for key in sorted(k for k in branch_required if k not in value):
                violations.append(f"mode={branch} 分支缺少必有字段: {key}")
    if contract.arg_mode_key and isinstance(arguments, dict):
        which = "present" if arguments.get(contract.arg_mode_key) else "absent"
        arg_required = contract.arg_branches.get(which)
        if arg_required:
            for key in sorted(k for k in arg_required if k not in value):
                violations.append(
                    f"{contract.arg_mode_key} {which} 分支缺少必有字段: {key}"
                )
    return violations
