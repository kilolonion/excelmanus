"""工具层共享辅助函数。"""

from __future__ import annotations

import logging
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Sequence

from excelmanus.engine_core.tool_result import ToolResult, error_result, from_payload

_logger = logging.getLogger(__name__)

# 文件存在性检查时，最多列出的可用文件数量
_MAX_SUGGESTION_FILES = 15
_EXCEL_SUFFIXES: frozenset[str] = frozenset({".xlsx", ".xls", ".xlsm", ".xlsb"})
OUTSIDE_WORKSPACE_MESSAGE = (
    "只能访问工作区里的文件，打不开电脑桌面或工作区外路径。"
    "把文件拖进对话后再说一次文件名。"
)
OUTSIDE_WORKSPACE_REMEDIATION = (
    "使用工作区相对路径。不要改用 shell、绝对路径或工作区外位置绕过权限。"
)
MISSING_NAMED_FILE_HINT = (
    "工作区没有这个相对路径。下列同目录文件仅供核对拼写；不要擅自替换目标。"
    "任务允许查找时可用 list_directory 或 analyze_spreadsheet(mode=files)。确实未上传时请用户提供。"
)
MISSING_NAMED_FILE_REMEDIATION = (
    "核对相对路径拼写；任务允许查找时用 list_directory 或 analyze_spreadsheet(mode=\"files\") 列候选，"
    "候选不唯一时问一个问题；确实未上传时请用户提供。不要擅自换成另一份表。"
)


def check_file_exists(safe_path: Path, user_path: str, guard: Any) -> ToolResult | None:
    """文件不存在时返回 ToolResult 错误，存在时返回 None。"""
    if safe_path.is_file():
        return None

    workspace_root: Path = guard.workspace_root
    suggestions: list[str] = []

    # 只列同目录下的兄弟文件，供文件名打错时对照；不要扫整个工作区。
    parent = safe_path.parent
    if parent.is_dir():
        try:
            for f in sorted(parent.iterdir()):
                if f.is_file() and f.suffix.lower() in _EXCEL_SUFFIXES:
                    try:
                        suggestions.append(str(f.relative_to(workspace_root)))
                    except ValueError:
                        suggestions.append(f.name)
                if len(suggestions) >= _MAX_SUGGESTION_FILES:
                    break
        except OSError:
            pass

    extra: dict[str, Any] = {
        "hint": MISSING_NAMED_FILE_HINT,
    }
    if suggestions:
        extra["available_excel_files"] = suggestions[:_MAX_SUGGESTION_FILES]
        extra["hint"] = (
            MISSING_NAMED_FILE_HINT
            + " 候选不是自动替换目标。"
        )

    return error_result(
        f"文件不存在: {user_path}",
        code="PATH_INVALID",
        remediation=MISSING_NAMED_FILE_REMEDIATION,
        fields=extra,
    )


# fuzzy matching 相似度阈值：≥ 此值时自动纠正
_FUZZY_MATCH_THRESHOLD: float = 0.6


def resolve_sheet_name(
    requested: str | None,
    available: Sequence[str],
) -> str | None:
    """对 sheet 名做三级模糊匹配。

    优先精确匹配；若失败则尝试忽略大小写匹配；
    若仍失败则尝试 SequenceMatcher fuzzy 匹配（阈值 0.6）。
    匹配成功返回 *实际* sheet 名（保留原始大小写），
    匹配失败返回 ``None``。

    Args:
        requested: LLM / 用户传入的 sheet 名，可为 None。
        available: workbook 中实际存在的 sheet 名列表。

    Returns:
        匹配到的实际 sheet 名，或 None。
    """
    if requested is None:
        return None

    # 精确匹配
    if requested in available:
        return requested

    # 大小写不敏感回退匹配
    lower = requested.lower()
    for name in available:
        if name.lower() == lower:
            return name

    # 不做静默 fuzzy 自动纠正：改写表名会让 agent 以为错误拼写可用，
    # 并把错误名称传播给用户。近似名只作为错误提示（见 check_sheet_name
    # 的 closest_match），全链路（数据操作与结构操作）行为一致。
    best_name, best_ratio = _find_closest_sheet_name(requested, available)
    if best_name is not None and best_ratio >= _FUZZY_MATCH_THRESHOLD:
        _logger.info(
            "sheet 名未精确匹配，最接近 '%s'（相似度 %.2f），已拒绝并建议纠正",
            best_name, best_ratio,
        )
    return None


