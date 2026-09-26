"""Admit source bytes into the durable attachment store."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from excelmanus.attachments.limits import (
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_IMAGES_PER_MESSAGE,
    DEFAULT_MAX_MESSAGE_IMAGE_BYTES,
    EXTENSION_TO_MEDIA,
)
from excelmanus.attachments.normalize import (
    NormalizationPolicy,
    assert_admission_limits,
    detect_image,
)
from excelmanus.attachments.store import AttachmentStore, get_attachment_store
from excelmanus.attachments.types import AttachmentError, ImageAttachmentRef, ImageDimensions


def decode_image_payload(data: str | bytes) -> bytes:
    if isinstance(data, bytes):
        return data
    raw = data.strip()
    if raw.startswith("data:") and ";base64," in raw:
        raw = raw.split(";base64,", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except Exception as exc:
        raise AttachmentError(f"invalid base64 image: {exc}", "INVALID_ATTACHMENT") from exc


def admit_image_bytes(
    data: bytes,
    *,
    media_type: str | None = None,
    name: str | None = None,
    store: AttachmentStore | None = None,
) -> ImageAttachmentRef:
    store = store or get_attachment_store()
    detected = detect_image(data, media_type)
    assert_admission_limits(data, detected)
    source_digest = store.put_source(data)
    # Keep the source bytes as the durable attachment.  Resizing, colour
    # conversion and provider-specific encoding belong to request projection;
    # admission must not silently replace the image the user supplied.
    digest = hashlib.sha256(data).hexdigest()
    ref = ImageAttachmentRef(
        attachment_id=f"sha256:{digest}",
        media_type=detected.media_type,
        bytes=len(data),
        width=detected.width,
        height=detected.height,
        name=_safe_name(name),
        source_digest=source_digest,
        source_dimensions=ImageDimensions(detected.width, detected.height),
        source_media_type=detected.media_type,
        source_bytes=len(data),
        source_orientation=detected.orientation,
        animated=detected.animated,
        frame_count=detected.frame_count,
    )
    return store.put(data, ref)


def admit_image_path(path: str | Path, *, store: AttachmentStore | None = None) -> ImageAttachmentRef:
    file_path = Path(path)
    if not file_path.is_file():
        raise AttachmentError(f"file does not exist: {path}", "PATH_INVALID")
    media = EXTENSION_TO_MEDIA.get(file_path.suffix.lower())
    data = file_path.read_bytes()
    if len(data) > DEFAULT_MAX_IMAGE_BYTES:
        raise AttachmentError(
            f"image exceeds {DEFAULT_MAX_IMAGE_BYTES} byte admission limit",
            "LIMIT_EXCEEDED",
        )
    return admit_image_bytes(data, media_type=media, name=file_path.name, store=store)


def admit_message_images(
    payloads: list[tuple[bytes, str | None, str | None]],
    *,
    store: AttachmentStore | None = None,
) -> list[ImageAttachmentRef]:
    if len(payloads) > DEFAULT_MAX_IMAGES_PER_MESSAGE:
        raise AttachmentError(
            f"message exceeds {DEFAULT_MAX_IMAGES_PER_MESSAGE} image admission limit",
            "LIMIT_EXCEEDED",
        )
    total = sum(len(item[0]) for item in payloads)
    if total > DEFAULT_MAX_MESSAGE_IMAGE_BYTES:
        raise AttachmentError(
            f"message exceeds {DEFAULT_MAX_MESSAGE_IMAGE_BYTES} image-byte admission limit",
            "LIMIT_EXCEEDED",
        )
    return [
        admit_image_bytes(data, media_type=media, name=name, store=store)
        for data, media, name in payloads
    ]


def _safe_name(name: str | None) -> str | None:
    if not name:
        return None
    leaf = Path(name).name.strip()
    return leaf or None


# Keep NormalizationPolicy imported for callers that customize durable form.
__all__ = [
    "NormalizationPolicy",
    "admit_image_bytes",
    "admit_image_path",
    "admit_message_images",
    "decode_image_payload",
]
