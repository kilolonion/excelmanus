"""工具确认协议：构建、序列化、解析。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .domain import Window
from .models import ChangeRecord, IntentTag
from .projection_models import ConfirmationProjection
from .projection_service import project_confirmation

_UNIFIED_RE = re.compile(
    r"^\[OK\] \[(?P<window>[^\]]+)\] (?P<op>[^:]+): (?P<range>[^|]+?) \| "
    r"(?P<rows>\d+)r x (?P<cols>\d+)c \| (?P<summary>.+?) \| intent=(?P<intent>\w+)"
    r"(?: \| hint=(?P<hint>.*))?$"
)
_ANCHORED_HEAD_RE = re.compile(
    r"^\[OK\] \[(?P<window>[^\]]+)\] (?P<op>[^:]+): (?P<range>[^|]+?) \| (?P<rows>\d+)r x (?P<cols>\d+)c \| (?P<summary>.+)$"
)
_ANCHORED_INTENT_RE = re.compile(r"^  intent: (?P<intent>\w+)$")
_ANCHORED_INTENT_CN_RE = re.compile(r"^  意图: (?P<intent>\w+)$")
_ANCHORED_HINT_RE = re.compile(r"^  hint: (?P<hint>.+)$")
_ANCHORED_HINT_CN_RE = re.compile(r"^  提示: (?P<hint>.+)$")


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
    localized_hint: str = ""


def build_confirmation_record(
    *,
    window: Window | None = None,
    tool_name: str | None = None,
    repeat_warning: bool = False,
    projection: ConfirmationProjection | None = None,
) -> ConfirmationRecord:
    """从窗口状态构建确认对象。"""
    if projection is None:
        if window is None or not tool_name:
            raise ValueError("window/tool_name or projection must be provided")
        projection = project_confirmation(
            window,
            tool_name=tool_name,
            repeat_warning=repeat_warning,
        )

    return ConfirmationRecord(
        window_label=projection.window_label,
        operation=projection.operation,
        range_ref=projection.range_ref,
        rows=max(0, int(projection.rows)),
        cols=max(0, int(projection.cols)),
        change_summary=projection.change_summary,
        intent=projection.intent,
        hint=projection.hint,
        localized_hint=projection.localized_hint,
    )


def serialize_confirmation(record: ConfirmationRecord | ConfirmationProjection, *, mode: str) -> str:
    """将确认对象序列化为文本。"""
    if isinstance(record, ConfirmationProjection):
        record = build_confirmation_record(projection=record)

    normalized_mode = str(mode or "anchored").strip().lower()
    if normalized_mode == "unified":
        base = (
            f"[OK] [{record.window_label}] {record.operation}: {record.range_ref} | "
            f"{record.rows}r x {record.cols}c | {record.change_summary} | intent={record.intent}"
        )
        tails: list[str] = []
        if record.hint:
            tails.append(f"hint={record.hint}")
        if record.localized_hint:
            tails.append(f"提示={record.localized_hint}")
        if hasattr(record, 'sheet_dimensions') and record.sheet_dimensions:
            dims_parts = [f"{name}({r}r×{c}c)" for name, r, c in record.sheet_dimensions]
            if len(dims_parts) > 20:
                dims_parts = dims_parts[:20]
                dims_parts.append(f"...(+{len(record.sheet_dimensions) - 20})")
            tails.append(f"sheets={' | '.join(dims_parts)}")
        if tails:
            return f"{base} | " + " | ".join(tails)
        return base

    lines = [
        (
            f"[OK] [{record.window_label}] {record.operation}: {record.range_ref} | "
            f"{record.rows}r x {record.cols}c | {record.change_summary}"
        ),
        f"  intent: {record.intent}",
        f"  意图: {record.intent}",
        "  hint: window snapshot synced; write actions must be confirmed by write-tool results.",
    ]
    if hasattr(record, 'sheet_dimensions') and record.sheet_dimensions:
        dims_parts = [f"{name}({r}r×{c}c)" for name, r, c in record.sheet_dimensions]
        # 限制显示数量，避免过长
        if len(dims_parts) > 20:
            dims_parts = dims_parts[:20]
            dims_parts.append(f"...(+{len(record.sheet_dimensions) - 20})")
        lines.append(f"  sheets: {' | '.join(dims_parts)}")
    if record.hint:
        lines.append(f"  hint: {record.hint}")
    if record.localized_hint:
        lines.append(f"  提示: {record.localized_hint}")
    return "\n".join(lines)


def parse_confirmation(text: str) -> ConfirmationRecord | None:
    """解析确认文本，便于往返校验。"""
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
    hint_cn = ""
    for line in lines[1:]:
        stripped = line.rstrip()
        intent_match = _ANCHORED_INTENT_RE.match(stripped)
        if intent_match is not None:
            intent = intent_match.group("intent").strip()
            continue
        intent_cn_match = _ANCHORED_INTENT_CN_RE.match(stripped)
        if intent_cn_match is not None:
            intent = intent_cn_match.group("intent").strip()
            continue
        hint_match = _ANCHORED_HINT_RE.match(stripped)
        if hint_match is not None:
            hint = hint_match.group("hint").strip()
            continue
        hint_cn_match = _ANCHORED_HINT_CN_RE.match(stripped)
        if hint_cn_match is not None:
            hint_cn = hint_cn_match.group("hint").strip()

    return ConfirmationRecord(
        window_label=head_match.group("window").strip(),
        operation=head_match.group("op").strip(),
        range_ref=head_match.group("range").strip(),
        rows=int(head_match.group("rows")),
        cols=int(head_match.group("cols")),
        change_summary=head_match.group("summary").strip(),
        intent=intent,
        hint=hint or hint_cn,
        localized_hint=hint_cn,
    )


def _latest_change_summary(change_log: list[ChangeRecord]) -> str:
    if not change_log:
        return ""
    latest = change_log[-1]
    if latest.affected_range and latest.affected_range != "-":
        return f"{latest.change_type}@{latest.affected_range}"
    return latest.change_type or latest.tool_summary
