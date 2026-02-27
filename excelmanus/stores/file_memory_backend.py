"""FileMemoryBackend：基于 Markdown 文件的持久记忆存储后端。

从 PersistentMemory 中提取的文件系统操作逻辑，实现 MemoryStorageBackend 协议。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from excelmanus.memory_format import (
    ENTRY_HEADER_RE,
    dedupe_new_entries,
    format_entries,
    normalize_content_key,
    parse_entries,
)
from excelmanus.memory_models import CATEGORY_TOPIC_MAP, MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

CORE_MEMORY_FILE = "MEMORY.md"
_LAYOUT_VERSION_FILE = ".layout_version"
_LAYOUT_VERSION = "2"
_MIGRATION_BACKUP_DIR = "migration_backups"
_RECENT_DEDUPE_WINDOW = 200


class FileMemoryBackend:
    """基于 Markdown 文件的持久记忆存储后端。"""

    def __init__(
        self,
        memory_dir: str,
        auto_load_lines: int = 200,
    ) -> None:
        self._memory_dir = Path(memory_dir).expanduser()
        self._auto_load_lines = auto_load_lines
        self._read_only_mode = False
        self._migration_error: str | None = None
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_layout_if_needed()

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    @property
    def read_only_mode(self) -> bool:
        return self._read_only_mode

    # ── MemoryStorageBackend 实现 ────────────────────────────

    def load_core(self, limit: int = 200) -> str:
        filepath = self._memory_dir / CORE_MEMORY_FILE
        if not filepath.exists():
            return ""
        try:
            with filepath.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            logger.warning("读取核心记忆文件失败: %s", filepath, exc_info=True)
            return ""

        if not lines:
            return ""
        effective_limit = limit or self._auto_load_lines
        if len(lines) <= effective_limit:
            return "".join(lines).rstrip("\n")

        selected = lines[-effective_limit:]
        start_idx: int | None = None
        for idx, line in enumerate(selected):
            if ENTRY_HEADER_RE.match(line.rstrip("\n")):
                start_idx = idx
                break
        if start_idx is not None:
            selected = selected[start_idx:]
        return "".join(selected).rstrip("\n")

    def load_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        filename = CATEGORY_TOPIC_MAP.get(category)
        if filename is None:
            return []
        filepath = self._memory_dir / filename
        if not filepath.exists():
            return []
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError:
            return []
        return [e for e in parse_entries(content) if e.category == category]

    def load_all(self) -> list[MemoryEntry]:
        filepath = self._memory_dir / CORE_MEMORY_FILE
        if not filepath.exists():
            return []
        try:
            raw = filepath.read_text(encoding="utf-8")
        except OSError:
            return []
        return parse_entries(raw)

    def save_entries(self, entries: list[MemoryEntry]) -> None:
        if not entries:
            return
        if self._read_only_mode:
            logger.warning(
                "持久记忆处于只读降级模式，跳过写入 %d 条记忆条目", len(entries)
            )
            return

        grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
        for entry in entries:
            filename = CATEGORY_TOPIC_MAP.get(entry.category)
            if filename is not None:
                grouped[filename].append(entry)
        grouped[CORE_MEMORY_FILE].extend(entries)

        global_seen_keys: set[tuple[str, str]] = set()
        for filename in grouped:
            filepath = self._memory_dir / filename
            if filepath.exists():
                try:
                    existing_text = filepath.read_text(encoding="utf-8")
                    for e in parse_entries(existing_text)[-_RECENT_DEDUPE_WINDOW:]:
                        normalized = normalize_content_key(e.content)
                        if normalized:
                            global_seen_keys.add((e.category.value, normalized))
                except OSError:
                    pass

        for filename, file_entries in grouped.items():
            filepath = self._memory_dir / filename
            try:
                self._append_entries(filepath, file_entries, global_seen_keys)
            except OSError:
                logger.warning("写入记忆文件失败: %s", filepath, exc_info=True)
                continue
            try:
                self._enforce_capacity(filepath)
            except OSError:
                logger.warning("容量管理写回失败: %s", filepath, exc_info=True)

    def delete_entry(self, entry_id: str) -> bool:
        if self._read_only_mode:
            return False
        target_files = [CORE_MEMORY_FILE] + list(CATEGORY_TOPIC_MAP.values())
        deleted = False
        for filename in target_files:
            filepath = self._memory_dir / filename
            if not filepath.exists():
                continue
            try:
                raw = filepath.read_text(encoding="utf-8")
            except OSError:
                continue
            file_entries = parse_entries(raw)
            filtered = [e for e in file_entries if e.id != entry_id]
            if len(filtered) < len(file_entries):
                deleted = True
                if filtered:
                    self._atomic_write(filepath, format_entries(filtered))
                else:
                    filepath.unlink(missing_ok=True)
        return deleted

    # ── 内部方法 ────────────────────────────────────────────

    def _append_entries(
        self,
        filepath: Path,
        new_entries: list[MemoryEntry],
        global_seen_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        existing = ""
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8")
        existing_entries = parse_entries(existing)
        filtered = dedupe_new_entries(existing_entries, new_entries, global_seen_keys)
        if not filtered:
            return
        new_content = format_entries(filtered)
        if existing.strip():
            combined = existing.rstrip("\n") + "\n\n" + new_content
        else:
            combined = new_content
        self._atomic_write(filepath, combined)

    def _atomic_write(self, filepath: Path, content: str) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=str(self._memory_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(content)
                tmp_f.flush()
                os.fsync(tmp_f.fileno())
            os.replace(tmp_path, str(filepath))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _enforce_capacity(self, filepath: Path) -> None:
        if not filepath.exists():
            return
        try:
            all_lines = filepath.read_text(encoding="utf-8").split("\n")
        except OSError:
            return
        if len(all_lines) <= 500:
            return

        entry_starts: list[int] = []
        for i, line in enumerate(all_lines):
            if ENTRY_HEADER_RE.match(line):
                entry_starts.append(i)

        if not entry_starts:
            kept_lines = all_lines[-400:]
            logger.info(
                "容量管理：%s 超过 500 行（%d 行），无条目边界，保留最后 400 行",
                filepath.name, len(all_lines),
            )
        elif len(all_lines) - entry_starts[-1] > 400:
            kept_lines = all_lines[-400:]
            logger.info(
                "容量管理：%s 超过 500 行（%d 行），强制保留最后 400 行",
                filepath.name, len(all_lines),
            )
        else:
            kept_start = entry_starts[-1]
            for idx in reversed(entry_starts):
                if len(all_lines) - idx <= 400:
                    kept_start = idx
                else:
                    break
            kept_lines = all_lines[kept_start:]
            kept_entries = sum(1 for s in entry_starts if s >= kept_start)
            removed_entries = len(entry_starts) - kept_entries
            logger.info(
                "容量管理：%s 超过 500 行（%d 行），保留最近 %d 条条目，移除 %d 条旧条目",
                filepath.name, len(all_lines), kept_entries, removed_entries,
            )

        self._atomic_write(filepath, "\n".join(kept_lines))

    # ── 文件布局迁移 ────────────────────────────────────────

    def _migrate_layout_if_needed(self) -> None:
        version_path = self._memory_dir / _LAYOUT_VERSION_FILE
        if version_path.exists():
            try:
                if version_path.read_text(encoding="utf-8").strip() == _LAYOUT_VERSION:
                    return
            except OSError:
                pass

        candidate_files = {
            CORE_MEMORY_FILE,
            "file_patterns.md",
            "user_prefs.md",
            "error_solutions.md",
            "general.md",
        }
        candidate_files.update(CATEGORY_TOPIC_MAP.values())
        existing_files = [
            f for f in sorted(candidate_files)
            if (self._memory_dir / f).exists()
        ]
        if not existing_files:
            self._write_layout_version()
            return

        backup_dir: Path | None = None
        try:
            backup_dir = self._create_migration_backup(existing_files)
            entries = self._collect_entries_for_migration(existing_files)
            deduped = self._dedupe_entries_for_migration(entries)
            self._rewrite_layout_files(deduped)
            self._write_layout_version()
            self._read_only_mode = False
            self._migration_error = None
        except Exception as exc:
            self._read_only_mode = True
            self._migration_error = str(exc)
            logger.warning(
                "记忆布局迁移失败，已降级为只读模式，错误=%s",
                self._migration_error,
                exc_info=True,
            )
            if backup_dir is not None:
                try:
                    self._restore_from_backup(backup_dir)
                except Exception:
                    logger.warning("回滚记忆备份失败: %s", backup_dir, exc_info=True)

    def _create_migration_backup(self, filenames: list[str]) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self._memory_dir / _MIGRATION_BACKUP_DIR / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            shutil.copy2(self._memory_dir / filename, backup_dir / filename)
        return backup_dir

    def _restore_from_backup(self, backup_dir: Path) -> None:
        for item in backup_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, self._memory_dir / item.name)

    def _collect_entries_for_migration(self, filenames: list[str]) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for filename in filenames:
            filepath = self._memory_dir / filename
            try:
                raw = filepath.read_text(encoding="utf-8")
            except OSError:
                continue
            parsed = parse_entries(raw)
            if parsed:
                entries.extend(parsed)
                continue
            normalized = raw.strip()
            if not normalized:
                continue
            inferred = _infer_category_by_filename(filename)
            if inferred is None:
                continue
            entries.append(
                MemoryEntry(content=normalized, category=inferred, timestamp=datetime.now())
            )
        return entries

    def _dedupe_entries_for_migration(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        latest_by_key: dict[tuple[str, str], MemoryEntry] = {}
        for entry in sorted(entries, key=lambda e: e.timestamp):
            normalized = normalize_content_key(entry.content)
            if not normalized:
                continue
            key = (entry.category.value, normalized)
            prev = latest_by_key.get(key)
            if prev is None or entry.timestamp >= prev.timestamp:
                latest_by_key[key] = entry
        return sorted(latest_by_key.values(), key=lambda e: e.timestamp)

    def _rewrite_layout_files(self, entries: list[MemoryEntry]) -> None:
        grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
        for entry in entries:
            filename = CATEGORY_TOPIC_MAP.get(entry.category)
            if filename:
                grouped[filename].append(entry)
        grouped[CORE_MEMORY_FILE] = list(entries)

        target_files = set(grouped)
        target_files.update(CATEGORY_TOPIC_MAP.values())
        target_files.add(CORE_MEMORY_FILE)

        for filename in sorted(target_files):
            filepath = self._memory_dir / filename
            file_entries = grouped.get(filename, [])
            if not file_entries:
                if filepath.exists():
                    filepath.unlink()
                continue
            self._atomic_write(filepath, format_entries(file_entries))
            self._enforce_capacity(filepath)

    def _write_layout_version(self) -> None:
        version_path = self._memory_dir / _LAYOUT_VERSION_FILE
        self._atomic_write(version_path, _LAYOUT_VERSION + "\n")


def _infer_category_by_filename(filename: str) -> MemoryCategory | None:
    """从文件名推断记忆类别。"""
    name = filename.strip().lower()
    if name == "file_patterns.md":
        return MemoryCategory.FILE_PATTERN
    if name == "user_prefs.md":
        return MemoryCategory.USER_PREF
    if name == "error_solutions.md":
        return MemoryCategory.ERROR_SOLUTION
    if name in {"general.md", CORE_MEMORY_FILE.lower()}:
        return MemoryCategory.GENERAL
    return None
