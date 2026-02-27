"""窗口变更的增量契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import DetailLevel, IntentTag


@dataclass(frozen=True)
class ExplorerDelta:
    """资源管理器窗口的变更契约。"""

    directory: str | None = None
    kind: str = "explorer"


@dataclass(frozen=True)
class SheetReadDelta:
    """表格读取摄入的变更契约。"""

    range_ref: str
    rows: int
    cols: int
    change_summary: str
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetWriteDelta:
    """表格写入更新的变更契约。"""

    target_range: str
    change_summary: str = ""
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetFilterDelta:
    """表格筛选更新的变更契约。"""

    filter_state: dict[str, Any] | None
    filtered_rows: int = 0
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetStyleDelta:
    """表格样式更新的变更契约。"""

    style_summary: str = ""
    freeze_panes: str | None = None
    column_widths: dict[str, Any] | None = None
    row_heights: dict[str, Any] | None = None
    merged_ranges: list[str] | None = None
    conditional_effects: list[str] | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class SheetFocusDelta:
    """表格焦点生命周期转换的变更契约。"""

    action: str
    detail_level: DetailLevel | None = None
    is_active: bool | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class LifecycleDelta:
    """共享生命周期状态的变更契约。"""

    detail_level: DetailLevel | None = None
    idle_turns: int | None = None
    last_access_seq: int | None = None
    dormant: bool | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class IntentDelta:
    """共享意图状态的变更契约。"""

    tag: IntentTag | None = None
    confidence: float | None = None
    source: str | None = None
    updated_turn: int | None = None
    lock_until_turn: int | None = None
    kind: str = "sheet"


@dataclass(frozen=True)
class FieldSetDelta:
    """管理器侧受控变更的通用字段设置增量。"""

    field: str
    value: Any
    kind: str


@dataclass(frozen=True)
class FieldAppendDelta:
    """管理器侧受控变更的通用追加增量。"""

    field: str
    value: Any
    kind: str


WindowDelta = (
    ExplorerDelta
    | SheetReadDelta
    | SheetWriteDelta
    | SheetFilterDelta
    | SheetStyleDelta
    | SheetFocusDelta
    | LifecycleDelta
    | IntentDelta
    | FieldSetDelta
    | FieldAppendDelta
)
