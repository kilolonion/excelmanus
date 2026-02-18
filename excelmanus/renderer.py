"""流式事件渲染器 — 将 AgentEngine 事件渲染为 Rich 终端组件。

负责将工具调用、思考过程、路由结果、执行摘要等事件
实时渲染为可视化卡片和状态行。
支持窄终端自适应和渲染异常降级。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from rich.columns import Columns
from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from excelmanus.events import EventType, ToolCallEvent

logger = logging.getLogger(__name__)

# 截断阈值常量
_RESULT_MAX_LEN = 200
_THINKING_THRESHOLD = 500
_THINKING_SUMMARY_LEN = 80
_NARROW_TERMINAL_WIDTH = 60
_SUBAGENT_SUMMARY_PREVIEW = 300

# 元工具：对用户隐藏内部细节，使用友好名称和描述
_META_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "activate_skill": ("⚙️", "激活技能指引"),
    "expand_tools": ("🔧", "展开工具参数"),
    "delegate_to_subagent": ("🧵", "委派子任务"),
    "list_subagents": ("📋", "查询可用助手"),
}

# 工具名称到图标的映射
# 任务状态到图标的映射
_STATUS_ICONS: dict[str, str] = {
    "pending": "⬜",
    "in_progress": "🔄",
    "completed": "✅",
    "failed": "❌",
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
    """截断文本，超过 max_len 时追加省略标记。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _format_arguments(arguments: Dict[str, Any]) -> str:
    """将参数字典格式化为可读字符串。"""
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


def _tool_icon(tool_name: str) -> str:
    """根据工具名称返回对应图标。"""
    return _TOOL_ICONS.get(tool_name, "🔧")


