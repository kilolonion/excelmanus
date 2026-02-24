"""memory_format：Markdown 格式记忆条目的解析、格式化与去重工具。

从 PersistentMemory 中提取的纯函数，供 FileMemoryBackend 和 PersistentMemory 共用。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from excelmanus.memory_models import MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

ENTRY_HEADER_RE = re.compile(
    r"^###\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})]\s+(\S+)\s*$"
)
TIMESTAMP_FMT = "%Y-%m-%d %H:%M"

_RECENT_DEDUPE_WINDOW = 200


def format_entries(entries: list[MemoryEntry]) -> str:
    """将 MemoryEntry 列表序列化为 Markdown 文本。"""
    if not entries:
        return ""
    parts: list[str] = []
    for entry in entries:
        ts = entry.timestamp.strftime(TIMESTAMP_FMT)
        header = f"### [{ts}] {entry.category.value}"
        parts.append(f"{header}\n\n{entry.content}\n\n---")
    return "\n\n".join(parts)


def parse_entries(content: str) -> list[MemoryEntry]:
    """将 Markdown 文本解析为 MemoryEntry 列表。"""
    if not content or not content.strip():
        return []

    entries: list[MemoryEntry] = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        match = ENTRY_HEADER_RE.match(line)
        if not match:
            i += 1
            continue

        ts_str, cat_str = match.group(1), match.group(2)
        try:
            timestamp = datetime.strptime(ts_str, TIMESTAMP_FMT)
        except ValueError:
            logger.warning("跳过时间戳格式不合规的条目: %s", line)
            i += 1
            continue
        try:
            category = MemoryCategory(cat_str)
        except ValueError:
            logger.warning("跳过未知类别的条目: %s", line)
            i += 1
            continue

        i += 1
        body_lines: list[str] = []
        while i < len(lines):
            if lines[i].strip() == "---":
                i += 1
                break
            body_lines.append(lines[i])
            i += 1

        body = "\n".join(body_lines).strip()
        if not body:
            logger.warning("跳过正文为空的条目: ts=%s, category=%s", ts_str, cat_str)
            continue

        entries.append(
            MemoryEntry(content=body, category=category, timestamp=timestamp)
        )
    return entries


def normalize_content_key(text: str) -> str:
    """归一化内容用于去重比较。"""
    return " ".join((text or "").split())


def dedupe_new_entries(
    existing_entries: list[MemoryEntry],
    new_entries: list[MemoryEntry],
    global_seen_keys: set[tuple[str, str]] | None = None,
) -> list[MemoryEntry]:
    """对新条目去重：排除已存在于 existing_entries / global_seen_keys 的条目。"""
    existing_keys = {
        (entry.category.value, normalize_content_key(entry.content))
        for entry in existing_entries[-_RECENT_DEDUPE_WINDOW:]
        if normalize_content_key(entry.content)
    }
    if global_seen_keys:
        existing_keys |= global_seen_keys

    batch_keys: set[tuple[str, str]] = set()
    result: list[MemoryEntry] = []
    for entry in new_entries:
        normalized = normalize_content_key(entry.content)
        if not normalized:
            continue
        key = (entry.category.value, normalized)
        if key in existing_keys or key in batch_keys:
            continue
        batch_keys.add(key)
        result.append(entry)
    return result
