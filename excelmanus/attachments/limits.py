"""DSH-aligned image admission, normalization, request, and offload defaults."""

from __future__ import annotations

# Admission
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMAGES_PER_MESSAGE = 20
DEFAULT_MAX_MESSAGE_IMAGE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 64_000_000
DEFAULT_MAX_IMAGE_DIMENSION = 8192
DEFAULT_MAX_ANIMATED_FRAMES = 1000

# Normalization (provider-independent durable form)
DEFAULT_NORMALIZED_IMAGE_MAX_PIXELS = 2048 * 2048
DEFAULT_NORMALIZED_IMAGE_MAX_DIMENSION = 8192
DEFAULT_NORMALIZED_IMAGE_MAX_BYTES = 4 * 1024 * 1024

# Request projection
DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET = 640_000
LOW_REQUEST_IMAGE_PIXEL_BUDGET = 512 * 512
DEFAULT_REQUEST_IMAGE_MAX_BYTES = 1 * 1024 * 1024
REQUEST_IMAGE_TRANSFORM_VERSION = "request-image-v5"
IMAGE_ENCODING_QUALITIES = (85, 75, 60)
WEBP_ENCODING_EFFORT = 0

# Request-size offload (Files / raw bytes)
DEFAULT_MAX_REQUEST_FILES_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_IMAGES_PER_REQUEST = 600
IMAGE_OFFLOAD_BYTE_QUANTUM = 64 * 1024 * 1024
IMAGE_OFFLOAD_COUNT_QUANTUM = 20

# Inline fallback when Files resolution fails
DEFAULT_MAX_INLINE_REQUEST_IMAGE_BYTES = 20 * 1024 * 1024
INLINE_IMAGE_OFFLOAD_BYTE_QUANTUM = 10 * 1024 * 1024

SUPPORTED_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
EXTENSION_TO_MEDIA = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}
