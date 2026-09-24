"""Two-phase oldest-first image offload with quantized prefixes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from excelmanus.attachments.types import ImageAttachmentRef, RequestImageOffloadPolicy


def base64_length(nbytes: int) -> int:
    return ((nbytes + 2) // 3) * 4


def offloaded_image_prefix_count(
    lengths: list[int],
    policy: RequestImageOffloadPolicy,
) -> int:
    total = sum(lengths)
    excess_count = 0 if policy.max_images is None else max(0, len(lengths) - policy.max_images)
    excess_bytes = 0 if policy.max_bytes is None else max(0, total - policy.max_bytes)
    if excess_count == 0 and excess_bytes == 0:
        return 0
    count_quantum = policy.count_quantum or 1
    byte_quantum = policy.byte_quantum or 1
    remove_count = 0 if excess_count == 0 else -(-excess_count // count_quantum) * count_quantum
    remove_bytes = 0 if excess_bytes == 0 else -(-excess_bytes // byte_quantum) * byte_quantum
    count = 0
    removed_bytes = 0
    for image_bytes in lengths:
        byte_target_met = (
            remove_bytes == 0
            or (removed_bytes >= remove_bytes if byte_quantum == 1 else removed_bytes > remove_bytes)
        )
        if count >= remove_count and byte_target_met:
            break
        removed_bytes += image_bytes
        count += 1
    return count


def required_image_indices(messages: Sequence[dict[str, Any]]) -> set[int]:
    """最新真实用户消息及其后的图片 occurrence。

    Tool-produced image observations are stored with a user wire role for
    provider compatibility, but they are not new user intent.  They must not
    move the user-turn boundary used by request offloading.
    """

    def is_internal_observation(message: Any) -> bool:
        return (
            isinstance(message, dict)
            and message.get("_prompt_kind") == "image_observation"
        )

    last_user = -1
    for index, message in enumerate(messages):
        if (
            isinstance(message, dict)
            and message.get("role") == "user"
            and not is_internal_observation(message)
        ):
            last_user = index
    if last_user < 0:
        return set()
    required: set[int] = set()
    occ = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        for _ in collect_image_refs(message.get("content")):
            if index >= last_user:
                required.add(occ)
            occ += 1
    return required


def offload_indices(
    lengths: list[int],
    policy: RequestImageOffloadPolicy,
    pin_seq: Sequence[str] | None = None,
    required: set[int] | None = None,
) -> tuple[set[int], bool]:
    """当前回合 required 优先。旧图从最老非 required 开始卸。"""
    want = offloaded_image_prefix_count(lengths, policy)
    n = len(lengths)
    if n == 0:
        return set(), False
    required = set(required or ())
    pins = list(pin_seq or ())
    indices: set[int] = set()
    n_pinned = min(len(pins), n) if pins else 0
    if pins:
        for i in range(n_pinned):
            if pins[i] == "offload" and i not in required:
                indices.add(i)
    still = max(0, want - len(indices))
    for i in range(n):
        if still <= 0:
            break
        if i in required or i in indices:
            continue
        indices.add(i)
        still -= 1
    unmet = still > 0
    if still > 0:
        for i in range(n):
            if still <= 0:
                break
            if i in required and i not in indices:
                indices.add(i)
                still -= 1
    return indices, unmet


def collect_image_refs(content: Any) -> list[ImageAttachmentRef]:
    refs: list[ImageAttachmentRef] = []
    _walk_collect(content, refs)
    return refs


def attachment_ids_from_messages(
    messages: Sequence[Any], *, event_log: Any | None = None,
) -> frozenset[str]:
    """Session-owned refs, including the sources of live compaction summaries.

    New summaries carry host-generated IDs for snapshot-only restores. Older
    event logs retain the original refs behind replace edges. Follow only live
    summaries' compaction ancestry: scanning the entire audit history would
    also grant access to images removed by rollback or session clear. Textual
    mentions of an attachment ID never grant access.
    """
    by_seq: dict[int, dict[str, Any]] = {}
    durable_messages = getattr(event_log, "durable_messages", None)
    if callable(durable_messages) and any(
        isinstance(message, dict) and message.get("_prompt_kind") == "compaction"
        for message in messages
    ):
        by_seq = {
            message["_seq"]: message for message in durable_messages()
            if isinstance(message, dict) and isinstance(message.get("_seq"), int)
        }
    found: set[str] = set()
    pending = list(messages)
    visited: set[int] = set()
    while pending:
        message = pending.pop()
        if not isinstance(message, dict):
            continue
        seq = message.get("_seq")
        if isinstance(seq, int):
            if seq in visited:
                continue
            visited.add(seq)
        for ref in collect_image_refs(message.get("content")):
            found.add(ref.attachment_id)
        compacted = message.get("_compacted_attachment_ids")
        if isinstance(compacted, list):
            found.update(item for item in compacted if isinstance(item, str) and item)
        if message.get("_prompt_kind") == "compaction":
            for source in by_seq.get(seq, {}).get("_shadows", []):
                if source in by_seq and source not in visited:
                    pending.append(by_seq[source])
    return frozenset(found)


def attachment_ids_from_engine(engine: Any) -> frozenset[str]:
    memory = getattr(engine, "_memory", None) or getattr(engine, "memory", None)
    messages = getattr(memory, "messages", None) or []
    return attachment_ids_from_messages(messages, event_log=getattr(memory, "event_log", None))


def _walk_collect(content: Any, refs: list[ImageAttachmentRef]) -> None:
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image" and isinstance(block.get("attachment"), dict):
            refs.append(ImageAttachmentRef.from_dict(block["attachment"]))
        elif block.get("type") == "tool-result":
            _walk_collect(block.get("content"), refs)


def offload_messages(
    messages: list[dict[str, Any]],
    policy: RequestImageOffloadPolicy,
    byte_length: Callable[[ImageAttachmentRef], int],
    placeholder: Callable[[ImageAttachmentRef], str],
    *,
    pin_seq: Sequence[str] | None = None,
    report: dict[str, Any] | None = None,
    required: set[int] | None = None,
) -> list[dict[str, Any]]:
    refs: list[ImageAttachmentRef] = []
    lengths: list[int] = []
    for message in messages:
        for ref in collect_image_refs(message.get("content")):
            refs.append(ref)
            raw = byte_length(ref)
            lengths.append(base64_length(raw) if policy.representation == "base64" else raw)
    required = set(required) if required is not None else required_image_indices(messages)
    indices, unmet = offload_indices(lengths, policy, pin_seq, required=required)
    required_omitted = bool(required & indices)
    if required_omitted:
        unmet = True
    pin_out = tuple("offload" if i in indices else "inline" for i in range(len(refs)))
    if report is not None:
        report["indices"] = tuple(sorted(indices))
        report["quota_unmet"] = bool(unmet)
        report["required_omitted"] = required_omitted
        report["required_indices"] = tuple(sorted(required))
        report["pin_seq"] = pin_out
        report["offloaded_ids"] = tuple(
            refs[i].attachment_id for i in range(len(refs)) if i in indices
        )
        report["inline_ids"] = tuple(
            refs[i].attachment_id for i in range(len(refs)) if i not in indices
        )
        report["rewrote_pinned_inline"] = bool(
            pin_seq
            and any(
                i < len(pin_seq) and pin_seq[i] == "inline" and i in indices
                for i in range(len(refs))
            )
        )
    if not indices:
        return messages
    cursor = {"i": 0}

    def replace(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        next_blocks: list[Any] | None = None
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image":
                occ = cursor["i"]
                cursor["i"] += 1
                if occ in indices:
                    next_blocks = content[:index] if next_blocks is None else next_blocks
                    ref = ImageAttachmentRef.from_dict(block["attachment"])
                    next_blocks.append({"type": "text", "text": placeholder(ref)})
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

    changed = False
    projected: list[dict[str, Any]] = []
    for message in messages:
        content = replace(message.get("content"))
        if content is message.get("content"):
            projected.append(message)
        else:
            changed = True
            projected.append({**message, "content": content})
    return projected if changed else messages
