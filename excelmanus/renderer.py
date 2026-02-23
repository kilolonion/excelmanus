"""流式事件渲染器 — 极简风格的统一终端渲染。

将 AgentEngine 事件渲染为极简风格输出：
- 工具调用：● tool_name(args) + └ ✓/✗ result
- 思考：dim italic 流式输出
- 子代理：树形进度 + 分隔线摘要
- 摘要：单行分隔线统计
- 审批/问题：内联式展示

配色使用 Excel 绿色系亮色主题。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.padding import Padding
from rich.text import Text

from excelmanus.cli.theme import THEME
from excelmanus.cli.utils import (
    RESULT_MAX_LEN,
    SUBAGENT_REASON_PREVIEW,
    SUBAGENT_SUMMARY_PREVIEW,
    THINKING_SUMMARY_LEN,
    THINKING_THRESHOLD,
    format_arguments,
    format_elapsed,
    format_subagent_tools,
    is_narrow_terminal,
    separator_line,
    truncate,
)
from excelmanus.events import EventType, ToolCallEvent

logger = logging.getLogger(__name__)

# 元工具：对用户隐藏内部细节，使用友好名称
_META_TOOL_DISPLAY: dict[str, str] = {
    "activate_skill": "激活技能指引",
    "delegate_to_subagent": "委派子任务",
    "list_subagents": "查询可用助手",
}

# 任务状态符号（纯文本，无 emoji）
_STATUS_SYMBOLS: dict[str, str] = {
    "pending": "○",
    "in_progress": "◐",
    "completed": THEME.SUCCESS,
    "failed": THEME.FAILURE,
}


class StreamRenderer:
    """极简风格流式事件渲染器。

    接收 ToolCallEvent 并渲染为极简风格终端输出。
    使用 ● 前缀、└/├ 树形结构、─ 分隔线，无 emoji 图标。
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._tool_start_times: dict[str, float] = {}
        self._subagent_last_tool_calls: dict[str, int] = {}
        self._streaming_text = False
        self._streaming_thinking = False
        self._text_buffer: list[str] = []

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
            EventType.SUBAGENT_ITERATION: self._render_subagent_iteration,
            EventType.SUBAGENT_END: self._render_subagent_end,
            EventType.SUBAGENT_SUMMARY: self._render_subagent_summary,
            EventType.CHAT_SUMMARY: self._render_chat_summary,
            EventType.TASK_LIST_CREATED: self._render_task_list,
            EventType.TASK_ITEM_UPDATED: self._render_task_update,
            EventType.USER_QUESTION: self._render_question,
            EventType.PENDING_APPROVAL: self._render_approval,
            EventType.APPROVAL_RESOLVED: self._render_approval_resolved,
            EventType.THINKING_DELTA: self._render_thinking_delta,
            EventType.TEXT_DELTA: self._render_text_delta,
            EventType.MODE_CHANGED: self._render_mode_changed,
        }
        handler = handlers.get(event.event_type)
        if handler:
            try:
                handler(event)
            except Exception as exc:
                logger.warning("渲染异常，降级为纯文本: %s", exc)
                self._fallback_render(event)

    def finish_streaming(self) -> None:
        """流式输出结束时调用，将缓冲文本以 Markdown 渲染输出。"""
        if self._streaming_text and self._text_buffer:
            full_text = "".join(self._text_buffer)
            self._console.print()
            # 回复文本用左缩进 Markdown 块，无 ● 前缀
            self._console.print(Padding(Markdown(full_text), (0, 2, 0, 2)))
        elif self._streaming_thinking:
            self._console.print()
        self._streaming_text = False
        self._streaming_thinking = False
        self._text_buffer.clear()

    # ------------------------------------------------------------------
    # 路由事件
    # ------------------------------------------------------------------

    def _render_route_start(self, event: ToolCallEvent) -> None:
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM}]正在匹配技能包…[/{THEME.DIM}]"
        )

    def _render_route_end(self, event: ToolCallEvent) -> None:
        if not event.skills_used:
            self._console.print(
                f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
                f" [{THEME.DIM}]路由完成[/{THEME.DIM}] {THEME.SEPARATOR}"
                f" [{THEME.GOLD}]通用模式[/{THEME.GOLD}]"
            )
            return
        skills_str = " ".join(
            f"[{THEME.BOLD} {THEME.CYAN}]{s}[/{THEME.BOLD} {THEME.CYAN}]"
            for s in event.skills_used
        )
        mode_label = event.route_mode.replace("_", " ")
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM}]路由完成[/{THEME.DIM}] {THEME.SEPARATOR}"
            f" {skills_str} [{THEME.DIM}]({mode_label})[/{THEME.DIM}]"
        )

    # ------------------------------------------------------------------
    # 迭代与思考
    # ------------------------------------------------------------------

    def _render_mode_changed(self, event: ToolCallEvent) -> None:
        """渲染模式变更提示。"""
        label_map = {
            "full_access": ("FULL ACCESS", THEME.GOLD),
            "plan_mode": ("PLAN MODE", THEME.CYAN),
        }
        label, color = label_map.get(event.mode_name, (event.mode_name, THEME.DIM))
        action = "已开启" if event.mode_enabled else "已关闭"
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{color}]{action} {label}[/{color}]"
        )

    def _render_iteration(self, event: ToolCallEvent) -> None:
        self._console.print()
        line = separator_line(50)
        self._console.print(
            f"  [{THEME.DIM}]{line}[/{THEME.DIM}]"
        )

    def _render_thinking(self, event: ToolCallEvent) -> None:
        if self._streaming_thinking:
            self._console.print()
            self._streaming_thinking = False
            return
        if not event.thinking:
            return
        summary = (
            truncate(event.thinking, THINKING_SUMMARY_LEN)
            if len(event.thinking) > THINKING_THRESHOLD
            else event.thinking
        )
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.DIM} italic]{rich_escape(summary)}[/{THEME.DIM} italic]"
        )

    def _render_thinking_delta(self, event: ToolCallEvent) -> None:
        if not event.thinking_delta:
            return
        if not self._streaming_thinking:
            self._streaming_thinking = True
            self._console.print(
                f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}] ",
                end="",
                style=f"{THEME.DIM} italic",
            )
        self._console.print(event.thinking_delta, end="", style=f"{THEME.DIM} italic")

    def _render_text_delta(self, event: ToolCallEvent) -> None:
        if not event.text_delta:
            return
        if self._streaming_thinking:
            self._console.print()
            self._streaming_thinking = False
        if not self._streaming_text:
            self._streaming_text = True
        self._text_buffer.append(event.text_delta)

    # ------------------------------------------------------------------
    # 工具调用
    # ------------------------------------------------------------------

    def _render_tool_start(self, event: ToolCallEvent) -> None:
        self._tool_start_times[event.tool_name] = time.monotonic()

        meta_name = _META_TOOL_DISPLAY.get(event.tool_name)
        if meta_name is not None:
            hint = self._meta_tool_hint(event.tool_name, event.arguments)
            line = (
                f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
                f" [{THEME.BOLD}]{rich_escape(meta_name)}[/{THEME.BOLD}]"
            )
            if hint:
                line += f" [{THEME.DIM}]{rich_escape(hint)}[/{THEME.DIM}]"
            self._console.print(line)
            return

        args_text = rich_escape(format_arguments(event.arguments))
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.BOLD} {THEME.PRIMARY}]{rich_escape(event.tool_name)}[/{THEME.BOLD} {THEME.PRIMARY}]"
            f"({args_text})"
        )

    def _render_tool_end(self, event: ToolCallEvent) -> None:
        start = self._tool_start_times.pop(event.tool_name, None)
        elapsed_str = ""
        if start is not None:
            elapsed = time.monotonic() - start
            elapsed_str = f" [{THEME.DIM}]({format_elapsed(elapsed)})[/{THEME.DIM}]"

        is_meta = event.tool_name in _META_TOOL_DISPLAY

        if event.success:
            detail = ""
            if not is_meta and event.result:
                detail = f" [{THEME.DIM}]{rich_escape(truncate(event.result, RESULT_MAX_LEN))}[/{THEME.DIM}]"
            self._console.print(
                f"  {THEME.TREE_END}"
                f" [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS}[/{THEME.PRIMARY_LIGHT}]{elapsed_str}{detail}"
            )
        else:
            error_msg = rich_escape(event.error or "未知错误")
            self._console.print(
                f"  {THEME.TREE_END}"
                f" [{THEME.RED}]{THEME.FAILURE}[/{THEME.RED}]{elapsed_str}"
                f" [{THEME.RED}]{error_msg}[/{THEME.RED}]"
            )

    # ------------------------------------------------------------------
    # 任务清单
    # ------------------------------------------------------------------

    def _render_task_list(self, event: ToolCallEvent) -> None:
        data = event.task_list_data
        if not data:
            return
        title = data.get("title", "")
        items = data.get("items", [])
        self._console.print(
            f"\n  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.BOLD}]{rich_escape(title)}[/{THEME.BOLD}]"
        )
        for i, item in enumerate(items):
            sym = _STATUS_SYMBOLS.get(item.get("status", "pending"), "○")
            self._console.print(f"  {THEME.TREE_MID} {sym} {i}. {rich_escape(item.get('title', ''))}")

    def _render_task_update(self, event: ToolCallEvent) -> None:
        idx = event.task_index
        status = event.task_status
        sym = _STATUS_SYMBOLS.get(status, "○")
        data = event.task_list_data or {}
        items = data.get("items", [])
        title = items[idx]["title"] if idx is not None and 0 <= idx < len(items) else f"#{idx}"
        self._console.print(f"  {THEME.TREE_MID} {sym} {idx}. {rich_escape(title)}")

        progress = data.get("progress", {})
        total = sum(progress.values())
        done = progress.get("completed", 0) + progress.get("failed", 0)
        if total > 0 and done == total:
            c = progress.get("completed", 0)
            f = progress.get("failed", 0)
            self._console.print(
                f"  {THEME.TREE_END} 全部完成:"
                f" [{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} {c}[/{THEME.PRIMARY_LIGHT}]"
                f" [{THEME.RED}]{THEME.FAILURE} {f}[/{THEME.RED}]"
            )

    # ------------------------------------------------------------------
    # 问题与审批
    # ------------------------------------------------------------------

    def _render_question(self, event: ToolCallEvent) -> None:
        header = (event.question_header or "").strip() or "待确认"
        text = (event.question_text or "").strip()
        options = event.question_options or []

        self._console.print()
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.BOLD}]{rich_escape(header)}[/{THEME.BOLD}]"
        )
        sep = separator_line(50)
        self._console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")

        if text:
            self._console.print(f"  {rich_escape(text)}")
            self._console.print()

        for i, option in enumerate(options, start=1):
            if not isinstance(option, dict):
                continue
            label = str(option.get("label", "")).strip()
            desc = str(option.get("description", "")).strip()
            prefix = f"  {THEME.CURSOR} " if i == 1 else "    "
            opt_text = f"{i}. {label}"
            if desc:
                opt_text += f" [{THEME.DIM}]{rich_escape(desc)}[/{THEME.DIM}]"
            self._console.print(f"{prefix}[{THEME.CYAN}]{opt_text}[/{THEME.CYAN}]")

        self._console.print()
        if event.question_multi_select:
            self._console.print(f"  [{THEME.DIM}]↑↓ 移动 · Space 选中 · Enter 提交 · Esc 取消[/{THEME.DIM}]")
        else:
            self._console.print(f"  [{THEME.DIM}]Esc to cancel · Tab to amend[/{THEME.DIM}]")

    def _render_approval(self, event: ToolCallEvent) -> None:
        tool_name = event.approval_tool_name or "未知工具"
        args = event.approval_arguments or {}
        risk_level = event.approval_risk_level or "high"

        # 风险等级颜色映射
        risk_colors = {"high": THEME.RED, "medium": "yellow", "low": "green"}
        risk_labels = {"high": "高风险", "medium": "中风险", "low": "低风险"}
        risk_color = risk_colors.get(risk_level, THEME.RED)
        risk_label = risk_labels.get(risk_level, "高风险")

        # 遍历所有参数构建摘要
        args_parts: list[str] = []
        for key, val in args.items():
            if val is None:
                continue
            display = str(val)
            if len(display) > 60:
                display = display[:57] + "..."
            args_parts.append(f"{key}={display}")
        args_text = ", ".join(args_parts) if args_parts else ""

        self._console.print()
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{risk_color}][{risk_label}][/{risk_color}]"
            f" [{THEME.BOLD}]{rich_escape(tool_name)}[/{THEME.BOLD}]"
        )
        sep = separator_line(50)
        self._console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
        if args_text:
            self._console.print(
                f"  [{THEME.DIM}]{rich_escape(args_text)}[/{THEME.DIM}]"
            )
        self._console.print(
            f"  Do you want to execute this tool?"
        )
        self._console.print(
            f"  {THEME.CURSOR} [{THEME.CYAN}]1. Yes[/{THEME.CYAN}]"
        )
        self._console.print(
            f"    [{THEME.CYAN}]2. Yes, allow all during this session[/{THEME.CYAN}]"
            f" [{THEME.DIM}](shift+tab)[/{THEME.DIM}]"
        )
        self._console.print(
            f"    [{THEME.CYAN}]3. No[/{THEME.CYAN}]"
        )
        self._console.print()
        self._console.print(
            f"  [{THEME.DIM}]Esc to cancel · Tab to amend[/{THEME.DIM}]"
        )

    def _render_approval_resolved(self, event: ToolCallEvent) -> None:
        """渲染审批已解决事件，作为工具调用链的一部分展示。"""
        tool_name = event.approval_tool_name or "未知工具"
        ok = event.success
        icon = THEME.SUCCESS if ok else THEME.FAILURE
        status = "已执行" if ok else "已拒绝"
        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" {icon} [{THEME.BOLD}]{rich_escape(tool_name)}[/{THEME.BOLD}]"
            f" [{THEME.DIM}]{status}[/{THEME.DIM}]"
        )
        result_text = (event.result or "").strip()
        if result_text:
            preview = result_text[:200] + ("…" if len(result_text) > 200 else "")
            self._console.print(
                f"    [{THEME.DIM}]{rich_escape(preview)}[/{THEME.DIM}]"
            )

    # ------------------------------------------------------------------
    # 子代理
    # ------------------------------------------------------------------

    def _render_subagent_start(self, event: ToolCallEvent) -> None:
        name_raw = (event.subagent_name or "subagent").strip() or "subagent"
        name = rich_escape(name_raw)
        reason_text = (event.subagent_reason or "触发子代理").strip() or "触发子代理"
        reason = rich_escape(truncate(reason_text, SUBAGENT_REASON_PREVIEW))
        tools_raw = event.subagent_tools or []
        tools = rich_escape(format_subagent_tools(tools_raw))
        key = (event.subagent_conversation_id or "").strip() or name_raw
        self._subagent_last_tool_calls[key] = 0

        self._console.print(
            f"  [{THEME.PRIMARY_LIGHT}]{THEME.AGENT_PREFIX}[/{THEME.PRIMARY_LIGHT}]"
            f" [{THEME.BOLD}]委派子任务[/{THEME.BOLD}]"
            f" [{THEME.DIM}]{THEME.SEPARATOR}[/{THEME.DIM}]"
            f" [{THEME.CYAN}]{name}[/{THEME.CYAN}]"
        )
        self._console.print(f"  {THEME.TREE_MID} [{THEME.DIM}]{reason}[/{THEME.DIM}]")
        self._console.print(f"  {THEME.TREE_END} [{THEME.DIM}]工具({len(tools_raw)}): {tools}[/{THEME.DIM}]")

    def _render_subagent_iteration(self, event: ToolCallEvent) -> None:
        turn = event.subagent_iterations or event.iteration or 0
        calls = event.subagent_tool_calls or 0
        name_raw = (event.subagent_name or "subagent").strip() or "subagent"
        key = (event.subagent_conversation_id or "").strip() or name_raw
        last_calls = self._subagent_last_tool_calls.get(key, 0)
        delta = calls - last_calls if calls >= last_calls else calls
        self._subagent_last_tool_calls[key] = calls

        delta_str = f" (+{delta})" if delta > 0 else ""
        self._console.print(
            f"  {THEME.TREE_MID} [{THEME.DIM}]轮次 {turn} · 工具调用 {calls} 次{delta_str}[/{THEME.DIM}]"
        )

    def _render_subagent_summary(self, event: ToolCallEvent) -> None:
        summary = (event.subagent_summary or "").strip()
        if not summary:
            return
        preview = rich_escape(truncate(summary, SUBAGENT_SUMMARY_PREVIEW))
        name = rich_escape((event.subagent_name or "subagent").strip() or "subagent")
        sep = separator_line(40)

        self._console.print()
        self._console.print(f"  [{THEME.DIM}]{THEME.SEPARATOR}{THEME.SEPARATOR} 子代理摘要 · {name} {sep}[/{THEME.DIM}]")
        self._console.print(f"  {preview}")
        self._console.print(f"  [{THEME.DIM}]{separator_line(50)}[/{THEME.DIM}]")

    def _render_subagent_end(self, event: ToolCallEvent) -> None:
        name_raw = (event.subagent_name or "subagent").strip() or "subagent"
        turns = event.subagent_iterations or 0
        calls = event.subagent_tool_calls or 0
        key = (event.subagent_conversation_id or "").strip() or name_raw
        self._subagent_last_tool_calls.pop(key, None)

        if event.subagent_success:
            status_str = f"[{THEME.PRIMARY_LIGHT}]{THEME.SUCCESS} 完成[/{THEME.PRIMARY_LIGHT}]"
        else:
            status_str = f"[{THEME.RED}]{THEME.FAILURE} 失败[/{THEME.RED}]"

        stats = f" [{THEME.DIM}]共 {turns} 轮, {calls} 次工具调用[/{THEME.DIM}]" if turns else ""
        self._console.print(
            f"  {THEME.TREE_END} {status_str}{stats}"
        )

    # ------------------------------------------------------------------
    # 执行摘要
    # ------------------------------------------------------------------

    def _render_chat_summary(self, event: ToolCallEvent) -> None:
        if event.total_tool_calls == 0:
            token_str = self._format_token_usage(event)
            if token_str:
                self._console.print()
                self._console.print(f"  [{THEME.DIM}]{token_str}[/{THEME.DIM}]")
            return

        elapsed_str = format_elapsed(event.elapsed_seconds)
        token_str = self._format_token_usage(event)

        sep = separator_line(50)
        parts = [
            f"{event.total_tool_calls} 次工具调用",
            f"{THEME.SUCCESS} {event.success_count} 成功",
            f"{THEME.FAILURE} {event.failure_count} 失败",
            elapsed_str,
        ]
        if token_str:
            parts.append(token_str)
        summary = " · ".join(parts)

        self._console.print()
        self._console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")
        self._console.print(f"  [{THEME.DIM}]{summary}[/{THEME.DIM}]")
        self._console.print(f"  [{THEME.DIM}]{sep}[/{THEME.DIM}]")

    @staticmethod
    def _format_token_usage(event: ToolCallEvent) -> str:
        if event.total_tokens <= 0:
            return ""
        return f"{event.prompt_tokens:,} + {event.completion_tokens:,} = {event.total_tokens:,} tokens"

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _meta_tool_hint(tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name == "activate_skill":
            reason = arguments.get("reason", "")
            return reason.strip() if isinstance(reason, str) and reason.strip() else ""
        if tool_name == "delegate_to_subagent":
            task = arguments.get("task", "")
            return truncate(task.strip(), 60) if isinstance(task, str) and task.strip() else ""
        return ""

    def _is_narrow(self) -> bool:
        return is_narrow_terminal(self._console)

    def _fallback_render(self, event: ToolCallEvent) -> None:
        try:
            P = THEME.AGENT_PREFIX
            if event.event_type == EventType.TOOL_CALL_START:
                self._console.print(f"  {P} {event.tool_name}({format_arguments(event.arguments)})")
            elif event.event_type == EventType.TOOL_CALL_END:
                sym = THEME.SUCCESS if event.success else THEME.FAILURE
                detail = event.result if event.success else (event.error or "")
                self._console.print(f"  {THEME.TREE_END} {sym} {truncate(detail, RESULT_MAX_LEN)}")
            elif event.event_type == EventType.THINKING:
                if event.thinking:
                    self._console.print(f"  {P} {truncate(event.thinking, THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.ITERATION_START:
                self._console.print(f"  {separator_line(30)}")
            elif event.event_type == EventType.ROUTE_END:
                skills = ", ".join(event.skills_used) if event.skills_used else "通用"
                self._console.print(f"  {P} 路由: {skills}")
            elif event.event_type == EventType.SUBAGENT_START:
                name = event.subagent_name or "subagent"
                reason = event.subagent_reason or "触发子代理"
                self._console.print(f"  {P} 委派子任务 → {name}: {truncate(reason, THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.SUBAGENT_SUMMARY:
                summary = event.subagent_summary or ""
                if summary:
                    name = event.subagent_name or "subagent"
                    self._console.print(f"  {P} 子代理摘要 · {name}: {truncate(summary, THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.SUBAGENT_END:
                name = event.subagent_name or "subagent"
                status = "完成" if event.subagent_success else "失败"
                self._console.print(f"  {THEME.TREE_END} {status}")
            elif event.event_type == EventType.CHAT_SUMMARY:
                if event.total_tool_calls > 0:
                    self._console.print(
                        f"  {event.total_tool_calls} 次调用 · "
                        f"{THEME.SUCCESS}{event.success_count} {THEME.FAILURE}{event.failure_count} · "
                        f"{format_elapsed(event.elapsed_seconds)}"
                    )
            elif event.event_type == EventType.USER_QUESTION:
                header = event.question_header or "待确认"
                text = event.question_text or ""
                self._console.print(f"  {P} {header}: {truncate(text, THINKING_SUMMARY_LEN)}")
        except Exception as exc:
            logger.error("纯文本降级渲染也失败: %s", exc)
