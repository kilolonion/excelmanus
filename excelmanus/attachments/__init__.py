"""Durable image attachments, request projection, and optional Files transport."""

from excelmanus.attachments.admit import admit_image_bytes, admit_image_path, decode_image_payload
from excelmanus.attachments.limits import (
    DEFAULT_REQUEST_IMAGE_MAX_BYTES,
    DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET,
)
from excelmanus.attachments.migrate import migrate_messages
from excelmanus.attachments.project import assemble_model_request, content_has_image
from excelmanus.attachments.store import get_attachment_store
from excelmanus.attachments.types import AttachmentError, ImageAttachmentRef

__all__ = [
    "AttachmentError",
    "DEFAULT_REQUEST_IMAGE_MAX_BYTES",
    "DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET",
    "ImageAttachmentRef",
    "admit_image_bytes",
    "admit_image_path",
    "assemble_model_request",
    "content_has_image",
    "decode_image_payload",
    "get_attachment_store",
    "migrate_messages",
]
