"""Durable / request image types. History stores refs only."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    """Content-addressed image source reference.

    New attachments keep the original source bytes as the durable object.  A
    provider-sized request variant is derived later from that object.  The
    ``original_dimensions`` field remains for refs written by the older
    normalized-object format.
    """

    attachment_id: str
    media_type: str
    bytes: int
    width: int
    height: int
    name: str | None = None
    original_dimensions: ImageDimensions | None = None
    source_digest: str | None = None
    source_dimensions: ImageDimensions | None = None
    source_media_type: str | None = None
    source_bytes: int | None = None
    source_orientation: int = 1
    animated: bool = False
    frame_count: int = 1
    parent_attachment_id: str | None = None
    crop_in_parent: dict[str, int] | None = None
    crop_zoom: float | None = None

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
        if self.source_digest:
            payload["sourceDigest"] = self.source_digest
        if self.source_dimensions is not None:
            payload["sourceDimensions"] = asdict(self.source_dimensions)
        if self.source_media_type:
            payload["sourceMediaType"] = self.source_media_type
        if self.source_bytes is not None:
            payload["sourceBytes"] = self.source_bytes
        if self.source_orientation != 1:
            payload["sourceOrientation"] = self.source_orientation
        if self.animated:
            payload["animated"] = True
        if self.frame_count > 1:
            payload["frameCount"] = self.frame_count
        if self.parent_attachment_id:
            payload["parentAttachmentId"] = self.parent_attachment_id
            payload["cropInParent"] = self.crop_in_parent
            if self.crop_zoom is not None and self.crop_zoom != 1:
                payload["cropZoom"] = self.crop_zoom
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ImageAttachmentRef:
        raw_orig = data.get("originalDimensions") or data.get("original_dimensions")
        orig = None
        if isinstance(raw_orig, dict):
            orig = ImageDimensions(int(raw_orig["width"]), int(raw_orig["height"]))
        raw_source = data.get("sourceDimensions") or data.get("source_dimensions")
        source_dimensions = None
        if isinstance(raw_source, dict):
            source_dimensions = ImageDimensions(int(raw_source["width"]), int(raw_source["height"]))
        return cls(
            attachment_id=str(data.get("attachmentId") or data.get("attachment_id") or ""),
            media_type=str(data.get("mediaType") or data.get("media_type") or "image/png"),
            bytes=int(data.get("bytes") or 0),
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
            name=data.get("name"),
            original_dimensions=orig,
            source_digest=data.get("sourceDigest"),
            source_dimensions=source_dimensions,
            source_media_type=data.get("sourceMediaType") or data.get("source_media_type"),
            source_bytes=(int(data["sourceBytes"]) if data.get("sourceBytes") is not None else None),
            source_orientation=max(1, int(data.get("sourceOrientation") or 1)),
            animated=bool(data.get("animated", False)),
            frame_count=max(1, int(data.get("frameCount") or 1)),
            parent_attachment_id=data.get("parentAttachmentId"),
            crop_in_parent=data.get("cropInParent"),
            crop_zoom=(float(data["cropZoom"]) if data.get("cropZoom") is not None else None),
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
    source: dict[str, Any] = field(default_factory=dict)
    transforms: tuple[dict[str, Any], ...] = ()
    encoding: str = "unknown"
    limitations: tuple[str, ...] = ()


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
