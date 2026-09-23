"""文件工具：提供工作区文件管理能力（查看、搜索、读取、复制、重命名、删除）。"""

from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta, error_result, from_payload, ok_result
from excelmanus.logger import get_logger
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.security import FileAccessGuard
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.file")

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
    "*.em-lock",
)
_EMPTY_LISTING_NOTE = (
    "可见文件为 0。默认会藏起 outputs 等噪音目录；隐藏项另计。"
    "用户点名的表不在这里时停下请用户拖入，不要继续扫别的目录凑数。"
)
_MISSING_UPLOADS_MESSAGE = (
    "工作区还没有 uploads，用户尚未把文件拖进对话。不要继续扫别的目录凑表。"
)


def _get_guard() -> FileAccessGuard:
    return require_guard()


def _attach_empty_listing_note(result: dict[str, Any]) -> dict[str, Any]:
    """可见文件为 0 时写明：这不是「去翻别的目录」的信号。"""
    total = int(result.get("total") or 0)
    if total != 0:
        return result
    omitted = result.get("omitted") or {}
    hidden = int(omitted.get("hidden") or 0)
    ignored = int(omitted.get("ignored_by_pattern") or 0)
    note = _EMPTY_LISTING_NOTE
    if hidden or ignored:
        note = f"{note} 本次另有 hidden={hidden}、ignored_by_pattern={ignored}。"
    result["note"] = note
    return result


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


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
) -> ToolResult:
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
        ToolResult（value 为目录内容）。
    """
    effective_mode = _resolve_mode(mode, depth)
    if effective_mode is None:
        return error_result("mode 仅支持 auto、flat、tree、overview", code="INVALID_ARGS")

    effective_offset, cursor_error = _resolve_offset(offset, cursor)
    if cursor_error is not None:
        return error_result(cursor_error, code="INVALID_ARGS")

    exclude_patterns = _normalize_exclude_patterns(
        exclude,
        use_default_excludes=use_default_excludes,
    )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(directory)

    if not safe_path.is_dir():
        code = "NOT_FOUND" if not safe_path.exists() else "INVALID_ARGS"
        rel = directory.replace("\\", "/").strip("/")
        if not safe_path.exists() and rel in {"uploads", "uploads/"}:
            return error_result(
                _MISSING_UPLOADS_MESSAGE,
                code=code,
                remediation="不要再 list。请用户把文件拖进对话。",
            )
        return error_result(f"路径 '{directory}' 不是一个有效的目录", code=code)

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
        return error_result("max_nodes 必须为正整数", code="INVALID_ARGS")

    paging_error = _validate_pagination(effective_offset, limit)
    if paging_error is not None:
        return error_result(paging_error, code="INVALID_ARGS")

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
    _attach_empty_listing_note(result)
    return from_payload(result)


def _list_directory_flat(
    directory: str,
    safe_path: Path,
    show_hidden: bool,
    offset: int,
    limit: int,
    exclude_patterns: list[str],
) -> ToolResult:
    """扁平分页模式（depth=0 时的原有行为）。"""
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return error_result(paging_error, code="INVALID_ARGS")

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
        return error_result(f"没有权限访问目录 '{directory}'", code="PERMISSION_DENIED")

    total = len(entries)
    end = offset + limit
    paged_entries = entries[offset:end]
    has_more = end < total
    payload = {
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
        }
    _attach_empty_listing_note(payload)
    return from_payload(payload)


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
) -> ToolResult:
    paging_error = _validate_pagination(offset, limit)
    if paging_error is not None:
        return error_result(paging_error, code="INVALID_ARGS")

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
        return error_result(f"没有权限访问目录 '{directory}'", code="PERMISSION_DENIED")

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

    payload = {
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
        }
    _attach_empty_listing_note(payload)
    return from_payload(payload)




def _format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f}TB"


def get_file_info(file_path: str) -> ToolResult:
    """获取文件的详细信息。

    Args:
        file_path: 文件路径（相对于工作目录）。

    Returns:
        JSON 格式的文件详情。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.exists():
        return error_result(f"路径 '{file_path}' 不存在", code="NOT_FOUND")

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

    return from_payload(info)


