"""CLI 异常分级与恢复建议面板。

将运行时异常分为四类，并为每类提供结构化恢复建议，
最终渲染为 Rich Panel 而非裸字符串。
"""

from __future__ import annotations

import logging
from enum import Enum

from rich.console import Console
from rich.markup import escape as rich_escape
from rich.panel import Panel

logger = logging.getLogger(__name__)


class CliErrorCategory(Enum):
    """CLI 异常分类。"""

    CONFIG = "config"
    NETWORK = "network"
    ENGINE = "engine"
    UNKNOWN = "unknown"


_CATEGORY_TITLES: dict[CliErrorCategory, str] = {
    CliErrorCategory.CONFIG: "⚙️  配置错误",
    CliErrorCategory.NETWORK: "🌐 网络错误",
    CliErrorCategory.ENGINE: "🔧 引擎错误",
    CliErrorCategory.UNKNOWN: "❓ 未知错误",
}

_CATEGORY_STYLES: dict[CliErrorCategory, str] = {
    CliErrorCategory.CONFIG: "#f0c674",
    CliErrorCategory.NETWORK: "#de935f",
    CliErrorCategory.ENGINE: "#cc6666",
    CliErrorCategory.UNKNOWN: "#cc6666",
}

_RECOVERY_HINTS: dict[CliErrorCategory, list[str]] = {
    CliErrorCategory.CONFIG: [
        "使用 /config list 检查环境变量配置",
        "确认 .env 文件中 API Key 和 Base URL 是否正确",
        "参考 /help 查看配置说明",
    ],
    CliErrorCategory.NETWORK: [
        "检查网络连接后重试",
        "使用 /config get EXCELMANUS_BASE_URL 确认 API 地址",
        "如使用代理，确认 HTTP_PROXY / HTTPS_PROXY 设置",
    ],
    CliErrorCategory.ENGINE: [
        "使用 /clear 清除对话历史后重试",
        "使用 /model 切换到其他模型",
        "使用 /save 保存当前对话以便反馈",
    ],
    CliErrorCategory.UNKNOWN: [
        "使用 /help 查看可用命令",
        "使用 /save 保存对话记录后联系开发者",
        "使用 /clear 重置会话后重试",
    ],
}


def classify_error(exc: BaseException) -> CliErrorCategory:
    """将异常分类为 CliErrorCategory。"""
    # 延迟导入避免循环依赖
    try:
        from excelmanus.config import ConfigError
        if isinstance(exc, ConfigError):
            return CliErrorCategory.CONFIG
    except ImportError:
        pass

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return CliErrorCategory.NETWORK

    if isinstance(exc, (ValueError, RuntimeError, TypeError, AttributeError)):
        return CliErrorCategory.ENGINE

    return CliErrorCategory.UNKNOWN


def recovery_hints(category: CliErrorCategory) -> list[str]:
    """返回对应分类的恢复建议列表。"""
    return list(_RECOVERY_HINTS.get(category, _RECOVERY_HINTS[CliErrorCategory.UNKNOWN]))


def render_error_panel(
    console: Console,
    *,
    error: BaseException,
    error_label: str = "操作",
) -> None:
    """渲染结构化错误面板，包含分类标题、错误信息和恢复建议。"""
    try:
        category = classify_error(error)
        title = _CATEGORY_TITLES.get(category, "❓ 错误")
        style = _CATEGORY_STYLES.get(category, "#cc6666")
        hints = recovery_hints(category)

        error_msg = str(error).strip() or "(无详细信息)"
        label = rich_escape(error_label)
        msg = rich_escape(error_msg)

        lines: list[str] = [
            f"[bold red]{label}时发生错误[/bold red]",
            "",
            f"  {msg}",
        ]

        if hints:
            lines.append("")
            lines.append("[dim white]恢复建议：[/dim white]")
            for hint in hints:
                lines.append(f"  [dim white]• {rich_escape(hint)}[/dim white]")

        content = "\n".join(lines)

        console.print()
        console.print(
            Panel(
                content,
                title=f"[bold {style}]{title}[/bold {style}]",
                title_align="left",
                border_style=style,
                expand=False,
                padding=(1, 2),
            )
        )
    except Exception as render_exc:
        # 二次异常降级为纯文本
        logger.warning("错误面板渲染失败: %s", render_exc)
        try:
            console.print(f"  [red]✗ {error_label}时发生错误：{error}[/red]")
        except Exception:
            pass
