"""窗口规则注册表（WURM v2）。"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from excelmanus.mcp.manager import parse_tool_prefix

from .models import IntentTag, WindowType

_FORMULA_HINT_RE = re.compile(
    r"(=|SUMIFS\s*\(|VLOOKUP\s*\(|XLOOKUP\s*\(|INDEX\s*\(|MATCH\s*\(|IF\s*\()",
    re.IGNORECASE,
)

_EXPLORER_TOOLS = {
    "list_directory",
    "find_files",
    "inspect_excel_files",
}

_SHEET_TOOLS = {
    "read_excel",
    "read_sheet",
    "analyze_data",
    "filter_data",
    "transform_data",
    "list_sheets",
    "describe_sheets",
    "write_excel",
    "write_to_sheet",
    "write_cells",
    "format_cells",
    "format_range",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "read_cell_styles",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
    "create_sheet",
    "copy_sheet",
    "rename_sheet",
    "delete_sheet",
    "copy_range_between_sheets",
    "focus_window_refill",
}

_MCP_EXPLORER_SUFFIXES = {
    "list_dir",
    "find_by_name",
}

_MCP_SHEET_SUFFIXES = {
    "read_sheet",
    "write_to_sheet",
    "format_range",
    "describe_sheets",
    "copy_sheet",
}

_READ_LIKE_TOOLS = {
    "read_excel",
    "read_sheet",
    "analyze_data",
    "filter_data",
    "transform_data",
    "read_cell_styles",
    "focus_window_refill",
}

_WRITE_LIKE_TOOLS = {
    "write_excel",
    "write_to_sheet",
    "write_cells",
    "format_cells",
    "format_range",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
}

_INTENT_USER_KEYWORDS: dict[IntentTag, tuple[str, ...]] = {
    IntentTag.AGGREGATE: ("汇总", "总计", "求和", "平均", "同比", "环比", "统计", "销量", "占比"),
    IntentTag.FORMAT: ("格式", "样式", "粗体", "颜色", "字体", "列宽", "行高", "边框", "合并", "条件格式"),
    IntentTag.VALIDATE: ("空值", "缺失", "异常", "重复", "校验", "完整性", "一致性", "脏数据"),
    IntentTag.FORMULA: ("公式", "函数", "引用", "计算错误", "VLOOKUP", "XLOOKUP", "SUMIFS"),
    IntentTag.ENTRY: ("写入", "录入", "填充", "更新", "覆盖", "新增"),
}

_INTENT_FORMAT_TOOLS = {
    "format_cells",
    "format_range",
    "adjust_column_width",
    "adjust_row_height",
    "merge_cells",
    "unmerge_cells",
    "add_color_scale",
    "add_data_bar",
    "add_conditional_rule",
    "read_cell_styles",
}
_INTENT_AGGREGATE_TOOLS = {"analyze_data", "transform_data"}
_INTENT_VALIDATE_TOOLS = {"filter_data"}
_INTENT_ENTRY_TOOLS = {"write_excel", "write_to_sheet", "write_cells"}

_INTENT_TO_TASK_TYPE: dict[IntentTag, str] = {
    IntentTag.AGGREGATE: "DATA_COMPARISON",
    IntentTag.FORMAT: "FORMAT_CHECK",
    IntentTag.VALIDATE: "ANOMALY_SEARCH",
    IntentTag.FORMULA: "FORMULA_DEBUG",
    IntentTag.ENTRY: "DATA_ENTRY",
    IntentTag.GENERAL: "GENERAL_BROWSE",
}


@dataclass(frozen=True)
class ToolMeta:
    """工具分类元信息。"""

    canonical_name: str
    window_type: WindowType | None
    read_like: bool = False
    write_like: bool = False
    rule_id: str = "unknown_tool"


@dataclass(frozen=True)
class IntentDecision:
    """意图判定结果。"""

    tag: IntentTag
    confidence: float
    source: str
    force: bool
    rule_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "source": self.source,
            "force": bool(self.force),
            "rule_id": self.rule_id,
        }


def classify_tool_meta(tool_name: str) -> ToolMeta:
    """工具分类（v2 中央规则）。"""
    name = (tool_name or "").strip()
    if not name:
        return ToolMeta(canonical_name="", window_type=None, rule_id="empty_tool")

    canonical = name
    if name.startswith("mcp_"):
        canonical = _canonicalize_mcp_tool_name(name)
        if canonical in _MCP_EXPLORER_SUFFIXES:
            return _build_tool_meta(canonical=canonical, window_type=WindowType.EXPLORER, rule_id="mcp_explorer")
        if canonical in _MCP_SHEET_SUFFIXES:
            return _build_tool_meta(canonical=canonical, window_type=WindowType.SHEET, rule_id="mcp_sheet")

    if canonical in _EXPLORER_TOOLS:
        return _build_tool_meta(canonical=canonical, window_type=WindowType.EXPLORER, rule_id="builtin_explorer")
    if canonical in _SHEET_TOOLS:
        return _build_tool_meta(canonical=canonical, window_type=WindowType.SHEET, rule_id="builtin_sheet")

    return _build_tool_meta(canonical=canonical, window_type=None, rule_id="unknown_tool")


def is_read_like_tool(tool_name: str) -> bool:
    """判断工具是否属于读取类。"""
    return classify_tool_meta(tool_name).read_like


def is_write_like_tool(tool_name: str) -> bool:
    """判断工具是否属于写入类。"""
    return classify_tool_meta(tool_name).write_like


def resolve_intent_decision(
    *,
    current_tag: IntentTag,
    current_confidence: float,
    current_lock_until_turn: int,
    current_turn: int,
    intent_enabled: bool,
    sticky_turns: int,
    user_intent_text: str,
    canonical_tool_name: str,
    arguments: dict[str, Any],
    result_json: dict[str, Any] | None,
) -> IntentDecision:
    """统一意图判定规则：用户语义 > 工具语义 > 继承 > 默认。"""
    if not intent_enabled:
        return IntentDecision(
            tag=current_tag,
            confidence=current_confidence,
            source="carry",
            force=False,
            rule_id="intent_disabled",
        )

    user_tag, user_conf, user_rule_id = _intent_from_user(user_intent_text)
    tool_tag, tool_conf, tool_rule_id = _intent_from_tool(
        canonical_tool_name=canonical_tool_name,
        arguments=arguments,
        result_json=result_json,
        current_tag=current_tag,
    )

    force_switch = user_tag != IntentTag.GENERAL and user_conf >= 0.75
    tag = current_tag
    confidence = max(0.0, min(1.0, float(current_confidence)))
    source = "carry"
    rule_id = "carry"

    if force_switch:
        tag, confidence, source, rule_id = user_tag, user_conf, "user_rule", user_rule_id
    elif user_tag != IntentTag.GENERAL and user_conf >= 0.5:
        tag, confidence, source, rule_id = user_tag, user_conf, "user_rule", user_rule_id
    elif tool_tag != IntentTag.GENERAL:
        tag, confidence, source, rule_id = tool_tag, tool_conf, "tool_rule", tool_rule_id
    elif current_tag == IntentTag.GENERAL:
        tag, confidence, source, rule_id = IntentTag.GENERAL, 0.0, "default", "fallback_general"

    # 粘性锁：锁期内除强制切换外不允许跨类抖动。
    if (
        not force_switch
        and current_lock_until_turn >= max(1, int(current_turn))
        and tag != current_tag
    ):
        return IntentDecision(
            tag=current_tag,
            confidence=max(0.0, min(1.0, float(current_confidence))),
            source="carry",
            force=False,
            rule_id="sticky_lock",
        )

    # 仅对切换生效的锁范围由调用方应用，此处返回决策。
    _ = sticky_turns
    return IntentDecision(
        tag=tag,
        confidence=max(0.0, min(1.0, float(confidence))),
        source=source,
        force=force_switch,
        rule_id=rule_id,
    )


def repeat_threshold(intent_tag: IntentTag, *, base_warn: int, base_trip: int) -> tuple[int, int]:
    """按 intent 返回 warn/trip 阈值。"""
    warn = max(1, int(base_warn))
    trip = max(warn + 1, int(base_trip))
    if intent_tag in {IntentTag.AGGREGATE, IntentTag.VALIDATE, IntentTag.FORMULA}:
        return warn, trip
    relaxed_warn = max(3, warn + 1)
    relaxed_trip = max(relaxed_warn + 1, trip + 1, 4)
    return relaxed_warn, relaxed_trip


def task_type_from_intent(intent_tag: IntentTag) -> str:
    """将 intent 映射为小模型任务类型。"""
    return _INTENT_TO_TASK_TYPE.get(intent_tag, "GENERAL_BROWSE")


def _build_tool_meta(*, canonical: str, window_type: WindowType | None, rule_id: str) -> ToolMeta:
    normalized = str(canonical or "").strip().lower()
    return ToolMeta(
        canonical_name=normalized,
        window_type=window_type,
        read_like=normalized in _READ_LIKE_TOOLS,
        write_like=normalized in _WRITE_LIKE_TOOLS,
        rule_id=rule_id,
    )


def _intent_from_user(text: str) -> tuple[IntentTag, float, str]:
    normalized = str(text or "").strip()
    if not normalized:
        return IntentTag.GENERAL, 0.0, "user_empty"

    lower = normalized.lower()
    tag_scores: dict[IntentTag, float] = {}
    for tag, keywords in _INTENT_USER_KEYWORDS.items():
        hit_count = sum(1 for keyword in keywords if keyword in normalized or keyword.lower() in lower)
        if hit_count <= 0:
            continue
        tag_scores[tag] = min(0.95, 0.55 + 0.15 * hit_count)

    if not tag_scores:
        explicit = lower.strip()
        try:
            tag = IntentTag(explicit)
        except ValueError:
            return IntentTag.GENERAL, 0.0, "user_no_match"
        return tag, 0.8, "user_explicit_enum"

    selected = max(tag_scores.items(), key=lambda item: item[1])
    return selected[0], selected[1], f"user_keyword_{selected[0].value}"


def _intent_from_tool(
    *,
    canonical_tool_name: str,
    arguments: dict[str, Any],
    result_json: dict[str, Any] | None,
    current_tag: IntentTag,
) -> tuple[IntentTag, float, str]:
    tool = str(canonical_tool_name or "").strip().lower()
    if tool in _INTENT_FORMAT_TOOLS:
        return IntentTag.FORMAT, 0.88, "tool_format"
    if tool in _INTENT_AGGREGATE_TOOLS:
        return IntentTag.AGGREGATE, 0.84, "tool_aggregate"
    if tool in _INTENT_VALIDATE_TOOLS:
        return IntentTag.VALIDATE, 0.9, "tool_validate"
    if tool in _INTENT_ENTRY_TOOLS:
        if _has_formula_signal(arguments=arguments, result_json=result_json):
            return IntentTag.FORMULA, 0.9, "tool_formula_signal"
        return IntentTag.ENTRY, 0.84, "tool_entry"
    if tool in {"read_excel", "read_sheet", "focus_window_refill"}:
        if current_tag != IntentTag.GENERAL:
            return current_tag, 0.7, "tool_read_carry"
        return IntentTag.AGGREGATE, 0.62, "tool_read_default_aggregate"
    return IntentTag.GENERAL, 0.0, "tool_no_match"


def _has_formula_signal(*, arguments: dict[str, Any], result_json: dict[str, Any] | None) -> bool:
    for candidate in _iter_text_values(arguments):
        if _FORMULA_HINT_RE.search(candidate):
            return True
    if isinstance(result_json, dict):
        for candidate in _iter_text_values(result_json):
            if _FORMULA_HINT_RE.search(candidate):
                return True
    return False


def _iter_text_values(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        results: list[str] = []
        for value in payload.values():
            results.extend(_iter_text_values(value))
        return results
    if isinstance(payload, (list, tuple)):
        results: list[str] = []
        for value in payload:
            results.extend(_iter_text_values(value))
        return results
    if isinstance(payload, (int, float, bool)):
        return [str(payload)]
    return []


def _canonicalize_mcp_tool_name(tool_name: str) -> str:
    try:
        _, original = parse_tool_prefix(tool_name)
    except ValueError:
        original = tool_name.removeprefix("mcp_")
        if "_" in original:
            original = original.split("_", 1)[1]
    return (original or "").strip().lower()
