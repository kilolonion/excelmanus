"""只读渲染/确认用的投影 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NoticeProjection:
    """渲染器使用的只读通知投影。"""

    window_id: str
    kind: str
    title: str
    identity: str
    range_ref: str
    rows: int
    cols: int
    intent: str
    summary: str = ""
    sheet_tabs: tuple[str, ...] = ()
    preview_rows: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ToolPayloadProjection:
    """感知块渲染器使用的只读工具载荷投影。"""

    window_type: str
    title: str
    identity: str = ""
    directory: str = ""
    entries: tuple[str, ...] = ()
    file: str = ""
    sheet: str = ""
    intent: str = "general"
    sheet_tabs: tuple[str, ...] = ()
    viewport_range: str = ""
    visible_rows: int = 0
    visible_cols: int = 0
    total_rows: int = 0
    total_cols: int = 0
    freeze_panes: str = ""
    style_summary: str = ""
    scroll_position: dict[str, Any] = field(default_factory=dict)
    status_bar: dict[str, Any] = field(default_factory=dict)
    column_widths: dict[str, Any] = field(default_factory=dict)
    row_heights: dict[str, Any] = field(default_factory=dict)
    merged_ranges: tuple[str, ...] = ()
    conditional_effects: tuple[str, ...] = ()
    sheet_dimensions: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True)
class ConfirmationProjection:
    """只读确认投影。"""

    window_label: str
    operation: str
    range_ref: str
    rows: int
    cols: int
    change_summary: str
    intent: str
    hint: str = ""
    localized_hint: str = ""
    sheet_dimensions: tuple[tuple[str, int, int], ...] = ()
