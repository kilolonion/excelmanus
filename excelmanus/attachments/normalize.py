"""Provider-independent 8-bit sRGB normalization (Pillow port of DSH)."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, ImageSequence

from excelmanus.attachments.limits import (
    DEFAULT_MAX_ANIMATED_FRAMES,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_IMAGE_DIMENSION,
    DEFAULT_MAX_IMAGE_PIXELS,
    DEFAULT_NORMALIZED_IMAGE_MAX_BYTES,
    DEFAULT_NORMALIZED_IMAGE_MAX_DIMENSION,
    DEFAULT_NORMALIZED_IMAGE_MAX_PIXELS,
    IMAGE_ENCODING_QUALITIES,
    SUPPORTED_MEDIA_TYPES,
)
from excelmanus.attachments.request import request_image_dimensions
from excelmanus.attachments.types import AttachmentError

_FORMAT_TO_MEDIA = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "JPG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


@dataclass(frozen=True)
class DetectedImage:
    media_type: str
    width: int
    height: int
    has_alpha: bool
    animated: bool
    mode: str
    frame_count: int = 1
    max_frame_pixels: int = 0
    max_frame_dimension: int = 0
    orientation: int = 1


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    media_type: str
    width: int
    height: int
    original_width: int | None = None
    original_height: int | None = None


@dataclass(frozen=True)
class NormalizationPolicy:
    max_pixels: int = DEFAULT_NORMALIZED_IMAGE_MAX_PIXELS
    max_dimension: int = DEFAULT_NORMALIZED_IMAGE_MAX_DIMENSION
    max_bytes: int = DEFAULT_NORMALIZED_IMAGE_MAX_BYTES


def _media_type_of_format(fmt: str | None, declared: str | None) -> str:
    key = (fmt or "").upper()
    if key in _FORMAT_TO_MEDIA:
        return _FORMAT_TO_MEDIA[key]
    if declared in SUPPORTED_MEDIA_TYPES or declared == "image/bmp":
        return declared or "image/png"
    raise AttachmentError(f"unsupported image format: {fmt or declared}", "UNSUPPORTED_FORMAT")


def detect_image(data: bytes, declared: str | None = None) -> DetectedImage:
    if not data:
        raise AttachmentError("empty image", "INVALID_ATTACHMENT")
    try:
        with Image.open(BytesIO(data)) as image:
            fmt = image.format
            image.load()
            n_frames = getattr(image, "n_frames", 1) or 1
            if n_frames > DEFAULT_MAX_ANIMATED_FRAMES:
                raise AttachmentError(
                    f"animated image has too many frames: {n_frames}",
                    "LIMIT_EXCEEDED",
                )
            # Read frame metadata before applying EXIF orientation.  Pillow's
            # exif_transpose returns a new image and can hide n_frames on GIFs;
            # doing it first silently turned animated inputs into single-frame
            # images during admission.
            first_mode = image.mode
            first_has_alpha = (
                first_mode in {"RGBA", "LA", "PA"}
                or (first_mode == "P" and "transparency" in image.info)
            )
            max_frame_pixels = int(image.width) * int(image.height)
            max_frame_dimension = max(int(image.width), int(image.height))
            for frame_idx in range(1, n_frames):
                try:
                    image.seek(frame_idx)
                except EOFError:
                    break
                frame_w, frame_h = image.size
                max_frame_pixels = max(max_frame_pixels, int(frame_w) * int(frame_h))
                max_frame_dimension = max(max_frame_dimension, int(frame_w), int(frame_h))
            try:
                image.seek(0)
            except EOFError:
                pass
            if n_frames > 1:
                width, height = int(image.width), int(image.height)
                has_alpha = first_has_alpha
                mode = first_mode
                orientation = 1
            else:
                orientation = int(image.getexif().get(274, 1) or 1)
                oriented = ImageOps.exif_transpose(image) or image
                width, height = int(oriented.width), int(oriented.height)
                has_alpha = (
                    oriented.mode in {"RGBA", "LA", "PA"}
                    or (oriented.mode == "P" and "transparency" in oriented.info)
                )
                mode = oriented.mode
            return DetectedImage(
                media_type=_media_type_of_format(fmt, declared),
                width=width,
                height=height,
                has_alpha=has_alpha,
                animated=n_frames > 1,
                mode=mode,
                frame_count=int(n_frames),
                max_frame_pixels=max_frame_pixels,
                max_frame_dimension=max_frame_dimension,
                orientation=orientation,
            )
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError(f"cannot decode image: {exc}", "INVALID_ATTACHMENT") from exc


def assert_admission_limits(
    data: bytes,
    detected: DetectedImage,
    *,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
    max_dimension: int = DEFAULT_MAX_IMAGE_DIMENSION,
) -> None:
    if len(data) > max_bytes:
        raise AttachmentError(
            f"image exceeds {max_bytes} byte admission limit",
            "LIMIT_EXCEEDED",
        )
    observed_pixels = max(detected.width * detected.height, detected.max_frame_pixels or 0)
    if observed_pixels > max_pixels:
        raise AttachmentError(
            f"image exceeds {max_pixels}-pixel decoded-size limit",
            "LIMIT_EXCEEDED",
        )
    observed_dimension = max(detected.width, detected.height, detected.max_frame_dimension or 0)
    if observed_dimension > max_dimension:
        raise AttachmentError(
            f"an image side exceeds the {max_dimension}px limit",
            "LIMIT_EXCEEDED",
        )


def can_passthrough(detected: DetectedImage, nbytes: int, policy: NormalizationPolicy) -> bool:
    return (
        detected.media_type in {"image/png", "image/jpeg", "image/webp"}
        and not detected.animated
        and detected.orientation == 1
        and detected.mode in {"RGB", "RGBA", "L", "LA"}
        and nbytes <= policy.max_bytes
        and detected.width * detected.height <= policy.max_pixels
        and max(detected.width, detected.height) <= policy.max_dimension
    )


def _initial_dimensions(detected: DetectedImage, policy: NormalizationPolicy) -> tuple[int, int]:
    budgeted = request_image_dimensions(detected.width, detected.height, policy.max_pixels)
    long_edge = max(budgeted[0], budgeted[1])
    if long_edge <= policy.max_dimension:
        return budgeted
    scale = policy.max_dimension / long_edge
    return (
        max(1, int(budgeted[0] * scale)),
        max(1, int(budgeted[1] * scale)),
    )


def _open_oriented(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as image:
        image.load()
        if getattr(image, "n_frames", 1) > 1:
            # Images are a static visual observation tool.  Animated inputs
            # are admitted with frame metadata, then the first frame is
            # selected for a request variant.  The request text reports that
            # reduction.
            return ImageSequence.Iterator(image)[0].copy()
        oriented = ImageOps.exif_transpose(image) or image
        return oriented.copy()


def _to_work_mode(image: Image.Image, has_alpha: bool) -> Image.Image:
    if has_alpha:
        return image.convert("RGBA")
    if image.mode == "P":
        return image.convert("RGB")
    if image.mode in {"I", "I;16", "F", "CMYK", "YCbCr"}:
        return image.convert("RGB")
    if image.mode == "LA":
        return image.convert("RGB")
    if image.mode == "L":
        return image.convert("RGB")
    if image.mode == "RGBA":
        return image.convert("RGB")
    return image.convert("RGB")


def _encode_ladder(image: Image.Image, has_alpha: bool, max_bytes: int) -> tuple[bytes, str]:
    candidates: list[tuple[int, bytes, str]] = []
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


def normalize_image(
    data: bytes,
    detected: DetectedImage | None = None,
    policy: NormalizationPolicy | None = None,
) -> NormalizedImage:
    policy = policy or NormalizationPolicy()
    detected = detected or detect_image(data)
    if can_passthrough(detected, len(data), policy):
        return NormalizedImage(
            data=data,
            media_type=detected.media_type,
            width=detected.width,
            height=detected.height,
        )
    try:
        width, height = _initial_dimensions(detected, policy)
        image = _open_oriented(data)
        work = _to_work_mode(image, detected.has_alpha)
        if (work.width, work.height) != (width, height):
            work = work.resize((width, height), Image.Resampling.LANCZOS)
        payload, media = _encode_ladder(work, detected.has_alpha, policy.max_bytes)
        attempts = 0
        while (
            len(payload) > policy.max_bytes
            and max(work.width, work.height) > 1
            and attempts < 8
        ):
            next_size = (
                max(1, int(work.width * 0.75)),
                max(1, int(work.height * 0.75)),
            )
            if next_size == work.size:
                break
            work = work.resize(next_size, Image.Resampling.LANCZOS)
            payload, media = _encode_ladder(work, detected.has_alpha, policy.max_bytes)
            attempts += 1
        if len(payload) > policy.max_bytes:
            raise AttachmentError(
                f"could not encode image under {policy.max_bytes} bytes",
                "LIMIT_EXCEEDED",
            )
        width, height = work.size
        scaled = width != detected.width or height != detected.height
        return NormalizedImage(
            data=payload,
            media_type=media,
            width=width,
            height=height,
            original_width=detected.width if scaled else None,
            original_height=detected.height if scaled else None,
        )
    except AttachmentError:
        raise
    except Exception as exc:
        raise AttachmentError(
            f"could not convert image to 8-bit sRGB: {exc}",
            "ATTACHMENT_WRITE_FAILED",
        ) from exc
