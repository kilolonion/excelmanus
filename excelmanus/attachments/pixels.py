"""Shared static visual decoding; every destructive transform is recorded."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps

from excelmanus.attachments.types import AttachmentError


@dataclass
class VisualPixels:
    image: Image.Image
    source: dict[str, Any]
    transforms: list[dict[str, Any]]
    limitations: list[str]


def decode_visual_source(data: bytes, basis: str = "original") -> VisualPixels:
    """Return owned, oriented RGB(A) pixels. Caller must close the image."""
    try:
        with Image.open(BytesIO(data)) as original:
            original.load()
            frames = int(getattr(original, "n_frames", 1) or 1)
            orientation = int(original.getexif().get(274, 1) or 1)
            fmt = str(original.format or "").lower()
            source_mode = original.mode
            encoded_size = list(original.size)
            icc = original.info.get("icc_profile")
            # The first frame is a deliberate, disclosed static observation.
            image = ImageOps.exif_transpose(original)
        steps: list[dict[str, Any]] = []
        limits: list[str] = []
        if basis == "legacy_normalized":
            limits.append("Original bytes unavailable; using legacy normalized pixels with unknown prior losses.")
        if frames > 1:
            steps.append({"operation": "select_frame", "frame": 0, "total_frames": frames})
            limits.append("Only frame 0 is observed; remaining animation frames are not inspected.")
        if orientation != 1:
            steps.append({"operation": "exif_transpose", "orientation": orientation})
        source = {
            "digest": hashlib.sha256(data).hexdigest(), "basis": basis,
            "width": image.width, "height": image.height,
            "encoded_size": encoded_size, "media_type": "image/" + ("jpeg" if fmt == "jpg" else fmt),
            "bytes": len(data), "mode": source_mode, "frame_count": frames,
        }
        alpha = "A" in image.getbands() or "transparency" in image.info
        target_mode = "RGBA" if alpha else "RGB"
        if icc:
            try:
                from PIL import ImageCms
                profile = ImageCms.ImageCmsProfile(BytesIO(icc))
                converted = ImageCms.profileToProfile(
                    image, profile, ImageCms.createProfile("sRGB"), outputMode=target_mode,
                )
                image.close()
                image = converted
                steps.append({"operation": "icc_to_srgb"})
            except (OSError, ValueError, TypeError) as exc:
                limits.append("Embedded colour profile could not be converted; colours may differ.")
                steps.append({"operation": "icc_unconverted", "reason": type(exc).__name__})
        if image.mode != target_mode:
            old_mode = image.mode
            converted = image.convert(target_mode)
            image.close()
            image = converted
            steps.append({"operation": "mode_conversion", "from": old_mode, "to": target_mode})
        return VisualPixels(image, source, steps, limits)
    except (OSError, ValueError) as exc:
        raise AttachmentError(f"Cannot decode image pixels: {exc}", "INVALID_ATTACHMENT") from exc
