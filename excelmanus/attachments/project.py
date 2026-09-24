"""Request-time image projection. Never mutates durable history."""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable
from typing import Any

from excelmanus.attachments.image_tokens import estimate_image_tokens
from excelmanus.attachments.limits import (
    DEFAULT_MAX_IMAGES_PER_REQUEST,
    DEFAULT_MAX_REQUEST_FILES_BYTES,
    IMAGE_OFFLOAD_BYTE_QUANTUM,
    IMAGE_OFFLOAD_COUNT_QUANTUM,
    LOW_REQUEST_IMAGE_PIXEL_BUDGET,
    DEFAULT_REQUEST_IMAGE_MAX_BYTES,
    DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET,
)
from excelmanus.attachments.offload import (
    collect_image_refs,
    offload_messages,
    required_image_indices,
)
from excelmanus.attachments.request import default_request_policy, read_image_request
from excelmanus.attachments.store import AttachmentStore, get_attachment_store
from excelmanus.attachments.types import (
    AttachmentError,
    ImageAttachmentRef,
    ImageRequestPolicy,
    RequestImageAttachment,
    RequestImageOffloadPolicy,
)

logger = logging.getLogger(__name__)


def content_has_image(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"image", "image_url"}:
            return True
        if block.get("type") == "tool-result" and content_has_image(block.get("content")):
            return True
    return False


def _quoted(value: str) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _display_name(ref: ImageAttachmentRef) -> str | None:
    raw = (ref.name or "").replace("\\", "/").strip()
    if not raw:
        return None
    leaf = raw.rsplit("/", 1)[-1].strip()
    return leaf or None


def _image_identity(ref: ImageAttachmentRef) -> str:
    name = _display_name(ref)
    if name:
        return f"{_quoted(name)} ({ref.attachment_id})"
    return ref.attachment_id