def find_files(pattern: str = "*", directory: str = ".", max_results: int = 50) -> ToolResult:
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
        return error_result(f"路径 '{directory}' 不是一个有效的目录", code="NOT_FOUND" if not safe_dir.exists() else "INVALID_ARGS")

    matches: list[dict[str, str]] = []
    try:
        for item in safe_dir.glob(pattern):
            # 跳过隐藏文件/目录与工作簿建议锁残留（<file>.em-lock 为内部工件）
            if any(part.startswith(".") for part in item.relative_to(safe_dir).parts):
                continue
            if item.name.endswith(".em-lock"):
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
        return error_result(f"没有权限访问目录 '{directory}'", code="PERMISSION_DENIED")

    result = {
        "pattern": pattern,
        "directory": directory,
        "total": len(matches),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }
    return from_payload(result)


def read_text_file(
    file_path: str,
    encoding: str = "utf-8",
    max_lines: int = 500,
    max_rows: int | None = None,
) -> ToolResult:
    """读取文本文件内容（CSV、TXT 等）。

    Args:
        file_path: 文件路径（相对于工作目录）。
        encoding: 文件编码，默认 utf-8。
        max_lines: 文本行数上限（规范名），默认 500。max_rows 是同义别名。

    Returns:
        JSON 格式的文件内容。
    """
    if max_rows is not None:
        max_lines = int(max_rows)
    guard = _get_guard()
    from excelmanus.engine_core.spill import is_spill_reference, retrieve_spill_result

    if is_spill_reference(file_path):
        return retrieve_spill_result(file_path, workspace_root=guard.workspace_root)
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.is_file():
        code = "NOT_FOUND" if not safe_path.exists() else "INVALID_ARGS"
        return error_result(f"路径 '{file_path}' 不是一个有效的文件", code=code)

    detected: str | None = None
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
        # 默认 utf-8 解码失败时按样本探测常见编码（gb18030 覆盖 GBK）；
        # 显式指定的编码不越权改判，直接报错。
        if encoding.lower().replace("_", "-") in {"utf-8", "utf8"}:
            for candidate in ("gb18030", "utf-16"):
                try:
                    with open(safe_path, "r", encoding=candidate) as f:
                        lines = []
                        truncated = False
                        for i, line in enumerate(f):
                            if i < max_lines:
                                lines.append(line.rstrip("\n"))
                            else:
                                truncated = True
                                break
                    detected = candidate
                    break
                except (UnicodeDecodeError, ValueError):
                    continue
        if detected is None:
            return error_result(
                f"无法以 {encoding} 编码读取文件 '{file_path}'，可能是二进制文件",
                code="DECODE_ERROR",
            )

    from excelmanus.workbook_commit import content_version_of_file, remember_content_version

    total_lines = len(lines)
    rel_path = str(safe_path.relative_to(guard.workspace_root)).replace("\\", "/")
    content_str = "\n".join(lines)
    version = content_version_of_file(safe_path)
    remember_content_version(rel_path, version)
    result = {
        "file_path": rel_path,
        "encoding": detected or encoding,
        "lines_read": total_lines,
        "truncated": truncated,
        "content": content_str,
        "content_version": version,
        "_text_preview": {
            "file_path": rel_path,
            "content": content_str,
            "line_count": total_lines,
            "truncated": truncated,
        },
    }
    return from_payload(result)