def _format_elapsed(seconds: float) -> str:
    """格式化耗时为人类可读字符串。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}m{secs:.0f}s"


class StreamRenderer:
    """流式事件渲染器。

    接收 ToolCallEvent 并渲染为 Rich 终端组件。
    支持窄终端自适应和渲染异常降级为纯文本。
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        # 记录每个工具调用的开始时间（用于计算单次耗时）
        self._tool_start_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def handle_event(self, event: ToolCallEvent) -> None:
        """事件分发入口，根据事件类型调用对应渲染方法。"""
        handlers = {
            EventType.TOOL_CALL_START: self._render_tool_start,
            EventType.TOOL_CALL_END: self._render_tool_end,
            EventType.THINKING: self._render_thinking,
            EventType.ITERATION_START: self._render_iteration,
            EventType.ROUTE_START: self._render_route_start,
            EventType.ROUTE_END: self._render_route_end,
            EventType.SUBAGENT_START: self._render_subagent_start,
            EventType.SUBAGENT_END: self._render_subagent_end,
            EventType.SUBAGENT_SUMMARY: self._render_subagent_summary,
            EventType.CHAT_SUMMARY: self._render_chat_summary,
            EventType.TASK_LIST_CREATED: self._render_task_list_created,
            EventType.TASK_ITEM_UPDATED: self._render_task_item_updated,
            EventType.USER_QUESTION: self._render_user_question,
            EventType.PENDING_APPROVAL: self._render_pending_approval,
        }
        handler = handlers.get(event.event_type)
        if handler:
            try:
                handler(event)
            except Exception as exc:
                # 渲染异常降级为纯文本输出
                logger.warning("渲染异常，降级为纯文本: %s", exc)
                self._fallback_render(event)

    # ------------------------------------------------------------------
    # 路由事件渲染
    # ------------------------------------------------------------------

    def _render_route_start(self, event: ToolCallEvent) -> None:
        """渲染路由开始状态。"""
        self._console.print("  🔀 [dim white]正在匹配技能包…[/dim white]")

    def _render_route_end(self, event: ToolCallEvent) -> None:
        """渲染路由结果。"""
        if not event.skills_used:
            self._console.print(
                "  🔀 [dim white]路由完成[/dim white] · [#f0c674]通用模式[/#f0c674]"
            )
            return

        skills_str = " ".join(
            f"[bold #b294bb]{s}[/bold #b294bb]" for s in event.skills_used
        )
        mode_label = event.route_mode.replace("_", " ")
        self._console.print(
            f"  🔀 [dim white]路由完成[/dim white] · {skills_str} [dim white]({mode_label})[/dim white]"
        )

    # ------------------------------------------------------------------
    # 迭代与思考渲染
    # ------------------------------------------------------------------

    def _render_iteration(self, event: ToolCallEvent) -> None:
        """渲染迭代轮次分隔线。"""
        if self._is_narrow():
            self._console.print(f"\n── 轮次 {event.iteration} ──")
        else:
            self._console.print()
            self._console.rule(
                f"[bold #81a2be]轮次 {event.iteration}[/bold #81a2be]",
                style="dim #5f87af",
            )

    def _render_thinking(self, event: ToolCallEvent) -> None:
        """渲染 LLM 思考过程。"""
        if not event.thinking:
            return

        summary = (
            _truncate(event.thinking, _THINKING_SUMMARY_LEN)
            if len(event.thinking) > _THINKING_THRESHOLD
            else event.thinking
        )

        if self._is_narrow():
            self._console.print(f"  💭 {summary}")
        else:
            self._console.print(f"  💭 [dim italic]{summary}[/dim italic]")

    # ------------------------------------------------------------------
    # 工具调用渲染
    # ------------------------------------------------------------------

    def _render_tool_start(self, event: ToolCallEvent) -> None:
        """渲染工具调用开始 — 紧凑的单行状态 + 参数。"""
        # 记录开始时间
        self._tool_start_times[event.tool_name] = time.monotonic()

        # 元工具使用友好名称，隐藏内部细节
        meta = _META_TOOL_DISPLAY.get(event.tool_name)
        if meta is not None:
            icon, display_name = meta
            # 从参数中提取用户可理解的描述
            hint = self._meta_tool_hint(event.tool_name, event.arguments)
            if self._is_narrow():
                self._console.print(f"  {icon} {display_name}")
                if hint:
                    self._console.print(f"     {rich_escape(hint)}", style="dim white")
            else:
                line = f"  {icon} [bold]{display_name}[/bold]"
                if hint:
                    line += f" [dim white]← {rich_escape(hint)}[/dim white]"
                self._console.print(line)
            return

        icon = _tool_icon(event.tool_name)
        args_text = rich_escape(_format_arguments(event.arguments))

        if self._is_narrow():
            self._console.print(f"  {icon} {rich_escape(event.tool_name)}")
            self._console.print(f"     {args_text}", style="dim white")
        else:
            self._console.print(
                f"  {icon} [bold]{rich_escape(event.tool_name)}[/bold] [dim white]← {args_text}[/dim white]"
            )

    def _render_tool_end(self, event: ToolCallEvent) -> None:
        """渲染工具调用结束 — 成功/失败状态 + 耗时 + 结果摘要。"""
        # 计算耗时
        start = self._tool_start_times.pop(event.tool_name, None)
        elapsed_str = ""
        if start is not None:
            elapsed = time.monotonic() - start
            elapsed_str = f" [dim white]({_format_elapsed(elapsed)})[/dim white]"

        # 元工具：简化结果展示，不暴露内部上下文
        is_meta = event.tool_name in _META_TOOL_DISPLAY

        if event.success:
            if is_meta:
                # 元工具只显示简洁的成功状态
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
                # 元工具失败也使用简洁提示
                self._console.print(f"     [red]❌[/red]{elapsed_str} [red]{error_msg}[/red]")
            elif self._is_narrow():
                self._console.print(f"     ❌ 失败{elapsed_str}")
                self._console.print(f"     {error_msg}", style="red")
            else:
                self._console.print(
                    f"     [red]❌ 失败[/red]{elapsed_str} [red]→ {error_msg}[/red]"
                )

    # ------------------------------------------------------------------
    # 任务清单渲染
    # ------------------------------------------------------------------

    def _render_task_list_created(self, event: ToolCallEvent) -> None:
        """渲染新建任务清单。"""
        data = event.task_list_data
        if not data:
            return
        title = data.get("title", "")
        items = data.get("items", [])
        if self._is_narrow():
            # 窄终端紧凑格式
            lines = [f"📋 {title}"]
            for i, item in enumerate(items):
                icon = _STATUS_ICONS.get(item["status"], "⬜")
                lines.append(f"{icon}{i}.{item['title']}")
        else:
            lines = [f"  📋 [bold]{title}[/bold]"]
            for i, item in enumerate(items):
                icon = _STATUS_ICONS.get(item["status"], "⬜")
                lines.append(f"     {icon} {i}. {item['title']}")
        self._console.print("\n".join(lines))

    def _render_task_item_updated(self, event: ToolCallEvent) -> None:
        """渲染任务项状态更新。"""
        idx = event.task_index
        status = event.task_status
        icon = _STATUS_ICONS.get(status, "❓")
        data = event.task_list_data or {}
        items = data.get("items", [])
        if idx is not None and 0 <= idx < len(items):
            title = items[idx]["title"]
        else:
            title = f"#{idx}"

        if self._is_narrow():
            self._console.print(f"{icon}{idx}.{title}")
        else:
            self._console.print(f"     {icon} {idx}. {title}")

        # 检查是否全部完成
        progress = data.get("progress", {})
        total = sum(progress.values())
        done = progress.get("completed", 0) + progress.get("failed", 0)
        if total > 0 and done == total:
            self._console.print(
                f"  📋 全部完成: ✅{progress.get('completed', 0)} ❌{progress.get('failed', 0)}"
            )

    def _render_user_question(self, event: ToolCallEvent) -> None:
        """渲染 ask_user 问题卡片。"""
        header = (event.question_header or "").strip() or "待确认"
        text = (event.question_text or "").strip()
        options = event.question_options or []

        lines: list[str] = []
        if text:
            lines.append(text)
            lines.append("")

        for i, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                continue
            label = str(option.get("label", "")).strip()
            description = str(option.get("description", "")).strip()
            if label and description:
                lines.append(f"{i}. {label} - {description}")
            elif label:
                lines.append(f"{i}. {label}")

        if options:
            lines.append("")
        if event.question_multi_select:
            lines.append("多选：每行输入一个选项，空行提交。")
        else:
            lines.append("单选：输入一个选项（编号或文本）。")

        if event.question_queue_size > 1:
            lines.append(f"队列中还有 {event.question_queue_size - 1} 个待回答问题。")

        content = "\n".join(lines) if lines else "请先回答当前问题。"
        self._console.print()
        self._console.print(
            Panel(
                rich_escape(content),
                title=f"[bold #f0c674]❓ {rich_escape(header)}[/bold #f0c674]",
                title_align="left",
                border_style="#de935f",
                expand=False,
                padding=(1, 2),
            )
        )

    def _render_pending_approval(self, event: ToolCallEvent) -> None:
        """渲染待确认审批卡片（与 ask_user 风格一致）。"""
        tool_name = event.approval_tool_name or "未知工具"
        approval_id = event.approval_id or ""
        args = event.approval_arguments or {}

        # 构建参数摘要（截取关键信息）
        args_summary_parts: list[str] = []
        for key in ("file_path", "sheet_name", "script", "command"):
            val = args.get(key)
            if val is not None:
                display = str(val)
                if len(display) > 60:
                    display = display[:57] + "..."
                args_summary_parts.append(f"{key}={display}")
        args_summary = ", ".join(args_summary_parts) if args_summary_parts else ""

        lines: list[str] = [
            f"工具: {tool_name}",
            f"ID: {approval_id}",
        ]
        if args_summary:
            lines.append(f"参数: {args_summary}")
        lines.append("")
        lines.append("1. ✅ 执行 - 确认并执行此操作")
        lines.append("2. ❌ 拒绝 - 取消此操作")
        lines.append("3. 🔓 全部授权 - 开启 fullAccess 后自动执行")
        lines.append("")
        lines.append("单选：输入编号或使用方向键选择。")

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
    # 执行摘要渲染
    # ------------------------------------------------------------------

    def _render_subagent_start(self, event: ToolCallEvent) -> None:
        """渲染 subagent 开始。"""
        reason = rich_escape(event.subagent_reason or "触发子代理")
        tools = ", ".join(event.subagent_tools) if event.subagent_tools else "(无)"
        if self._is_narrow():
            self._console.print("  🧵 subagent 启动")
            self._console.print(f"     原因: {reason}", style="dim white")
            self._console.print(f"     工具: {rich_escape(tools)}", style="dim white")
        else:
            self._console.print(
                f"  🧵 [bold #81a2be]subagent 启动[/bold #81a2be] "
                f"[dim white]原因: {reason} | 工具: {rich_escape(tools)}[/dim white]"
            )

    def _render_subagent_summary(self, event: ToolCallEvent) -> None:
        """渲染 subagent 摘要。"""
        summary = (event.subagent_summary or "").strip()
        if not summary:
            return
        preview = _truncate(summary, _SUBAGENT_SUMMARY_PREVIEW)

        if self._is_narrow():
            self._console.print("  🧾 subagent 摘要", style="#81a2be")
            self._console.print(f"     {rich_escape(preview)}", style="dim white")
            return

        self._console.print(
            Panel(
                rich_escape(preview),
                title="[bold #81a2be]🧾 subagent 摘要[/bold #81a2be]",
                title_align="left",
                border_style="dim #5f87af",
                expand=False,
                padding=(0, 1),
            )
        )

    def _render_subagent_end(self, event: ToolCallEvent) -> None:
        """渲染 subagent 结束。"""
        status = "完成" if event.subagent_success else "失败"
        color = "green" if event.subagent_success else "red"
        if self._is_narrow():
            icon = "✅" if event.subagent_success else "❌"
            self._console.print(f"  🧵 subagent {icon}{status}")
        else:
            self._console.print(
                f"  🧵 subagent [bold {color}]{status}[/bold {color}]"
            )

    def _render_chat_summary(self, event: ToolCallEvent) -> None:
        """渲染执行摘要面板。"""
        # 没有工具调用时仅显示 token 用量（纯对话）
        if event.total_tool_calls == 0:
            token_str = self._format_token_usage(event)
            if token_str:
                self._console.print()
                self._console.print(f"  tokens：{token_str}", style="dim white")
            return

        elapsed_str = _format_elapsed(event.elapsed_seconds)
        token_str = self._format_token_usage(event)

        if self._is_narrow():
            self._console.print()
            parts = [
                f"📋 {event.total_tool_calls} 次调用",
                f"✅{event.success_count} ❌{event.failure_count}",
                f"⏱ {elapsed_str}",
            ]
            if token_str:
                parts.append(f"tokens：{token_str}")
            self._console.print(" · ".join(parts), style="dim white")
            return

        # 构建摘要表格
        table = Table(show_header=False, show_edge=False, pad_edge=False, expand=False)
        table.add_column(style="dim white")
        table.add_column()

        table.add_row("工具调用", f"[bold]{event.total_tool_calls}[/bold] 次")
        table.add_row(
            "执行结果",
            f"[green]✅ {event.success_count}[/green]  "
            f"[red]❌ {event.failure_count}[/red]",
        )
        table.add_row("迭代轮次", f"{event.total_iterations}")
        table.add_row("总耗时", f"[bold]{elapsed_str}[/bold]")
        if token_str:
            table.add_row("tokens", token_str)

        self._console.print()
        self._console.print(
            Panel(
                table,
                title="[bold]📋 执行摘要[/bold]",
                title_align="left",
                border_style="dim #5f875f" if event.failure_count == 0 else "dim #de935f",
                expand=False,
                padding=(0, 2),
            )
        )
    @staticmethod
    def _format_token_usage(event: ToolCallEvent) -> str:
        """格式化 token 用量为可读字符串，无数据时返回空串。"""
        if event.total_tokens <= 0:
            return ""
        prompt = f"{event.prompt_tokens:,}"
        completion = f"{event.completion_tokens:,}"
        total = f"{event.total_tokens:,}"
        return f"[dim #81a2be]{prompt}[/dim #81a2be] tokens 输入 + [dim #81a2be]{completion}[/dim #81a2be] tokens 输出 = [bold #81a2be]{total}[/bold #81a2be] tokens"

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_tool_hint(tool_name: str, arguments: Dict[str, Any]) -> str:
        """从元工具参数中提取用户可理解的简短描述，隐藏内部名称。"""
        if tool_name == "activate_skill":
            reason = arguments.get("reason", "")
            if isinstance(reason, str) and reason.strip():
                return reason.strip()
            return ""
        if tool_name == "delegate_to_subagent":
            task = arguments.get("task", "")
            if isinstance(task, str) and task.strip():
                return _truncate(task.strip(), 60)
            return ""
        return ""

    def _is_narrow(self) -> bool:
        """判断终端是否为窄终端（宽度 < 60）。"""
        explicit_width = getattr(self._console, "_width", None)
        if isinstance(explicit_width, int) and explicit_width > 0:
            return explicit_width < _NARROW_TERMINAL_WIDTH
        return self._console.width < _NARROW_TERMINAL_WIDTH

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
                self._console.print(
                    f"  {icon} {_truncate(detail, _RESULT_MAX_LEN)}"
                )
            elif event.event_type == EventType.THINKING:
                if event.thinking:
                    self._console.print(
                        f"💭 {_truncate(event.thinking, _THINKING_SUMMARY_LEN)}"
                    )
            elif event.event_type == EventType.ITERATION_START:
                self._console.print(f"── 轮次 {event.iteration} ──")
            elif event.event_type == EventType.ROUTE_END:
                skills = ", ".join(event.skills_used) if event.skills_used else "通用"
                self._console.print(f"🔀 路由: {skills}")
            elif event.event_type == EventType.SUBAGENT_START:
                reason = event.subagent_reason or "触发子代理"
                self._console.print(f"🧵 subagent 启动: {_truncate(reason, _THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.SUBAGENT_SUMMARY:
                summary = event.subagent_summary or ""
                if summary:
                    self._console.print(f"🧾 subagent 摘要: {_truncate(summary, _THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.SUBAGENT_END:
                status = "完成" if event.subagent_success else "失败"
                self._console.print(f"🧵 subagent 结束: {status}")
            elif event.event_type == EventType.CHAT_SUMMARY:
                if event.total_tool_calls > 0:
                    self._console.print(
                        f"📋 {event.total_tool_calls} 次调用 · "
                        f"✅{event.success_count} ❌{event.failure_count} · "
                        f"⏱ {_format_elapsed(event.elapsed_seconds)}"
                    )
            elif event.event_type == EventType.USER_QUESTION:
                header = event.question_header or "待确认"
                text = event.question_text or ""
                self._console.print(f"❓ {header}: {_truncate(text, _THINKING_SUMMARY_LEN)}")
        except Exception as exc:
            # 最终兜底：即使纯文本也失败，仅记录日志，绝不崩溃
            logger.error("纯文本降级渲染也失败: %s", exc)
