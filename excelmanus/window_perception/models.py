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

    range_ref: str = "A1:J25"
    visible_rows: int = 25
    visible_cols: int = 10
    total_rows: int = 0
    total_cols: int = 0


@dataclass
class WindowState:
    """单个窗口状态。"""

    id: str
    type: WindowType
    title: str
    file_path: str | None = None
    sheet_name: str | None = None
    directory: str | None = None
    sheet_tabs: list[str] = field(default_factory=list)
    viewport: Viewport | None = None
    freeze_panes: str | None = None
    style_summary: str = ""
    preview_rows: list[Any] = field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # v2 主字段：结构化 schema，columns 继续保留兼容旧渲染路径。
    schema: list[ColumnDef] = field(default_factory=list)
    columns: list[ColumnDef] = field(default_factory=list)
    total_rows: int = 0
    total_cols: int = 0
    viewport_range: str = ""
    cached_ranges: list[CachedRange] = field(default_factory=list)
    data_buffer: list[dict[str, Any]] = field(default_factory=list)
    max_cached_rows: int = 200
    stale_hint: str | None = None
    filter_state: dict[str, Any] | None = None
    unfiltered_buffer: list[dict[str, Any]] | None = None
    operation_history: list[OpEntry] = field(default_factory=list)
    max_history_entries: int = 20
    change_log: list[ChangeRecord] = field(default_factory=list)
    max_change_records: int = 5
    current_iteration: int = 0
    detail_level: DetailLevel = DetailLevel.FULL
    # 空闲轮次：每次 build_system_notice 周期内，未被访问窗口递增。
    idle_turns: int = 0
    # 操作序号（非对话 turn），用于窗口优先级排序。
    last_access_seq: int = 0
    # 休眠窗口不参与当前轮渲染，但保留缓存以便后续唤醒复用。
    dormant: bool = False
    # 意图层：用于控制序列化维度偏好。
    intent_tag: IntentTag = IntentTag.GENERAL
    intent_confidence: float = 0.0
    intent_source: str = "default"
    intent_updated_turn: int = 0
    intent_lock_until_turn: int = 0


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
    default_cols: int = 10
    minimized_tokens: int = 80
    background_after_idle: int = 1
    suspend_after_idle: int = 3
    terminate_after_idle: int = 5
    window_full_max_rows: int = 25
    window_full_total_budget_tokens: int = 500
    window_data_buffer_max_rows: int = 200
