"""工具确认协议：构建、序列化、解析。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .models import ChangeRecord, IntentTag, WindowState

_UNIFIED_RE = re.compile(
    r"^\[OK\] \[(?P<window>[^\]]+)\] (?P<op>[^:]+): (?P<range>[^|]+?) \| "
    r"(?P<rows>\d+)r x (?P<cols>\d+)c \| (?P<summary>.+?) \| intent=(?P<intent>\w+)"
    r"(?: \| hint=(?P<hint>.*))?$"
)
_ANCHORED_HEAD_RE = re.compile(
    r"^\[OK\] \[(?P<window>[^\]]+)\] (?P<op>[^:]+): (?P<range>[^|]+?) \| (?P<rows>\d+)r x (?P<cols>\d+)c \| (?P<summary>.+)$"
)
_ANCHORED_INTENT_RE = re.compile(r"^  intent: (?P<intent>\w+)$")
_ANCHORED_HINT_RE = re.compile(r"^  hint: (?P<hint>.+)$")


@dataclass(frozen=True)
class ConfirmationRecord:
    """确认结构对象。"""

    window_label: str
    operation: str
    range_ref: str
    rows: int
    cols: int
    change_summary: str
    intent: str
    hint: str = ""


def build_confirmation_record(
    *,
    window: WindowState,
    tool_name: str,
    repeat_warning: bool = False,
) -> ConfirmationRecord:
    """从窗口状态构建确认对象。"""
    rows = int(window.total_rows or (window.viewport.total_rows if window.viewport else 0) or len(window.data_buffer))
    cols = int(window.total_cols or (window.viewport.total_cols if window.viewport else 0) or len(window.columns or window.schema))
    file_name = window.file_path or "未知文件"
    sheet_name = window.sheet_name or "未知Sheet"
    window_label = f"{window.id}: {file_name} / {sheet_name}"
    latest_change = _latest_change_summary(window.change_log)
    change_summary = latest_change or "状态同步"
    hint = ""
    if repeat_warning:
        hint = f"intent[{window.intent_tag.value}] data already in window {window.id}"
    return ConfirmationRecord(
        window_label=window_label,
        operation=tool_name,
        range_ref=(window.viewport_range or "-"),
        rows=max(0, rows),
        cols=max(0, cols),
        change_summary=change_summary,
        intent=window.intent_tag.value,
        hint=hint,
    )


def serialize_confirmation(record: ConfirmationRecord, *, mode: str) -> str:
    """将确认对象序列化为文本。"""
    normalized_mode = str(mode or "anchored").strip().lower()
    if normalized_mode == "unified":
        base = (
            f"[OK] [{record.window_label}] {record.operation}: {record.range_ref} | "
            f"{record.rows}r x {record.cols}c | {record.change_summary} | intent={record.intent}"
        )
        if record.hint:
            return f"{base} | hint={record.hint}"
        return base

    lines = [
        (
            f"[OK] [{record.window_label}] {record.operation}: {record.range_ref} | "
            f"{record.rows}r x {record.cols}c | {record.change_summary}"
        ),
        f"  intent: {record.intent}",
        "  hint: data merged into window, prefer referencing window content.",
    ]
    if record.hint:
        lines.append(f"  hint: {record.hint}")
    return "\n".join(lines)


def parse_confirmation(text: str) -> ConfirmationRecord | None:
    """解析确认文本，便于 round-trip 校验。"""
    normalized = str(text or "").strip()
    if not normalized:
        return None

    lines = normalized.splitlines()
    head = lines[0].strip()
    matched = _UNIFIED_RE.match(head)
    if matched is not None:
        return ConfirmationRecord(
            window_label=matched.group("window").strip(),
            operation=matched.group("op").strip(),
            range_ref=matched.group("range").strip(),
            rows=int(matched.group("rows")),
            cols=int(matched.group("cols")),
            change_summary=matched.group("summary").strip(),
            intent=matched.group("intent").strip(),
            hint=(matched.group("hint") or "").strip(),
        )

    head_match = _ANCHORED_HEAD_RE.match(head)
    if head_match is None:
        return None

    intent = IntentTag.GENERAL.value
    hint = ""
    for line in lines[1:]:
        stripped = line.rstrip()
        intent_match = _ANCHORED_INTENT_RE.match(stripped)
        if intent_match is not None:
            intent = intent_match.group("intent").strip()
            continue
        hint_match = _ANCHORED_HINT_RE.match(stripped)
        if hint_match is not None:
            hint = hint_match.group("hint").strip()

    return ConfirmationRecord(
        window_label=head_match.group("window").strip(),
        operation=head_match.group("op").strip(),
        range_ref=head_match.group("range").strip(),
        rows=int(head_match.group("rows")),
        cols=int(head_match.group("cols")),
        change_summary=head_match.group("summary").strip(),
        intent=intent,
        hint=hint,
    )


def _latest_change_summary(change_log: list[ChangeRecord]) -> str:
    if not change_log:
        return ""
    latest = change_log[-1]
    if latest.affected_range and latest.affected_range != "-":
        return f"{latest.change_type}@{latest.affected_range}"
    return latest.change_type or latest.tool_summary
