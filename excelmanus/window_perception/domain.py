"""窗口领域模型，带类型化容器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import CachedRange, ChangeRecord, ColumnDef, DetailLevel, IntentTag, OpEntry, Viewport, WindowType


@dataclass
class LifecycleState:
    """横切生命周期状态。"""

    detail_level: DetailLevel = DetailLevel.FULL
    idle_turns: int = 0
    last_access_seq: int = 0
    dormant: bool = False


@dataclass
class IntentState:
    """横切意图状态。"""

    tag: IntentTag = IntentTag.GENERAL
    confidence: float = 0.0
    source: str = "default"
    updated_turn: int = 0
    lock_until_turn: int = 0


@dataclass
class AuditState:
    """横切审计状态。"""

    operation_history: list[OpEntry] = field(default_factory=list)
    max_history_entries: int = 20
    change_log: list[ChangeRecord] = field(default_factory=list)
    max_change_records: int = 5
    current_iteration: int = 0
    delta_audit: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FocusState:
    """横切焦点状态。"""

    is_active: bool = False
    last_action: str = ""


@dataclass
class BaseWindow:
    """所有窗口类型的公共基类。"""

    id: str
    kind: str
    title: str
    lifecycle: LifecycleState = field(default_factory=LifecycleState)
    intent: IntentState = field(default_factory=IntentState)
    audit: AuditState = field(default_factory=AuditState)
    focus: FocusState = field(default_factory=FocusState)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> WindowType:
        return WindowType.EXPLORER if self.kind == WindowType.EXPLORER.value else WindowType.SHEET

    @type.setter
    def type(self, value: WindowType | str) -> None:
        token = str(value.value if isinstance(value, WindowType) else value).strip().lower()
        if token in {WindowType.EXPLORER.value, WindowType.FOLDER.value}:
            self.kind = WindowType.EXPLORER.value
        else:
            self.kind = WindowType.SHEET.value

    @property
    def detail_level(self) -> DetailLevel:
        return self.lifecycle.detail_level

    @detail_level.setter
    def detail_level(self, value: DetailLevel) -> None:
        self.lifecycle.detail_level = value

    @property
    def idle_turns(self) -> int:
        return self.lifecycle.idle_turns

    @idle_turns.setter
    def idle_turns(self, value: int) -> None:
        self.lifecycle.idle_turns = int(value)

    @property
    def last_access_seq(self) -> int:
        return self.lifecycle.last_access_seq

    @last_access_seq.setter
    def last_access_seq(self, value: int) -> None:
        self.lifecycle.last_access_seq = int(value)

    @property
    def dormant(self) -> bool:
        return self.lifecycle.dormant

    @dormant.setter
    def dormant(self, value: bool) -> None:
        self.lifecycle.dormant = bool(value)

    @property
    def intent_tag(self) -> IntentTag:
        return self.intent.tag

    @intent_tag.setter
    def intent_tag(self, value: IntentTag) -> None:
        self.intent.tag = IntentTag(value)

    @property
    def intent_confidence(self) -> float:
        return self.intent.confidence

    @intent_confidence.setter
    def intent_confidence(self, value: float) -> None:
        self.intent.confidence = float(value)

    @property
    def intent_source(self) -> str:
        return self.intent.source

    @intent_source.setter
    def intent_source(self, value: str) -> None:
        self.intent.source = str(value)

    @property
    def intent_updated_turn(self) -> int:
        return self.intent.updated_turn

    @intent_updated_turn.setter
    def intent_updated_turn(self, value: int) -> None:
        self.intent.updated_turn = int(value)

    @property
    def intent_lock_until_turn(self) -> int:
        return self.intent.lock_until_turn

    @intent_lock_until_turn.setter
    def intent_lock_until_turn(self, value: int) -> None:
        self.intent.lock_until_turn = int(value)

    @property
    def operation_history(self) -> list[OpEntry]:
        return self.audit.operation_history

    @operation_history.setter
    def operation_history(self, value: list[OpEntry]) -> None:
        self.audit.operation_history = list(value)

    @property
    def max_history_entries(self) -> int:
        return self.audit.max_history_entries

    @max_history_entries.setter
    def max_history_entries(self, value: int) -> None:
        self.audit.max_history_entries = int(value)

    @property
    def change_log(self) -> list[ChangeRecord]:
        return self.audit.change_log

    @change_log.setter
    def change_log(self, value: list[ChangeRecord]) -> None:
        self.audit.change_log = list(value)

    @property
    def max_change_records(self) -> int:
        return self.audit.max_change_records

    @max_change_records.setter
    def max_change_records(self, value: int) -> None:
        self.audit.max_change_records = int(value)

    @property
    def current_iteration(self) -> int:
        return self.audit.current_iteration

    @current_iteration.setter
    def current_iteration(self, value: int) -> None:
        self.audit.current_iteration = int(value)


@dataclass
class ExplorerData:
    """资源管理器窗口专用数据容器。"""

    directory: str = "."
    entries: list[str] = field(default_factory=list)


@dataclass
class ExplorerWindow(BaseWindow):
    """带类型化数据容器的资源管理器窗口。"""

    data: ExplorerData = field(default_factory=ExplorerData)

    @classmethod
    def new(
        cls,
        *,
        id: str,
        title: str,
        directory: str,
        lifecycle: LifecycleState | None = None,
        intent: IntentState | None = None,
        audit: AuditState | None = None,
        focus: FocusState | None = None,
    ) -> "ExplorerWindow":
        return cls(
            id=id,
            title=title,
            kind=WindowType.EXPLORER.value,
            lifecycle=lifecycle or LifecycleState(),
            intent=intent or IntentState(),
            audit=audit or AuditState(),
            focus=focus or FocusState(),
            data=ExplorerData(directory=directory or "."),
        )

    @property
    def directory(self) -> str:
        return self.data.directory

    @directory.setter
    def directory(self, value: str | None) -> None:
        self.data.directory = str(value or ".")

    @property
    def entries(self) -> list[str]:
        return self.data.entries

    @entries.setter
    def entries(self, value: list[str]) -> None:
        self.data.entries = [str(item) for item in value]


@dataclass
class SheetCache:
    """表格缓存/状态容器。"""

    preview_rows: list[Any] = field(default_factory=list)
    data_buffer: list[dict[str, Any]] = field(default_factory=list)
    cached_ranges: list[CachedRange] = field(default_factory=list)
    max_cached_rows: int = 200
    stale_hint: str | None = None
    unfiltered_buffer: list[dict[str, Any]] | None = None
    last_op_kind: str | None = None        # 取值："read" | "write" | "filter" | None
    last_write_range: str | None = None    # 写操作受影响范围


@dataclass
class SheetStyle:
    """表格样式/状态容器。"""

    freeze_panes: str | None = None
    summary: str = ""
    column_widths: dict[str, Any] = field(default_factory=dict)
    row_heights: dict[str, Any] = field(default_factory=dict)
    merged_ranges: list[str] = field(default_factory=list)
    conditional_effects: list[str] = field(default_factory=list)


@dataclass
class SheetFilter:
    """表格筛选/状态容器。"""

    state: dict[str, Any] | None = None
    status_bar: dict[str, Any] = field(default_factory=dict)


@dataclass
class SheetSchema:
    """表格结构/状态容器。"""

    schema: list[ColumnDef] = field(default_factory=list)
    columns: list[ColumnDef] = field(default_factory=list)


@dataclass
class SheetFocus:
    """表格焦点/视口辅助。"""

    viewport_range: str = ""
    scroll_position: dict[str, Any] = field(default_factory=dict)


@dataclass
class SheetData:
    """表格专用类型化数据容器。"""

    file_path: str = ""
    sheet_name: str = ""
    sheet_tabs: list[str] = field(default_factory=list)
    viewport: Viewport | None = None
    cache: SheetCache = field(default_factory=SheetCache)
    style: SheetStyle = field(default_factory=SheetStyle)
    filter: SheetFilter = field(default_factory=SheetFilter)
    schema: SheetSchema = field(default_factory=SheetSchema)
    focus: SheetFocus = field(default_factory=SheetFocus)
    total_rows: int = 0
    total_cols: int = 0
    sheet_dimensions: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass
class SheetWindow(BaseWindow):
    """带类型化数据容器的表格窗口。"""

    data: SheetData = field(default_factory=SheetData)

    @classmethod
    def new(
        cls,
        *,
        id: str,
        title: str,
        file_path: str,
        sheet_name: str,
        lifecycle: LifecycleState | None = None,
        intent: IntentState | None = None,
        audit: AuditState | None = None,
        focus: FocusState | None = None,
    ) -> "SheetWindow":
        return cls(
            id=id,
            title=title,
            kind=WindowType.SHEET.value,
            lifecycle=lifecycle or LifecycleState(),
            intent=intent or IntentState(),
            audit=audit or AuditState(),
            focus=focus or FocusState(),
            data=SheetData(
                file_path=str(file_path or ""),
                sheet_name=str(sheet_name or ""),
            ),
        )

    @property
    def file_path(self) -> str:
        return self.data.file_path

    @file_path.setter
    def file_path(self, value: str | None) -> None:
        self.data.file_path = str(value or "")

    @property
    def sheet_name(self) -> str:
        return self.data.sheet_name

    @sheet_name.setter
    def sheet_name(self, value: str | None) -> None:
        self.data.sheet_name = str(value or "")

    @property
    def sheet_tabs(self) -> list[str]:
        return self.data.sheet_tabs

    @sheet_tabs.setter
    def sheet_tabs(self, value: list[str]) -> None:
        self.data.sheet_tabs = [str(item) for item in value]

    @property
    def viewport(self) -> Viewport | None:
        return self.data.viewport

    @viewport.setter
    def viewport(self, value: Viewport | None) -> None:
        self.data.viewport = value

    @property
    def freeze_panes(self) -> str | None:
        return self.data.style.freeze_panes

    @freeze_panes.setter
    def freeze_panes(self, value: str | None) -> None:
        self.data.style.freeze_panes = value

    @property
    def style_summary(self) -> str:
        return self.data.style.summary

    @style_summary.setter
    def style_summary(self, value: str) -> None:
        self.data.style.summary = str(value or "")

    @property
    def preview_rows(self) -> list[Any]:
        return self.data.cache.preview_rows

    @preview_rows.setter
    def preview_rows(self, value: list[Any]) -> None:
        self.data.cache.preview_rows = list(value)

    @property
    def schema(self) -> list[ColumnDef]:
        return self.data.schema.schema

    @schema.setter
    def schema(self, value: list[ColumnDef]) -> None:
        self.data.schema.schema = list(value)

    @property
    def columns(self) -> list[ColumnDef]:
        return self.data.schema.columns

    @columns.setter
    def columns(self, value: list[ColumnDef]) -> None:
        self.data.schema.columns = list(value)

    @property
    def total_rows(self) -> int:
        if self.data.total_rows > 0:
            return self.data.total_rows
        if self.data.viewport is not None:
            return int(self.data.viewport.total_rows)
        return 0

    @total_rows.setter
    def total_rows(self, value: int) -> None:
        self.data.total_rows = max(0, int(value))
        if self.data.viewport is not None:
            self.data.viewport.total_rows = max(0, int(value))

    @property
    def total_cols(self) -> int:
        if self.data.total_cols > 0:
            return self.data.total_cols
        if self.data.viewport is not None:
            return int(self.data.viewport.total_cols)
        return 0

    @total_cols.setter
    def total_cols(self, value: int) -> None:
        self.data.total_cols = max(0, int(value))
        if self.data.viewport is not None:
            self.data.viewport.total_cols = max(0, int(value))

    @property
    def viewport_range(self) -> str:
        if self.data.focus.viewport_range:
            return self.data.focus.viewport_range
        if self.data.viewport is not None:
            return self.data.viewport.range_ref
        return ""

    @viewport_range.setter
    def viewport_range(self, value: str) -> None:
        token = str(value or "")
        self.data.focus.viewport_range = token
        if self.data.viewport is not None:
            self.data.viewport.range_ref = token

    @property
    def cached_ranges(self) -> list[CachedRange]:
        return self.data.cache.cached_ranges

    @cached_ranges.setter
    def cached_ranges(self, value: list[CachedRange]) -> None:
        self.data.cache.cached_ranges = list(value)

    @property
    def data_buffer(self) -> list[dict[str, Any]]:
        return self.data.cache.data_buffer

    @data_buffer.setter
    def data_buffer(self, value: list[dict[str, Any]]) -> None:
        self.data.cache.data_buffer = list(value)

    @property
    def max_cached_rows(self) -> int:
        return self.data.cache.max_cached_rows

    @max_cached_rows.setter
    def max_cached_rows(self, value: int) -> None:
        self.data.cache.max_cached_rows = max(1, int(value))

    @property
    def stale_hint(self) -> str | None:
        return self.data.cache.stale_hint

    @stale_hint.setter
    def stale_hint(self, value: str | None) -> None:
        self.data.cache.stale_hint = value

    @property
    def filter_state(self) -> dict[str, Any] | None:
        return self.data.filter.state

    @filter_state.setter
    def filter_state(self, value: dict[str, Any] | None) -> None:
        self.data.filter.state = dict(value) if isinstance(value, dict) else None

    @property
    def unfiltered_buffer(self) -> list[dict[str, Any]] | None:
        return self.data.cache.unfiltered_buffer

    @unfiltered_buffer.setter
    def unfiltered_buffer(self, value: list[dict[str, Any]] | None) -> None:
        self.data.cache.unfiltered_buffer = list(value) if isinstance(value, list) else None

    @property
    def last_op_kind(self) -> str | None:
        return self.data.cache.last_op_kind

    @last_op_kind.setter
    def last_op_kind(self, value: str | None) -> None:
        self.data.cache.last_op_kind = value

    @property
    def last_write_range(self) -> str | None:
        return self.data.cache.last_write_range

    @last_write_range.setter
    def last_write_range(self, value: str | None) -> None:
        self.data.cache.last_write_range = value

    @property
    def scroll_position(self) -> dict[str, Any]:
        return self.data.focus.scroll_position

    @scroll_position.setter
    def scroll_position(self, value: dict[str, Any]) -> None:
        self.data.focus.scroll_position = dict(value) if isinstance(value, dict) else {}

    @property
    def status_bar(self) -> dict[str, Any]:
        return self.data.filter.status_bar

    @status_bar.setter
    def status_bar(self, value: dict[str, Any]) -> None:
        self.data.filter.status_bar = dict(value) if isinstance(value, dict) else {}

    @property
    def column_widths(self) -> dict[str, Any]:
        return self.data.style.column_widths

    @column_widths.setter
    def column_widths(self, value: dict[str, Any]) -> None:
        self.data.style.column_widths = dict(value) if isinstance(value, dict) else {}

    @property
    def row_heights(self) -> dict[str, Any]:
        return self.data.style.row_heights

    @row_heights.setter
    def row_heights(self, value: dict[str, Any]) -> None:
        self.data.style.row_heights = dict(value) if isinstance(value, dict) else {}

    @property
    def merged_ranges(self) -> list[str]:
        return self.data.style.merged_ranges

    @merged_ranges.setter
    def merged_ranges(self, value: list[str]) -> None:
        self.data.style.merged_ranges = [str(item) for item in value if str(item).strip()]

    @property
    def conditional_effects(self) -> list[str]:
        return self.data.style.conditional_effects

    @conditional_effects.setter
    def conditional_effects(self, value: list[str]) -> None:
        self.data.style.conditional_effects = [str(item) for item in value if str(item).strip()]

    @property
    def sheet_dimensions(self) -> dict[str, tuple[int, int]]:
        return self.data.sheet_dimensions

    @sheet_dimensions.setter
    def sheet_dimensions(self, value: dict[str, tuple[int, int]]) -> None:
        self.data.sheet_dimensions = dict(value) if isinstance(value, dict) else {}


Window = ExplorerWindow | SheetWindow
