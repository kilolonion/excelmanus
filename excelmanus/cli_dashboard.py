"""Dashboard 显示域模型 — CLI Dashboard 渲染层的状态数据结构。

提供 Dashboard 模式所需的状态模型：
- UiLayoutMode: 布局模式枚举
- DashboardTimelineEntry: 时间线条目
- DashboardTurnState: 单回合状态（时间线、子代理、状态）
- DashboardMetrics: 累积统计指标
- DashboardSessionBadges: 会话级徽章

约束：
- 时间线最多 200 条，超出后折叠。
- thinking / thinking_delta 合并显示，避免刷屏。
- subagent 事件保留信息密度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

_TIMELINE_MAX_ENTRIES = 200


class UiLayoutMode(Enum):
    """CLI 布局模式。"""

    DASHBOARD = "dashboard"
    CLASSIC = "classic"


@dataclass
class DashboardTimelineEntry:
    """时间线中的单条事件。"""

    icon: str = ""
    label: str = ""
    detail: str = ""
    elapsed_ms: float | None = None
    category: str = "tool"  # tool / subagent / approval / question / system


@dataclass
class DashboardTurnState:
    """单回合的 Dashboard 状态。"""

    turn_number: int = 0
    model_name: str = ""
    route_mode: str = ""
    skills_used: list[str] = field(default_factory=list)
    timeline: list[DashboardTimelineEntry] = field(default_factory=list)
    folded_count: int = 0
    status: str = "idle"  # idle / thinking / tool_exec / subagent / summarizing

    # subagent 状态
    subagent_active: bool = False
    subagent_name: str = ""
    subagent_turns: int = 0
    subagent_tool_calls: int = 0
    subagent_delta_calls: int = 0
    _subagent_last_calls: int = field(default=0, repr=False)

    def add_timeline_entry(self, entry: DashboardTimelineEntry) -> None:
        """追加时间线条目，超过上限时裁剪最旧的条目。"""
        self.timeline.append(entry)
        overflow = len(self.timeline) - _TIMELINE_MAX_ENTRIES
        if overflow > 0:
            self.timeline = self.timeline[overflow:]
            self.folded_count += overflow

    def reset_for_new_turn(
        self, *, turn_number: int, model_name: str = ""
    ) -> None:
        """重置为新回合状态。"""
        self.turn_number = turn_number
        self.model_name = model_name
        self.route_mode = ""
        self.skills_used = []
        self.timeline = []
        self.folded_count = 0
        self.status = "thinking"
        self.subagent_active = False
        self.subagent_name = ""
        self.subagent_turns = 0
        self.subagent_tool_calls = 0
        self.subagent_delta_calls = 0
        self._subagent_last_calls = 0

    def update_subagent_iteration(
        self, *, turn: int, total_calls: int
    ) -> None:
        """更新 subagent 轮次与工具调用统计，计算增量。"""
        self.subagent_delta_calls = total_calls - self._subagent_last_calls
        self._subagent_last_calls = total_calls
        self.subagent_turns = turn
        self.subagent_tool_calls = total_calls


@dataclass
class DashboardMetrics:
    """回合级累积统计指标。"""

    total_tool_calls: int = 0
    success_count: int = 0
    failure_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0

    def record_tool_result(self, *, success: bool) -> None:
        """记录单次工具调用结果。"""
        self.total_tool_calls += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1

    def record_tokens(self, *, prompt: int, completion: int) -> None:
        """累加 token 用量。"""
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens = self.prompt_tokens + self.completion_tokens


@dataclass
class DashboardSessionBadges:
    """会话级状态徽章。"""

    plan_mode: bool = False
    full_access: bool = False
    backup_enabled: bool = True
    layout_mode: str = "dashboard"

    def to_badges_list(self) -> list[str]:
        """生成徽章文本列表。"""
        badges: list[str] = []
        if self.plan_mode:
            badges.append("[plan]")
        if self.full_access:
            badges.append("[fullAccess]")
        if not self.backup_enabled:
            badges.append("[backup:off]")
        badges.append(f"[{self.layout_mode}]")
        return badges
