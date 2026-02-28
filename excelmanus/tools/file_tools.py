"""文件工具：提供工作区文件管理能力（查看、搜索、读取、复制、重命名、删除）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.file")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "file"
SKILL_DESCRIPTION = "文件系统工具集：查看、搜索、读取、复制、重命名、删除"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None
_MAX_LIST_PAGE_SIZE = 500
_MAX_TREE_NODES = 2000
_MAX_OVERVIEW_HOTSPOTS = 10
_MAX_OVERVIEW_EXTENSIONS = 20
_DEFAULT_EXCLUDE_PATTERNS = (
    ".git",
    ".venv",
    "node_modules",
    ".worktrees",
    "outputs",
    "dist",
    "build",
    "__pycache__",
)


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
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


def _new_omitted_stats() -> dict[str, int]:
    return {
        "hidden": 0,
        "ignored_by_pattern": 0,
        "permission_denied": 0,
    }


def _resolve_mode(mode: str, depth: int) -> str | None:
    normalized = (mode or "auto").strip().lower()
    if normalized not in {"auto", "flat", "tree", "overview"}:
        return None
    if normalized == "auto":
        return "flat" if depth == 0 else "tree"
    return normalized


def _resolve_offset(offset: int, cursor: str | None) -> tuple[int, str | None]:
    if cursor is None or not str(cursor).strip():
        return offset, None

    raw = str(cursor).strip()
    if not raw.isdigit():
        return offset, "cursor 必须是非负整数字符串"
    return int(raw), None


def _normalize_exclude_patterns(
    exclude: list[str] | None,
    *,
    use_default_excludes: bool,
) -> list[str]:
    patterns: list[str] = []
    if use_default_excludes:
        patterns.extend(_DEFAULT_EXCLUDE_PATTERNS)

    if exclude:
        for pattern in exclude:
            cleaned = str(pattern or "").strip()
            if cleaned:
                patterns.append(cleaned)

    deduped: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if pattern in seen:
            continue
        seen.add(pattern)
        deduped.append(pattern)
    return deduped


def _matches_exclude_pattern(relative_path: Path, patterns: list[str]) -> bool:
    if not patterns:
        return False

    rel = relative_path.as_posix().lstrip("./")
    name = relative_path.name

    for pattern in patterns:
        normalized = pattern.replace("\\", "/").strip().lstrip("./")
        if not normalized:
            continue
        if normalized.endswith("/**"):
            prefix = normalized[:-3].rstrip("/")
            if rel == prefix or rel.startswith(f"{prefix}/"):
                return True
            continue
        if "/" in normalized:
            if fnmatch(rel, normalized) or rel == normalized or rel.startswith(f"{normalized}/"):
                return True
            continue
        if name == normalized or fnmatch(name, normalized):
            return True
        if fnmatch(rel, normalized) or rel.startswith(f"{normalized}/"):
            return True
    return False



def list_directory(
    directory: str = ".",
    show_hidden: bool = False,
    depth: int = 2,
    offset: int = 0,
    limit: int = 100,
    mode: str = "auto",
    cursor: str | None = None,
    exclude: list[str] | None = None,
    use_default_excludes: bool = True,
    max_nodes: int = _MAX_TREE_NODES,
) -> str:
    """列出指定目录下的文件和子目录。

    Args:
        directory: 目标目录路径（相对于工作目录），默认为当前工作目录。
        show_hidden: 是否显示隐藏文件（以 . 开头），默认不显示。
        depth: 递归深度。0 = 仅当前层（扁平分页模式），1 = 含直接子目录内容，
               2 = 默认两层，-1 = 无限递归。默认 2。
        offset: 分页起始偏移（flat/overview 模式生效），默认 0。
        limit: 分页大小（flat/overview 模式生效），默认 100，最大 500。
        mode: 扫描模式。auto=depth 推断，flat=扁平分页，tree=递归树，overview=摘要模式。
        cursor: 游标分页（数字字符串），提供后会覆盖 offset。
        exclude: 额外排除规则（支持目录名或 glob）。
        use_default_excludes: 是否启用默认噪音目录排除规则。
        max_nodes: tree 模式最大节点数，超过后截断。

    Returns:
        JSON 格式的目录内容。
    """
    effective_mode = _resolve_mode(mode, depth)
    if effective_mode is None:
        return json.dumps(
            {"error": "mode 仅支持 auto、flat、tree、overview"},
            ensure_ascii=False,
        )

    effective_offset, cursor_error = _resolve_offset(offset, cursor)
    if cursor_error is not None:
        return json.dumps({"error": cursor_error}, ensure_ascii=False)

    exclude_patterns = _normalize_exclude_patterns(
        exclude,
        use_default_excludes=use_default_excludes,
    )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(directory)

    if not safe_path.is_dir():
        return json.dumps(
            {"error": f"路径 '{directory}' 不是一个有效的目录"},
            ensure_ascii=False,
        )

    if effective_mode == "flat":
        return _list_directory_flat(
            directory=directory,
            safe_path=safe_path,
            show_hidden=show_hidden,
            offset=effective_offset,
            limit=limit,
            exclude_patterns=exclude_patterns,
        )

    if effective_mode == "overview":
        return _list_directory_overview(
            directory=directory,
            safe_path=safe_path,
            show_hidden=show_hidden,
            offset=effective_offset,
            limit=limit,
            exclude_patterns=exclude_patterns,
        )

    if max_nodes <= 0:
        return json.dumps({"error": "max_nodes 必须为正整数"}, ensure_ascii=False)

    paging_error = _validate_pagination(effective_offset, limit)
    if paging_error is not None:
        return json.dumps({"error": paging_error}, ensure_ascii=False)

    stats: dict[str, Any] = {
        "scanned_count": 0,
        "returned_nodes": 0,
        "truncated": False,
        "omitted": _new_omitted_stats(),
    }
    tree = _build_tree(
        dir_path=safe_path,
        root_path=safe_path,
        show_hidden=show_hidden,
        remaining_depth=depth,
        exclude_patterns=exclude_patterns,
        stats=stats,
        max_nodes=max_nodes,
    )
    entries = [
        {k: v for k, v in item.items() if k != "children"}
        for item in tree
    ]
    end = effective_offset + limit
    paged_entries = entries[effective_offset:end]
    has_more = end < len(entries)
    result = {
        "directory": directory,
        "absolute_path": str(safe_path),
        "mode": "tree",
        "depth": depth,
        "tree": tree,
        "entries": paged_entries,
        "total": len(entries),
        "offset": effective_offset,
        "limit": limit,
        "returned": len(paged_entries),
        "has_more": has_more,
        "next_cursor": str(end) if has_more else None,
        "truncated": bool(stats["truncated"]),
        "omitted": stats["omitted"],
        "summary": {
            "scanned_count": int(stats["scanned_count"]),
            "returned_nodes": int(stats["returned_nodes"]),
        },
        "exclude_patterns": exclude_patterns,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _list_directory_flat(
    directory: str,
    safe_path: Path,
    show_hidden: bool,
    offset: int,
    limit: int,
    exclude_patterns: list[str],
) -> str:
    """扁平分页模式（depth=0 时的原有行为）。"""
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return json.dumps({"error": paging_error}, ensure_ascii=False)

    entries: list[dict[str, str]] = []
    omitted = _new_omitted_stats()
    scanned_count = 0
    total_files = 0
    total_directories = 0
    try:
        for item in sorted(safe_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            scanned_count += 1
            if not show_hidden and item.name.startswith("."):
                omitted["hidden"] += 1
                continue
            rel = item.relative_to(safe_path)
            if _matches_exclude_pattern(rel, exclude_patterns):
                omitted["ignored_by_pattern"] += 1
                continue
            entry_type = "directory" if item.is_dir() else "file"
            entry: dict[str, str] = {"name": item.name, "type": entry_type}
            if item.is_file():
                entry["size"] = _format_size(item.stat().st_size)
                total_files += 1
            else:
                total_directories += 1
            entries.append(entry)
    except PermissionError:
        return json.dumps(
            {"error": f"没有权限访问目录 '{directory}'"}, ensure_ascii=False
        )

    total = len(entries)
    end = offset + limit
    paged_entries = entries[offset:end]
    has_more = end < total
    return json.dumps(
        {
            "directory": directory,
            "absolute_path": str(safe_path),
            "mode": "flat",
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(paged_entries),
            "returned_count": len(paged_entries),
            "has_more": has_more,
            "next_cursor": str(end) if has_more else None,
            "truncated": has_more,
            "scanned_count": scanned_count,
            "omitted": omitted,
            "summary": {
                "total_visible": total,
                "total_files": total_files,
                "total_directories": total_directories,
            },
            "exclude_patterns": exclude_patterns,
            "entries": paged_entries,
        },
        ensure_ascii=False,
        indent=2,
    )


def _build_tree(
    dir_path: Path,
    root_path: Path,
    show_hidden: bool,
    remaining_depth: int,
    exclude_patterns: list[str],
    stats: dict[str, Any],
    max_nodes: int,
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
    if int(stats["returned_nodes"]) >= max_nodes:
        stats["truncated"] = True
        return entries

    try:
        items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        stats["omitted"]["permission_denied"] += 1
        return entries

    for item in items:
        stats["scanned_count"] += 1
        if not show_hidden and item.name.startswith("."):
            stats["omitted"]["hidden"] += 1
            continue

        relative = item.relative_to(root_path)
        if _matches_exclude_pattern(relative, exclude_patterns):
            stats["omitted"]["ignored_by_pattern"] += 1
            continue
        if int(stats["returned_nodes"]) >= max_nodes:
            stats["truncated"] = True
            break

        if item.is_dir():
            entry: dict[str, Any] = {"name": item.name, "type": "directory"}
            stats["returned_nodes"] += 1
            # remaining_depth: -1 无限，>1 继续递归，==1 不再展开
            if remaining_depth == -1 or remaining_depth > 1:
                next_depth = -1 if remaining_depth == -1 else remaining_depth - 1
                entry["children"] = _build_tree(
                    item,
                    root_path,
                    show_hidden,
                    next_depth,
                    exclude_patterns,
                    stats,
                    max_nodes,
                )
            entries.append(entry)
        elif item.is_file():
            entries.append(
                {
                    "name": item.name,
                    "type": "file",
                    "size": _format_size(item.stat().st_size),
                }
            )
            stats["returned_nodes"] += 1

    return entries


def _count_visible_children(
    dir_path: Path,
    *,
    root_path: Path,
    show_hidden: bool,
    exclude_patterns: list[str],
) -> int | None:
    try:
        children = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return None

    count = 0
    for child in children:
        if not show_hidden and child.name.startswith("."):
            continue
        rel = child.relative_to(root_path)
        if _matches_exclude_pattern(rel, exclude_patterns):
            continue
        count += 1
    return count


def _list_directory_overview(
    directory: str,
    safe_path: Path,
    show_hidden: bool,
    offset: int,
    limit: int,
    exclude_patterns: list[str],
) -> str:
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return json.dumps({"error": paging_error}, ensure_ascii=False)

    entries: list[dict[str, Any]] = []
    hotspots: list[dict[str, Any]] = []
    omitted = _new_omitted_stats()
    scanned_count = 0
    total_file_size_bytes = 0
    total_files = 0
    total_directories = 0
    by_extension: dict[str, int] = {}

    try:
        items = sorted(safe_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return json.dumps(
            {"error": f"没有权限访问目录 '{directory}'"},
            ensure_ascii=False,
        )

    for item in items:
        scanned_count += 1
        if not show_hidden and item.name.startswith("."):
            omitted["hidden"] += 1
            continue

        rel = item.relative_to(safe_path)
        if _matches_exclude_pattern(rel, exclude_patterns):
            omitted["ignored_by_pattern"] += 1
            continue

        item_type = "directory" if item.is_dir() else "file"
        entry: dict[str, Any] = {"name": item.name, "type": item_type}
        if item.is_file():
            size_bytes = item.stat().st_size
            entry["size"] = _format_size(size_bytes)
            entry["size_bytes"] = size_bytes
            total_file_size_bytes += size_bytes
            total_files += 1
            ext = item.suffix.lower() if item.suffix else "[none]"
            by_extension[ext] = by_extension.get(ext, 0) + 1
        else:
            total_directories += 1
            direct_children = _count_visible_children(
                item,
                root_path=safe_path,
                show_hidden=show_hidden,
                exclude_patterns=exclude_patterns,
            )
            entry["direct_children"] = direct_children
            hotspots.append(
                {
                    "path": item.name,
                    "direct_children": direct_children if direct_children is not None else -1,
                }
            )
            if direct_children is None:
                omitted["permission_denied"] += 1
        entries.append(entry)

    total = len(entries)
    end = offset + limit
    paged_entries = entries[offset:end]
    has_more = end < total
    sorted_exts = sorted(
        by_extension.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )[:_MAX_OVERVIEW_EXTENSIONS]
    top_hotspots = sorted(
        hotspots,
        key=lambda row: (-int(row["direct_children"]), str(row["path"])),
    )[:_MAX_OVERVIEW_HOTSPOTS]

    return json.dumps(
        {
            "directory": directory,
            "absolute_path": str(safe_path),
            "mode": "overview",
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(paged_entries),
            "returned_count": len(paged_entries),
            "has_more": has_more,
            "next_cursor": str(end) if has_more else None,
            "truncated": has_more,
            "entries": paged_entries,
            "hotspots": top_hotspots,
            "omitted": omitted,
            "summary": {
                "total_visible": total,
                "total_files": total_files,
                "total_directories": total_directories,
                "total_file_size_bytes": total_file_size_bytes,
                "by_extension": {ext: count for ext, count in sorted_exts},
                "scanned_count": scanned_count,
            },
            "exclude_patterns": exclude_patterns,
        },
        ensure_ascii=False,
        indent=2,
    )




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
    rel_path = str(safe_path.relative_to(guard.workspace_root))
    content_str = "\n".join(lines)
    result = {
        "file": safe_path.name,
        "encoding": encoding,
        "lines_read": total_lines,
        "truncated": truncated,
        "content": content_str,
        "_text_preview": {
            "file_path": rel_path,
            "content": content_str,
            "line_count": total_lines,
            "truncated": truncated,
        },
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


def offer_download(file_path: str, description: str = "") -> str:
    """向用户提供工作区内文件的可下载链接。

    文件必须位于工作区内。工具返回包含 ``_file_download`` 标记的 JSON，
    由 tool_dispatcher 检测后发射 FILE_DOWNLOAD SSE 事件，前端渲染为下载卡片。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    if not safe_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    filename = safe_path.name
    size = _format_size(safe_path.stat().st_size)

    return json.dumps(
        {
            "status": "success",
            "file_path": file_path,
            "filename": filename,
            "size": size,
            "description": description or f"文件 {filename} 已准备好下载",
            "_file_download": {
                "file_path": file_path,
                "filename": filename,
                "description": description or f"文件 {filename} 已准备好下载",
            },
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
            description=(
                "列出目录下的文件和子目录，支持扁平分页、递归树、overview 摘要模式。"
                "适用场景：浏览工作区文件结构、确认文件是否存在、了解目录布局。"
                "不适用：已知确切文件路径时直接操作，无需先浏览目录。"
                "工作区特殊目录：uploads/（用户上传的附件）、outputs/（agent 产出物）、"
                "outputs/backups/（备份工作副本）。"
                "浏览子目录时指定 directory 参数（如 directory=\"uploads\"）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "目标目录路径（相对于工作目录）",
                        "default": ".",
                    },
                    "show_hidden": {
                        "type": "boolean",
                        "description": "是否显示隐藏文件",
                        "default": False,
                    },
                    "depth": {
                        "type": "integer",
                        "description": "递归深度（0=仅当前层，-1=无限递归）",
                        "default": 2,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "flat", "tree", "overview"],
                        "description": "扫描模式：auto=根据 depth 推断，flat=扁平分页，tree=递归树，overview=摘要",
                        "default": "auto",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "分页起始偏移",
                        "default": 0,
                        "minimum": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "分页大小（默认100，最大500）",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                    "cursor": {
                        "type": "string",
                        "description": "游标分页（覆盖 offset）",
                    },
                    "exclude": {
                        "type": "array",
                        "description": "额外排除规则（目录名或 glob）",
                        "items": {"type": "string"},
                    },
                    "use_default_excludes": {
                        "type": "boolean",
                        "description": "是否启用默认噪音目录排除",
                        "default": True,
                    },
                    "max_nodes": {
                        "type": "integer",
                        "description": "tree 模式最多返回节点数",
                        "default": 2000,
                        "minimum": 1,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=list_directory,
            max_result_chars=0,
            write_effect="none",
        ),
        ToolDef(
            name="read_text_file",
            description=(
                "读取文本文件内容（md、txt、py、json、csv、yaml、toml 等）。"
                "适用场景：查看脚本源码、配置文件、文档、日志等非 Excel 文本文件。"
                "不适用：Excel/二进制文件请用 read_excel。"
                "返回文件内容与行数信息；超长文件自动截断。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件路径（相对于工作目录）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码",
                        "default": "utf-8",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "最大读取行数（默认200）",
                        "default": 200,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_text_file,
            max_result_chars=6000,
            write_effect="none",
        ),
        ToolDef(
            name="copy_file",
            description=(
                "复制文件到工作区内的新位置（不覆盖已有文件）。"
                "适用场景：创建文件副本、备份原始文件后再修改。"
                "目标路径已存在时会报错，需先 delete_file 或使用不同名称。"
                "相关工具：list_directory（先确认目标路径）、rename_file（移动而非复制）。"
            ),
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
            write_effect="workspace_write",
        ),
        ToolDef(
            name="rename_file",
            description=(
                "重命名或移动文件到工作区内的新位置（不覆盖已有文件）。"
                "适用场景：文件重命名、移动到子目录。"
                "目标路径已存在时会报错。"
                "相关工具：list_directory（先确认目标路径不冲突）、copy_file（保留原文件）。"
            ),
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
            write_effect="workspace_write",
        ),
        ToolDef(
            name="delete_file",
            description=(
                "安全删除文件（仅限文件，不删目录），需 confirm=true 二次确认。"
                "适用场景：清理不需要的文件、删除后重建。"
                "首次调用不传 confirm 时返回文件信息供确认，第二次调用传 confirm=true 才执行删除。"
                "相关工具：list_directory（先确认文件存在）。"
            ),
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
            write_effect="workspace_write",
        ),
        ToolDef(
            name="offer_download",
            description=(
                "向用户提供工作区内文件的可下载链接。"
                "当你完成文件生成/处理后，使用此工具让用户能够直接下载结果文件。"
                "前端会渲染为醒目的下载卡片。"
                "适用场景：生成报告、导出数据、处理完成后提供结果文件。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "要提供下载的文件路径（相对于工作目录）",
                    },
                    "description": {
                        "type": "string",
                        "description": "对文件的简短描述（显示在下载卡片上）",
                        "default": "",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=offer_download,
            write_effect="none",
        ),
    ]
