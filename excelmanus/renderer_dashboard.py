"""Dashboard 渲染器 — 三段布局的 CLI Dashboard 渲染引擎。

输入：ToolCallEvent
输出：Rich 终端组件（header / timeline / footer）

提供 start_turn / handle_event / finish_turn / fail_turn 生命周期方法。
在渲染异常时降级为纯文本输出，绝不中断会话。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from excelmanus.cli_dashboard import (
    DashboardMetrics,
    DashboardSessionBadges,
    DashboardTimelineEntry,
    DashboardTurnState,
)
from excelmanus.events import EventType, ToolCallEvent

logger = logging.getLogger(__name__)

_NARROW_TERMINAL_WIDTH = 60
_RESULT_MAX_LEN = 200
_THINKING_SUMMARY_LEN = 80
_SUBAGENT_SUMMARY_PREVIEW = 300
_SUBAGENT_REASON_PREVIEW = 220

# 元工具友好名称
_META_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "activate_skill": ("⚙️", "激活技能指引"),
    "expand_tools": ("🔧", "展开工具参数"),
    "delegate_to_subagent": ("🧵", "委派子任务"),
    "list_subagents": ("📋", "查询可用助手"),
}

_TOOL_ICONS: dict[str, str] = {
    "read_excel": "📖",
    "write_excel": "📝",
    "analyze_data": "📊",
    "filter_data": "🔍",
    "sort_data": "🔃",
    "create_chart": "📈",
    "format_cells": "🎨",
    "set_column_width": "↔️",
    "merge_cells": "🔗",
    "add_formula": "🧮",
    "create_pivot_table": "📋",
    "validate_data": "✅",
    "conditional_format": "🌈",
}


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _tool_icon(tool_name: str) -> str:
    return _TOOL_ICONS.get(tool_name, "🔧")


def _format_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


def _format_arguments(arguments: Dict[str, Any]) -> str:
    if not arguments:
        return "无参数"
    parts = []
    for key, value in arguments.items():
        if isinstance(value, str):
            display = _truncate(value, 60)
            parts.append(f'{key}="{display}"')
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


class DashboardRenderer:
    """Dashboard 模式渲染器。

    三段布局：
    - 顶部 header：会话/回合状态
    - 中部 body：事件时间线
    - 底部 footer：动态状态条 + 完成摘要
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._state = DashboardTurnState()
        self._metrics = DashboardMetrics()
        self._tool_start_times: dict[str, float] = {}
        # 流式输出状态（与 StreamRenderer 兼容）
        self._streaming_text = False
        self._streaming_thinking = False

    @property
    def state(self) -> DashboardTurnState:
        return self._state

    @property
    def metrics(self) -> DashboardMetrics:
        return self._metrics

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start_turn(
        self,
        turn_number: int,
        model_name: str = "",
        *,
        badges: DashboardSessionBadges | None = None,
    ) -> None:
        """开始新回合：重置状态，渲染 header。"""
        self._state.reset_for_new_turn(
            turn_number=turn_number, model_name=model_name
        )
        self._metrics = DashboardMetrics()
        self._render_header(badges)

    def handle_event(self, event: ToolCallEvent) -> None:
        """事件分发入口。"""
        try:
            self._dispatch_event(event)
        except Exception as exc:
            logger.warning("Dashboard 渲染异常，降级为纯文本: %s", exc)
            self._fallback_render(event)

    def finish_turn(
        self,
        *,
        elapsed_seconds: float = 0.0,
        total_tokens: int = 0,
    ) -> None:
        """结束回合：渲染摘要 footer。"""
        self._state.status = "idle"
        self._metrics.elapsed_seconds = elapsed_seconds
        self._render_footer_summary()

    def fail_turn(self, error: str) -> None:
        """回合异常：渲染错误 footer。"""
        self._state.status = "idle"
        self._console.print(
            f"  [red]❌ 回合异常：{rich_escape(error)}[/red]"
        )

    def finish_streaming(self) -> None:
        """流式输出结束时调用，确保换行。"""
        if self._streaming_text or self._streaming_thinking:
            self._console.print()
            self._streaming_text = False
            self._streaming_thinking = False

    # ------------------------------------------------------------------
    # Header 渲染
    # ------------------------------------------------------------------

    def _render_header(self, badges: DashboardSessionBadges | None = None) -> None:
        """渲染顶部状态栏。"""
        s = self._state
        parts: list[str] = []
        if s.model_name:
            parts.append(f"[bold #f0c674]{rich_escape(s.model_name)}[/bold #f0c674]")
        parts.append(f"[dim white]#{s.turn_number}[/dim white]")
        if badges:
            for badge in badges.to_badges_list():
                parts.append(f"[dim #888888]{rich_escape(badge)}[/dim #888888]")

        header_text = "  ".join(parts)

        if self._is_narrow():
            self._console.print(f"\n── {header_text} ──")
        else:
            self._console.print()
            self._console.rule(
                f"[bold #81a2be]回合 {s.turn_number}[/bold #81a2be]  {header_text}",
                style="dim #5f87af",
            )

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def _dispatch_event(self, event: ToolCallEvent) -> None:
        """根据事件类型分发到对应渲染方法。"""
        handlers = {
            EventType.TOOL_CALL_START: self._on_tool_start,
            EventType.TOOL_CALL_END: self._on_tool_end,
            EventType.THINKING: self._on_thinking,
            EventType.THINKING_DELTA: self._on_thinking_delta,
            EventType.TEXT_DELTA: self._on_text_delta,
            EventType.ITERATION_START: self._on_iteration,
            EventType.ROUTE_START: self._on_route_start,
            EventType.ROUTE_END: self._on_route_end,
            EventType.SUBAGENT_START: self._on_subagent_start,
            EventType.SUBAGENT_ITERATION: self._on_subagent_iteration,
            EventType.SUBAGENT_END: self._on_subagent_end,
            EventType.SUBAGENT_SUMMARY: self._on_subagent_summary,
            EventType.CHAT_SUMMARY: self._on_chat_summary,
            EventType.TASK_LIST_CREATED: self._on_task_list,
            EventType.TASK_ITEM_UPDATED: self._on_task_update,
            EventType.USER_QUESTION: self._on_question,
            EventType.PENDING_APPROVAL: self._on_approval,
        }
        handler = handlers.get(event.event_type)
        if handler:
            handler(event)

    # ------------------------------------------------------------------
    # Tool events
    # ------------------------------------------------------------------

    def _on_tool_start(self, event: ToolCallEvent) -> None:
        self._state.status = "tool_exec"
        self._tool_start_times[event.tool_name] = time.monotonic()

        meta = _META_TOOL_DISPLAY.get(event.tool_name)
        if meta is not None:
            icon, display_name = meta
            hint = self._meta_tool_hint(event.tool_name, event.arguments)
            line = f"  {icon} [bold]{display_name}[/bold]"
            if hint:
                line += f" [dim white]← {rich_escape(hint)}[/dim white]"
            self._console.print(line)
        else:
            icon = _tool_icon(event.tool_name)
            args_text = rich_escape(_format_arguments(event.arguments))
            if self._is_narrow():
                self._console.print(f"  {icon} {rich_escape(event.tool_name)}")
                self._console.print(f"     {args_text}", style="dim white")
            else:
                self._console.print(
                    f"  {icon} [bold]{rich_escape(event.tool_name)}[/bold] [dim white]← {args_text}[/dim white]"
                )

        self._state.add_timeline_entry(DashboardTimelineEntry(
            icon=icon if not meta else meta[0],
            label=event.tool_name,
            detail=_format_arguments(event.arguments),
            category="tool",
        ))

    def _on_tool_end(self, event: ToolCallEvent) -> None:
        start = self._tool_start_times.pop(event.tool_name, None)
        elapsed_str = ""
        if start is not None:
            elapsed = time.monotonic() - start
            elapsed_str = f" [dim white]({_format_elapsed(elapsed)})[/dim white]"

        self._metrics.record_tool_result(success=event.success)
        is_meta = event.tool_name in _META_TOOL_DISPLAY

        if event.success:
            if is_meta:
                self._console.print(f"     [green]✅[/green]{elapsed_str}")
            else:
                detail = rich_escape(_truncate(event.result, _RESULT_MAX_LEN)) if event.result else ""
                if self._is_narrow():
                    self._console.print(f"     ✅ 成功{elapsed_str}")
                    if detail:
                        self._console.print(f"     {detail}", style="dim white")
                else:
                    line = f"     [green]✅ 成功[/green]{elapsed_str}"
                    if detail:
                        line += f" [dim white]→ {detail}[/dim white]"
                    self._console.print(line)
        else:
            error_msg = rich_escape(event.error or "未知错误")
            if is_meta:
                self._console.print(f"     [red]❌[/red]{elapsed_str} [red]{error_msg}[/red]")
            elif self._is_narrow():
                self._console.print(f"     ❌ 失败{elapsed_str}")
                self._console.print(f"     {error_msg}", style="red")
            else:
                self._console.print(
                    f"     [red]❌ 失败[/red]{elapsed_str} [red]→ {error_msg}[/red]"
                )

        self._state.status = "thinking"

    # ------------------------------------------------------------------
    # Thinking / text delta
    # ------------------------------------------------------------------

    def _on_thinking(self, event: ToolCallEvent) -> None:
        if self._streaming_thinking:
            self._console.print()
            self._streaming_thinking = False
            return
        if not event.thinking:
            return
        summary = _truncate(event.thinking, _THINKING_SUMMARY_LEN)
        self._console.print(f"  💭 [dim italic]{summary}[/dim italic]")

    def _on_thinking_delta(self, event: ToolCallEvent) -> None:
        if not event.thinking_delta:
            return
        if not self._streaming_thinking:
            self._streaming_thinking = True
            self._console.print("  💭 ", end="", style="dim italic")
        self._console.print(event.thinking_delta, end="", style="dim italic")

    def _on_text_delta(self, event: ToolCallEvent) -> None:
        if not event.text_delta:
            return
        if self._streaming_thinking:
            self._console.print()
            self._streaming_thinking = False
        if not self._streaming_text:
            self._streaming_text = True
            self._console.print()
        self._console.print(event.text_delta, end="")

    # ------------------------------------------------------------------
    # Iteration / Route
    # ------------------------------------------------------------------

    def _on_iteration(self, event: ToolCallEvent) -> None:
        pass  # Dashboard 不需要额外的迭代分隔线

    def _on_route_start(self, event: ToolCallEvent) -> None:
        self._console.print("  🔀 [dim white]正在匹配技能包…[/dim white]")

    def _on_route_end(self, event: ToolCallEvent) -> None:
        self._state.route_mode = event.route_mode
        self._state.skills_used = list(event.skills_used) if event.skills_used else []

        if not event.skills_used:
            self._console.print(
                "  🔀 [dim white]路由完成[/dim white] · [#f0c674]通用模式[/#f0c674]"
            )
        else:
            skills_str = " ".join(
                f"[bold #b294bb]{s}[/bold #b294bb]" for s in event.skills_used
            )
            mode_label = event.route_mode.replace("_", " ")
            self._console.print(
                f"  🔀 [dim white]路由完成[/dim white] · {skills_str} [dim white]({mode_label})[/dim white]"
            )

    # ------------------------------------------------------------------
    # Subagent
    # ------------------------------------------------------------------

    def _on_subagent_start(self, event: ToolCallEvent) -> None:
        self._state.status = "subagent"
        self._state.subagent_active = True
        self._state.subagent_name = (event.subagent_name or "subagent").strip() or "subagent"

        name = rich_escape(self._state.subagent_name)
        reason = rich_escape(_truncate(
            (event.subagent_reason or "触发子代理").strip() or "触发子代理",
            _SUBAGENT_REASON_PREVIEW,
        ))
        permission = rich_escape((event.subagent_permission_mode or "").strip() or "未声明")
        conv_id = rich_escape((event.subagent_conversation_id or "").strip() or "未声明")
        tools_raw = event.subagent_tools or []
        tools_count = len(tools_raw)

        self._console.print(
            f"  🧵 [bold #81a2be]subagent 启动[/bold #81a2be] "
            f"[dim white]代理: {name} | 权限: {permission} | 会话: {conv_id}[/dim white]"
        )
        self._console.print(f"     [dim white]任务: {reason}[/dim white]")
        self._console.print(f"     [dim white]工具({tools_count})[/dim white]")

        self._state.add_timeline_entry(DashboardTimelineEntry(
            icon="🧵", label=f"subagent:{self._state.subagent_name}",
            detail=reason, category="subagent",
        ))

    def _on_subagent_iteration(self, event: ToolCallEvent) -> None:
        turn = event.subagent_iterations or event.iteration or 0
        calls = event.subagent_tool_calls or 0
        self._state.update_subagent_iteration(turn=turn, total_calls=calls)

        name = rich_escape(self._state.subagent_name)
        delta = self._state.subagent_delta_calls
        if calls > 0 and delta > 0:
            text = (
                f"  🧵 代理:{name} · 轮次 {turn} · 累计工具 {calls} 次"
                f"（本轮 +{delta}）"
            )
        else:
            text = f"  🧵 代理:{name} · 轮次 {turn} · 累计工具 {calls} 次"
        self._console.print(text, style="dim #81a2be")

    def _on_subagent_end(self, event: ToolCallEvent) -> None:
        self._state.subagent_active = False
        status = "完成" if event.subagent_success else "失败"
        color = "green" if event.subagent_success else "red"
        name = rich_escape(self._state.subagent_name)
        turns = event.subagent_iterations or 0
        calls = event.subagent_tool_calls or 0
        stats = f"共 {turns} 轮对话, {calls} 次工具调用" if turns else ""

        parts = f"  🧵 subagent [bold {color}]{status}[/bold {color}] · 代理: {name}"
        if stats:
            parts += f" [dim white]({stats})[/dim white]"
        self._console.print(parts)
        self._state.status = "thinking"

    def _on_subagent_summary(self, event: ToolCallEvent) -> None:
        self._state.status = "summarizing"
        summary = (event.subagent_summary or "").strip()
        if not summary:
            return
        preview = _truncate(summary, _SUBAGENT_SUMMARY_PREVIEW)
        name = rich_escape((event.subagent_name or "subagent").strip() or "subagent")
        turns = event.subagent_iterations or 0
        calls = event.subagent_tool_calls or 0
        meta = f"轮次: {turns} · 工具: {calls}" if turns or calls else ""

        panel_body = rich_escape(preview)
        if meta:
            panel_body = f"[dim]{rich_escape(meta)}[/dim]\n{panel_body}"

        if self._is_narrow():
            self._console.print(f"  🧾 subagent 摘要 · 代理: {name}", style="#81a2be")
            if meta:
                self._console.print(f"     {rich_escape(meta)}", style="dim white")
            self._console.print(f"     {rich_escape(preview)}", style="dim white")
        else:
            self._console.print(
                Panel(
                    panel_body,
                    title=f"[bold #81a2be]🧾 subagent 摘要 · {name}[/bold #81a2be]",
                    title_align="left",
                    border_style="dim #5f87af",
                    expand=False,
                    padding=(0, 1),
                )
            )

    # ------------------------------------------------------------------
    # Chat summary / task list / question / approval
    # ------------------------------------------------------------------

    def _on_chat_summary(self, event: ToolCallEvent) -> None:
        self._metrics.record_tokens(
            prompt=event.prompt_tokens, completion=event.completion_tokens
        )

    def _on_task_list(self, event: ToolCallEvent) -> None:
        data = event.task_list_data
        if not data:
            return
        title = data.get("title", "")
        items = data.get("items", [])
        _STATUS_ICONS = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}
        lines = [f"  📋 [bold]{title}[/bold]"]
        for i, item in enumerate(items):
            icon = _STATUS_ICONS.get(item.get("status", ""), "⬜")
            lines.append(f"     {icon} {i}. {item.get('title', '')}")
        self._console.print("\n".join(lines))

    def _on_task_update(self, event: ToolCallEvent) -> None:
        _STATUS_ICONS = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}
        idx = event.task_index
        status = event.task_status
        icon = _STATUS_ICONS.get(status, "❓")
        data = event.task_list_data or {}
        items = data.get("items", [])
        title = items[idx]["title"] if idx is not None and 0 <= idx < len(items) else f"#{idx}"
        self._console.print(f"     {icon} {idx}. {title}")

    def _on_question(self, event: ToolCallEvent) -> None:
        header = (event.question_header or "").strip() or "待确认"
        text = (event.question_text or "").strip()
        options = event.question_options or []
        # 仅输出简洁提示，完整问题由交互选择器渲染
        option_count = len(options) if isinstance(options, list) else 0
        hint = f"  [bold #f0c674]❓ {rich_escape(header)}[/bold #f0c674]"
        if option_count > 0:
            hint += f"  [dim white]({option_count} 个选项，请在下方选择)[/dim white]"
        self._console.print(hint)

    def _on_approval(self, event: ToolCallEvent) -> None:
        tool_name = event.approval_tool_name or "未知工具"
        approval_id = event.approval_id or ""
        args = event.approval_arguments or {}
        args_parts: list[str] = []
        for key in ("file_path", "sheet_name", "script", "command"):
            val = args.get(key)
            if val is not None:
                display = str(val)[:60]
                args_parts.append(f"{key}={display}")
        args_summary = ", ".join(args_parts) if args_parts else ""
        lines = [f"工具: {tool_name}", f"ID: {approval_id}"]
        if args_summary:
            lines.append(f"参数: {args_summary}")
        content = "\n".join(lines)
        self._console.print()
        self._console.print(
            Panel(
                rich_escape(content),
                title="[bold #f0c674]⚠️ 检测到高风险操作[/bold #f0c674]",
                title_align="left",
                border_style="#de935f",
                expand=False,
                padding=(1, 2),
            )
        )

    # ------------------------------------------------------------------
    # Footer summary
    # ------------------------------------------------------------------

    def _render_footer_summary(self) -> None:
        """渲染回合结束摘要。"""
        m = self._metrics
        elapsed_str = _format_elapsed(m.elapsed_seconds)

        if m.total_tool_calls == 0:
            # 纯文本回复：简洁完成标记
            self._console.print()
            self._console.rule(
                f"[dim white]✓ 回合完成  ⏱ {elapsed_str}[/dim white]",
                style="dim #5f5f5f",
            )
            return

        if self._is_narrow():
            parts = [
                f"📋 {m.total_tool_calls} 次调用",
                f"✅{m.success_count} ❌{m.failure_count}",
                f"⏱ {elapsed_str}",
            ]
            self._console.print()
            self._console.print(" · ".join(parts), style="dim white")
        else:
            table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
            table.add_column(style="dim white")
            table.add_column()
            table.add_row("工具调用", f"[bold]{m.total_tool_calls}[/bold] 次")
            table.add_row(
                "执行结果",
                f"[green]✅ {m.success_count}[/green]  [red]❌ {m.failure_count}[/red]",
            )
            table.add_row("总耗时", f"[bold]{elapsed_str}[/bold]")
            if m.total_tokens > 0:
                table.add_row("tokens", f"{m.total_tokens:,}")

            self._console.print()
            self._console.print(
                Panel(
                    table,
                    title="[bold]📋 执行摘要[/bold]",
                    title_align="left",
                    border_style="dim #5f875f" if m.failure_count == 0 else "dim #de935f",
                    expand=False,
                    padding=(0, 2),
                )
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_narrow(self) -> bool:
        try:
            explicit_width = getattr(self._console, "_width", None)
            if isinstance(explicit_width, int) and explicit_width > 0:
                return explicit_width < _NARROW_TERMINAL_WIDTH
            w = self._console.width
            if isinstance(w, int):
                return w < _NARROW_TERMINAL_WIDTH
        except Exception:
            pass
        return False

    @staticmethod
    def _meta_tool_hint(tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name == "activate_skill":
            reason = arguments.get("reason", "")
            return reason.strip() if isinstance(reason, str) else ""
        if tool_name == "delegate_to_subagent":
            task = arguments.get("task", "")
            return _truncate(task.strip(), 60) if isinstance(task, str) else ""
        return ""

    def _fallback_render(self, event: ToolCallEvent) -> None:
        """渲染异常时的纯文本降级输出。"""
        try:
            if event.event_type == EventType.TOOL_CALL_START:
                self._console.print(
                    f"🔧 {event.tool_name} ({_format_arguments(event.arguments)})"
                )
            elif event.event_type == EventType.TOOL_CALL_END:
                icon = "✅" if event.success else "❌"
                detail = event.result if event.success else (event.error or "")
                self._console.print(f"  {icon} {_truncate(detail, _RESULT_MAX_LEN)}")
            elif event.event_type == EventType.THINKING:
                if event.thinking:
                    self._console.print(f"💭 {_truncate(event.thinking, _THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.SUBAGENT_START:
                name = event.subagent_name or "subagent"
                self._console.print(f"🧵 subagent 启动 · 代理:{name}")
            elif event.event_type == EventType.SUBAGENT_END:
                name = event.subagent_name or "subagent"
                status = "完成" if event.subagent_success else "失败"
                self._console.print(f"🧵 subagent {status} · 代理:{name}")
        except Exception as exc:
            logger.error("纯文本降级渲染也失败: %s", exc)
