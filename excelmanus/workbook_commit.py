"""应用内工作簿提交的兼容入口。

正式写路径是 ``WorkspaceFileService``。本模块保留 ``CommitResult`` / 锁 /
seen-version 以及 ``commit_*`` 薄包装，供既有工具与测试调用。
"""

from __future__ import annotations

import contextvars
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from excelmanus.logger import get_logger
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError

logger = get_logger("workbook_commit")

_seen_versions: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "excelmanus_seen_content_versions",
    default=None,
)


_WS_SEP = "::"


def normalize_version_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip()


def _workspace_prefix() -> str | None:
    from excelmanus.tools.context import current_call

    call = current_call()
    if call is None:
        return None
    return call.binding.workspace.identity_key()


def _qualified_version_key(path: str) -> str:
    rel = normalize_version_path(path)
    prefix = _workspace_prefix()
    if not prefix or not rel:
        return rel
    if rel.startswith(f"{prefix}{_WS_SEP}"):
        return rel
    if _WS_SEP in rel and (rel.startswith("id:") or rel.startswith("path:")):
        return rel
    return f"{prefix}{_WS_SEP}{rel}"


def remember_content_version(path: str, version: str | None) -> None:
    """记录当前执行上下文里某文件最近一次读到/写到的内容版本。

    键绑定 WorkspaceRef.identity_key，避免同相对路径跨工作区串版本。
    """
    key = _qualified_version_key(path)
    if not key or not version:
        return
    bag = _seen_versions.get()
    if bag is None:
        bag = {}
        _seen_versions.set(bag)
    bag[key] = version


def peek_seen_content_version(path: str) -> str | None:
    bag = _seen_versions.get()
    if not bag:
        return None
    key = _qualified_version_key(path)
    found = bag.get(key)
    if found:
        return found
    return bag.get(normalize_version_path(path))


def seed_seen_versions(mapping: dict[str, str] | None) -> None:
    _seen_versions.set({})
    for path, version in (mapping or {}).items():
        remember_content_version(path, version)


def export_seen_versions() -> dict[str, str]:
    bag = dict(_seen_versions.get() or {})
    prefix = _workspace_prefix()
    if not prefix:
        return bag
    marker = f"{prefix}{_WS_SEP}"
    out: dict[str, str] = {}
    for key, ver in bag.items():
        if key.startswith(marker):
            out[key[len(marker):]] = ver
        elif _WS_SEP in key and (key.startswith("id:") or key.startswith("path:")):
            continue
        else:
            out[key] = ver
    return out

CommitStatus = Literal["committed", "conflict", "rejected", "failed"]


