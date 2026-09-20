"""File mutation transaction log: prepare → publishing → committed.

See docs/design/architecture-refactor-20260913/reviews/batch-04-design.md.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

TxState = Literal["prepared", "publishing", "committed", "aborted", "failed_partial"]
TERMINAL_STATES = frozenset({"committed", "aborted", "failed_partial"})

# Windows 下杀毒/索引器可能短暂持有新文件句柄，os.replace 会瞬时 PermissionError；
# 有界重试只覆盖这种瞬态锁，真实冲突仍 fail-closed 上抛。
_REPLACE_RETRIES = 8
_REPLACE_RETRY_DELAY = 0.05


class FileBusyError(PermissionError):
    """A sharing/lock violation remained after bounded retries."""


def replace_with_retry(
    src: str,
    dst: str,
    *,
    retries: int = _REPLACE_RETRIES,
    delay: float = _REPLACE_RETRY_DELAY,
) -> None:
    for attempt in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            winerror = getattr(exc, "winerror", None)
            # Access denied (5) is not a transient sharing violation.
            if winerror is not None and winerror not in (32, 33):
                raise
            if attempt == retries - 1:
                if winerror in (32, 33):
                    raise FileBusyError(f"文件被其他程序占用：{dst}。请关闭 Excel 或文件预览后重试。") from exc
                raise
            time.sleep(delay * (attempt + 1))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tx-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class TxLog:
    """One workspace's ``.excelmanus/tx`` tree."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).expanduser().resolve()
        self.tx_root = self.root / ".excelmanus" / "tx"

    def dir_for(self, tx_id: str) -> Path:
        return self.tx_root / tx_id

    def intent_path(self, tx_id: str) -> Path:
        return self.dir_for(tx_id) / "intent.json"

    def receipt_path(self, operation_id: str) -> Path:
        key = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.tx_root / "receipts" / f"{key}.json"

    def blob_path(self, tx_id: str, digest: str) -> Path:
        return self.dir_for(tx_id) / "blobs" / digest

    def put_blob(self, tx_id: str, data: bytes, digest: str) -> None:
        dest = self.blob_path(tx_id, digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
            return
        fd, name = tempfile.mkstemp(prefix=".blob-", dir=str(dest.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            replace_with_retry(name, str(dest))
        finally:
            Path(name).unlink(missing_ok=True)

    def write_intent(self, intent: dict[str, Any]) -> None:
        tx_id = str(intent.get("tx_id") or "")
        if not tx_id:
            raise ValueError("intent missing tx_id")
        write_json_atomic(self.intent_path(tx_id), intent)

    def read_intent(self, tx_id: str) -> dict[str, Any] | None:
        return read_json(self.intent_path(tx_id))

    def append_outbox(self, tx_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        current = {e["event_id"]: e for e in self.read_outbox(tx_id) if e.get("event_id")}
        for index, event in enumerate(events):
            event_id = str(event.get("event_id") or f"{tx_id}:{index}")
            current[event_id] = {**event, "event_id": event_id}
        write_json_atomic(self.dir_for(tx_id) / "outbox.json", {"events": list(current.values())})

    def read_outbox(self, tx_id: str) -> list[dict[str, Any]]:
        stored = read_json(self.dir_for(tx_id) / "outbox.json")
        if stored is not None:
            return list(stored.get("events") or [])
        dest = self.dir_for(tx_id) / "outbox.jsonl"
        if not dest.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in dest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                events.append(rec)
        return events

    def write_receipt(self, operation_id: str, receipt: dict[str, Any]) -> None:
        if not operation_id:
            return
        write_json_atomic(self.receipt_path(operation_id), receipt)

    def read_receipt(self, operation_id: str) -> dict[str, Any] | None:
        if not operation_id:
            return None
        result = read_json(self.receipt_path(operation_id))
        if result is None and operation_id.replace("-", "").replace("_", "").isalnum():
            # Read-only migration of the former literal filename format.
            result = read_json(self.tx_root / "receipts" / f"{operation_id}.json")
        return result

    def external_receipt_path(self, operation_id: str) -> Path:
        key = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self.tx_root / "external" / f"{key}.json"

    def write_external_receipt(self, operation_id: str, receipt: dict[str, Any]) -> None:
        if operation_id:
            write_json_atomic(self.external_receipt_path(operation_id), receipt)

    def read_external_receipt(self, operation_id: str) -> dict[str, Any] | None:
        if not operation_id:
            return None
        return read_json(self.external_receipt_path(operation_id))

    def discard_recovery_blobs(self, tx_id: str) -> None:
        """History now owns the bytes. Keep intent/receipt for idempotent replay."""
        directory = (self.dir_for(tx_id) / "blobs").resolve()
        directory.relative_to(self.tx_root.resolve())
        if directory.is_dir():
            for child in directory.iterdir():
                if child.is_file():
                    child.unlink()

    def prune_terminal_blobs(self, keep: int = 100) -> int:
        """回收残留事务的 before/after 字节：删 blobs，保留 intent/receipt/outbox。

        只回收 history 已落账（recorded）的 committed/aborted 事务；
        failed_partial 与 pending_recover 的字节是恢复依赖，必须保留。
        最近 ``keep`` 个事务不动，避免触碰仍在推进或刚完成的工作。
        """
        if not self.tx_root.is_dir():
            return 0
        removed = 0
        try:
            dirs = [c for c in self.tx_root.iterdir() if c.is_dir() and c.name != "receipts"]
        except OSError:
            return 0
        dirs.sort(key=lambda c: c.stat().st_mtime, reverse=True)
        for child in dirs[keep:]:
            intent = read_json(child / "intent.json")
            if not intent or intent.get("state") not in {"committed", "aborted"}:
                continue
            operation_id = str(intent.get("operation_id") or "")
            receipt = self.read_receipt(operation_id) if operation_id else None
            if not receipt or receipt.get("history_state") != "recorded":
                continue
            blobs = child / "blobs"
            if not blobs.is_dir():
                continue
            try:
                for blob in blobs.iterdir():
                    if blob.is_file():
                        blob.unlink()
                        removed += 1
            except OSError:
                continue
        return removed

    def iter_intents(self) -> list[dict[str, Any]]:
        if not self.tx_root.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for child in self.tx_root.iterdir():
            if not child.is_dir() or child.name == "receipts":
                continue
            intent = read_json(child / "intent.json")
            if intent:
                found.append(intent)
        return found
