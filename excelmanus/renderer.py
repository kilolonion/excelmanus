"""流式事件渲染器 — 将 AgentEngine 事件渲染为 Rich 终端组件。

负责将工具调用、思考过程等事件实时渲染为可视化卡片和折叠块，
支持窄终端自适应和渲染异常降级。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from excelmanus.events import EventType, ToolCallEvent

logger = logging.getLogger(__name__)

# 截断阈值常量
_RESULT_MAX_LEN = 200
_THINKING_THRESHOLD = 500
_THINKING_SUMMARY_LEN = 80
_NARROW_TERMINAL_WIDTH = 60


def _truncate(text: str, max_len: int) -> str:
    """截断文本，超过 max_len 时追加省略标记。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _format_arguments(arguments: Dict[str, Any]) -> str:
    """将参数字典格式化为可读字符串。"""
    if not arguments:
        return "无参数"
    parts = []
    for key, value in arguments.items():
        if isinstance(value, str):
            parts.append(f'{key}="{value}"')
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


class StreamRenderer:
    """流式事件渲染器。

    接收 ToolCallEvent 并渲染为 Rich 终端组件。
    支持窄终端自适应和渲染异常降级为纯文本。
    """

    def __init__(self, console: Console) -> None:
        self._console = console

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
    # 内部渲染方法
    # ------------------------------------------------------------------

    def _is_narrow(self) -> bool:
        """判断终端是否为窄终端（宽度 < 60）。"""
        return self._console.width < _NARROW_TERMINAL_WIDTH

    def _render_tool_start(self, event: ToolCallEvent) -> None:
        """渲染工具调用开始卡片。

        宽终端：Rich Panel 包含工具名称和参数摘要。
        窄终端：简化为无边框纯文本。
        """
        args_text = _format_arguments(event.arguments)
        # 高亮文件路径参数
        args_display = self._highlight_paths(args_text)

        if self._is_narrow():
            # 窄终端：简化输出，无边框
            self._console.print(f"🔧 {event.tool_name}")
            self._console.print(f"  参数: {args_display}")
        else:
            content = Text()
            content.append("参数: ")
            content.append(args_display)

            panel = Panel(
                content,
                title=f"🔧 {event.tool_name}",
                title_align="left",
                border_style="blue",
                expand=False,
            )
            self._console.print(panel)

    def _render_tool_end(self, event: ToolCallEvent) -> None:
        """渲染工具调用结束卡片（成功/失败）。

        成功：✅ 绿色标记 + 结果摘要（超 200 字符截断）。
        失败：❌ 红色标记 + 错误信息。
        """
        if event.success:
            status_icon = "✅"
            status_text = "成功"
            status_style = "green"
            detail = _truncate(event.result, _RESULT_MAX_LEN) if event.result else ""
            detail_label = "结果"
        else:
            status_icon = "❌"
            status_text = "失败"
            status_style = "red"
            detail = event.error or "未知错误"
            detail_label = "错误"

        if self._is_narrow():
            # 窄终端：简化输出
            self._console.print(f"  状态: {status_icon} {status_text}")
            if detail:
                self._console.print(f"  {detail_label}: {detail}")
        else:
            content = Text()
            content.append(f"状态: {status_icon} ", style="bold")
            content.append(status_text, style=status_style)
            if detail:
                content.append(f"\n{detail_label}: {detail}")

            panel = Panel(
                content,
                title=f"🔧 {event.tool_name}",
                title_align="left",
                border_style=status_style,
                expand=False,
            )
            self._console.print(panel)

    def _render_thinking(self, event: ToolCallEvent) -> None:
        """渲染 LLM 思考过程折叠块。

        空思考内容跳过渲染。
        超过 500 字符时摘要截断到 80 字符 + 省略标记。
        """
        if not event.thinking:
            return

        summary = _truncate(event.thinking, _THINKING_SUMMARY_LEN) if len(
            event.thinking
        ) > _THINKING_THRESHOLD else event.thinking

        if self._is_narrow():
            self._console.print(f"💭 {summary}")
        else:
            self._console.print(f"💭 思考: {summary}", style="dim")

    def _render_iteration(self, event: ToolCallEvent) -> None:
        """渲染迭代轮次标题。"""
        if self._is_narrow():
            self._console.print(f"── 轮次 {event.iteration} ──")
        else:
            self._console.rule(f"轮次 {event.iteration}", style="cyan")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _highlight_paths(self, text: str) -> str:
        """高亮文本中的文件路径（简单实现：识别常见文件扩展名）。

        当前实现返回原始文本，由 Rich 的 markup 或 Text 对象
        在终端中自然呈现。后续可扩展为 Rich Text 高亮。
        """
        # 文件路径在 Rich 输出中通过引号包裹已有足够辨识度
        return text

    def _fallback_render(self, event: ToolCallEvent) -> None:
        """渲染异常时的纯文本降级输出。"""
        try:
            if event.event_type == EventType.TOOL_CALL_START:
                self._console.print(f"🔧 {event.tool_name} ({_format_arguments(event.arguments)})")
            elif event.event_type == EventType.TOOL_CALL_END:
                icon = "✅" if event.success else "❌"
                detail = event.result if event.success else (event.error or "")
                self._console.print(f"  {icon} {_truncate(detail, _RESULT_MAX_LEN)}")
            elif event.event_type == EventType.THINKING:
                if event.thinking:
                    self._console.print(f"💭 {_truncate(event.thinking, _THINKING_SUMMARY_LEN)}")
            elif event.event_type == EventType.ITERATION_START:
                self._console.print(f"── 轮次 {event.iteration} ──")
        except Exception as exc:
            # 最终兜底：即使纯文本也失败，仅记录日志，绝不崩溃
            logger.error("纯文本降级渲染也失败: %s", exc)
