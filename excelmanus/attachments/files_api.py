"""Optional OpenAI-compatible Files transport. Never written into history."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from excelmanus.attachments.store import AttachmentStore, _atomic_write, get_attachment_store

logger = logging.getLogger(__name__)

FILES_TTL_SECONDS = 7 * 24 * 3600
FILES_REFRESH_REMAINING = 3600
OWNED_FILE_PREFIX = "em-"
QUOTA_CLEANUP_BATCH = 20

_FILE_ID_RE = re.compile(r"\bfile-[A-Za-z0-9_-]+\b")
_QUOTA_RE = re.compile(
    r"(?:quota|storage|stored files|file count|too many files)",
    re.IGNORECASE,
)
_STALE_FILE_RE = re.compile(
    r"(?:expired|not[_ -]?found|deleted|do(?:es)? not exist|"
    r"not created under (?:this|your) account|"
    r"invalid.{0,20}file[_ -]?(?:id|api)|"
    r"file[_ -]?(?:id|api).{0,20}invalid)",
    re.IGNORECASE,
)
_HAS_FILE_RE = re.compile(r"\bfile(?:[_ -]?(?:id|api))?\b", re.IGNORECASE)


def files_api_enabled(config: Any, base_url: str) -> bool:
    mode = str(getattr(config, "image_files_api", "auto") or "auto").strip().lower()
    if mode in {"true", "1", "yes"}:
        return True
    if mode in {"false", "0", "no"}:
        return False
    return "deepseek" in (base_url or "").lower()


def is_files_quota_error(error: object) -> bool:
    return bool(_QUOTA_RE.search(str(error)))


def is_stale_file_error(error: object) -> bool:
    detail = str(error)
    return bool(_HAS_FILE_RE.search(detail) and _STALE_FILE_RE.search(detail))


def extract_file_ids_from_error(error: object) -> list[str]:
    return list(dict.fromkeys(_FILE_ID_RE.findall(str(error))))


def collect_wire_file_ids(messages: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "file":
                continue
            file_id = block.get("file_id")
            if not file_id and isinstance(block.get("file"), dict):
                file_id = block["file"].get("file_id")
            if file_id:
                found.append(str(file_id))
    return found


_INDEX_LOCKS: dict[str, asyncio.Lock] = {}
_LEASED_FILE_IDS: set[str] = set()
_ACCEPTED_FILE_IDS: set[str] = set()
_REQUEST_LEASES: dict[str, set[str]] = {}


def _scope_key(base_url: str, api_key: str | None, *, route: Any = None) -> str:
    """Full-credential scope. Old 12-char indexes are ignored (migrate by re-upload)."""
    if route is not None:
        scope = str(getattr(route, "credential_scope", "") or "")
        if scope:
            return scope
    from excelmanus.request.route import credential_scope

    return credential_scope(str(base_url or ""), api_key)


def _index_lock(path: Path) -> asyncio.Lock:
    key = str(path)
    lock = _INDEX_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _INDEX_LOCKS[key] = lock
    return lock


def lease_file_ids(request_id: str, file_ids: list[str] | tuple[str, ...]) -> None:
    owned = {str(item) for item in file_ids if item}
    _REQUEST_LEASES[str(request_id)] = owned
    _LEASED_FILE_IDS.update(owned)


def release_file_ids(request_id: str) -> None:
    owned = _REQUEST_LEASES.pop(str(request_id), set())
    still = set()
    for other in _REQUEST_LEASES.values():
        still.update(other)
    still.update(_ACCEPTED_FILE_IDS)
    for file_id in owned:
        if file_id not in still:
            _LEASED_FILE_IDS.discard(file_id)


def accept_file_leases(file_ids: list[str] | tuple[str, ...]) -> None:
    _ACCEPTED_FILE_IDS.clear()
    _ACCEPTED_FILE_IDS.update(str(item) for item in file_ids if item)
    _LEASED_FILE_IDS.update(_ACCEPTED_FILE_IDS)


def leased_file_ids() -> frozenset[str]:
    return frozenset(_LEASED_FILE_IDS | _ACCEPTED_FILE_IDS)


def _index_path(store: AttachmentStore, scope: str, variant_id: str) -> Path:
    digest = variant_id.removeprefix("sha256:")
    return store.files_index / scope / f"{digest}.json"


def _read_index(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expires = int(data.get("expires_at") or 0)
    if expires and expires - time.time() <= FILES_REFRESH_REMAINING:
        return None
    if not data.get("file_id"):
        return None
    return data


def _write_index(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _invalidate(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _file_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def _upload(client: Any, data: bytes, media_type: str, variant_id: str, *, purpose: str) -> tuple[str, int]:
    ext = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(media_type, "bin")
    digest = variant_id.removeprefix("sha256:")[:12]
    filename = f"{OWNED_FILE_PREFIX}{digest}.{ext}"
    handle = BytesIO(data)
    handle.name = filename
    created = await client.files.create(file=handle, purpose=purpose)
    file_id = _file_attr(created, "id")
    if not file_id:
        raise RuntimeError("files.create returned no id")
    expires_at = int(time.time()) + FILES_TTL_SECONDS
    raw_exp = _file_attr(created, "expires_at")
    if raw_exp:
        expires_at = int(raw_exp)
    return str(file_id), expires_at


async def reclaim_oldest_owned(client: Any, *, limit: int = QUOTA_CLEANUP_BATCH) -> int:
    """Delete the oldest harness-owned remote files. Returns deleted count."""
    files_api = getattr(client, "files", None)
    if files_api is None or not hasattr(files_api, "list") or not hasattr(files_api, "delete"):
        return 0
    rows: list[Any] = []
    after: str | None = None
    for _ in range(10):
        kwargs: dict[str, Any] = {}
        if after:
            kwargs["after"] = after
        try:
            listed = await files_api.list(**kwargs)
        except Exception:
            logger.warning("Files list failed during quota cleanup", exc_info=True)
            break
        page = _file_attr(listed, "data", []) or []
        rows.extend(page)
        if not _file_attr(listed, "has_more", False):
            break
        last_id = _file_attr(listed, "last_id", None)
        if not last_id and page:
            last_id = _file_attr(page[-1], "id", None)
        if not last_id or str(last_id) == after:
            break
        after = str(last_id)
    owned: list[tuple[int, str]] = []
    for item in rows:
        name = str(_file_attr(item, "filename", "") or "")
        file_id = _file_attr(item, "id")
        if not file_id or not name.startswith(OWNED_FILE_PREFIX):
            continue
        created = int(_file_attr(item, "created_at", 0) or 0)
        owned.append((created, str(file_id)))
    owned.sort()
    protected = leased_file_ids()
    deleted = 0
    for _, file_id in owned:
        if deleted >= max(1, limit):
            break
        if file_id in protected:
            continue
        try:
            await files_api.delete(file_id)
            deleted += 1
        except Exception:
            logger.warning("Files delete failed during quota cleanup: %s", file_id, exc_info=True)
    return deleted


async def _upload_with_quota(
    client: Any,
    data: bytes,
    media_type: str,
    variant_id: str,
    *,
    purpose: str,
) -> tuple[str, int]:
    try:
        return await _upload(client, data, media_type, variant_id, purpose=purpose)
    except Exception as exc:
        if not is_files_quota_error(exc):
            raise
        deleted = await reclaim_oldest_owned(client)
        if deleted <= 0:
            raise
        return await _upload(client, data, media_type, variant_id, purpose=purpose)


def _file_block(file_id: str, *, deepseek_style: bool) -> dict[str, Any]:
    if deepseek_style:
        return {"type": "file", "file_id": file_id}
    return {"type": "file", "file": {"file_id": file_id}}


def _iter_projected_images(messages: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, Any]]]:
    found: list[tuple[int, int, dict[str, Any]]] = []
    for mi, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image_url":
                found.append((mi, bi, block))
    return found


async def _resolve_file_id(
    client: Any,
    block: dict[str, Any],
    *,
    store: AttachmentStore,
    scope: str,
    purpose: str,
) -> str | None:
    url = str((block.get("image_url") or {}).get("url") or "")
    variant_id = str(block.get("_variant_id") or "")
    if not url.startswith("data:") or ";base64," not in url or not variant_id:
        return None
    header, b64 = url.split(";base64,", 1)
    media = header.replace("data:", "", 1) or "image/png"
    raw = base64.b64decode(b64)
    index_path = _index_path(store, scope, variant_id)
    async with _index_lock(index_path):
        record = _read_index(index_path)
        if record:
            return str(record["file_id"])
        file_id, expires_at = await _upload_with_quota(
            client, raw, media, variant_id, purpose=purpose,
        )
        winner = _read_index(index_path)
        if winner:
            loser_id = str(file_id)
            if loser_id and loser_id != str(winner.get("file_id") or ""):
                deleter = getattr(getattr(client, "files", None), "delete", None)
                if callable(deleter):
                    try:
                        await deleter(loser_id)
                    except Exception:
                        logger.warning("orphan file_id after lost upload race: %s", loser_id, exc_info=True)
            return str(winner["file_id"])
        _write_index(index_path, {
            "file_id": file_id,
            "variant_id": variant_id,
            "expires_at": expires_at,
            "bytes": len(raw),
        })
        return file_id


async def apply_files_transport(
    messages: list[dict[str, Any]],
    client: Any,
    *,
    base_url: str,
    api_key: str | None,
    store: AttachmentStore | None = None,
    route: Any = None,
) -> list[dict[str, Any]]:
    """Replace projected image_url data URIs with file_id blocks. History is untouched.

    One request never mixes file ids and inline images: any resolution failure
    returns the original projected messages so the caller can keep inline.
    """
    if client is None or not hasattr(client, "files"):
        return messages
    targets = [
        item for item in _iter_projected_images(messages)
        if str((item[2].get("image_url") or {}).get("url") or "").startswith("data:")
        and ";base64," in str((item[2].get("image_url") or {}).get("url") or "")
        and str(item[2].get("_variant_id") or "")
    ]
    if not targets:
        return messages
    store = store or get_attachment_store()
    scope = _scope_key(base_url, api_key, route=route)
    deepseek_style = "deepseek" in (str(getattr(route, "endpoint", None) or base_url) or "").lower()
    purpose = "user_data" if deepseek_style else "assistants"
    resolved: dict[tuple[int, int], str] = {}
    try:
        for mi, bi, block in targets:
            file_id = await _resolve_file_id(
                client, block, store=store, scope=scope, purpose=purpose,
            )
            if not file_id:
                return messages
            resolved[(mi, bi)] = file_id
    except Exception:
        logger.warning("Files resolution failed; keeping inline request images", exc_info=True)
        return messages
    if len(resolved) != len(targets):
        return messages

    rewritten: list[dict[str, Any]] = []
    for mi, message in enumerate(messages):
        content = message.get("content")
        if not isinstance(content, list):
            rewritten.append(message)
            continue
        out: list[Any] = []
        changed = False
        for bi, block in enumerate(content):
            file_id = resolved.get((mi, bi))
            if file_id:
                out.append(_file_block(file_id, deepseek_style=deepseek_style))
                changed = True
                continue
            if isinstance(block, dict):
                out.append({k: v for k, v in block.items() if not str(k).startswith("_")})
            else:
                out.append(block)
        if changed:
            cleaned = {k: v for k, v in message.items() if not str(k).startswith("_")}
            rewritten.append({**cleaned, "content": out})
        else:
            rewritten.append(message)
    return rewritten


def invalidate_file_ids(
    file_ids: list[str],
    *,
    base_url: str,
    api_key: str | None,
    store: AttachmentStore | None = None,
    route: Any = None,
) -> int:
    """Drop local file-id mappings. Returns number of index files removed."""
    if not file_ids:
        return 0
    store = store or get_attachment_store()
    scope = _scope_key(base_url, api_key, route=route)
    root = store.files_index / scope
    if not root.is_dir():
        return 0
    wanted = set(file_ids)
    removed = 0
    for path in root.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if record.get("file_id") in wanted:
            _invalidate(path)
            removed += 1
    return removed


def invalidate_scope(
    *,
    base_url: str,
    api_key: str | None,
    store: AttachmentStore | None = None,
    route: Any = None,
) -> int:
    """Drop every mapping for one endpoint+key scope (unnamed stale-file errors)."""
    store = store or get_attachment_store()
    scope = _scope_key(base_url, api_key, route=route)
    root = store.files_index / scope
    if not root.is_dir():
        return 0
    removed = 0
    for path in root.glob("*.json"):
        _invalidate(path)
        removed += 1
    return removed