def _extension(media_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(media_type, ".bin")


def _attachment_descriptor(ref: ImageAttachmentRef) -> str:
    """投影可见描述：只暴露 content-addressed id + 媒体类型，不含宿主路径。"""
    return (
        f" attachment_id={ref.attachment_id} media_type={ref.media_type}"
        f" ({ref.width}x{ref.height}px)."
        " Source dimensions, format, and byte size may differ."
        f" Copy to a writable path ending in {_extension(ref.media_type)} before editing."
    )


def text_only_image_text(ref: ImageAttachmentRef) -> str:
    digest = ref.attachment_id.removeprefix("sha256:")[:8]
    return f"[image omitted because this model accepts text only; attachment sha256:{digest}]"


def request_image_handle_text(
    ref: ImageAttachmentRef,
    version: RequestImageAttachment,
) -> str:
    preview = f"Image {_image_identity(ref)}; request preview {version.width}x{version.height}px; attachment-to-request scale=({version.width / ref.width:.8g},{version.height / ref.height:.8g}), origin=(0,0)."
    return preview + _attachment_descriptor(ref)


def offloaded_image_text(ref: ImageAttachmentRef) -> str:
    identity = f"image omitted to fit request image limits; {_image_identity(ref)}."
    return f"[{identity}{_attachment_descriptor(ref)}]"


def missing_image_text(ref: ImageAttachmentRef) -> str:
    return f"[image missing from store; {_image_identity(ref)}.{_attachment_descriptor(ref)}]"


def corrupt_image_text(ref: ImageAttachmentRef) -> str:
    return f"[image unreadable; {_image_identity(ref)}.{_attachment_descriptor(ref)}]"


def project_images_for_text_model(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def replace(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        next_blocks: list[Any] | None = None
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image":
                next_blocks = content[:index] if next_blocks is None else next_blocks
                ref = ImageAttachmentRef.from_dict(block.get("attachment") or {})
                next_blocks.append({"type": "text", "text": text_only_image_text(ref)})
                continue
            if isinstance(block, dict) and block.get("type") == "tool-result":
                inner = replace(block.get("content"))
                if inner is not block.get("content"):
                    next_blocks = content[:index] if next_blocks is None else next_blocks
                    next_blocks.append({**block, "content": inner})
                    continue
            if next_blocks is not None:
                next_blocks.append(block)
        return content if next_blocks is None else next_blocks

    if not any(content_has_image(m.get("content")) for m in messages):
        return messages
    return [
        message if replace(message.get("content")) is message.get("content")
        else {**message, "content": replace(message.get("content"))}
        for message in messages
    ]


def resolve_image_request_policy(config: Any) -> ImageRequestPolicy:
    raw = getattr(config, "image_pixel_budget", DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET)
    if raw == "low":
        pixels = LOW_REQUEST_IMAGE_PIXEL_BUDGET
    else:
        try:
            pixels = int(raw)
        except (TypeError, ValueError):
            pixels = DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET
        if pixels <= 0:
            pixels = DEFAULT_REQUEST_IMAGE_PIXEL_BUDGET
    max_bytes = int(getattr(config, "image_max_bytes", DEFAULT_REQUEST_IMAGE_MAX_BYTES) or DEFAULT_REQUEST_IMAGE_MAX_BYTES)
    return ImageRequestPolicy(max_pixels=pixels, max_bytes=max(1, max_bytes))


def default_offload_policy(*, representation: str = "raw") -> RequestImageOffloadPolicy:
    return RequestImageOffloadPolicy(
        max_images=DEFAULT_MAX_IMAGES_PER_REQUEST,
        max_bytes=DEFAULT_MAX_REQUEST_FILES_BYTES,
        count_quantum=IMAGE_OFFLOAD_COUNT_QUANTUM,
        byte_quantum=IMAGE_OFFLOAD_BYTE_QUANTUM,
        representation=representation,  # type: ignore[arg-type]
    )


def _materialize_images(
    messages: list[dict[str, Any]],
    versions: dict[str, RequestImageAttachment],
    failures: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    def replace(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        out: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                ref = ImageAttachmentRef.from_dict(block.get("attachment") or {})
                version = versions.get(ref.attachment_id)
                if version is None:
                    reason = (failures or {}).get(ref.attachment_id, "missing")
                    if reason == "corrupt":
                        text = corrupt_image_text(ref)
                    elif reason == "omitted":
                        text = offloaded_image_text(ref)
                    else:
                        text = missing_image_text(ref)
                    out.append({"type": "text", "text": text})
                    continue
                handle = request_image_handle_text(ref, version)
                data_uri = (
                    f"data:{version.media_type};base64,"
                    f"{base64.b64encode(version.data).decode('ascii')}"
                )
                out.append({"type": "text", "text": handle})
                out.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri, "detail": block.get("detail", "auto")},
                    "_variant_id": version.variant_id,
                    "_attachment_id": ref.attachment_id,
                })
                continue
            if isinstance(block, dict) and block.get("type") == "tool-result":
                out.append({**block, "content": replace(block.get("content"))})
                continue
            out.append(block)
        return out

    return [
        {**message, "content": replace(message.get("content"))}
        if isinstance(message.get("content"), list)
        else message
        for message in messages
    ]


def assemble_model_request(
    messages: list[dict[str, Any]],
    *,
    vision_capable: bool,
    config: Any | None = None,
    store: AttachmentStore | None = None,
    policy: ImageRequestPolicy | None = None,
    offload: RequestImageOffloadPolicy | None = None,
    conservative_byte_length: Callable[[ImageAttachmentRef], int] | None = None,
    pin_seq: list[str] | tuple[str, ...] | None = None,
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project durable image refs into wire parts. Input history is not mutated."""
    store = store or get_attachment_store()
    policy = policy or (resolve_image_request_policy(config) if config is not None else default_request_policy())
    offload = offload or default_offload_policy()
    sink: dict[str, Any] = report if report is not None else {}
    if not any(content_has_image(m.get("content")) for m in messages):
        sink.update({
            "pin_seq": (),
            "quota_unmet": False,
            "rewrote_pinned_inline": False,
        })
        return messages
    if not vision_capable:
        sink.update({
            "pin_seq": (),
            "quota_unmet": False,
            "rewrote_pinned_inline": False,
        })
        return project_images_for_text_model(messages)

    def conservative_len(ref: ImageAttachmentRef) -> int:
        if conservative_byte_length is not None:
            return conservative_byte_length(ref)
        return min(ref.bytes, policy.max_bytes)

    def _remaining_pins(
        n_original: int,
        stage1_indices: set[int],
        active_pins: list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...] | None:
        if not active_pins:
            return None
        rem: list[str] = []
        for i in range(n_original):
            if i in stage1_indices:
                continue
            if i >= len(active_pins):
                break
            rem.append(active_pins[i])
        return tuple(rem)

    required = required_image_indices(messages)

    def _project(active_pins: list[str] | tuple[str, ...] | None) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, RequestImageAttachment], dict[str, str]]:
        r1: dict[str, Any] = {}
        stage1 = offload_messages(
            messages,
            offload,
            conservative_len,
            lambda ref: offloaded_image_text(ref),
            pin_seq=active_pins,
            report=r1,
            required=required,
        )
        versions: dict[str, RequestImageAttachment] = {}
        failures: dict[str, str] = {}
        for message in stage1:
            for ref in collect_image_refs(message.get("content")):
                if ref.attachment_id in versions or ref.attachment_id in failures:
                    continue
                try:
                    versions[ref.attachment_id] = read_image_request(store, ref, policy)
                except AttachmentError as exc:
                    failures[ref.attachment_id] = (
                        "corrupt" if "CORRUPT" in str(getattr(exc, "code", "")) else "missing"
                    )
                    logger.warning(
                        "附件请求版本读取失败，按 %s 占位: %s",
                        failures[ref.attachment_id],
                        ref.attachment_id,
                        exc_info=True,
                    )
                except Exception:
                    failures[ref.attachment_id] = "missing"
                    logger.warning(
                        "附件请求版本读取失败，按 missing 占位: %s",
                        ref.attachment_id,
                        exc_info=True,
                    )

        def exact_len(ref: ImageAttachmentRef) -> int:
            version = versions.get(ref.attachment_id)
            return version.bytes if version is not None else conservative_len(ref)

        n_original = len(r1.get("pin_seq") or ())
        r2: dict[str, Any] = {}
        stage2 = offload_messages(
            stage1,
            offload,
            exact_len,
            lambda ref: offloaded_image_text(ref),
            pin_seq=_remaining_pins(n_original, set(r1.get("indices") or ()), active_pins),
            report=r2,
            required=required_image_indices(stage1),
        )
        s1_off = set(r1.get("indices") or ())
        remaining_original = [i for i in range(n_original) if i not in s1_off]
        s2_off = {remaining_original[j] for j in (r2.get("indices") or ()) if j < len(remaining_original)}
        all_off = s1_off | s2_off
        merged: dict[str, Any] = {
            "quota_unmet": bool(r1.get("quota_unmet") or r2.get("quota_unmet")),
            "rewrote_pinned_inline": bool(
                r1.get("rewrote_pinned_inline") or r2.get("rewrote_pinned_inline")
            ),
            "pin_seq": tuple("offload" if i in all_off else "inline" for i in range(n_original)),
        }
        merged["required_omitted"] = bool(r1.get("required_omitted") or r2.get("required_omitted"))
        if merged["required_omitted"]:
            merged["quota_unmet"] = True
        return stage2, merged, versions, failures

    projected, inner, versions, failures = _project(pin_seq)
    if inner.get("quota_unmet") and pin_seq:
        projected, inner, versions, failures = _project(None)
        inner["rewrote_pinned_inline"] = True
    sink.update(inner)
    return _materialize_images(projected, versions, failures)


def strip_projection_meta(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop request-only keys before a provider that rejects unknown fields."""

    def clean_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        cleaned: list[Any] = []
        for block in content:
            if isinstance(block, dict):
                cleaned.append({k: v for k, v in block.items() if not str(k).startswith("_")})
            else:
                cleaned.append(block)
        return cleaned

    return [{**message, "content": clean_content(message.get("content"))} for message in messages]


def estimate_message_image_tokens(message: dict[str, Any], *, deepseek: bool = False) -> int:
    total = 0
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image" and isinstance(block.get("attachment"), dict):
            ref = ImageAttachmentRef.from_dict(block["attachment"])
            total += estimate_image_tokens(ref.width, ref.height, deepseek=deepseek)
        elif block.get("type") == "image_url":
            total += 85
    return total