def copy_file(source: str, destination: str) -> ToolResult:
    """复制文件到工作区内的新位置。

    Args:
        source: 源文件路径（相对于工作目录）。
        destination: 目标路径（相对于工作目录）。

    Returns:
        操作结果描述。
    """
    from excelmanus.security.source_isolation import (
        PROBE_FILE_FORBIDDEN,
        is_probe_path,
        probe_error_message,
    )
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import (
        CommitError,
        commit_bytes,
        remember_content_version,
    )

    if is_probe_path(destination):
        return error_result(
            probe_error_message(destination),
            code=PROBE_FILE_FORBIDDEN,
        )

    guard = _get_guard()
    src_path = guard.resolve_and_validate(source)
    dst_path = guard.resolve_and_validate(destination)

    if not src_path.is_file():
        return error_result(f"源路径 '{source}' 不是一个有效的文件", code="NOT_FOUND" if not src_path.exists() else "INVALID_ARGS")

    if dst_path.exists():
        return error_result(f"目标路径 '{destination}' 已存在，拒绝覆盖", code="FILE_EXISTS")

    dst_rel = str(dst_path.relative_to(guard.workspace_root)).replace("\\", "/")
    try:
        from excelmanus.tools.context import operation_id_for
        cr = commit_bytes(
            guard=guard,
            file_path=dst_rel,
            data=src_path.read_bytes(),
            expected_version=None,
            operation_id=operation_id_for(dst_rel),
        )
    except CommitError as exc:
        return commit_error_result(exc)
    remember_content_version(dst_rel, cr.content_version)

    return from_payload(
        {
            "status": "success",
            "source": source,
            "destination": cr.path,
            "size": _format_size(cr.bytes_written),
            "content_version": cr.content_version,
        },
    )


def rename_file(
    source: str,
    destination: str,
    expected_version: str | None = None,
) -> ToolResult:
    """重命名或移动文件（工作区内）。

    Args:
        source: 源文件路径（相对于工作目录）。
        destination: 目标路径（相对于工作目录）。
        expected_version: 源文件本轮已读 content_version。

    Returns:
        操作结果描述。
    """
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import CommitError, commit_move, remember_content_version

    guard = _get_guard()
    src_path = guard.resolve_and_validate(source)
    dst_path = guard.resolve_and_validate(destination)

    if not src_path.is_file():
        return error_result(f"源路径 '{source}' 不是一个有效的文件", code="NOT_FOUND" if not src_path.exists() else "INVALID_ARGS")

    if dst_path.exists():
        return error_result(f"目标路径 '{destination}' 已存在，拒绝覆盖", code="FILE_EXISTS")

    src_rel = str(src_path.relative_to(guard.workspace_root)).replace("\\", "/")
    dst_rel = str(dst_path.relative_to(guard.workspace_root)).replace("\\", "/")
    try:
        from excelmanus.tools.context import operation_id_for
        cr = commit_move(
            guard=guard,
            source=src_rel,
            destination=dst_rel,
            expected_version=expected_version,
            operation_id=operation_id_for(dst_rel),
        )
    except CommitError as exc:
        return commit_error_result(exc)
    remember_content_version(dst_rel, cr.content_version)

    return from_payload(
        {
            "status": "success",
            "source": source,
            "destination": cr.path,
            "content_version": cr.content_version,
        },
    )


def delete_file(
    file_path: str,
    confirm: bool = False,
    confirmed: bool | None = None,
    ack: bool | None = None,
    expected_version: str | None = None,
) -> ToolResult:
    """安全删除文件（仅限文件，不删除目录）。

    Args:
        file_path: 要删除的文件路径（相对于工作目录）。
        confirm: 是否确认删除，必须为 True 才执行删除。confirmed / ack 是同义别名。
        expected_version: 本轮已读 content_version。

    Returns:
        操作结果描述。
    """
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import CommitError, commit_unlink

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.exists():
        return error_result(f"路径 '{file_path}' 不存在", code="NOT_FOUND")

    if safe_path.is_dir():
        return error_result(
            f"路径 '{file_path}' 是目录，delete_file 仅允许删除文件",
            code="INVALID_ARGS",
        )

    accepted = bool(confirm) or bool(confirmed) or bool(ack)
    if not accepted:
        stat = safe_path.stat()
        return from_payload(
            {
                "status": "confirmation_required",
                "message": "请将 confirm 设为 true 以确认删除（confirmed / ack 同义）",
                "file_path": file_path,
                "size": _format_size(stat.st_size),
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            },
        )

    rel = str(safe_path.relative_to(guard.workspace_root)).replace("\\", "/")
    size = _format_size(safe_path.stat().st_size)
    try:
        from excelmanus.tools.context import operation_id_for
        cr = commit_unlink(
            guard=guard,
            file_path=rel,
            expected_version=expected_version,
            operation_id=operation_id_for(rel),
        )
    except CommitError as exc:
        return commit_error_result(exc)

    return from_payload(
        {
            "status": "success",
            "deleted": cr.path,
            "size": size,
            "previous_version": cr.previous_version,
        },
    )


