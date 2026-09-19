"""Durable / request image types. History stores refs only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ImageMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]


class AttachmentError(Exception):
    """Admission, storage, or projection failure."""

    def __init__(self, message: str, code: str = "ATTACHMENT_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageDimensions:
    width: int
    height: int


@dataclass(frozen=True)
class ImageAttachmentRef:
    """Content-addressed normalized image. Never a path, URL, or file_id."""

    attachment_id: str
    media_type: str
    bytes: int
    width: int
    height: int
    name: str | None = None
    original_dimensions: ImageDimensions | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attachmentId": self.attachment_id,
            "mediaType": self.media_type,
            "bytes": self.bytes,
            "width": self.width,
            "height": self.height,
        }
        if self.name:
            payload["name"] = self.name
        if self.original_dimensions is not None:
            payload["originalDimensions"] = asdict(self.original_dimensions)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageAttachmentRef:
        raw_orig = data.get("originalDimensions") or data.get("original_dimensions")
        orig = None
        if isinstance(raw_orig, dict):
            orig = ImageDimensions(int(raw_orig["width"]), int(raw_orig["height"]))
        return cls(
            attachment_id=str(data.get("attachmentId") or data.get("attachment_id") or ""),
            media_type=str(data.get("mediaType") or data.get("media_type") or "image/png"),
            bytes=int(data.get("bytes") or 0),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            name=data.get("name"),
            original_dimensions=orig,
        )


@dataclass(frozen=True)
class ImageRequestPolicy:
    max_pixels: int
    max_bytes: int


@dataclass(frozen=True)
class RequestImageAttachment:
    variant_id: str
    attachment: ImageAttachmentRef
    data: bytes
    media_type: str
    bytes: int
    width: int
    height: int
    has_alpha: bool = False


@dataclass(frozen=True)
class ImageAttachmentAccess:
    readonly_path: str


@dataclass(frozen=True)
class RequestImageOffloadPolicy:
    max_images: int | None = None
    max_bytes: int | None = None
    count_quantum: int = 1
    byte_quantum: int = 1
    representation: Literal["raw", "base64"] = "raw"
