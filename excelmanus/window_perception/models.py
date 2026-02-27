"""窗口感知层数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class WindowType(str, Enum):
    """窗口类型。"""

    EXPLORER = "explorer"
    FOLDER = "explorer"
    SHEET = "sheet"


class WindowRenderAction(str, Enum):
    """窗口渲染动作。"""

    KEEP = "keep"
    MINIMIZE = "minimize"
    CLOSE = "close"


class DetailLevel(str, Enum):
    """窗口细节级别。"""

    FULL = "full"
    SUMMARY = "summary"
    ICON = "icon"
    NONE = "none"


class IntentTag(str, Enum):
    """窗口意图标签。"""

    AGGREGATE = "aggregate"
    FORMAT = "format"
    VALIDATE = "validate"
    FORMULA = "formula"
    ENTRY = "entry"
    GENERAL = "general"


@dataclass
class ColumnDef:
    """列定义。"""

    name: str
    inferred_type: str = "unknown"


@dataclass
class CachedRange:
    """缓存范围块。"""

    range_ref: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    is_current_viewport: bool = False
    added_at_iteration: int = 0


@dataclass
class OpEntry:
    """单次操作记录。"""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    success: bool = True


@dataclass
class ChangeRecord:
    """单次变更记录。"""

    operation: str
    tool_summary: str
    affected_range: str
    change_type: str
    iteration: int
    affected_row_indices: list[int] = field(default_factory=list)


@dataclass
class Viewport:
    """窗口视口信息。"""

    range_ref: str = "A1:T25"
    visible_rows: int = 25
    visible_cols: int = 20
    total_rows: int = 0
    total_cols: int = 0


@dataclass
class WindowSnapshot:
    """单个窗口渲染快照。"""

    window_id: str
    action: WindowRenderAction
    rendered_text: str
    estimated_tokens: int


@dataclass
class PerceptionBudget:
    """窗口感知预算配置。"""

    system_budget_tokens: int = 3000
    tool_append_tokens: int = 500
    max_windows: int = 6
    default_rows: int = 25
    default_cols: int = 20
    minimized_tokens: int = 80
    background_after_idle: int = 2
    suspend_after_idle: int = 5
    terminate_after_idle: int = 8
    window_full_max_rows: int = 25
    window_full_total_budget_tokens: int = 500
    window_data_buffer_max_rows: int = 200
