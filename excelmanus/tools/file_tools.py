"""文件工具：提供工作区文件管理能力（查看、搜索、读取、复制、重命名、删除）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.file")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "file"
SKILL_DESCRIPTION = "文件系统工具集：查看、搜索、读取、复制、重命名、删除"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None
_MAX_LIST_PAGE_SIZE = 500


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


def _validate_pagination(offset: int, limit: int, *, max_limit: int = _MAX_LIST_PAGE_SIZE) -> str | None:
    """校验分页参数，返回错误信息或 None。"""
    if offset < 0:
        return "offset 必须大于或等于 0"
    if limit <= 0:
        return "limit 必须为正整数"
    if limit > max_limit:
        return f"limit 不能超过 {max_limit}"
    return None


# ── 工具函数 ──────────────────────────────────────────────



def list_directory(
    directory: str = ".",
    show_hidden: bool = False,
    depth: int = 2,
    offset: int = 0,
    limit: int = 100,
) -> str:
    """列出指定目录下的文件和子目录。

    Args:
        directory: 目标目录路径（相对于工作目录），默认为当前工作目录。
        show_hidden: 是否显示隐藏文件（以 . 开头），默认不显示。
        depth: 递归深度。0 = 仅当前层（扁平分页模式），1 = 含直接子目录内容，
               2 = 默认两层，-1 = 无限递归。默认 2。
        offset: 分页起始偏移（仅 depth=0 时生效），默认 0。
        limit: 分页大小（仅 depth=0 时生效），默认 100，最大 500。

    Returns:
        JSON 格式的目录内容（depth=0 为扁平列表，depth>=1 为嵌套树）。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(directory)

    if not safe_path.is_dir():
        return json.dumps(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            ensure_ascii=False,
        )

    # depth=0：保持原有扁平分页行为
    if depth == 0:
        return _list_directory_flat(directory, safe_path, show_hidden, offset, limit)

    # depth>=1 或 -1：递归树模式
    tree = _build_tree(safe_path, show_hidden, depth)
    result = {
        "directory": directory,
        "absolute_path": str(safe_path),
        "depth": depth,
        "tree": tree,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _list_directory_flat(
    directory: str,
    safe_path: Path,
    show_hidden: bool,
    offset: int,
    limit: int,
) -> str:
    """扁平分页模式（depth=0 时的原有行为）。"""
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return json.dumps({"error": paging_error}, ensure_ascii=False)

    entries: list[dict[str, str]] = []
    try:
        for item in sorted(safe_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if not show_hidden and item.name.startswith("."):
                continue
            entry_type = "directory" if item.is_dir() else "file"
            entry: dict[str, str] = {"name": item.name, "type": entry_type}
            if item.is_file():
                entry["size"] = _format_size(item.stat().st_size)
            entries.append(entry)
    except PermissionError:
        return json.dumps(
            {"error": f"没有权限访问目录 '{directory}'"}, ensure_ascii=False
        )

    total = len(entries)
    end = offset + limit
    paged_entries = entries[offset:end]
    return json.dumps(
        {
            "directory": directory,
            "absolute_path": str(safe_path),
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(paged_entries),
            "has_more": end < total,
            "entries": paged_entries,
        },
        ensure_ascii=False,
        indent=2,
    )


def _build_tree(
    dir_path: Path,
    show_hidden: bool,
    remaining_depth: int,
) -> list[dict[str, Any]]:
    """递归构建目录树。

    Args:
        dir_path: 当前目录的绝对路径。
        show_hidden: 是否包含隐藏条目。
        remaining_depth: 剩余递归层数，-1 表示无限。

    Returns:
        嵌套的条目列表，目录条目含 ``children`` 字段。
    """
    entries: list[dict[str, Any]] = []
    try:
        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return entries

    for item in items:
        if not show_hidden and item.name.startswith("."):
            continue

        if item.is_dir():
            entry: dict[str, Any] = {"name": item.name, "type": "directory"}
            # remaining_depth: -1 无限，>1 继续递归，==1 不再展开
            if remaining_depth == -1 or remaining_depth > 1:
                next_depth = -1 if remaining_depth == -1 else remaining_depth - 1
                entry["children"] = _build_tree(item, show_hidden, next_depth)
            entries.append(entry)
        elif item.is_file():
            entries.append({
                "name": item.name,
                "type": "file",
                "size": _format_size(item.stat().st_size),
            })

    return entries




def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f}TB"


def get_file_info(file_path: str) -> str:
    """获取文件的详细信息。

    Args:
        file_path: 文件路径（相对于工作目录）。

    Returns:
        JSON 格式的文件详情。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.exists():
        return json.dumps({"error": f"路径 '{file_path}' 不存在"}, ensure_ascii=False)

    stat = safe_path.stat()
    info: dict[str, Any] = {
        "name": safe_path.name,
        "path": file_path,
        "absolute_path": str(safe_path),
        "type": "directory" if safe_path.is_dir() else "file",
        "size": _format_size(stat.st_size),
        "size_bytes": stat.st_size,
        "extension": safe_path.suffix.lstrip(".") if safe_path.is_file() else None,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
    }

    # 目录额外信息：子项数量
    if safe_path.is_dir():
        try:
            children = list(safe_path.iterdir())
            info["children_count"] = len(children)
        except PermissionError:
            info["children_count"] = "无权限"

    return json.dumps(info, ensure_ascii=False, indent=2)


def find_files(pattern: str = "*", directory: str = ".", max_results: int = 50) -> str:
    """按 glob 模式搜索工作区内的文件。

    Args:
        pattern: glob 搜索模式，如 '*.xlsx'、'**/*.csv'。
        directory: 搜索起始目录（相对于工作目录），默认当前目录。
        max_results: 最大返回结果数，默认 50。

    Returns:
        JSON 格式的搜索结果列表。
    """
    guard = _get_guard()
    safe_dir = guard.resolve_and_validate(directory)

    if not safe_dir.is_dir():
        return json.dumps(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            ensure_ascii=False,
        )

    matches: list[dict[str, str]] = []
    try:
        for item in safe_dir.glob(pattern):
            # 跳过隐藏文件/目录
            if any(part.startswith(".") for part in item.relative_to(safe_dir).parts):
                continue
            # 安全校验：确保结果仍在工作区内
            try:
                guard.resolve_and_validate(str(item))
            except Exception:
                continue

            entry: dict[str, str] = {
                "name": item.name,
                "path": str(item.relative_to(guard.workspace_root)),
                "absolute_path": str(item),
                "type": "directory" if item.is_dir() else "file",
            }
            if item.is_file():
                entry["size"] = _format_size(item.stat().st_size)
            matches.append(entry)

            if len(matches) >= max_results:
                break
    except PermissionError:
        return json.dumps(
            {"error": f"没有权限访问目录 '{directory}'"},
            ensure_ascii=False,
        )

    result = {
        "pattern": pattern,
        "directory": directory,
        "total": len(matches),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def read_text_file(
    file_path: str, encoding: str = "utf-8", max_lines: int = 200
) -> str:
    """读取文本文件内容（CSV、TXT 等）。

    Args:
        file_path: 文件路径（相对于工作目录）。
        encoding: 文件编码，默认 utf-8。
        max_lines: 最大读取行数，默认 200。

    Returns:
        JSON 格式的文件内容。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.is_file():
        return json.dumps(
            {"error": f"路径 '{file_path}' 不是一个有效的文件"},
            ensure_ascii=False,
        )

    try:
        with open(safe_path, "r", encoding=encoding) as f:
            lines = []
            truncated = False
            for i, line in enumerate(f):
                if i < max_lines:
                    lines.append(line.rstrip("\n"))
                else:
                    truncated = True
                    break
    except UnicodeDecodeError:
        return json.dumps(
            {"error": f"无法以 {encoding} 编码读取文件 '{file_path}'，可能是二进制文件"},
            ensure_ascii=False,
        )

    total_lines = len(lines)
    result = {
        "file": safe_path.name,
        "encoding": encoding,
        "lines_read": total_lines,
        "truncated": truncated,
        "content": "\n".join(lines),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def copy_file(source: str, destination: str) -> str:
    """复制文件到工作区内的新位置。

    Args:
        source: 源文件路径（相对于工作目录）。
        destination: 目标路径（相对于工作目录）。

    Returns:
        操作结果描述。
    """
    guard = _get_guard()
    src_path = guard.resolve_and_validate(source)
    dst_path = guard.resolve_and_validate(destination)

    if not src_path.is_file():
        return json.dumps(
            {"error": f"源路径 '{source}' 不是一个有效的文件"},
            ensure_ascii=False,
        )

    if dst_path.exists():
        return json.dumps(
            {"error": f"目标路径 '{destination}' 已存在，拒绝覆盖"},
            ensure_ascii=False,
        )

    # 确保目标目录存在
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, dst_path)

    return json.dumps(
        {
            "status": "success",
            "source": source,
            "destination": destination,
            "size": _format_size(dst_path.stat().st_size),
        },
        ensure_ascii=False,
        indent=2,
    )


def rename_file(source: str, destination: str) -> str:
    """重命名或移动文件（工作区内）。

    Args:
        source: 源文件路径（相对于工作目录）。
        destination: 目标路径（相对于工作目录）。

    Returns:
        操作结果描述。
    """
    guard = _get_guard()
    src_path = guard.resolve_and_validate(source)
    dst_path = guard.resolve_and_validate(destination)

    if not src_path.is_file():
        return json.dumps(
            {"error": f"源路径 '{source}' 不是一个有效的文件"},
            ensure_ascii=False,
        )

    if dst_path.exists():
        return json.dumps(
            {"error": f"目标路径 '{destination}' 已存在，拒绝覆盖"},
            ensure_ascii=False,
        )

    # 确保目标目录存在
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.rename(dst_path)

    return json.dumps(
        {
            "status": "success",
            "source": source,
            "destination": destination,
        },
        ensure_ascii=False,
        indent=2,
    )


def delete_file(file_path: str, confirm: bool = False) -> str:
    """安全删除文件（仅限文件，不删除目录）。

    Args:
        file_path: 要删除的文件路径（相对于工作目录）。
        confirm: 是否确认删除，必须为 True 才执行删除。

    Returns:
        操作结果描述。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.exists():
        return json.dumps(
            {"error": f"路径 '{file_path}' 不存在"},
            ensure_ascii=False,
        )

    if safe_path.is_dir():
        return json.dumps(
            {"error": f"路径 '{file_path}' 是目录，delete_file 仅允许删除文件"},
            ensure_ascii=False,
        )

    if not confirm:
        # 返回待删除文件信息，供 LLM 二次确认
        stat = safe_path.stat()
        return json.dumps(
            {
                "status": "pending_confirmation",
                "message": "请将 confirm 设为 true 以确认删除",
                "file": file_path,
                "size": _format_size(stat.st_size),
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )

    size = _format_size(safe_path.stat().st_size)
    safe_path.unlink()

    return json.dumps(
        {
            "status": "success",
            "deleted": file_path,
            "size": size,
        },
        ensure_ascii=False,
        indent=2,
    )


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回文件系统 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="list_directory",
            description="列出指定目录下的文件和子目录。默认递归展开两层返回完整目录树结构；depth=0 时退化为扁平分页列表",
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
                    "depth": {
                        "type": "integer",
                        "description": "递归深度。0=仅当前层（扁平分页），1=含直接子目录，2=默认两层，-1=无限递归",
                        "default": 2,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "分页起始偏移（仅 depth=0 时生效），默认 0",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "分页大小（仅 depth=0 时生效），默认 100，最大 500",
                        "default": 100,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=list_directory,
            max_result_chars=0,
        ),
        # get_file_info, find_files, read_text_file: Batch 5 精简，由 run_code/run_shell 替代
        ToolDef(
            name="copy_file",
            description="复制文件到工作区内的新位置（不覆盖已有文件）",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "源文件路径（相对于工作目录）",
                    },
                    "destination": {
                        "type": "string",
                        "description": "目标路径（相对于工作目录）",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
            func=copy_file,
        ),
        ToolDef(
            name="rename_file",
            description="重命名或移动文件到工作区内的新位置（不覆盖已有文件）",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "源文件路径（相对于工作目录）",
                    },
                    "destination": {
                        "type": "string",
                        "description": "目标路径（相对于工作目录）",
                    },
                },
                "required": ["source", "destination"],
                "additionalProperties": False,
            },
            func=rename_file,
        ),
        ToolDef(
            name="delete_file",
            description="安全删除文件（仅限文件，不删目录）。首次调用返回文件信息，需 confirm=true 二次确认才执行删除",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要删除的文件路径（相对于工作目录）",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "是否确认删除，必须为 true 才执行",
                        "default": False,
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=delete_file,
        ),
    ]
