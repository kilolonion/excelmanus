"""Rewrite legacy inline image_url history into durable attachment refs."""

from __future__ import annotations

from typing import Any

from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload
from excelmanus.attachments.store import AttachmentStore, get_attachment_store
from excelmanus.attachments.types import AttachmentError, ImageAttachmentRef

DEGRADED_IMAGE_PREFIX = "[图片 #"


def _is_degraded_text(content: Any) -> bool:
    return isinstance(content, str) and DEGRADED_IMAGE_PREFIX in content and "已在之前的对话中发送" in content


def _ref_block(ref: ImageAttachmentRef) -> dict[str, Any]:
    return {"type": "image", "attachment": ref.to_dict()}


def migrate_content(content: Any, store: AttachmentStore | None = None) -> Any:
    """Convert one message content value. Degraded placeholders stay text."""
    store = store or get_attachment_store()
    if _is_degraded_text(content):
        return content
    if not isinstance(content, list):
        return content
    out: list[Any] = []
    changed = False
    for block in content:
        if not isinstance(block, dict):
            out.append(block)
            continue
        if block.get("type") == "image" and isinstance(block.get("attachment"), dict):
            out.append(block)
            continue
        if block.get("type") == "image_url":
            url = ""
            image = block.get("image_url")
            if isinstance(image, dict):
                url = str(image.get("url") or "")
            if url.startswith("data:") and ";base64," in url:
                try:
                    raw = decode_image_payload(url)
                    header = url.split(";base64,", 1)[0]
                    media = header.replace("data:", "", 1) or "image/png"
                    ref = admit_image_bytes(raw, media_type=media, store=store)
                    out.append(_ref_block(ref))
                    changed = True
                    continue
                except (AttachmentError, OSError):
                    # 解码失败或存储层 IO 错误（磁盘满/权限）都不得让会话恢复失败
                    out.append({"type": "text", "text": "[image omitted: unreadable historical attachment]"})
                    changed = True
                    continue
            out.append({"type": "text", "text": "[image omitted: unsupported historical image URL]"})
            changed = True
            continue
        out.append(block)
    return out if changed else content


def migrate_messages(messages: list[dict[str, Any]], store: AttachmentStore | None = None) -> list[dict[str, Any]]:
    store = store or get_attachment_store()
    migrated: list[dict[str, Any]] = []
    for message in messages:
        content = migrate_content(message.get("content"), store)
        if content is message.get("content"):
            migrated.append(message)
        else:
            cleaned = {k: v for k, v in message.items() if k not in {"_image_id", "_image_downgraded"}}
            cleaned["content"] = content
            migrated.append(cleaned)
    return migrated
