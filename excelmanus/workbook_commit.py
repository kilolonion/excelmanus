"""应用内工作簿提交的唯一入口。

调用方在 ``mutate_fn`` 里改 openpyxl Workbook（或返回要写入的字节）。
本模块负责：路径守卫、文件锁、期望版本校验、同目录临时文件 + ``os.replace``、
返回 ``sha256:<hex>`` 内容版本。

保证上限：锁只约束合作写入者（含沙盒 wrapper 的同名 ``.em-lock``）；
不阻止锁外进程在校验后抢写。
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import tempfile
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


def normalize_version_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip()


def remember_content_version(path: str, version: str | None) -> None:
    """记录当前执行上下文里某文件最近一次读到/写到的内容版本。

    键只使用规范化相对路径，不用 basename 兜底（同名文件会串版本）。
    """
    key = normalize_version_path(path)
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
    return bag.get(normalize_version_path(path))


def seed_seen_versions(mapping: dict[str, str] | None) -> None:
    _seen_versions.set({})
    for path, version in (mapping or {}).items():
        remember_content_version(path, version)


def export_seen_versions() -> dict[str, str]:
    return dict(_seen_versions.get() or {})

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
    os.replace(str(src_tmp), str(dest))


def _record_revision_pair(
    workspace_root: Path,
    rel: str,
    before_bytes: bytes | None,
    after_bytes: bytes,
) -> None:
    """Hidden history after a successful AtomicPublish. Never fail the user commit."""
    try:
        from excelmanus.workspace.revisions import RevisionStore

        RevisionStore(workspace_root).capture_edit_pair(
            rel,
            before_bytes=before_bytes,
            after_bytes=after_bytes,
        )
    except Exception:
        logger.debug("revision capture failed for %s", rel, exc_info=True)


def commit_bytes(
    *,
    guard: FileAccessGuard,
    file_path: str,
    data: bytes,
    expected_version: str | None = None,
    record_history: bool = True,
) -> CommitResult:
    """把 ``data`` 原子写入工作区内路径。

    ``expected_version`` 为 ``None`` 时允许创建新文件；若目标已存在则要求匹配。
    传 ``"sha256:..."`` 时必须与磁盘当前内容一致，否则 ``VERSION_CONFLICT``。
    """
    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc

    rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
    from excelmanus.workspace.identity import is_reserved_relative

    if is_reserved_relative(rel):
        raise CommitError("PATH_INVALID", f"reserved namespace: {rel}", fields={"path": rel})
    lock_path = lock_path_for(dest)
    fh = _acquire_lock(lock_path)
    tmp_path: Path | None = None
    try:
        before_bytes = dest.read_bytes() if dest.is_file() else None
        current = content_version_of(before_bytes) if before_bytes is not None else None
        if expected_version is None:
            if current is not None:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 已存在（{current}），创建写入必须带 expected_version",
                    fields={"path": rel, "content_version": current},
                )
        else:
            if current is None:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 不存在，无法按版本 {expected_version} 更新",
                    fields={"path": rel, "expected_version": expected_version},
                )
            if current != expected_version:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 版本冲突：期望 {expected_version}，实际 {current}",
                    fields={
                        "path": rel,
                        "content_version": current,
                        "expected_version": expected_version,
                    },
                )

        fd, tmp_name = tempfile.mkstemp(suffix=dest.suffix or ".xlsx", dir=str(dest.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        tmp_path.write_bytes(data)
        _atomic_replace(tmp_path, dest)
        tmp_path = None
        new_ver = content_version_of(data)
        if record_history:
            _record_revision_pair(guard.workspace_root, rel, before_bytes, data)
        return CommitResult(
            path=rel,
            content_version=new_ver,
            previous_version=current,
            status="committed",
            bytes_written=len(data),
        )
    except CommitError:
        raise
    except Exception as exc:
        raise CommitError("SAVE_FAILED", f"写入 {rel} 失败：{exc}") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        _release_lock(fh)


def commit_workbook(
    *,
    guard: FileAccessGuard,
    file_path: str,
    mutate_fn: Callable[[Any], None],
    expected_version: str | None = None,
    create: bool = False,
    record_history: bool = True,
) -> CommitResult:
    """加载（或新建）openpyxl Workbook，执行 ``mutate_fn``，再原子提交。

    ``create=True`` 且文件不存在时不校验 ``expected_version``。
    """
    from openpyxl import Workbook, load_workbook

    try:
        dest = guard.resolve_and_validate(file_path)
    except SecurityViolationError as exc:
        raise CommitError("PATH_INVALID", str(exc)) from exc

    lock_path = lock_path_for(dest)
    fh = _acquire_lock(lock_path)
    tmp_path: Path | None = None
    rel = str(dest.relative_to(guard.workspace_root)).replace("\\", "/")
    from excelmanus.workspace.identity import is_reserved_relative

    if is_reserved_relative(rel):
        _release_lock(fh)
        raise CommitError("PATH_INVALID", f"reserved namespace: {rel}", fields={"path": rel})
    try:
        before_bytes = dest.read_bytes() if dest.is_file() else None
        current = content_version_of(before_bytes) if before_bytes is not None else None
        if dest.is_file():
            if expected_version is None:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"更新 {rel} 必须提供 expected_version",
                    fields={"path": rel, "content_version": current},
                )
            elif current != expected_version:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 版本冲突：期望 {expected_version}，实际 {current}",
                    fields={
                        "path": rel,
                        "content_version": current,
                        "expected_version": expected_version,
                    },
                )
            keep_vba = dest.suffix.lower() in {".xlsm", ".xlsb"}
            wb = load_workbook(str(dest), keep_vba=keep_vba)
        else:
            if not create:
                raise CommitError("PATH_INVALID", f"文件不存在：{rel}")
            wb = Workbook()
            current = None

        mutate_fn(wb)

        fd, tmp_name = tempfile.mkstemp(suffix=dest.suffix or ".xlsx", dir=str(dest.parent))
        os.close(fd)
        tmp_path = Path(tmp_name)
        wb.save(str(tmp_path))
        data = tmp_path.read_bytes()
        _atomic_replace(tmp_path, dest)
        tmp_path = None
        if record_history:
            _record_revision_pair(guard.workspace_root, rel, before_bytes, data)
        return CommitResult(
            path=rel,
            content_version=content_version_of(data),
            previous_version=current,
            status="committed",
            bytes_written=len(data),
        )
    except CommitError:
        raise
    except Exception as exc:
        raise CommitError("SAVE_FAILED", f"写入 {rel} 失败：{exc}") from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        _release_lock(fh)