def offer_download(
    file_path: str,
    description: str = "",
    expected_version: str | None = None,
) -> ToolResult:
    """向用户提供工作区内文件的可下载链接。

    下载事实放在 ui_meta.download，由分派器投影为 FILE_DOWNLOAD 事件。
    """
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    if not safe_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    from excelmanus.workbook_commit import content_version_of

    content_version = content_version_of(safe_path.read_bytes())
    if expected_version and expected_version != content_version:
        return error_result(
            "下载目标版本已改变，请先重新读取文件并确认最终版本。",
            code="VERSION_CONFLICT",
            fields={
                "file_path": file_path,
                "expected_version": expected_version,
                "content_version": content_version,
                "committed": False,
            },
        )

    filename = safe_path.name
    size = _format_size(safe_path.stat().st_size)
    description_text = description or f"文件 {filename} 已准备好下载"
    payload = {
        "status": "success",
        "file_path": file_path,
        "filename": filename,
        "size": size,
        "description": description_text,
        "content_version": content_version,
    }
    return ok_result(
        payload,
        ui_meta=ToolUiMeta(
            files=[file_path],
            download={
                "file_path": file_path,
                "filename": filename,
                "description": description_text,
                "content_version": content_version,
            },
        ),
    )


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回文件系统 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="list_directory",
            description=(
                "列出工作区内的文件和子目录。"
                "用户要求查看或汇总工作区时使用；点名文件仍应直接对该相对路径操作。"
                "桌面和绝对路径不可达。uploads/ 只读；文件历史在 .excelmanus/revisions/。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "工作区相对路径，不要填桌面或绝对路径",
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
                "也可取回工具大结果：file_path 原样传 spill/result_spill/selection_spill 字段的值（spill:…）；返回完整原始结果，不按文本行数截断。"
                "适用场景：查看脚本源码、配置文件、文档、日志等非 Excel 文本文件。"
                "不适用：Excel/二进制文件请用 inspect_spreadsheet。"
                "返回文件内容与行数信息；超长文件自动截断。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "工作区相对文件路径，或结果字段返回的完整 spill:… 句柄。句柄不是磁盘路径，原样传入，不要拼接字段名或目录。",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码",
                        "default": "utf-8",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "文本行数上限（规范名）。语义等同表格侧 max_rows。",
                        "default": 500,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "deprecated 别名，等同 max_lines。",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=read_text_file,
            max_result_chars=10000,
            write_effect="none",
        ),
        ToolDef(
            name="copy_file",
            description=(
                "复制文件到工作区内的新位置（不覆盖已有文件）。"
                "适用场景：把 uploads/ 只读附件备份到 outputs/ 再改副本。"
                "目标路径已存在时会报错，需先 delete_file 或使用不同名称。"
                "不要改 uploads/ 原件。"
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
                    "expected_version": {
                        "type": "string",
                        "description": "源文件本轮已读 content_version；缺省则 VERSION_CONFLICT",
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
                "安全删除文件（仅限文件，不删目录）。"
                "执行层强制两段确认：未带 confirm/confirmed/ack 时返回 confirmation_required，不删。"
                "第二次调用传 confirm=true 才执行删除。"
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
                    "confirmed": {
                        "type": "boolean",
                        "description": "confirm 的同义别名",
                    },
                    "ack": {
                        "type": "boolean",
                        "description": "confirm 的同义别名",
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "本轮已读 content_version；缺省则 VERSION_CONFLICT",
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
            description=TOOL_DESCRIPTIONS["offer_download"],
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
                    "expected_version": {
                        "type": "string",
                        "description": "可选；仅当文件当前 content_version 与此值一致时才生成下载事实。",
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
            func=offer_download,
            write_effect="none",
        ),
    ]