def _find_closest_sheet_name(
    requested: str,
    available: Sequence[str],
) -> tuple[str | None, float]:
    """在 available 中找到与 requested 最接近的 sheet 名。

    Returns:
        (best_match_name, best_ratio)，无候选时返回 (None, 0.0)。
    """
    if not available:
        return None, 0.0
    req_lower = requested.lower()
    best_name: str | None = None
    best_ratio: float = 0.0
    for name in available:
        ratio = SequenceMatcher(None, req_lower, name.lower()).ratio()
        if ratio > best_ratio:
            best_name = name
            best_ratio = ratio
    return best_name, best_ratio


def get_worksheet(wb: Any, sheet_name: str | None) -> Any:
    """从 workbook 中获取工作表，支持 case-insensitive fallback。

    若 sheet_name 为 None，返回 active sheet。
    若 sheet_name 非空但无法匹配，抛出 ValueError 并附带可用 sheet 列表。

    Args:
        wb: openpyxl Workbook 对象。
        sheet_name: 请求的 sheet 名。

    Returns:
        匹配到的 Worksheet 对象。

    Raises:
        ValueError: sheet_name 非空但在 workbook 中未找到匹配项。
    """
    if not sheet_name:
        return wb.active
    resolved = resolve_sheet_name(sheet_name, wb.sheetnames)
    if resolved is not None:
        return wb[resolved]
    raise ValueError(
        f"工作表 '{sheet_name}' 不存在。"
        f"该文件包含以下工作表: {wb.sheetnames}。"
        f"请使用正确的工作表名称重试。"
    )


def check_sheet_name(safe_path: Path, sheet_name: str | None) -> tuple[str | None, ToolResult | None]:
    """验证 sheet 名。成功返回 (resolved_name, None)；失败返回 (None, ToolResult)。"""
    if sheet_name is None:
        return None, None

    try:
        from openpyxl import load_workbook
        wb = load_workbook(safe_path, read_only=True, data_only=True)
        try:
            available = wb.sheetnames
            resolved = resolve_sheet_name(sheet_name, available)
            if resolved is not None:
                # fuzzy 纠正时附带提示（非精确匹配才提示）
                if resolved != sheet_name and resolved.lower() != sheet_name.lower():
                    _logger.info(
                        "check_sheet_name: fuzzy 纠正 '%s' → '%s'",
                        sheet_name, resolved,
                    )
                return resolved, None
            extra: dict[str, Any] = {"available_sheets": available}
            closest, ratio = _find_closest_sheet_name(sheet_name, available)
            if closest is not None and ratio > 0.3:
                extra["closest_match"] = closest
                extra["similarity"] = round(ratio, 2)
                extra["hint"] = (
                    f"该文件包含以下工作表: {available}。"
                    f"最接近的是 '{closest}'（相似度 {ratio:.0%}），请确认后重试。"
                )
            else:
                extra["hint"] = f"该文件包含以下工作表: {available}。请使用正确的工作表名称重试。"
            return None, error_result(
                f"工作表 '{sheet_name}' 不存在",
                code="SHEET_NOT_FOUND",
                fields=extra,
            )
        finally:
            wb.close()
    except Exception as exc:
        _logger.warning("check_sheet_name 异常: %s", exc)
        return sheet_name, None  # 异常时放行，让下游处理


def ensure_openpyxl_compatible(safe_path: Path) -> Path:
    """确保路径指向 openpyxl 可操作的文件格式（.xlsx/.xlsm）。

    若为 .xls/.xlsb，返回隐藏的只读预览 backing，不发布同目录副本。
    CSV 文件原样返回（由调用方处理）。

    Args:
        safe_path: 经 guard.resolve_and_validate 后的绝对路径。

    Returns:
        openpyxl 可直接打开的文件路径。
    """
    from excelmanus.xls_converter import needs_conversion

    if not needs_conversion(safe_path):
        return safe_path

    try:
        from excelmanus.tools.context import current_call

        call = current_call()
        if call is None:
            raise ValueError("转换预览缺少工作区上下文")
        from excelmanus.workbook.snapshot import open_snapshot_at

        workspace = call.binding.workspace
        relative = safe_path.relative_to(workspace.root).as_posix()
        return open_snapshot_at(safe_path, relative=relative, workspace=workspace).backing_path
    except Exception as exc:
        _logger.warning("工具层 xls 转换失败，返回原路径: %s (%s)", safe_path.name, exc)
        return safe_path


