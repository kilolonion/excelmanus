"""Route-owned request image versions and variantId cache."""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from excelmanus.attachments.limits import (
    DEFAULT_REQUEST_IMAGE_MAX_BYTES,
    DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET,
    IMAGE_ENCODING_QUALITIES,
    REQUEST_IMAGE_TRANSFORM_VERSION,
    WEBP_ENCODING_EFFORT,
)
from excelmanus.attachments.store import AttachmentStore, _atomic_write
from excelmanus.attachments.types import (
    AttachmentError,
    ImageAttachmentRef,
    ImageRequestPolicy,
    RequestImageAttachment,
)


def request_image_dimensions(width: int, height: int, max_pixels: int) -> tuple[int, int]:
    """Aspect-preserving integer size within a hard total-pixel budget. No enlargement."""
    if width <= 0 or height <= 0 or max_pixels <= 0:
        raise AttachmentError("image dimensions and maxPixels must be positive", "INVALID_ATTACHMENT")
    scale = min(1.0, (max_pixels / (width * height)) ** 0.5)
    if scale == 1:
        return width, height
    if width >= height:
        projected_width = max(1, int(width * scale))
        projected_height = max(1, round(projected_width * height / width))
        while projected_width * projected_height > max_pixels and projected_width > 1:
            projected_width -= 1
            projected_height = max(1, round(projected_width * height / width))
        return projected_width, projected_height
    projected_height = max(1, int(height * scale))
    projected_width = max(1, round(projected_height * width / height))
    while projected_width * projected_height > max_pixels and projected_height > 1:
        projected_height -= 1
        projected_width = max(1, round(projected_height * width / height))
    return projected_width, projected_height


def request_image_variant_id(attachment: ImageAttachmentRef, policy: ImageRequestPolicy) -> str:
    source_dimensions = attachment.source_dimensions or attachment.original_dimensions
    descriptor = json.dumps(
        {
            "transformVersion": REQUEST_IMAGE_TRANSFORM_VERSION,
            "attachmentId": attachment.attachment_id,
            "sourceDigest": attachment.source_digest,
            "sourceDimensions": (
                source_dimensions.width,
                source_dimensions.height,
            ) if source_dimensions is not None else None,
            "routePixelBudget": policy.max_pixels,
            "encodedByteBudget": policy.max_bytes,
            "encoding": {
                "webpQualities": list(IMAGE_ENCODING_QUALITIES),
                "webpEffort": WEBP_ENCODING_EFFORT,
                "jpegQualities": list(IMAGE_ENCODING_QUALITIES),
                "order": ["alpha:webp", "opaque:jpeg"],
                "colourspace": "srgb",
            },
        },
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(descriptor.encode('utf-8')).hexdigest()}"


def default_request_policy() -> ImageRequestPolicy:
    return ImageRequestPolicy(
        max_pixels=DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET,
        max_bytes=DEFAULT_REQUEST_IMAGE_MAX_BYTES,
    )


def _cache_path(store: AttachmentStore, variant_id: str) -> Path:
    digest = variant_id.removeprefix("sha256:")
    return store.request_cache / digest[:2] / digest


def _has_alpha(image: Image.Image) -> bool:
    """统一判断图片是否带 alpha，包括 P 模式 transparency。"""
    return (
        image.mode in {"RGBA", "LA", "PA"}
        or "A" in image.mode
        or (image.mode == "P" and "transparency" in image.info)
    )


def _open_request_image(data: bytes) -> tuple[Image.Image, bool, bool]:
    """Decode a source into a correctly oriented static request image.

    Returns ``(image, animated, transformed)``.  EXIF orientation is applied
    before dimensions are used for resizing.  Animated inputs are deliberately
    reduced to their first frame for the static vision API, with the reduction
    reported in the request handle.
    """
    with Image.open(BytesIO(data)) as image:
        image.load()
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        animated = frame_count > 1
        if animated:
            return ImageSequence.Iterator(image)[0].copy(), True, True
        orientation = int(image.getexif().get(274, 1) or 1)
        oriented = ImageOps.exif_transpose(image) or image
        return oriented.copy(), False, orientation != 1


