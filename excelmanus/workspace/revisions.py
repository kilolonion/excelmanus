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
parentRevisionId, label?
reason: beforeEdit | afterEdit | checkpoint | beforeRestore
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

VALID_REASONS = frozenset({
    "beforeEdit",
    "afterEdit",
    "checkpoint",
    "beforeRestore",
})

DEFAULT_PRUNE_KEEP = 40


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
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "RevisionRecord":
        return cls(
            id=str(data.get("id") or ""),
            path=str(data.get("path") or ""),
            sequence=int(data.get("sequence") or 0),
            reason=str(data.get("reason") or ""),
            sha256=str(data.get("sha256") or ""),
            transaction_id=str(data.get("transactionId") or data.get("transaction_id") or ""),
            parent_revision_id=data.get("parentRevisionId") or data.get("parent_revision_id"),
            label=data.get("label"),
        )


def _canonical_rel(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./").strip("/")


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        return self._root / self.path_key(canonical_path)

    def _blob_paths(self, canonical_path: str, digest: str) -> tuple[Path, Path]:
        blob_dir = self._dir_for(canonical_path) / "blobs"
        return blob_dir / digest, blob_dir / f"{digest}.xlsx"

    def put_blob(self, canonical_path: str, data: bytes) -> str:
        digest = content_sha256(data)
        blob_dir = self._dir_for(canonical_path) / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        dest, legacy = self._blob_paths(canonical_path, digest)
        if dest.exists() or legacy.exists():
            return digest
        dest.write_bytes(data)
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
    ) -> RevisionRecord:
        if reason not in VALID_REASONS:
            raise ValueError(f"invalid revision reason: {reason}")
        rel = _canonical_rel(path)
        sha = self.put_blob(rel, data)
        existing = self._list_all(rel)
        record = RevisionRecord(
            id=revision_id or secrets.token_hex(8),
            path=rel,
            sequence=(existing[-1].sequence + 1) if existing else 1,
            reason=reason,
            sha256=sha,
            transaction_id=transaction_id,
            parent_revision_id=parent_revision_id,
            label=label,
        )
        rec_dir = self._dir_for(rel) / "records"
        rec_dir.mkdir(parents=True, exist_ok=True)
        dest = rec_dir / f"{record.id}.json"
        dest.write_text(
            json.dumps(record.to_json_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return record

    def get(self, path: str, revision_id: str) -> RevisionRecord | None:
        rel = _canonical_rel(path)
        dest = self._dir_for(rel) / "records" / f"{revision_id}.json"
        if not dest.is_file():
            return None
        data = json.loads(dest.read_text(encoding="utf-8"))
        return RevisionRecord.from_json_dict(data)

    def list(self, path: str) -> list[RevisionRecord]:
        """Committed history only. Discarded transactions are already gone from disk."""
        return self._list_all(path)

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
        """Keep the newest ``keep`` records; GC unreferenced blobs."""
        records = self._list_all(path)
        if keep < 0 or len(records) <= keep:
            return 0
        drop = records[: len(records) - keep]
        keep_recs = records[len(records) - keep:]
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
    ) -> RevisionRecord:
        tx = transaction_id or secrets.token_hex(8)
        rec = self.add_record(
            path=path,
            data=data,
            reason="checkpoint",
            transaction_id=tx,
            label=label,
        )
        self.prune(path)
        return rec

    def read_revision(self, path: str, revision_id: str) -> tuple[RevisionRecord, bytes]:
        rec = self.get(path, revision_id)
        if rec is None:
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
            rec = self.add_record(
                path=path,
                data=after_bytes,
                reason="afterEdit",
                transaction_id=transaction_id,
                parent_revision_id=parent_revision_id,
            )
            self.prune(path)
            return rec
        except Exception:
            self.discard_transaction(transaction_id)
            raise
