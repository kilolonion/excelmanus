"""文件系统 Skill：提供当前文件夹文件列表查看工具。"""

from __future__ import annotations

import json
from pathlib import Path

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.skills import ToolDef

logger = get_logger("skills.file")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "file"
SKILL_DESCRIPTION = "文件系统工具集：查看目录内容"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。

    Args:
        workspace_root: 工作目录根路径。
    """
    global _guard
    _guard = FileAccessGuard(workspace_root)


# ── 工具函数 ──────────────────────────────────────────────


def list_directory(directory: str = ".", show_hidden: bool = False) -> str:
    """列出指定目录下的文件和子目录。

    Args:
        directory: 目标目录路径（相对于工作目录），默认为当前工作目录。
        show_hidden: 是否显示隐藏文件（以 . 开头），默认不显示。

    Returns:
        JSON 格式的目录内容列表。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(directory)

    if not safe_path.is_dir():
        return json.dumps(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            ensure_ascii=False,
        )

    entries: list[dict[str, str]] = []
    try:
        for item in sorted(safe_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # 跳过隐藏文件（除非明确要求显示）
            if not show_hidden and item.name.startswith("."):
                continue

            entry_type = "directory" if item.is_dir() else "file"
            entry: dict[str, str] = {
                "name": item.name,
                "type": entry_type,
            }

            # 文件附加大小信息
            if item.is_file():
                size = item.stat().st_size
                entry["size"] = _format_size(size)

            entries.append(entry)
    except PermissionError:
        return json.dumps(
            {"error": f"没有权限访问目录 '{directory}'"},
            ensure_ascii=False,
        )

    result = {
        "directory": directory,
        "total": len(entries),
        "entries": entries,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f}TB"


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回文件系统 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="list_directory",
            description="列出指定目录下的文件和子目录，返回名称、类型和大小信息",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "目标目录路径（相对于工作目录），默认为当前目录",
                        "default": ".",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "是否显示隐藏文件（以 . 开头），默认不显示",
                        "default": False,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=list_directory,
        ),
    ]
