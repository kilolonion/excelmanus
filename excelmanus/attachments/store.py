"""Content-addressed durable image objects under $EXCELMANUS_HOME/attachments."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from excelmanus.attachments.types import AttachmentError, ImageAttachmentRef
from excelmanus.data_home import get_excelmanus_home

_STORE: AttachmentStore | None = None

# request-images / files-index 是可再生派生数据；远端 Files 亦按相同 TTL 过期。
CACHE_TTL_SECONDS = 7 * 24 * 3600


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class AttachmentStore:
    """Immutable sha256 objects plus optional sidecar metadata."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_excelmanus_home() / "attachments"
        self.objects = self.root / "v1" / "objects"
        self.meta = self.root / "v1" / "meta"
        self.request_cache = self.root / "request-images"
        self.files_index = self.root / "files-index"
        self.sources = self.root / "sources"

    def put_source(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.sources / digest[:2] / digest
        if not path.is_file():
            _atomic_write(path, data)
        return digest

    def get_source(self, ref: ImageAttachmentRef) -> bytes:
        import re
        digest = ref.source_digest
        if not digest or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AttachmentError("Original image source is unavailable", "ATTACHMENT_MISSING")
        path = self.sources / digest[:2] / digest
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise AttachmentError("Original image digest mismatch", "ATTACHMENT_CORRUPT")
        return data

    def object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest

    def meta_path(self, digest: str) -> Path:
        return self.meta / digest[:2] / f"{digest}.json"

    def put(self, data: bytes, ref: ImageAttachmentRef) -> ImageAttachmentRef:
        digest = ref.attachment_id.removeprefix("sha256:")
        expected = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise AttachmentError(
                "attachmentId does not match stored bytes",
                "INVALID_ATTACHMENT_REF",
            )
        target = self.object_path(digest)
        if not target.is_file():
            _atomic_write(target, data)
        _atomic_write(
            self.meta_path(digest),
            json.dumps(ref.to_dict(), ensure_ascii=False).encode("utf-8"),
        )
        return ref

    def get_bytes(self, ref: ImageAttachmentRef) -> bytes:
        digest = ref.attachment_id.removeprefix("sha256:")
        path = self.object_path(digest)
        if not path.is_file():
            raise AttachmentError(
                f"missing attachment object sha256:{digest[:8]}",
                "ATTACHMENT_MISSING",
            )
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise AttachmentError(
                f"corrupt attachment object sha256:{digest[:8]}",
                "ATTACHMENT_CORRUPT",
            )
        return data

    def host_path(self, ref: ImageAttachmentRef) -> Path | None:
        digest = ref.attachment_id.removeprefix("sha256:")
        path = self.object_path(digest)
        return path if path.is_file() else None

    def get_ref(self, attachment_id: str) -> ImageAttachmentRef | None:
        """Load a durable ref from sidecar metadata. Missing objects return None."""
        digest = str(attachment_id or "").removeprefix("sha256:").strip()
        if not digest:
            return None
        meta = self.meta_path(digest)
        obj = self.object_path(digest)
        if not meta.is_file() or not obj.is_file():
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("attachmentId", f"sha256:{digest}")
        return ImageAttachmentRef.from_dict(data)


def get_attachment_store() -> AttachmentStore:
    global _STORE
    if _STORE is None:
        _STORE = AttachmentStore()
    return _STORE


def reset_attachment_store() -> None:
    """Test helper: drop the process singleton."""
    global _STORE
    _STORE = None


def sweep_stale_caches(
    store: AttachmentStore | None = None,
    *,
    max_age_seconds: int = CACHE_TTL_SECONDS,
) -> dict[str, int]:
    """清理可再生派生缓存目录，返回 {request_images, files_index} 删除数。

    只动派生数据：request-images（请求版变体，可由 objects 重算）与
    files-index（远端 Files 映射，远端文件本身按 expires_at 过期）。
    objects/ 与 meta/ 是被会话历史引用的 durable 内容，永不清理；
    一并回收 _atomic_write 崩溃残留的临时文件。
    """
    store = store or get_attachment_store()
    now = time.time()
    removed = {"request_images": 0, "files_index": 0}
    for key, directory in (
        ("request_images", store.request_cache),
        ("files_index", store.files_index),
    ):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            try:
                if not path.is_file():
                    continue
                if now - path.stat().st_mtime <= max_age_seconds:
                    continue
                path.unlink()
                removed[key] += 1
            except OSError:
                continue
    return removed
