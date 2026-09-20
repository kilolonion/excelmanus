"""RevisionStore — hidden history under ``.excelmanus/revisions/``.

规则：

    若开历史且文件已存在：beforeEdit → AtomicPublish → afterEdit → prune
    新建：beforeEdit 可空；afterEdit 仍记
    失败：discard 该 transactionId；失败事务的记录不得出现在 list

Layout::

    <workspace>/.excelmanus/revisions/<sha256(canonicalPath)>/
      records/<revisionId>.json
      blobs/<contentSha256>

Record fields: id, path, sequence, reason, sha256, transactionId,
parentRevisionId, label?, createdAt
reason: beforeEdit | afterEdit | checkpoint | beforeRestore
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

VALID_REASONS = frozenset({
    "beforeEdit",
    "afterEdit",
    "checkpoint",
    "beforeRestore",
    "deleted",
    "moved",
})

_PROTECTED_REASONS = frozenset({"checkpoint", "deleted", "moved"})

DEFAULT_PRUNE_KEEP = 40
_REVISION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RevisionIntegrityError(ValueError):
    """Blob bytes do not match the recorded sha256."""


@dataclass(frozen=True)
class RevisionRecord:
    id: str
    path: str
    sequence: int
    reason: str
    sha256: str
    transaction_id: str
    parent_revision_id: str | None = None
    label: str | None = None
    created_at: str | None = None
    lineage_id: str | None = None
    exists_after: bool | None = None
    op: str | None = None
    committed: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "sequence": self.sequence,
            "reason": self.reason,
            "sha256": self.sha256,
            "transactionId": self.transaction_id,
            "parentRevisionId": self.parent_revision_id,
            "label": self.label,
            "createdAt": self.created_at,
            "lineageId": self.lineage_id,
            "existsAfter": self.exists_after,
            "op": self.op,
            "committed": self.committed,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Shape consumed by manage_spreadsheet_versions / ui_meta.revision."""
        return {
            "revision_id": self.id,
            "content_version": f"sha256:{self.sha256}",
            "reason": self.reason,
            "sequence": self.sequence,
            "transaction_id": self.transaction_id,
            "label": self.label or "",
            "parent_revision_id": self.parent_revision_id,
            "created_at": self.created_at or "",
            "lineage_id": self.lineage_id or "",
            "exists_after": self.exists_after,
            "op": self.op or "",
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "RevisionRecord":
        committed = data.get("committed")
        if committed is None:
            committed = True
        exists_after = data.get("existsAfter")
        if exists_after is None:
            exists_after = data.get("exists_after")
        return cls(
            id=str(data.get("id") or ""),
            path=str(data.get("path") or ""),
            sequence=int(data.get("sequence") or 0),
            reason=str(data.get("reason") or ""),
            sha256=str(data.get("sha256") or ""),
            transaction_id=str(data.get("transactionId") or data.get("transaction_id") or ""),
            parent_revision_id=data.get("parentRevisionId") or data.get("parent_revision_id"),
            label=data.get("label"),
            created_at=data.get("createdAt") or data.get("created_at") or None,
            lineage_id=data.get("lineageId") or data.get("lineage_id"),
            exists_after=exists_after,
            op=data.get("op"),
            committed=bool(committed),
        )


def _canonical_rel(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./").strip("/")


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_protected(rec: RevisionRecord) -> bool:
    if rec.label:
        return True
    if rec.reason in _PROTECTED_REASONS:
        return True
    if rec.exists_after is False:
        return True
    return False


class RevisionStore:
    """Records + content-addressed blobs. Not a user-facing path."""

    def __init__(self, workspace_root: str | Path) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._root = self._workspace_root / ".excelmanus" / "revisions"

    @property
    def root(self) -> Path:
        return self._root

    def path_key(self, canonical_path: str) -> str:
        return hashlib.sha256(_canonical_rel(canonical_path).encode("utf-8")).hexdigest()

    def _dir_for(self, canonical_path: str) -> Path:
        directory = self._root / self.path_key(canonical_path)
        for node in (self._root.parent, self._root, directory, directory / "blobs", directory / "records"):
            if node.is_symlink():
                raise RevisionIntegrityError("history directory must not be a symlink")
        return directory

    def _blob_paths(self, canonical_path: str, digest: str) -> tuple[Path, Path]:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RevisionIntegrityError("invalid blob digest")
        blob_dir = self._dir_for(canonical_path) / "blobs"
        if (blob_dir / digest).is_symlink() or (blob_dir / f"{digest}.xlsx").is_symlink():
            raise RevisionIntegrityError("history blob must not be a symlink")
        return blob_dir / digest, blob_dir / f"{digest}.xlsx"

    def put_blob(self, canonical_path: str, data: bytes) -> str:
        digest = content_sha256(data)
        blob_dir = self._dir_for(canonical_path) / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        dest, legacy = self._blob_paths(canonical_path, digest)
        if dest.exists() or legacy.exists():
            if self.read_blob(canonical_path, digest) is not None:
                return digest
        fd, tmp_name = tempfile.mkstemp(prefix=".blob-", dir=str(blob_dir))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            from excelmanus.workspace.txlog import replace_with_retry
            replace_with_retry(tmp_name, str(dest))
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return digest

    def read_blob(self, canonical_path: str, content_sha256_hex: str) -> bytes | None:
        dest, legacy = self._blob_paths(canonical_path, content_sha256_hex)
        path = dest if dest.is_file() else legacy if legacy.is_file() else None
        if path is None:
            return None
        data = path.read_bytes()
        actual = content_sha256(data)
        if actual != content_sha256_hex:
            raise RevisionIntegrityError(
                f"blob digest mismatch for {canonical_path}: expected {content_sha256_hex}, got {actual}"
            )
        return data

    def add_record(
        self,
        *,
        path: str,
        data: bytes,
        reason: str,
        transaction_id: str,
        parent_revision_id: str | None = None,
        label: str | None = None,
        revision_id: str | None = None,
        lineage_id: str | None = None,
        exists_after: bool | None = None,
        op: str | None = None,
        committed: bool = True,
    ) -> RevisionRecord:
        if reason not in VALID_REASONS:
            raise ValueError(f"invalid revision reason: {reason}")
        rel = _canonical_rel(path)
        sha = self.put_blob(rel, data)
        existing = self._list_all(rel)
        if exists_after is None:
            exists_after = reason != "deleted"
        record = RevisionRecord(
            id=revision_id or secrets.token_hex(8),
            path=rel,
            sequence=(existing[-1].sequence + 1) if existing else 1,
            reason=reason,
            sha256=sha,
            transaction_id=transaction_id,
            parent_revision_id=parent_revision_id,
            label=label,
            created_at=_utc_now(),
            lineage_id=lineage_id,
            exists_after=exists_after,
            op=op,
            committed=committed,
        )
        rec_dir = self._dir_for(rel) / "records"
        rec_dir.mkdir(parents=True, exist_ok=True)
        dest = rec_dir / f"{record.id}.json"
        from excelmanus.workspace.txlog import write_json_atomic

        write_json_atomic(dest, record.to_json_dict())
        return record

    def write_record(self, record: RevisionRecord) -> None:
        dest = self._dir_for(record.path) / "records" / f"{record.id}.json"
        from excelmanus.workspace.txlog import write_json_atomic

        write_json_atomic(dest, record.to_json_dict())

    def get(self, path: str, revision_id: str) -> RevisionRecord | None:
        rel = _canonical_rel(path)
        if not _REVISION_ID_RE.fullmatch(str(revision_id or "")):
            return None
        dest = self._dir_for(rel) / "records" / f"{revision_id}.json"
        if dest.is_symlink():
            raise RevisionIntegrityError("history record must not be a symlink")
        if not dest.is_file():
            return None
        try:
            rec = RevisionRecord.from_json_dict(json.loads(dest.read_text(encoding="utf-8")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RevisionIntegrityError("invalid history record") from exc
        if rec.path != rel or rec.id != revision_id:
            raise RevisionIntegrityError("history record identity mismatch")
        return rec

    def list(self, path: str) -> list[RevisionRecord]:
        """Committed history only. Uncommitted prepare records stay hidden."""
        return [rec for rec in self._list_all(path) if rec.committed]

    def delete_record(self, record: RevisionRecord) -> None:
        if not _REVISION_ID_RE.fullmatch(record.id):
            raise ValueError("invalid revision id")
        (self._dir_for(record.path) / "records" / f"{record.id}.json").unlink(missing_ok=True)
        self._gc_blobs(record.path)

    def _list_all(self, path: str) -> list[RevisionRecord]:
        rel = _canonical_rel(path)
        rec_dir = self._dir_for(rel) / "records"
        if not rec_dir.is_dir():
            return []
        records: list[RevisionRecord] = []
        for fp in rec_dir.glob("*.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                records.append(RevisionRecord.from_json_dict(data))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        records.sort(key=lambda r: (r.sequence, r.id))
        return records

    def find_by_transaction(self, transaction_id: str) -> list[RevisionRecord]:
        if not transaction_id or not self._root.is_dir():
            return []
        found: list[RevisionRecord] = []
        for rec_dir in self._root.glob("*/records"):
            if not rec_dir.is_dir():
                continue
            for fp in rec_dir.glob("*.json"):
                try:
                    rec = RevisionRecord.from_json_dict(
                        json.loads(fp.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    continue
                if rec.transaction_id == transaction_id:
                    found.append(rec)
        found.sort(key=lambda r: (r.path, r.sequence, r.id))
        return found

    def discard_transaction(self, transaction_id: str) -> int:
        """Delete every record for ``transaction_id`` and GC unreferenced blobs."""
        records = self.find_by_transaction(transaction_id)
        if not records:
            return 0
        paths = {rec.path for rec in records}
        removed = 0
        for rec in records:
            dest = self._dir_for(rec.path) / "records" / f"{rec.id}.json"
            try:
                dest.unlink()
                removed += 1
            except OSError:
                pass
        for path in paths:
            self._gc_blobs(path, self._list_all(path))
        return removed

    def prune(self, path: str, keep: int = DEFAULT_PRUNE_KEEP) -> int:
        """Keep the newest ``keep`` volatile records. Labels / tombstones stay."""
        records = self._list_all(path)
        if keep < 0:
            return 0
        protected = [rec for rec in records if _is_protected(rec)]
        volatile = [rec for rec in records if not _is_protected(rec)]
        if len(volatile) <= keep:
            return 0
        drop = volatile[: len(volatile) - keep]
        keep_recs = protected + volatile[len(volatile) - keep :]
        rel = _canonical_rel(path)
        rec_dir = self._dir_for(rel) / "records"
        removed = 0
        for rec in drop:
            dest = rec_dir / f"{rec.id}.json"
            try:
                dest.unlink()
                removed += 1
            except OSError:
                pass
        self._gc_blobs(rel, keep_recs)
        return removed

    def _gc_blobs(self, path: str, records: Iterable[RevisionRecord] | None = None) -> int:
        rel = _canonical_rel(path)
        keep_blobs = {r.sha256 for r in (records if records is not None else self._list_all(rel))}
        blob_dir = self._dir_for(rel) / "blobs"
        if not blob_dir.is_dir():
            return 0
        removed = 0
        for blob in blob_dir.iterdir():
            if not blob.is_file():
                continue
            key = blob.stem if blob.suffix else blob.name
            if key not in keep_blobs:
                try:
                    blob.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def capture_edit_pair(
        self,
        path: str,
        *,
        before_bytes: bytes | None,
        after_bytes: bytes,
        transaction_id: str | None = None,
        prune_keep: int = DEFAULT_PRUNE_KEEP,
    ) -> tuple[str, RevisionRecord | None, RevisionRecord]:
        """Record beforeEdit (optional) + afterEdit. Discard the tx on partial failure."""
        tx = transaction_id or secrets.token_hex(8)
        before_rec: RevisionRecord | None = None
        try:
            if before_bytes is not None:
                before_rec = self.add_record(
                    path=path,
                    data=before_bytes,
                    reason="beforeEdit",
                    transaction_id=tx,
                )
            after_rec = self.add_record(
                path=path,
                data=after_bytes,
                reason="afterEdit",
                transaction_id=tx,
                parent_revision_id=before_rec.id if before_rec is not None else None,
            )
            self.prune(path, keep=prune_keep)
            return tx, before_rec, after_rec
        except Exception:
            self.discard_transaction(tx)
            raise

    def checkpoint(
        self,
        path: str,
        data: bytes,
        *,
        label: str | None = None,
        transaction_id: str | None = None,
        lineage_id: str | None = None,
    ) -> RevisionRecord:
        tx = transaction_id or secrets.token_hex(8)
        rec = self.add_record(
            path=path,
            data=data,
            reason="checkpoint",
            transaction_id=tx,
            label=label,
            lineage_id=lineage_id,
            exists_after=True,
            op="checkpoint",
        )
        self.prune(path)
        return rec

    def read_revision(self, path: str, revision_id: str) -> tuple[RevisionRecord, bytes]:
        rec = self.get(path, revision_id)
        if rec is None or not rec.committed or rec.path != _canonical_rel(path):
            raise KeyError(f"revision not found: {revision_id}")
        data = self.read_blob(path, rec.sha256)
        if data is None:
            raise FileNotFoundError(f"revision blob missing: {revision_id}")
        return rec, data

    def restore_prepare(
        self,
        path: str,
        revision_id: str,
        current_bytes: bytes | None,
        *,
        transaction_id: str | None = None,
    ) -> tuple[str, RevisionRecord | None, bytes]:
        """Load target blob and record beforeRestore. Caller then AtomicPublish + afterEdit."""
        _target, blob = self.read_revision(path, revision_id)
        tx = transaction_id or secrets.token_hex(8)
        before_rec: RevisionRecord | None = None
        try:
            if current_bytes is not None:
                before_rec = self.add_record(
                    path=path,
                    data=current_bytes,
                    reason="beforeRestore",
                    transaction_id=tx,
                    exists_after=True,
                    op="restore",
                    committed=False,
                )
            return tx, before_rec, blob
        except Exception:
            self.discard_transaction(tx)
            raise

    def finish_restore(
        self,
        path: str,
        *,
        transaction_id: str,
        after_bytes: bytes,
        parent_revision_id: str | None,
    ) -> RevisionRecord:
        try:
            if parent_revision_id:
                before = self.get(path, parent_revision_id)
                if before is not None and not before.committed:
                    self.write_record(
                        RevisionRecord(
                            id=before.id,
                            path=before.path,
                            sequence=before.sequence,
                            reason=before.reason,
                            sha256=before.sha256,
                            transaction_id=before.transaction_id,
                            parent_revision_id=before.parent_revision_id,
                            label=before.label,
                            created_at=before.created_at,
                            lineage_id=before.lineage_id,
                            exists_after=before.exists_after,
                            op=before.op,
                            committed=True,
                        )
                    )
            rec = self.add_record(
                path=path,
                data=after_bytes,
                reason="afterEdit",
                transaction_id=transaction_id,
                parent_revision_id=parent_revision_id,
                exists_after=True,
                op="restore",
            )
            self.prune(path)
            return rec
        except Exception:
            self.discard_transaction(transaction_id)
            raise