class CommitError(Exception):
    """提交失败。``code`` 与工具层 ``ToolError.code`` 对齐。"""

    def __init__(self, code: str, message: str, *, fields: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fields = dict(fields or {})


@dataclass(frozen=True)
class CommitResult:
    path: str
    content_version: str
    previous_version: str | None
    status: CommitStatus
    warnings: tuple[str, ...] = ()
    bytes_written: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def content_version_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def content_version_of_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return content_version_of(path.read_bytes())


def lock_path_for(dest: Path) -> Path:
    """与沙盒 wrapper 共用的 ``<filename>.em-lock`` 路径。"""
    return dest.with_name(dest.name + ".em-lock")


def _acquire_lock(lock_path: Path):
    """跨进程建议锁。Unix 用 fcntl；Windows 用 msvcrt。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        fh.close()
        raise
    return fh


def _release_lock(fh: Any) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def _atomic_replace(src_tmp: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    from excelmanus.workspace.txlog import replace_with_retry

    replace_with_retry(str(src_tmp), str(dest))


def commit_bytes(
    *,
    guard: FileAccessGuard,
    file_path: str,
    data: bytes,
    expected_version: str | None = None,
    operation_id: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """把 ``data`` 原子写入工作区内路径。实现委托 ``WorkspaceFileService``。"""
    del record_history
    from excelmanus.workspace.file_service import TargetSpec, receipt_to_commit_result, service_for_guard

    svc = service_for_guard(guard)
    rel = None
    try:
        dest = guard.resolve_and_validate(file_path)
        rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
        if operation_id:
            existing = svc.get_receipt(operation_id, recover=True)
            if existing is not None:
                raw = svc.txlog.read_receipt(operation_id) or {}
                target = (raw.get("targets") or [{}])[-1]
                original_spec = TargetSpec(
                    op=target.get("op") or ("update" if dest.is_file() else "create"),
                    path=rel,
                    data=data,
                    expected_version=target.get("before_version"),
                )
                expected_digest = svc._intent_digest([original_spec], [])
                if raw.get("intent_digest") != expected_digest:
                    raise CommitError("OPERATION_ID_REUSED", f"operation_id {operation_id} 已用于不同意图")
                return receipt_to_commit_result(existing, bytes_written=len(data))
        if dest.is_file():
            if expected_version is None:
                current = content_version_of(dest.read_bytes())
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 已存在（{current}），创建写入必须带 expected_version",
                    fields={"path": rel, "content_version": current},
                )
            receipt = svc.update(rel, data, expected_version=expected_version, operation_id=operation_id)
        else:
            if expected_version is not None:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 不存在，无法按版本 {expected_version} 更新",
                    fields={"path": rel, "expected_version": expected_version},
                )
            receipt = svc.create(rel, data, operation_id=operation_id)
        return receipt_to_commit_result(receipt, bytes_written=len(data))
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc
    except CommitError:
        raise
    except Exception as exc:
        raise CommitError("SAVE_FAILED", f"写入 {rel or file_path} 失败：{exc}") from exc


def resolve_expected_version(
    file_path: str,
    expected_version: str | None,
    *,
    exists: bool,
    selection_bound: bool = False,
    abs_path: "Path | str | None" = None,
) -> str | None:
    """CAS token：显式 version，或（无行号选择时）peek_seen。禁止用最新 seen 套旧行号。

    ``abs_path``：调用方已校验的盘上路径；VERSION_CONFLICT 时用它计算当前版本，
    让错误直接携带可重试的 content_version，省去一次额外读取。
    """
    explicit = (expected_version or "").strip()
    rel = normalize_version_path(file_path)
    if selection_bound:
        if not explicit:
            raise CommitError(
                "SELECTION_STALE",
                f"{rel or file_path} 带 selection/source_rows 的写入必须提供该选择的 content_version，不能使用最新 seen",
                fields={"path": rel or file_path},
            )
        return explicit
    seen = explicit or peek_seen_content_version(file_path)
    if exists and not seen:
        current: str | None = None
        probe = Path(abs_path) if abs_path else None
        if probe is not None:
            try:
                current = content_version_of_file(probe)
            except OSError:
                current = None
        raise CommitError(
            "VERSION_CONFLICT",
            f"{rel or file_path} 已存在，缺少 expected_version",
            fields={"path": rel or file_path, "content_version": current or ""},
        )
    return seen or None


def commit_unlink(
    *,
    guard: FileAccessGuard,
    file_path: str,
    expected_version: str | None = None,
    operation_id: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """Atomically delete a live workspace file after CAS."""
    del record_history
    from excelmanus.workspace.file_service import receipt_to_commit_result, service_for_guard

    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc
    rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
    if not dest.is_file():
        raise CommitError("PATH_INVALID", f"文件不存在：{rel}", fields={"path": rel})
    seen = resolve_expected_version(rel, expected_version, exists=True, abs_path=dest)
    receipt = service_for_guard(guard).delete(rel, expected_version=seen, operation_id=operation_id)
    return receipt_to_commit_result(receipt)


def commit_move(
    *,
    guard: FileAccessGuard,
    source: str,
    destination: str,
    expected_version: str | None = None,
    operation_id: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """CAS the source file, then atomically rename within the workspace."""
    del record_history
    from excelmanus.workspace.file_service import receipt_to_commit_result, service_for_guard

    try:
        src = guard.resolve_and_validate(source)
        dst = guard.resolve_and_validate(destination)
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc
    src_rel = str(src.relative_to(guard.workspace_root)).replace("\\", "/")
    dst_rel = str(dst.relative_to(guard.workspace_root)).replace("\\", "/")
    if not src.is_file():
        raise CommitError("PATH_INVALID", f"源路径不是文件：{src_rel}", fields={"path": src_rel})
    if dst.exists():
        raise CommitError("PATH_INVALID", f"目标路径已存在：{dst_rel}", fields={"path": dst_rel})
    seen = resolve_expected_version(src_rel, expected_version, exists=True, abs_path=src)
    receipt = service_for_guard(guard).move(src_rel, dst_rel, expected_version=seen, operation_id=operation_id)
    result = receipt_to_commit_result(receipt)
    extra = dict(result.extra)
    extra.setdefault("source", src_rel)
    return CommitResult(
        path=result.path,
        content_version=result.content_version,
        previous_version=result.previous_version,
        status=result.status,
        warnings=result.warnings,
        bytes_written=result.bytes_written,
        extra=extra,
    )


def commit_workbook(
    *,
    guard: FileAccessGuard,
    file_path: str,
    mutate_fn: Callable[[Any], None],
    expected_version: str | None = None,
    create: bool = False,
    operation_id: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """加载（或新建）openpyxl Workbook，执行 ``mutate_fn``，再原子提交。"""
    del record_history
    from io import BytesIO

    from openpyxl import Workbook, load_workbook

    from excelmanus.workspace.file_service import receipt_to_commit_result, service_for_guard

    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc
    rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
    suffix = dest.suffix.lower()

    def builder(before: bytes | None) -> bytes:
        fresh_default_sheets: list[str] = []
        if before is None:
            wb = Workbook()
            fresh_default_sheets = list(wb.sheetnames)
        else:
            wb = load_workbook(BytesIO(before), keep_vba=suffix in {".xlsm", ".xlsb"})
        mutate_fn(wb)
        # 新建簿自带的默认空表（如 "Sheet"）若全程未动且已有其它表，剔除免留空壳。
        if fresh_default_sheets and len(wb.sheetnames) > 1:
            for name in fresh_default_sheets:
                if name not in wb.sheetnames:
                    continue
                ws = wb[name]
                if ws.max_row <= 1 and ws.max_column <= 1 and ws["A1"].value is None:
                    del wb[name]
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    try:
        receipt = service_for_guard(guard).update_with_builder(
            rel,
            builder,
            expected_version=expected_version,
            create=create,
            operation_id=operation_id,
            intent={"kind": "workbook_builder", "path": rel},
        )
        live = dest if dest.is_file() else (Path(guard.workspace_root) / receipt.primary_path())
        data_len = live.stat().st_size if live.is_file() else 0
        return receipt_to_commit_result(receipt, bytes_written=data_len)
    except CommitError as exc:
        if exc.code == "NOT_FOUND":
            raise CommitError("PATH_INVALID", exc.message, fields=exc.fields) from exc
        raise
    except Exception as exc:
        raise CommitError("SAVE_FAILED", f"写入 {rel} 失败：{exc}") from exc