def _encode_request(image: Image.Image, has_alpha: bool, max_bytes: int) -> tuple[bytes, str]:
    candidates: list[tuple[int, bytes, str]] = []
    if has_alpha:
        # Preserve transparent screenshots whenever the request cap allows it.
        # Fall back to the quality ladder only when lossless WebP is too large.
        lossless = BytesIO()
        image.save(lossless, format="WEBP", lossless=True, method=0)
        payload = lossless.getvalue()
        if len(payload) <= max_bytes:
            return payload, "image/webp"
        candidates.append((len(payload), payload, "image/webp"))
    for quality in IMAGE_ENCODING_QUALITIES:
        buf = BytesIO()
        if has_alpha:
            image.save(buf, format="WEBP", quality=quality, method=0)
            media = "image/webp"
        else:
            image.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
            media = "image/jpeg"
        payload = buf.getvalue()
        candidates.append((len(payload), payload, media))
        if len(payload) <= max_bytes:
            return payload, media
    smallest = min(candidates, key=lambda item: item[0])
    return smallest[1], smallest[2]


def read_image_request(
    store: AttachmentStore,
    ref: ImageAttachmentRef,
    policy: ImageRequestPolicy | None = None,
) -> RequestImageAttachment:
    policy = policy or default_request_policy()
    if policy.max_pixels <= 0 or policy.max_bytes <= 0:
        raise AttachmentError("request policy must be positive", "INVALID_ATTACHMENT")
    # New refs point directly at source bytes.  Older refs point at a
    # normalized object but retain sourceDigest; prefer that source when it is
    # still available, then fall back to the legacy object for compatibility.
    try:
        source = store.get_source(ref) if ref.source_digest else store.get_bytes(ref)
    except (AttachmentError, OSError):
        source = store.get_bytes(ref)
    variant_id = request_image_variant_id(ref, policy)
    cached_path = _cache_path(store, variant_id)
    if cached_path.is_file():
        data = cached_path.read_bytes()
        with Image.open(BytesIO(data)) as probe:
            probe.load()
            return RequestImageAttachment(
                variant_id=variant_id,
                attachment=ref,
                data=data,
                media_type=_media_of(probe),
                bytes=len(data),
                width=probe.width,
                height=probe.height,
                has_alpha=_has_alpha(probe),
            )

    with Image.open(BytesIO(source)) as probe:
        source_format = (probe.format or "").upper()
        source_media = _media_of(probe)
    work, animated, transformed = _open_request_image(source)
    try:
        source_width, source_height = work.width, work.height
        logical_dimensions = ref.source_dimensions or ref.original_dimensions
        logical_width = logical_dimensions.width if logical_dimensions is not None else (ref.width or source_width)
        logical_height = logical_dimensions.height if logical_dimensions is not None else (ref.height or source_height)
        width, height = request_image_dimensions(
            logical_width,
            logical_height,
            policy.max_pixels,
        )
        passthrough_formats = {"PNG", "JPEG", "JPG", "WEBP", "GIF"}
        if (
            not animated
            and not transformed
            and (width, height) == (source_width, source_height)
            and len(source) <= policy.max_bytes
            and source_format in passthrough_formats
        ):
            return RequestImageAttachment(
                variant_id=variant_id,
                attachment=ref,
                data=source,
                media_type=source_media,
                bytes=len(source),
                width=source_width,
                height=source_height,
                has_alpha=_has_alpha(work),
            )

        has_alpha = _has_alpha(work)
        work = work.convert("RGBA" if has_alpha else "RGB")
        if (work.width, work.height) != (width, height):
            work = work.resize((width, height), Image.Resampling.LANCZOS)
        data, media = _encode_request(work, has_alpha, policy.max_bytes)
        _atomic_write(cached_path, data)
        return RequestImageAttachment(
            variant_id=variant_id,
            attachment=ref,
            data=data,
            media_type=media,
            bytes=len(data),
            width=width,
            height=height,
            has_alpha=has_alpha,
        )
    finally:
        work.close()


def _media_of(image: Image.Image) -> str:
    fmt = (image.format or "").upper()
    return {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
        "GIF": "image/gif",
    }.get(fmt, "image/jpeg")