class MutationAborted(Exception):
    """mutate_fn 内取消提交（不写盘）。``commit_workbook`` 会把它包成 SAVE_FAILED。"""

    def __init__(self, result: ToolResult | dict[str, Any]) -> None:
        if isinstance(result, dict):
            result = from_payload(result)
        self.result = result
        super().__init__(result.model_text)


def workspace_relpath(guard: Any, path: Path | str) -> str:
    """把已校验的绝对路径转成工作区相对路径，供 ``commit_*`` 使用。"""
    dest = path if isinstance(path, Path) else Path(path)
    return str(dest.relative_to(guard.workspace_root))


def prepare_excel_commit_path(guard: Any, file_path: str) -> tuple[Path, str]:
    """解析用户路径、必要时转 xlsx，返回 (绝对路径, 工作区相对路径)。"""
    safe_path = guard.resolve_and_validate(file_path)
    from excelmanus.xls_converter import needs_conversion
    if needs_conversion(safe_path):
        from excelmanus.workbook_commit import CommitError

        raise CommitError("CONVERSION_REQUIRED", "请先显式转换为 .xlsx 后编辑旧版工作簿")
    rel = workspace_relpath(guard, safe_path)
    rel_posix = rel.replace("\\", "/").lstrip("./")
    if rel_posix == "uploads" or rel_posix.startswith("uploads/"):
        from excelmanus.workbook_commit import CommitError

        raise CommitError(
            "PATH_INVALID",
            "uploads/ 是只读附件。请 copy_file 到 outputs/ 再改副本。",
            fields={"path": rel_posix},
        )
    return safe_path, rel


def commit_workbook_tool(
    *,
    guard: Any,
    file_path: str,
    mutate_fn: Any,
    expected_version: str | None = None,
    create: bool = False,
    selection_bound: bool = False,
    operation_id: str | None = None,
) -> Any:
    """工具写入入口：优先使用本轮已读到的 content_version，再原子提交。"""
    from excelmanus.workbook_commit import (
        CommitError,
        commit_workbook,
        peek_seen_content_version,
        remember_content_version,
    )

    seen = expected_version
    try:
        dest = Path(guard.resolve_and_validate(file_path))
    except Exception:
        dest = None
    if dest is not None and dest.is_file():
        if selection_bound:
            seen = (expected_version or "").strip()
            if not seen:
                raise CommitError(
                    "SELECTION_STALE",
                    f"{file_path} 带 selection/source_rows 的写入必须提供该选择的 content_version，不能使用最新 seen",
                    fields={"path": file_path},
                )
        else:
            seen = expected_version or peek_seen_content_version(file_path)
    result = commit_workbook(
        guard=guard,
        file_path=file_path,
        mutate_fn=mutate_fn,
        expected_version=seen,
        create=create,
        operation_id=operation_id or _operation_id_for(file_path),
    )
    remember_content_version(file_path, result.content_version)
    remember_content_version(result.path, result.content_version)
    return result


def commit_bytes_tool(
    *,
    guard: Any,
    file_path: str,
    data: bytes,
    expected_version: str | None = None,
    operation_id: str | None = None,
) -> Any:
    """字节写入入口：同样优先使用已读版本。"""
    from excelmanus.workbook_commit import (
        commit_bytes,
        peek_seen_content_version,
        remember_content_version,
    )

    seen = expected_version
    try:
        dest = Path(guard.resolve_and_validate(file_path))
    except Exception:
        dest = None
    if dest is not None and dest.is_file():
        seen = expected_version or peek_seen_content_version(file_path)
    result = commit_bytes(
        guard=guard,
        file_path=file_path,
        data=data,
        expected_version=seen,
        operation_id=operation_id or _operation_id_for(file_path),
    )
    remember_content_version(file_path, result.content_version)
    remember_content_version(result.path, result.content_version)
    return result


def _operation_id_for(path: str | None = None) -> str | None:
    from excelmanus.tools.context import operation_id_for

    return operation_id_for(path)


def commit_error_result(exc: Any) -> ToolResult:
    """把 ``CommitError`` 映射为工具层错误结果。"""
    fields = getattr(exc, "fields", None)
    extra = dict(fields) if isinstance(fields, dict) else {}
    return error_result(
        getattr(exc, "message", str(exc)),
        code=getattr(exc, "code", "SAVE_FAILED"),
        fields=extra or None,
    )


def unwrap_mutation_abort(exc: BaseException) -> MutationAborted | None:
    """从 ``CommitError`` 中取出 mutate_fn 抛出的 ``MutationAborted``。"""
    if isinstance(exc, MutationAborted):
        return exc
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, MutationAborted):
        return cause
    return None
