"""WorkspaceFileService — the only formal user-file mutation entry.

See docs/design/architecture-refactor-20260913/reviews/batch-04-design.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from excelmanus.logger import get_logger
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.workbook_commit import (
    CommitError,
    CommitResult,
    _acquire_lock,
    _release_lock,
    content_version_of,
    lock_path_for,
    normalize_version_path,
    resolve_expected_version,
)
from excelmanus.workspace.identity import (
    IdentityError,
    is_reserved_relative,
    is_sensitive_relative,
    resolve_canonical,
)
from excelmanus.workspace.revisions import (
    RevisionIntegrityError,
    RevisionRecord,
    RevisionStore,
    content_sha256,
)
from excelmanus.workspace.txlog import FileBusyError, TxLog, read_json, replace_with_retry, write_json_atomic

logger = get_logger("workspace.file_service")

Op = Literal["create", "update", "move", "delete", "restore", "mkdir"]
ReceiptState = Literal["committed", "aborted", "failed_partial", "conflict", "rejected"]
HistoryState = Literal["recorded", "pending_recover", "missing"]
PublishStatus = Literal["pending", "published", "skipped", "conflict", "failed"]

_EMPTY_SHA = content_sha256(b"")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: str) -> str:
    return normalize_version_path(path)


@dataclass(frozen=True)
class MutationTarget:
    path: str
    op: Op
    lineage_id: str | None
    exists_before: bool
    exists_after: bool
    before_version: str | None
    after_version: str | None
    from_path: str | None = None
    to_path: str | None = None
    restore_revision_id: str | None = None
    publish_status: PublishStatus = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadDependency:
    path: str
    version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MutationReceipt:
    operation_id: str
    tx_id: str
    workspace_key: str
    state: ReceiptState
    history_state: HistoryState
    targets: tuple[MutationTarget, ...]
    read_dependencies: tuple[ReadDependency, ...] = ()
    published_paths: tuple[str, ...] = ()
    retryable: bool = False
    resumeable: bool = False
    failure_class: str | None = None
    error_code: str | None = None
    message: str = ""
    consistency: Literal["local_commit", "external_unverified"] = "local_commit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "tx_id": self.tx_id,
            "workspace_key": self.workspace_key,
            "state": self.state,
            "history_state": self.history_state,
            "targets": [t.to_dict() for t in self.targets],
            "read_dependencies": [d.to_dict() for d in self.read_dependencies],
            "published_paths": list(self.published_paths),
            "retryable": self.retryable,
            "resumeable": self.resumeable,
            "failure_class": self.failure_class,
            "error_code": self.error_code,
            "message": self.message,
            "consistency": self.consistency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MutationReceipt":
        targets = tuple(
            MutationTarget(
                path=str(t.get("path") or ""),
                op=t.get("op") or "update",
                lineage_id=t.get("lineage_id"),
                exists_before=bool(t.get("exists_before")),
                exists_after=bool(t.get("exists_after")),
                before_version=t.get("before_version"),
                after_version=t.get("after_version"),
                from_path=t.get("from_path"),
                to_path=t.get("to_path"),
                restore_revision_id=t.get("restore_revision_id"),
                publish_status=t.get("publish_status") or "pending",
            )
            for t in (data.get("targets") or [])
            if isinstance(t, dict)
        )
        deps = tuple(
            ReadDependency(path=str(d.get("path") or ""), version=str(d.get("version") or ""))
            for d in (data.get("read_dependencies") or [])
            if isinstance(d, dict)
        )
        return cls(
            operation_id=str(data.get("operation_id") or ""),
            tx_id=str(data.get("tx_id") or ""),
            workspace_key=str(data.get("workspace_key") or ""),
            state=data.get("state") or "aborted",
            history_state=data.get("history_state") or "missing",
            targets=targets,
            read_dependencies=deps,
            published_paths=tuple(str(p) for p in (data.get("published_paths") or [])),
            retryable=bool(data.get("retryable")),
            resumeable=bool(data.get("resumeable")),
            failure_class=data.get("failure_class"),
            error_code=data.get("error_code"),
            message=str(data.get("message") or ""),
            consistency=data.get("consistency") or "local_commit",
        )

    def primary_path(self) -> str:
        if not self.targets:
            return ""
        last = self.targets[-1]
        return last.to_path or last.path

    def primary_version(self) -> str:
        if not self.targets:
            return ""
        last = self.targets[-1]
        return last.after_version or ""

    def to_commit_result(self) -> CommitResult:
        path = self.primary_path()
        after = self.primary_version()
        before = self.targets[-1].before_version if self.targets else None
        status: Literal["committed", "conflict", "rejected", "failed"]
        if self.state == "committed":
            status = "committed"
        elif self.error_code == "VERSION_CONFLICT":
            status = "conflict"
        elif self.state in {"rejected", "aborted"}:
            status = "rejected"
        else:
            status = "failed"
        extra = {
            "receipt": self.to_dict(),
            "lineage_id": self.targets[-1].lineage_id if self.targets else None,
        }
        if self.targets and self.targets[-1].from_path:
            extra["source"] = self.targets[-1].from_path
        warnings: tuple[str, ...] = ()
        if self.state == "committed" and self.history_state != "recorded":
            warnings = (self.history_state,)
        return CommitResult(
            path=path,
            content_version=after if after is not None else "",
            previous_version=before,
            status=status,
            warnings=warnings,
            bytes_written=0,
            extra=extra,
        )


@dataclass
class TargetSpec:
    op: Op
    path: str
    data: bytes | None = None
    expected_version: str | None = None
    from_path: str | None = None
    to_path: str | None = None
    restore_revision_id: str | None = None
    restore_missing: bool = False
    observe_live: bool = False
    read_dependency: ReadDependency | None = None
    builder: Callable[[bytes | None], bytes] | None = None
    intent: dict[str, Any] | None = None
    event_context: dict[str, Any] | None = None


@dataclass
class _PreparedTarget:
    spec: TargetSpec
    rel: str
    dest: Path
    lineage_id: str
    exists_before: bool
    exists_after: bool
    before_bytes: bytes | None
    after_bytes: bytes | None
    before_version: str | None
    after_version: str | None
    from_rel: str | None = None
    to_rel: str | None = None
    src_dest: Path | None = None
    publish_status: PublishStatus = "pending"
    is_dir: bool = False


class LineageIndex:
    """Path ↔ lineage projection. Not a live identity registry."""

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root / ".excelmanus" / "lineages"
        self._store = RevisionStore(workspace_root)

    def lineage_path(self, lineage_id: str) -> Path:
        safe = str(lineage_id).replace(":", "_").replace("/", "_")
        return self._root / f"{safe}.json"

    def path_index_path(self, rel: str) -> Path:
        return self._root / "by-path" / f"{self._store.path_key(rel)}.json"

    def load_lineage(self, lineage_id: str) -> dict[str, Any]:
        data = read_json(self.lineage_path(lineage_id))
        if data:
            return data
        return {
            "lineage_id": lineage_id,
            "closed": False,
            "current_path": "",
            "records": [],
        }

    def save_lineage(self, data: dict[str, Any]) -> None:
        write_json_atomic(self.lineage_path(str(data["lineage_id"])), data)

    def load_path(self, rel: str) -> dict[str, Any]:
        data = read_json(self.path_index_path(rel))
        if data:
            return data
        return {"path": rel, "current_lineage_id": None, "closed_lineage_ids": []}

    def save_path(self, rel: str, data: dict[str, Any]) -> None:
        write_json_atomic(self.path_index_path(rel), data)

    def current_lineage_id(self, rel: str) -> str | None:
        return self.load_path(rel).get("current_lineage_id")


class WorkspaceFileService:
    """Formal mutation entry for one workspace root."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.guard = FileAccessGuard(str(self.root))
        self.store = RevisionStore(self.root)
        self.txlog = TxLog(self.root)
        self.lineages = LineageIndex(self.root)

    def workspace_key(self) -> str:
        try:
            from excelmanus.tools.context import current_call

            call = current_call()
            if call is not None:
                return call.binding.workspace.identity_key()
        except Exception:
            pass
        return f"path:{self.root}"

    def locate(self, file_path: str) -> dict[str, Any]:
        rel = self._canonical(file_path)
        dest = self.root / rel
        live = dest.is_file()
        lineage_id = self.lineages.current_lineage_id(rel)
        if live and not lineage_id:
            lineage_id = self._ensure_legacy_lineage(rel)
        version = content_version_of(dest.read_bytes()) if live else None
        return {
            "path": rel,
            "exists": live,
            "lineage_id": lineage_id,
            "version": version,
        }

    def list_history(self, file_path: str) -> list[RevisionRecord]:
        # Listing can migrate legacy indexes. Serialize it with mutations and GC.
        with self._workspace_lock():
            return self._list_history(file_path)

    def _list_history(self, file_path: str) -> list[RevisionRecord]:
        rel = self._canonical(file_path)
        lineage_id = self.lineages.current_lineage_id(rel)
        if not lineage_id:
            closed = self.lineages.load_path(rel).get("closed_lineage_ids") or []
            lineage_id = closed[-1] if closed else self._ensure_legacy_lineage(rel, create=False)
        if not lineage_id:
            return [r for r in self.store.list(rel) if r.committed]
        data = self.lineages.load_lineage(lineage_id)
        records: dict[str, RevisionRecord] = {}
        paths = {rel}
        for item in data.get("records") or []:
            path = str(item.get("path") or rel)
            paths.add(path)
            try:
                rec = self.store.get(path, str(item.get("revision_id") or ""))
            except RevisionIntegrityError:
                continue
            if rec is not None and rec.committed and (not rec.lineage_id or rec.lineage_id == lineage_id):
                records[rec.id] = rec
        # Records own history; the lineage index is only a projection. Include
        # checkpoints written by older releases (and migrated overlay backups)
        # even when their index entry was never written.
        for path in paths:
            items = self.store.list(path)
            for index, rec in enumerate(items):
                owner = rec.lineage_id
                if not owner and rec.reason == "checkpoint":
                    before = next((r for r in reversed(items[:index]) if r.lineage_id), None)
                    after = next((r for r in items[index + 1:] if r.lineage_id), None)
                    owner = before.lineage_id if before else after.lineage_id if after else lineage_id
                if owner == lineage_id:
                    records[rec.id] = rec
        return sorted(records.values(), key=lambda r: (r.created_at or "", r.sequence, r.id))

    def checkpoint(
        self, file_path: str, *, expected_version: str, label: str | None = None,
    ) -> RevisionRecord:
        """Capture and index the same locked, version-checked bytes."""
        rel = self._canonical(file_path, must_exist=True)
        dest = self.root / rel
        with self._workspace_lock():
            handle = _acquire_lock(lock_path_for(dest))
            try:
                data = dest.read_bytes()
                actual = content_version_of(data)
                if actual != expected_version:
                    raise CommitError("STALE_SNAPSHOT", "检查点源文件版本已变化", fields={"content_version": actual})
                lineage_id = self._ensure_legacy_lineage(rel)
                digest = content_sha256(data)
                existing = next(
                    (item for item in self.store.list(rel)
                     if item.reason == "checkpoint" and item.label == label and item.sha256 == digest
                     and item.lineage_id == lineage_id),
                    None,
                )
                if existing is not None:
                    self._index_record(lineage_id, existing, current_path=rel, closed=False)
                    return existing
                rec = self.store.checkpoint(rel, data, label=label, lineage_id=lineage_id)
                self._index_record(lineage_id, rec, current_path=rel, closed=False)
                return rec
            finally:
                _release_lock(handle)

    def read_history(self, file_path: str, revision_id: str) -> tuple[RevisionRecord, bytes]:
        rel = self._canonical(file_path)
        with self._workspace_lock():
            rec, data = self._load_revision(rel, revision_id)
            if not rec.committed:
                raise CommitError("NOT_FOUND", "修订尚未提交")
            return rec, data

    def delete_checkpoint(self, file_path: str, revision_id: str) -> None:
        """Explicitly remove one checkpoint; never remove automatic recovery history."""
        rel = self._canonical(file_path)
        with self._workspace_lock():
            rec = next((r for r in self._list_history(rel) if r.id == revision_id), None)
            if rec is None:
                raise CommitError("NOT_FOUND", "检查点不存在")
            if rec.reason != "checkpoint":
                raise CommitError("INVALID_ARGS", "只能手动删除检查点，自动历史由保留策略管理")
            self.store.delete_record(rec)

    def create(
        self,
        file_path: str,
        data: bytes,
        *,
        operation_id: str | None = None,
    ) -> MutationReceipt:
        return self.apply_batch(
            [TargetSpec(op="create", path=file_path, data=data, expected_version=None)],
            operation_id=operation_id,
        )

    def update(
        self,
        file_path: str,
        data: bytes,
        *,
        expected_version: str | None,
        operation_id: str | None = None,
        observe_live: bool = False,
    ) -> MutationReceipt:
        return self.apply_batch(
            [
                TargetSpec(
                    op="update",
                    path=file_path,
                    data=data,
                    expected_version=expected_version,
                    observe_live=observe_live,
                )
            ],
            operation_id=operation_id,
        )

    def put_bytes(
        self,
        file_path: str,
        data: bytes,
        *,
        expected_version: str | None,
        operation_id: str | None = None,
        observe_live: bool = False,
    ) -> MutationReceipt:
        dest = self.root / self._canonical(file_path)
        if dest.is_file():
            return self.update(
                file_path,
                data,
                expected_version=expected_version,
                operation_id=operation_id,
                observe_live=observe_live,
            )
        return self.create(file_path, data, operation_id=operation_id)

    def update_with_builder(
        self,
        file_path: str,
        builder: Callable[[bytes | None], bytes],
        *,
        expected_version: str | None,
        create: bool = False,
        operation_id: str | None = None,
        intent: dict[str, Any] | None = None,
        event_context: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        rel = self._canonical(file_path)
        dest = self.root / rel
        if dest.is_file():
            return self.apply_batch(
                [
                    TargetSpec(
                        op="update",
                        path=file_path,
                        expected_version=expected_version,
                        builder=builder,
                        intent=intent,
                        event_context=event_context,
                    )
                ],
                operation_id=operation_id,
            )
        if not create:
            raise CommitError("NOT_FOUND", f"文件不存在：{rel}", fields={"path": rel})
        return self.apply_batch(
            [TargetSpec(op="create", path=file_path, builder=builder, intent=intent, event_context=event_context)],
            operation_id=operation_id,
        )

    def move(
        self,
        source: str,
        destination: str,
        *,
        expected_version: str | None,
        operation_id: str | None = None,
        observe_live: bool = False,
    ) -> MutationReceipt:
        return self.apply_batch(
            [
                TargetSpec(
                    op="move",
                    path=source,
                    to_path=destination,
                    expected_version=expected_version,
                    observe_live=observe_live,
                )
            ],
            operation_id=operation_id,
        )

    def move_tree(
        self,
        source: str,
        destination: str,
        *,
        observe_live: bool = True,
        operation_id: str | None = None,
    ) -> MutationReceipt:
        src_rel = self._canonical(source)
        dst_rel = self._canonical(destination)
        src = self.root / src_rel
        dst = self.root / dst_rel
        if src.is_file():
            return self.move(
                src_rel,
                dst_rel,
                expected_version=None,
                observe_live=observe_live,
                operation_id=operation_id,
            )
        if not src.is_dir():
            raise CommitError("NOT_FOUND", f"路径不存在：{src_rel}", fields={"path": src_rel})
        if dst.exists():
            raise CommitError("FILE_EXISTS", f"目标路径已存在：{dst_rel}", fields={"path": dst_rel})
        specs: list[TargetSpec] = []
        for child in sorted(src.rglob("*")):
            if child.is_file():
                child_rel = child.relative_to(self.root).as_posix()
                dest_rel = f"{dst_rel}/{child.relative_to(src).as_posix()}"
                specs.append(
                    TargetSpec(
                        op="move",
                        path=child_rel,
                        to_path=dest_rel,
                        observe_live=observe_live,
                    )
                )
        if not specs:
            dst.mkdir(parents=True, exist_ok=True)
            try:
                src.rmdir()
            except OSError:
                pass
            return self._empty_ok_receipt(operation_id, dst_rel, op="move")
        receipt = self.apply_batch(specs, operation_id=operation_id)
        self._rm_empty_dirs(src)
        return receipt

    def delete(
        self,
        file_path: str,
        *,
        expected_version: str | None,
        operation_id: str | None = None,
        observe_live: bool = False,
    ) -> MutationReceipt:
        return self.apply_batch(
            [
                TargetSpec(
                    op="delete",
                    path=file_path,
                    expected_version=expected_version,
                    observe_live=observe_live,
                )
            ],
            operation_id=operation_id,
        )

    def delete_tree(
        self,
        file_path: str,
        *,
        observe_live: bool = True,
        operation_id: str | None = None,
    ) -> MutationReceipt:
        rel = self._canonical(file_path, must_exist=False)
        dest = self.root / rel
        specs: list[TargetSpec] = []
        if dest.is_file():
            specs.append(TargetSpec(op="delete", path=rel, observe_live=observe_live))
        elif dest.is_dir():
            for child in sorted(dest.rglob("*"), reverse=True):
                if child.is_file():
                    child_rel = child.relative_to(self.root).as_posix()
                    specs.append(
                        TargetSpec(op="delete", path=child_rel, observe_live=observe_live)
                    )
            if not specs:
                dest.rmdir()
                return self._empty_ok_receipt(operation_id, rel, op="delete")
        else:
            raise CommitError("NOT_FOUND", f"路径不存在：{rel}", fields={"path": rel})
        receipt = self.apply_batch(specs, operation_id=operation_id)
        if dest.is_dir():
            self._rm_empty_dirs(dest)
        return receipt

    def mkdir(self, file_path: str, *, operation_id: str | None = None) -> MutationReceipt:
        return self.apply_batch(
            [TargetSpec(op="mkdir", path=file_path)],
            operation_id=operation_id,
        )

    def restore(
        self,
        file_path: str,
        revision_id: str,
        *,
        expected_version: str | None,
        restore_missing: bool = False,
        operation_id: str | None = None,
        event_context: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        return self.apply_batch(
            [
                TargetSpec(
                    op="restore",
                    path=file_path,
                    restore_revision_id=revision_id,
                    expected_version=expected_version,
                    restore_missing=restore_missing,
                    event_context=event_context,
                )
            ],
            operation_id=operation_id,
        )

    @contextmanager
    def _workspace_lock(self):
        # One lock order for mutations AND recovery. A live prepared transaction
        # cannot be mistaken for a crashed one by another request/process.
        handle = _acquire_lock(self.txlog.tx_root / "workspace.lock")
        try:
            yield
        finally:
            _release_lock(handle)

    def apply_batch(
        self,
        specs: list[TargetSpec],
        *,
        operation_id: str | None = None,
        actor: str = "runtime",
        read_dependencies: list[ReadDependency] | None = None,
    ) -> MutationReceipt:
        if not specs:
            raise CommitError("INVALID_ARGS", "empty mutation batch")
        if operation_id and any(s.builder is not None and s.intent is None for s in specs):
            raise CommitError("INVALID_ARGS", "replayable builder requires a canonical intent descriptor")
        op_id = (operation_id or "").strip() or secrets.token_hex(16)
        from excelmanus.code_mode import reject_new_foreground_commit

        blocked = reject_new_foreground_commit()
        if blocked:
            raise CommitError("CANCELLED", blocked)
        deps = list(read_dependencies or []) + [s.read_dependency for s in specs if s.read_dependency]
        digest = self._intent_digest(specs, deps)
        with self._workspace_lock():
            self._recover_locked()
            existing = self.txlog.read_receipt(op_id)
            if existing:
                if existing.get("intent_digest") != digest:
                    raise CommitError("OPERATION_ID_REUSED", f"operation_id {op_id} 已用于不同意图")
                return MutationReceipt.from_dict(existing)
            # Do not let a new intent race or supersede unfinished recovery.
            for old in self.txlog.iter_intents():
                if old.get("state") == "failed_partial" or old.get("history_state") == "pending_recover":
                    raise CommitError("RECOVERY_REQUIRED", "工作区仍有待恢复事务，请先恢复或显式终止", fields={"tx_id": old.get("tx_id")})
            with ExitStack() as stack:
                for path in sorted(self._lock_paths(specs)):
                    handle = _acquire_lock(lock_path_for(path))
                    stack.callback(_release_lock, handle)
                for dep in deps:
                    path = self.root / self._canonical(dep.path)
                    actual = content_version_of(path.read_bytes()) if path.is_file() else None
                    if actual != dep.version:
                        raise CommitError(
                            "VERSION_CONFLICT",
                            f"读取依赖已变化：{dep.path}",
                            fields={
                                "path": self._canonical(dep.path),
                                "expected_version": dep.version,
                                "content_version": actual or "",
                            },
                        )
                prepared = [self._prepare_target(spec) for spec in specs]
                targets = [p.rel for p in prepared]
                sources = [p.from_rel for p in prepared if p.from_rel]
                if len(set(targets)) != len(targets) or set(sources) & set(targets):
                    raise CommitError("INVALID_ARGS", "同一批次目标重叠，请合并同文件操作")
                tx_id = secrets.token_hex(16)
                intent = self._write_prepared(tx_id, op_id, digest, actor, prepared, deps)
                try:
                    self._publish(tx_id, prepared, intent)
                except CommitError as exc:
                    intent["state"] = "failed_partial" if any(p.publish_status == "published" for p in prepared) else "aborted"
                    intent["error_code"] = exc.code
                    intent["message"] = str(exc)
                    self.txlog.write_intent(intent)
                    receipt = self._finish_intent(intent, prepared)
                    if receipt.state == "aborted":
                        raise exc
                    return receipt
                intent["state"] = "committed"
                return self._finish_intent(intent, prepared)

    def _finish_intent(self, intent: dict[str, Any], prepared: list[_PreparedTarget]) -> MutationReceipt:
        published = [p for p in prepared if p.publish_status == "published"]
        history = self._project_history(intent["tx_id"], published) if published else "recorded"
        intent["history_state"] = history
        intent["targets"] = [self._intent_target(p) for p in prepared]
        self.txlog.write_intent(intent)
        events = self._outbox_events(published)
        self.txlog.append_outbox(intent["tx_id"], events)
        error = None
        if intent["state"] != "committed":
            error = CommitError(intent.get("error_code") or "SAVE_FAILED", intent.get("message") or "事务未全部完成")
        receipt = self._receipt_from_prepared(
            intent["operation_id"], intent["tx_id"], prepared,
            [ReadDependency(**d) for d in intent.get("read_dependencies", [])],
            state=intent["state"], history_state=history, error=error,
            retryable=False, resumeable=intent["state"] == "failed_partial" or history != "recorded",
        )
        self.txlog.write_receipt(receipt.operation_id, {**receipt.to_dict(), "intent_digest": intent["intent_digest"]})
        if receipt.state in {"committed", "aborted"} and history == "recorded":
            self.txlog.discard_recovery_blobs(receipt.tx_id)
        try:
            # 机会式 GC：回收历史终态事务残留的 before/after 字节（R9 容量要求）。
            self.txlog.prune_terminal_blobs()
        except Exception:
            logger.debug("tx blob 回收失败，忽略", exc_info=True)
        return receipt

    def recover(self) -> list[MutationReceipt]:
        with self._workspace_lock():
            return self._recover_locked()

    def get_receipt(self, operation_id: str, *, recover: bool = True) -> MutationReceipt | None:
        """Read an idempotent mutation receipt, optionally settling recovery first."""
        with self._workspace_lock():
            if recover:
                self._recover_locked()
            raw = self.txlog.read_receipt(operation_id)
            return MutationReceipt.from_dict(raw) if raw else None

    def deliver_outbox(self, consumer: Callable[[dict[str, Any]], None], *, consumer_id: str) -> int:
        """Deliver committed effects at least once; acknowledge only after success."""
        delivered = 0
        key = hashlib.sha256(consumer_id.encode()).hexdigest()
        with self._workspace_lock():
            for intent in sorted(self.txlog.iter_intents(), key=lambda i: i.get("created_at", "")):
                tx_id = intent["tx_id"]
                ack_path = self.txlog.dir_for(tx_id) / f"outbox-{key}.json"
                ack = read_json(ack_path) or {}
                done = set(ack.get("delivered", []))
                for event in self.txlog.read_outbox(tx_id):
                    event_id = event.get("event_id")
                    if not event_id or event_id in done:
                        continue
                    consumer({**event, "workspace_root": str(self.root)})
                    done.add(event_id)
                    write_json_atomic(ack_path, {"delivered": sorted(done)})
                    delivered += 1
        return delivered

    def _recover_locked(self) -> list[MutationReceipt]:
        recovered = []
        for intent in sorted(self.txlog.iter_intents(), key=lambda i: i.get("created_at", "")):
            if intent.get("state") in {"committed", "aborted"} and intent.get("history_state") == "recorded" and self.txlog.read_receipt(intent.get("operation_id", "")):
                continue
            try:
                receipt = self._recover_intent(intent)
                if receipt is not None:
                    recovered.append(receipt)
            except Exception:
                logger.warning("tx recover failed: %s", intent.get("tx_id"), exc_info=True)
        return recovered

    def abort_recovery(self, operation_id: str) -> MutationReceipt:
        """Explicitly abandon remaining writes; already published files/history stay."""
        with self._workspace_lock():
            intent = next((i for i in self.txlog.iter_intents() if i.get("operation_id") == operation_id), None)
            if intent is None:
                raise CommitError("NOT_FOUND", "找不到待恢复事务")
            if intent.get("state") == "committed":
                return MutationReceipt.from_dict(self.txlog.read_receipt(operation_id) or {})
            prepared = self._targets_from_intent(intent)
            intent["state"] = "aborted"
            intent["message"] = "已终止剩余操作；已发布文件保留"
            return self._finish_intent(intent, prepared)

    def _lock_paths(self, specs: list[TargetSpec]) -> set[Path]:
        paths: set[Path] = set()
        for spec in specs:
            if spec.op == "move":
                paths.add(self.root / self._canonical(spec.path))
                paths.add(self.root / self._canonical(spec.to_path or ""))
            else:
                paths.add(self.root / self._canonical(spec.path, must_exist=False))
        return paths

    def _canonical(self, raw: str, *, must_exist: bool = False) -> str:
        try:
            ident = resolve_canonical(self.root, raw)
        except IdentityError as exc:
            raise CommitError("PATH_INVALID", str(exc), fields={"path": raw}) from exc
        rel = ident.relative
        if is_reserved_relative(rel) or is_sensitive_relative(rel):
            raise CommitError("PATH_INVALID", f"reserved namespace: {rel}", fields={"path": rel})
        self._reject_symlink_components(raw)
        try:
            self.guard.resolve_and_validate(rel)
        except SecurityViolationError as exc:
            raise CommitError("PATH_INVALID", str(exc), fields={"path": rel}) from exc
        if must_exist and not (self.root / rel).exists():
            raise CommitError("NOT_FOUND", f"路径不存在：{rel}", fields={"path": rel})
        return rel

    def _reject_symlink_components(self, raw: str) -> None:
        """字面路径上任一已存在组件为符号链接（或中间组件非目录）即拒绝。

        resolve_canonical 折叠符号链接后 containment 仍可能通过（链接
        目标仍在 root 内时），必须按 lstat 语义逐段检查**字面**输入
        路径（而非 ident.relative），防止 mkdir/write 穿透符号链接
        逃逸到非预期位置。
        """
        raw = str(raw).replace("\\", "/").removeprefix("<path>/")
        literal = os.path.normpath(
            raw if os.path.isabs(raw) else os.path.join(self.root, raw)
        )
        try:
            literal_rel = os.path.relpath(literal, self.root)
        except ValueError:
            return  # 不同盘符等极端情形交给上游 containment 判定
        if literal_rel.startswith(".."):
            return  # 越界已由 resolve_canonical 拒绝
        parts = Path(literal_rel).parts
        cursor = self.root
        for i, part in enumerate(parts):
            cursor = cursor / part
            if os.path.islink(cursor) or getattr(os.path, "isjunction", lambda p: False)(cursor):
                raise CommitError(
                    "PATH_INVALID",
                    f"路径包含符号链接组件：{raw}",
                    fields={"path": raw},
                )
            if (
                i < len(parts) - 1
                and os.path.lexists(cursor)
                and not os.path.isdir(cursor)
            ):
                raise CommitError(
                    "PATH_INVALID",
                    f"路径中间组件不是目录：{raw}",
                    fields={"path": raw},
                )

    def _resolve_expected(
        self,
        rel: str,
        expected_version: str | None,
        *,
        exists: bool,
        observe_live: bool,
        current: str | None = None,
    ) -> str | None:
        if observe_live and exists:
            return current
        return resolve_expected_version(rel, expected_version, exists=exists, abs_path=self.root / rel)

    def _prepare_target(self, spec: TargetSpec) -> _PreparedTarget:
        if spec.op == "move":
            return self._prepare_move(spec)
        rel = self._canonical(spec.path)
        dest = self.root / rel
        if spec.op == "mkdir":
            if dest.is_file():
                raise CommitError("FILE_EXISTS", f"目标已是文件：{rel}", fields={"path": rel})
            return _PreparedTarget(
                spec=spec,
                rel=rel,
                dest=dest,
                lineage_id="",
                exists_before=dest.is_dir(),
                exists_after=True,
                before_bytes=None,
                after_bytes=None,
                before_version=None,
                after_version=None,
                is_dir=True,
            )
        if spec.op == "create":
            if dest.exists():
                current = content_version_of(dest.read_bytes()) if dest.is_file() else None
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 已存在（{current}），创建写入必须带 expected_version",
                    fields={"path": rel, "content_version": current},
                )
            data = spec.builder(None) if spec.builder is not None else (spec.data if spec.data is not None else b"")
            return _PreparedTarget(
                spec=spec,
                rel=rel,
                dest=dest,
                lineage_id=secrets.token_hex(16),
                exists_before=False,
                exists_after=True,
                before_bytes=None,
                after_bytes=data,
                before_version=None,
                after_version=content_version_of(data),
            )
        if spec.op == "restore":
            return self._prepare_restore(spec, rel, dest)
        exists = dest.is_file()
        before = dest.read_bytes() if exists else None
        current = content_version_of(before) if before is not None else None
        if spec.op == "update":
            if not exists:
                raise CommitError("NOT_FOUND", f"文件不存在：{rel}", fields={"path": rel})
            seen = self._resolve_expected(
                rel, spec.expected_version, exists=True, observe_live=spec.observe_live, current=current
            )
            if seen != current:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 版本冲突：期望 {seen}，实际 {current}",
                    fields={"path": rel, "content_version": current, "expected_version": seen},
                )
            data = spec.builder(before) if spec.builder is not None else (spec.data if spec.data is not None else b"")
            lineage_id = self.lineages.current_lineage_id(rel) or self._ensure_legacy_lineage(rel) or secrets.token_hex(16)
            return _PreparedTarget(
                spec=spec,
                rel=rel,
                dest=dest,
                lineage_id=lineage_id,
                exists_before=True,
                exists_after=True,
                before_bytes=before,
                after_bytes=data,
                before_version=current,
                after_version=content_version_of(data),
            )
        if spec.op == "delete":
            if dest.is_dir() and not dest.is_file():
                return _PreparedTarget(
                    spec=spec,
                    rel=rel,
                    dest=dest,
                    lineage_id="",
                    exists_before=True,
                    exists_after=False,
                    before_bytes=None,
                    after_bytes=None,
                    before_version=None,
                    after_version=None,
                    is_dir=True,
                )
            if not exists:
                raise CommitError("NOT_FOUND", f"文件不存在：{rel}", fields={"path": rel})
            seen = self._resolve_expected(
                rel, spec.expected_version, exists=True, observe_live=spec.observe_live, current=current
            )
            if seen != current:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 版本冲突：期望 {seen}，实际 {current}",
                    fields={"path": rel, "content_version": current, "expected_version": seen},
                )
            lineage_id = self.lineages.current_lineage_id(rel) or self._ensure_legacy_lineage(rel) or secrets.token_hex(16)
            return _PreparedTarget(
                spec=spec,
                rel=rel,
                dest=dest,
                lineage_id=lineage_id,
                exists_before=True,
                exists_after=False,
                before_bytes=before,
                after_bytes=before,
                before_version=current,
                after_version=None,
            )
        raise CommitError("PATH_INVALID", f"unsupported op: {spec.op}")

    def _prepare_move(self, spec: TargetSpec) -> _PreparedTarget:
        src_rel = self._canonical(spec.path)
        dst_rel = self._canonical(spec.to_path or "")
        src = self.root / src_rel
        dst = self.root / dst_rel
        if not src.is_file():
            raise CommitError("NOT_FOUND", f"源路径不是文件：{src_rel}", fields={"path": src_rel})
        if dst.exists():
            raise CommitError("FILE_EXISTS", f"目标路径已存在：{dst_rel}", fields={"path": dst_rel})
        before = src.read_bytes()
        current = content_version_of(before)
        seen = self._resolve_expected(
            src_rel, spec.expected_version, exists=True, observe_live=spec.observe_live, current=current
        )
        if seen != current:
            raise CommitError(
                "VERSION_CONFLICT",
                f"{src_rel} 版本冲突：期望 {seen}，实际 {current}",
                fields={"path": src_rel, "content_version": current, "expected_version": seen},
            )
        lineage_id = (
            self.lineages.current_lineage_id(src_rel)
            or self._ensure_legacy_lineage(src_rel)
            or secrets.token_hex(16)
        )
        return _PreparedTarget(
            spec=spec,
            rel=dst_rel,
            dest=dst,
            lineage_id=lineage_id,
            exists_before=True,
            exists_after=True,
            before_bytes=before,
            after_bytes=before,
            before_version=current,
            after_version=current,
            from_rel=src_rel,
            to_rel=dst_rel,
            src_dest=src,
        )

    def _prepare_restore(self, spec: TargetSpec, rel: str, dest: Path) -> _PreparedTarget:
        revision_id = (spec.restore_revision_id or "").strip()
        if not revision_id:
            raise CommitError("PATH_INVALID", "restore 需要 revision_id", fields={"path": rel})
        rec, blob = self._load_revision(rel, revision_id)
        exists = dest.is_file()
        before = dest.read_bytes() if exists else None
        current = content_version_of(before) if before is not None else None
        rec_lineage = (
            rec.lineage_id
            or self.lineages.current_lineage_id(rec.path)
            or f"legacy:{self.store.path_key(rec.path)}"
        )
        live_lineage = self.lineages.current_lineage_id(rel)
        if exists and live_lineage and live_lineage != rec_lineage:
            raise CommitError(
                "PATH_OCCUPIED",
                f"{rel} 已被另一文件占用，不能恢复旧身份",
                fields={"path": rel, "lineage_id": live_lineage},
            )
        if exists:
            seen = self._resolve_expected(
                rel, spec.expected_version, exists=True, observe_live=False, current=current
            )
            if seen != current:
                raise CommitError(
                    "VERSION_CONFLICT",
                    f"{rel} 版本冲突：期望 {seen}，实际 {current}",
                    fields={"path": rel, "content_version": current, "expected_version": seen},
                )
        else:
            allowed_missing = spec.restore_missing or (spec.expected_version or "").strip() == "absent"
            if not allowed_missing:
                raise CommitError("NOT_FOUND", "文件不存在，无法恢复", fields={"path": rel})
        return _PreparedTarget(
            spec=spec,
            rel=rel,
            dest=dest,
            lineage_id=rec_lineage,
            exists_before=exists,
            exists_after=True,
            before_bytes=before,
            after_bytes=blob,
            before_version=current,
            after_version=content_version_of(blob),
        )

    def _load_revision(self, rel: str, revision_id: str) -> tuple[RevisionRecord, bytes]:
        try:
            return self.store.read_revision(rel, revision_id)
        except (KeyError, FileNotFoundError, RevisionIntegrityError):
            for rec in self._list_history(rel):
                if rec.id == revision_id:
                    data = self.store.read_blob(rec.path, rec.sha256)
                    if data is None:
                        raise CommitError("NOT_FOUND", f"revision blob missing: {revision_id}")
                    return rec, data
            rec = self.store.get(rel, revision_id)
            if rec is not None and rec.committed:
                data = self.store.read_blob(rec.path, rec.sha256)
                if data is None:
                    raise CommitError("NOT_FOUND", f"revision blob missing: {revision_id}")
                return rec, data
            raise CommitError("NOT_FOUND", f"找不到 revision_id={revision_id}", fields={"revision_id": revision_id})

    def _write_prepared(
        self,
        tx_id: str,
        operation_id: str,
        digest: str,
        actor: str,
        prepared: list[_PreparedTarget],
        deps: list[ReadDependency],
    ) -> dict[str, Any]:
        for item in prepared:
            payload = item.after_bytes if item.after_bytes is not None else item.before_bytes
            if payload is not None:
                self.txlog.put_blob(tx_id, payload, content_sha256(payload))
            if item.before_bytes is not None:
                self.txlog.put_blob(tx_id, item.before_bytes, content_sha256(item.before_bytes))
        intent = {
            "tx_id": tx_id,
            "operation_id": operation_id,
            "intent_digest": digest,
            "actor": actor,
            "workspace_key": self.workspace_key(),
            "state": "prepared",
            "created_at": _utc_now(),
            "read_dependencies": [d.to_dict() for d in deps],
            "targets": [self._intent_target(item) for item in prepared],
        }
        self.txlog.write_intent(intent)
        return intent

    def _intent_target(self, item: _PreparedTarget) -> dict[str, Any]:
        return {
            "path": item.rel,
            "op": item.spec.op,
            "lineage_id": item.lineage_id,
            "exists_before": item.exists_before,
            "exists_after": item.exists_after,
            "before_sha256": content_sha256(item.before_bytes) if item.before_bytes is not None else None,
            "after_sha256": content_sha256(item.after_bytes) if item.after_bytes is not None and item.exists_after else None,
            "expected_version": item.before_version,
            "from_path": item.from_rel,
            "to_path": item.to_rel,
            "publish_status": item.publish_status,
            "is_dir": item.is_dir,
            "restore_revision_id": item.spec.restore_revision_id,
            "event_context": item.spec.event_context,
        }

    def _publish(self, tx_id: str, prepared: list[_PreparedTarget], intent: dict[str, Any]) -> None:
        del tx_id
        intent["state"] = "publishing"
        self.txlog.write_intent(intent)
        for index, item in enumerate(prepared):
            try:
                self._publish_one(item)
                item.publish_status = "published"
            except CommitError:
                item.publish_status = "failed"
                raise
            except OSError as exc:
                item.publish_status = "failed"
                if isinstance(exc, FileBusyError) or getattr(exc, "winerror", None) in (32, 33):
                    raise CommitError("FILE_LOCKED", f"文件被占用：{item.rel}。请关闭 Excel 或文件预览后重试。") from exc
                if isinstance(exc, PermissionError):
                    raise CommitError("SAVE_FAILED", f"没有权限写入 {item.rel}；请检查目录权限或选择可写目录。") from exc
                raise CommitError("SAVE_FAILED", f"写入 {item.rel} 失败：{exc}") from exc
            intent["targets"][index]["publish_status"] = item.publish_status
            self.txlog.write_intent(intent)

    def _publish_one(self, item: _PreparedTarget) -> None:
        source = item.src_dest if item.spec.op == "move" else item.dest
        if not item.is_dir and source is not None:
            actual = content_version_of(source.read_bytes()) if source.is_file() else None
            if actual != item.before_version or source.exists() != item.exists_before:
                raise CommitError("VERSION_CONFLICT", f"发布前文件已变化：{item.from_rel or item.rel}")
            if item.spec.op == "move" and item.dest.exists():
                raise CommitError("PATH_OCCUPIED", f"移动目标已被占用：{item.rel}")
        if item.spec.op == "mkdir" or (item.is_dir and item.spec.op != "delete"):
            item.dest.mkdir(parents=True, exist_ok=True)
            return
        if item.spec.op == "delete":
            if item.is_dir:
                item.dest.rmdir()
                return
            item.dest.unlink()
            return
        if item.spec.op == "move":
            assert item.src_dest is not None
            item.dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(str(item.src_dest), str(item.dest))
            return
        item.dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(suffix=item.dest.suffix or ".bin", dir=str(item.dest.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            with tmp.open("wb") as handle:
                handle.write(item.after_bytes or b"")
                handle.flush()
                os.fsync(handle.fileno())
            replace_with_retry(str(tmp), str(item.dest))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _project_history(self, tx_id: str, prepared: list[_PreparedTarget]) -> HistoryState:
        try:
            for item in prepared:
                if item.spec.op == "mkdir" or item.is_dir:
                    continue
                self._project_one(tx_id, item)
            return "recorded"
        except Exception:
            logger.warning("history projection failed tx=%s", tx_id, exc_info=True)
            return "pending_recover"

    def _project_one(self, tx_id: str, item: _PreparedTarget) -> None:
        def add(**kwargs: Any) -> RevisionRecord:
            # Each history item has a stable identity, even if record persistence
            # succeeded but the lineage index/next record did not.
            reason = kwargs["reason"]
            path = kwargs["path"]
            digest = content_sha256(kwargs["data"])
            for old in self.store.find_by_transaction(tx_id):
                if old.path == path and old.reason == reason and old.sha256 == digest and old.exists_after == kwargs["exists_after"]:
                    return old
            material = f"{tx_id}|{path}|{reason}|{kwargs['exists_after']}"
            kwargs["revision_id"] = hashlib.sha256(material.encode()).hexdigest()[:24]
            return self.store.add_record(**kwargs)

        parent_id: str | None = None
        if item.spec.op == "move" and item.from_rel:
            rec = add(
                path=item.from_rel,
                data=item.before_bytes or b"",
                reason="moved",
                transaction_id=tx_id,
                lineage_id=item.lineage_id,
                exists_after=False,
                op="move",
            )
            parent_id = rec.id
            self._index_record(item.lineage_id, rec, current_path=item.rel, closed=False, vacate=item.from_rel)
            dest_rec = add(
                path=item.rel,
                data=item.after_bytes or b"",
                reason="moved",
                transaction_id=tx_id,
                parent_revision_id=parent_id,
                lineage_id=item.lineage_id,
                exists_after=True,
                op="move",
            )
            self._index_record(item.lineage_id, dest_rec, current_path=item.rel, closed=False)
            return
        if item.spec.op == "delete":
            rec = add(
                path=item.rel,
                data=item.before_bytes or b"",
                reason="deleted",
                transaction_id=tx_id,
                lineage_id=item.lineage_id,
                exists_after=False,
                op="delete",
            )
            self._index_record(item.lineage_id, rec, current_path=item.rel, closed=True, vacate=item.rel)
            return
        if item.before_bytes is not None and item.spec.op in {"update", "restore"}:
            before = add(
                path=item.rel,
                data=item.before_bytes,
                reason="beforeRestore" if item.spec.op == "restore" else "beforeEdit",
                transaction_id=tx_id,
                lineage_id=item.lineage_id,
                exists_after=True,
                op=item.spec.op,
            )
            parent_id = before.id
            self._index_record(item.lineage_id, before, current_path=item.rel, closed=False)
        rec = add(
            path=item.rel,
            data=item.after_bytes or b"",
            reason="afterEdit",
            transaction_id=tx_id,
            parent_revision_id=parent_id,
            lineage_id=item.lineage_id,
            exists_after=True,
            op=item.spec.op,
        )
        self._index_record(item.lineage_id, rec, current_path=item.rel, closed=False)
        self.store.prune(item.rel)

    def _index_record(
        self,
        lineage_id: str,
        rec: RevisionRecord,
        *,
        current_path: str,
        closed: bool,
        vacate: str | None = None,
    ) -> None:
        if not lineage_id:
            return
        data = self.lineages.load_lineage(lineage_id)
        data["current_path"] = current_path
        data["closed"] = closed
        records = list(data.get("records") or [])
        if not any(r.get("path") == rec.path and r.get("revision_id") == rec.id for r in records):
            records.append({"path": rec.path, "revision_id": rec.id, "op": rec.op or rec.reason})
        records = [r for r in records if self.store.get(r["path"], r["revision_id"]) is not None]
        data["records"] = records
        self.lineages.save_lineage(data)
        path_info = self.lineages.load_path(current_path)
        path_info["current_lineage_id"] = None if closed else lineage_id
        if closed:
            closed_ids = list(path_info.get("closed_lineage_ids") or [])
            if lineage_id not in closed_ids:
                closed_ids.append(lineage_id)
            path_info["closed_lineage_ids"] = closed_ids
        self.lineages.save_path(current_path, path_info)
        if vacate:
            vacated = self.lineages.load_path(vacate)
            vacated["current_lineage_id"] = None
            if closed:
                closed_ids = list(vacated.get("closed_lineage_ids") or [])
                if lineage_id not in closed_ids:
                    closed_ids.append(lineage_id)
                vacated["closed_lineage_ids"] = closed_ids
            self.lineages.save_path(vacate, vacated)

    def _ensure_legacy_lineage(self, rel: str, *, create: bool = True) -> str | None:
        existing = self.lineages.current_lineage_id(rel)
        if existing:
            return existing
        recs = [r for r in self.store.list(rel) if r.committed]
        dest = self.root / rel
        if not recs:
            if dest.is_file() and create:
                lineage_id = secrets.token_hex(16)
                info = self.lineages.load_path(rel)
                info["current_lineage_id"] = lineage_id
                self.lineages.save_path(rel, info)
                self.lineages.save_lineage(
                    {"lineage_id": lineage_id, "closed": False, "current_path": rel, "records": []}
                )
                return lineage_id
            return None
        lineage_id = recs[0].lineage_id or f"legacy:{self.store.path_key(rel)}"
        last = recs[-1]
        closed = last.reason == "deleted" or last.exists_after is False
        if last.reason == "afterEdit" and last.sha256 == _EMPTY_SHA and not dest.is_file():
            closed = True
        data = self.lineages.load_lineage(lineage_id)
        data["current_path"] = rel
        data["closed"] = closed
        if not data.get("records"):
            data["records"] = [{"path": r.path, "revision_id": r.id, "op": r.op or r.reason} for r in recs]
        self.lineages.save_lineage(data)
        info = self.lineages.load_path(rel)
        info["current_lineage_id"] = None if closed else lineage_id
        if closed:
            closed_ids = list(info.get("closed_lineage_ids") or [])
            if lineage_id not in closed_ids:
                closed_ids.append(lineage_id)
            info["closed_lineage_ids"] = closed_ids
        self.lineages.save_path(rel, info)
        return None if closed and not dest.is_file() else lineage_id

    def _outbox_events(self, prepared: list[_PreparedTarget]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in prepared:
            events.append(
                {
                    "identity": f"./{item.rel}" if item.rel else "",
                    "path": item.rel,
                    "lineage_id": item.lineage_id,
                    "op": item.spec.op,
                    "exists_after": item.exists_after,
                    "before_version": item.before_version,
                    "after_version": item.after_version,
                    "from_path": item.from_rel,
                    "context": item.spec.event_context,
                }
            )
        return events

    def _receipt_from_prepared(
        self,
        operation_id: str,
        tx_id: str,
        prepared: list[_PreparedTarget],
        deps: list[ReadDependency],
        *,
        state: ReceiptState,
        history_state: HistoryState,
        error: CommitError | None = None,
        retryable: bool | None = None,
        resumeable: bool = False,
    ) -> MutationReceipt:
        targets = tuple(
            MutationTarget(
                path=item.rel,
                op=item.spec.op,
                lineage_id=item.lineage_id or None,
                exists_before=item.exists_before,
                exists_after=item.exists_after,
                before_version=item.before_version,
                after_version=item.after_version,
                from_path=item.from_rel,
                to_path=item.to_rel,
                restore_revision_id=item.spec.restore_revision_id,
                publish_status=item.publish_status,
            )
            for item in prepared
        )
        published = tuple(item.rel for item in prepared if item.publish_status == "published")
        if retryable is None:
            retryable = not published and (error is None or error.code == "VERSION_CONFLICT")
        failure_class = None
        if error is not None:
            if error.code in {"VERSION_CONFLICT", "PATH_OCCUPIED", "OPERATION_ID_REUSED", "FILE_EXISTS"}:
                failure_class = "conflict"
            elif error.code in {"PATH_INVALID", "NOT_FOUND"}:
                failure_class = "not_found" if error.code == "NOT_FOUND" else "invalid_args"
            else:
                failure_class = "internal"
        return MutationReceipt(
            operation_id=operation_id,
            tx_id=tx_id,
            workspace_key=self.workspace_key(),
            state=state,
            history_state=history_state,
            targets=targets,
            read_dependencies=tuple(deps),
            published_paths=published,
            retryable=bool(retryable),
            resumeable=resumeable,
            failure_class=failure_class,
            error_code=None if error is None else error.code,
            message="" if error is None else error.message,
        )

    def _empty_ok_receipt(self, operation_id: str | None, rel: str, *, op: Op) -> MutationReceipt:
        return MutationReceipt(
            operation_id=operation_id or secrets.token_hex(16),
            tx_id="",
            workspace_key=self.workspace_key(),
            state="committed",
            history_state="recorded",
            targets=(
                MutationTarget(
                    path=rel,
                    op=op,
                    lineage_id=None,
                    exists_before=True,
                    exists_after=op != "delete",
                    before_version=None,
                    after_version=None,
                    publish_status="published",
                ),
            ),
            published_paths=(rel,),
        )

    def _intent_digest(self, specs: list[TargetSpec], deps: list[ReadDependency] | None = None) -> str:
        payload = [{
            "op": spec.op, "path": self._canonical(spec.path),
            "to": self._canonical(spec.to_path) if spec.to_path else None,
            "expected": spec.expected_version, "restore": spec.restore_revision_id,
            "restore_missing": spec.restore_missing, "observe_live": spec.observe_live,
            "data": content_sha256(spec.data) if spec.data is not None else None,
            "builder": spec.intent if spec.builder is not None else None,
        } for spec in specs]
        raw = json.dumps({"targets": payload, "dependencies": [d.to_dict() for d in deps or []]}, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _targets_from_intent(self, intent: dict[str, Any], *, load_blobs: bool = True) -> list[_PreparedTarget]:
        prepared = []
        for raw in intent.get("targets") or []:
            rel = self._canonical(raw["path"])
            before = self._read_tx_blob(intent["tx_id"], raw.get("before_sha256")) if load_blobs else None
            after = self._read_tx_blob(intent["tx_id"], raw.get("after_sha256")) if load_blobs else None
            from_rel = raw.get("from_path")
            prepared.append(_PreparedTarget(
                spec=TargetSpec(op=raw["op"], path=rel, restore_revision_id=raw.get("restore_revision_id"),
                                event_context=raw.get("event_context")),
                rel=rel, dest=self.root / rel, lineage_id=raw.get("lineage_id") or "",
                exists_before=bool(raw.get("exists_before")), exists_after=bool(raw.get("exists_after")),
                before_bytes=before, after_bytes=after if after is not None else before,
                before_version=raw.get("expected_version"),
                after_version=f"sha256:{raw['after_sha256']}" if raw.get("after_sha256") else None,
                from_rel=from_rel, to_rel=raw.get("to_path"),
                src_dest=self.root / self._canonical(from_rel) if from_rel else None,
                is_dir=bool(raw.get("is_dir")) or raw["op"] == "mkdir",
                publish_status=raw.get("publish_status", "pending"),
            ))
        return prepared

    def _read_tx_blob(self, tx_id: str, digest: str | None) -> bytes | None:
        if not digest:
            return None
        path = self.txlog.blob_path(tx_id, digest)
        if not path.is_file():
            raise CommitError("RECOVERY_REQUIRED", f"事务恢复内容缺失：{tx_id}/{digest}")
        data = path.read_bytes()
        if content_sha256(data) != digest:
            raise CommitError("RECOVERY_REQUIRED", f"事务恢复内容损坏：{tx_id}/{digest}")
        return data

    def _matches_after(self, item: _PreparedTarget) -> bool:
        if item.spec.op == "move" and item.src_dest is not None and item.src_dest.exists():
            return False
        if item.is_dir:
            return item.dest.is_dir() if item.exists_after else not item.dest.exists()
        if not item.exists_after:
            return not item.dest.exists()
        return item.dest.is_file() and content_version_of(item.dest.read_bytes()) == item.after_version

    def _recover_intent(self, intent: dict[str, Any]) -> MutationReceipt | None:
        state = intent.get("state")
        prepared = self._targets_from_intent(intent, load_blobs=state != "prepared")
        intent.setdefault("intent_digest", "legacy:" + intent["tx_id"])
        # No publication ever starts while state is prepared. Recovery owns the
        # workspace lock, so this cannot be a live preparation.
        if state == "prepared":
            intent["state"] = "aborted"
            intent["message"] = "发布前中断，未执行文件变更"
        elif state in {"publishing", "failed_partial"}:
            with ExitStack() as stack:
                paths = {p.dest for p in prepared} | {p.src_dest for p in prepared if p.src_dest}
                for path in sorted(paths):
                    handle = _acquire_lock(lock_path_for(path))
                    stack.callback(_release_lock, handle)
                for item in prepared:
                    if item.publish_status == "published" or self._matches_after(item):
                        item.publish_status = "published"
                        continue
                    try:
                        self._publish_one(item)
                        item.publish_status = "published"
                    except (CommitError, OSError) as exc:
                        item.publish_status = "failed"
                        intent["error_code"] = getattr(exc, "code", "SAVE_FAILED")
                        intent["message"] = str(exc)
                        break
                    intent["targets"] = [self._intent_target(p) for p in prepared]
                    self.txlog.write_intent(intent)
            intent["state"] = "committed" if all(p.publish_status == "published" for p in prepared) else "failed_partial"
        return self._finish_intent(intent, prepared)

    def _rm_empty_dirs(self, start: Path) -> None:
        cursor = start
        while cursor != self.root and cursor.is_dir():
            try:
                next(cursor.iterdir())
                break
            except StopIteration:
                parent = cursor.parent
                try:
                    cursor.rmdir()
                except OSError:
                    break
                cursor = parent

    def raise_if_failed(self, receipt: MutationReceipt) -> MutationReceipt:
        if receipt.state == "committed":
            return receipt
        raise CommitError(
            receipt.error_code or "SAVE_FAILED",
            receipt.message or receipt.state,
            fields={"receipt": receipt.to_dict(), "path": receipt.primary_path()},
        )


def file_service(workspace_root: str | Path) -> WorkspaceFileService:
    return WorkspaceFileService(workspace_root)


def service_for_guard(guard: FileAccessGuard) -> WorkspaceFileService:
    return WorkspaceFileService(guard.workspace_root)


def receipt_to_commit_result(receipt: MutationReceipt, *, bytes_written: int = 0) -> CommitResult:
    if receipt.state != "committed":
        raise CommitError(
            receipt.error_code or "SAVE_FAILED",
            receipt.message or receipt.state,
            fields={"receipt": receipt.to_dict(), "path": receipt.primary_path()},
        )
    result = receipt.to_commit_result()
    if bytes_written:
        return CommitResult(
            path=result.path,
            content_version=result.content_version,
            previous_version=result.previous_version,
            status=result.status,
            warnings=result.warnings,
            bytes_written=bytes_written,
            extra=result.extra,
        )
    return result
