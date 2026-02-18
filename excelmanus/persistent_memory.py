"""持久记忆管理器：负责记忆文件的读写、加载与容量控制。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from excelmanus.memory_models import CATEGORY_TOPIC_MAP, MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

# 核心记忆文件名
CORE_MEMORY_FILE = "MEMORY.md"
_LAYOUT_VERSION_FILE = ".layout_version"
_LAYOUT_VERSION = "2"
_MIGRATION_BACKUP_DIR = "migration_backups"
_RECENT_DEDUPE_WINDOW = 200


class PersistentMemory:
    """持久记忆管理器。

    管理 MemoryDir 下的核心记忆文件（MEMORY.md）和主题文件的读写。
    """

    def __init__(self, memory_dir: str, auto_load_lines: int = 200) -> None:
        """初始化，确保目录存在。"""
        self._memory_dir = Path(memory_dir).expanduser()
        self._auto_load_lines = auto_load_lines
        self._read_only_mode = False
        self._migration_error: str | None = None
        # 自动创建目录及所有父目录
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_layout_if_needed()

    @property
    def memory_dir(self) -> Path:
        """返回记忆存储目录路径。"""
        return self._memory_dir

    @property
    def auto_load_lines(self) -> int:
        """返回自动加载行数上限。"""
        return self._auto_load_lines

    @property
    def read_only_mode(self) -> bool:
        """迁移失败时会进入只读降级模式。"""
        return self._read_only_mode

    # --- 条目头部正则：### [YYYY-MM-DD HH:MM] category ---
    _ENTRY_HEADER_RE = re.compile(
        r"^###\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})]\s+(\S+)\s*$"
    )
    _TIMESTAMP_FMT = "%Y-%m-%d %H:%M"

    def load_core(self) -> str:
        """读取 MEMORY.md 最近 auto_load_lines 行，返回文本内容。

        文件不存在或为空时返回空字符串。
        为避免从条目中间开始，优先对齐到最近窗口内的条目头。
        """
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
        if len(lines) <= self._auto_load_lines:
            return "".join(lines).rstrip("\n")

        selected = lines[-self._auto_load_lines:]
        start_idx: int | None = None
        for idx, line in enumerate(selected):
            if self._ENTRY_HEADER_RE.match(line.rstrip("\n")):
                start_idx = idx
                break
        if start_idx is not None:
            selected = selected[start_idx:]

        return "".join(selected).rstrip("\n")

    def format_entries(self, entries: list[MemoryEntry]) -> str:
        """将 MemoryEntry 列表序列化为 Markdown 文本。"""
        if not entries:
            return ""
        parts: list[str] = []
        for entry in entries:
            ts = entry.timestamp.strftime(self._TIMESTAMP_FMT)
            header = f"### [{ts}] {entry.category.value}"
            parts.append(f"{header}\n\n{entry.content}\n\n---")
        return "\n\n".join(parts)

    def _parse_entries(self, content: str) -> list[MemoryEntry]:
        """将 Markdown 文本解析为 MemoryEntry 列表（内部实现）。"""
        if not content or not content.strip():
            return []

        entries: list[MemoryEntry] = []
        lines = content.split("\n")

        i = 0
        while i < len(lines):
            line = lines[i]
            match = self._ENTRY_HEADER_RE.match(line)
            if not match:
                i += 1
                continue

            ts_str, cat_str = match.group(1), match.group(2)

            # 解析时间戳
            try:
                timestamp = datetime.strptime(ts_str, self._TIMESTAMP_FMT)
            except ValueError:
                logger.warning("跳过时间戳格式不合规的条目: %s", line)
                i += 1
                continue

            # 解析类别
            try:
                category = MemoryCategory(cat_str)
            except ValueError:
                logger.warning("跳过未知类别的条目: %s", line)
                i += 1
                continue

            # 收集正文：从 header 下一行到 --- 分隔线之前
            i += 1
            body_lines: list[str] = []
            while i < len(lines):
                if lines[i].strip() == "---":
                    i += 1  # 跳过分隔线
                    break
                body_lines.append(lines[i])
                i += 1

            # 去除首尾空行，合并正文
            body = "\n".join(body_lines).strip()
            if not body:
                logger.warning("跳过正文为空的条目: ts=%s, category=%s", ts_str, cat_str)
                continue

            entries.append(
                MemoryEntry(
                    content=body,
                    category=category,
                    timestamp=timestamp,
                )
            )

        return entries

    def parse_entries(self, content: str) -> list[MemoryEntry]:
        """兼容入口：解析 Markdown 为结构化条目。"""
        return self._parse_entries(content)

    def load_topic(self, topic_name: str) -> str:
        """按需读取指定主题文件的全部内容。"""
        filepath = self._memory_dir / topic_name
        if not filepath.exists():
            return ""
        try:
            content = filepath.read_text(encoding="utf-8")
            return content.rstrip("\n")
        except OSError:
            logger.warning("读取主题文件失败: %s", filepath, exc_info=True)
            return ""

    def save_entries(self, entries: list[MemoryEntry]) -> None:
        """将记忆条目按类别分发写入对应文件，并同步写入核心 MEMORY.md。

        设计说明（双写）：
        - 每条记忆同时写入对应主题文件（file_patterns.md 等）和 MEMORY.md。
        - MEMORY.md 作为核心文件，支持自动加载全量记忆；主题文件支持按需加载。
        - 为防止两个文件内容漂移后重复积累，写入前先收集所有目标文件的现有
          content key，作为跨文件去重基准（global_seen_keys）传入每次写入。
        """
        if not entries:
            return
        if self._read_only_mode:
            logger.warning(
                "持久记忆处于只读降级模式，跳过写入 %d 条记忆条目",
                len(entries),
            )
            return

        grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
        for entry in entries:
            filename = CATEGORY_TOPIC_MAP.get(entry.category)
            if filename is not None:
                grouped[filename].append(entry)

        # 核心记忆文件保留所有类别，便于自动加载（双写设计）
        grouped[CORE_MEMORY_FILE].extend(entries)

        # 跨文件去重：预先收集所有目标文件的现有 content key，
        # 避免同一条目因文件漂移而在多个文件中重复写入。
        global_seen_keys: set[tuple[str, str]] = set()
        for filename in grouped:
            filepath = self._memory_dir / filename
            if filepath.exists():
                try:
                    existing_text = filepath.read_text(encoding="utf-8")
                    for e in self._parse_entries(existing_text)[-_RECENT_DEDUPE_WINDOW:]:
                        normalized = self._normalize_content_key(e.content)
                        if normalized:
                            global_seen_keys.add((e.category.value, normalized))
                except OSError:
                    pass  # 读取失败时降级为文件内去重

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

    def _append_entries(
        self,
        filepath: Path,
        new_entries: list[MemoryEntry],
        global_seen_keys: set[tuple[str, str]] | None = None,
    ) -> None:
        existing = ""
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8")

        existing_entries = self._parse_entries(existing)
        filtered_entries = self._dedupe_new_entries(
            existing_entries, new_entries, global_seen_keys
        )
        if not filtered_entries:
            return

        new_content = self.format_entries(filtered_entries)
        if existing.strip():
            combined = existing.rstrip("\n") + "\n\n" + new_content
        else:
            combined = new_content

        self._atomic_write(filepath, combined)

    @staticmethod
    def _normalize_content_key(text: str) -> str:
        return " ".join((text or "").split())

    def _dedupe_new_entries(
        self,
        existing_entries: list[MemoryEntry],
        new_entries: list[MemoryEntry],
        global_seen_keys: set[tuple[str, str]] | None = None,
    ) -> list[MemoryEntry]:
        existing_keys = {
            (entry.category.value, self._normalize_content_key(entry.content))
            for entry in existing_entries[-_RECENT_DEDUPE_WINDOW:]
            if self._normalize_content_key(entry.content)
        }
        # 合并跨文件已知 keys（来自 save_entries 的全局去重基准）
        if global_seen_keys:
            existing_keys |= global_seen_keys

        batch_keys: set[tuple[str, str]] = set()
        result: list[MemoryEntry] = []
        for entry in new_entries:
            normalized = self._normalize_content_key(entry.content)
            if not normalized:
                continue
            key = (entry.category.value, normalized)
            if key in existing_keys or key in batch_keys:
                continue
            batch_keys.add(key)
            result.append(entry)
        return result

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
        """容量管理：当文件超过 500 行时，保留最近条目使其降至 400 行以内。"""
        if not filepath.exists():
            return

        try:
            all_lines = filepath.read_text(encoding="utf-8").split("\n")
        except OSError:
            logger.warning("容量检查时读取文件失败: %s", filepath, exc_info=True)
            return

        if len(all_lines) <= 500:
            return

        # 从末尾向前扫描，按条目边界保留，使总行数 ≤ 400
        entry_starts: list[int] = []
        for i, line in enumerate(all_lines):
            if self._ENTRY_HEADER_RE.match(line):
                entry_starts.append(i)

        if not entry_starts:
            kept_lines = all_lines[-400:]
            removed_count = len(all_lines) - len(kept_lines)
            logger.info(
                "容量管理：%s 超过 500 行（%d 行），无条目边界，保留最后 400 行，移除 %d 行",
                filepath.name, len(all_lines), removed_count,
            )
        else:
            # 边界情况：最后一个条目本身就超过 400 行，无法按条目边界裁剪，直接截断行
            if len(all_lines) - entry_starts[-1] > 400:
                kept_lines = all_lines[-400:]
                removed_count = len(all_lines) - len(kept_lines)
                logger.info(
                    "容量管理：%s 超过 500 行（%d 行），最后一个条目本身超过 400 行，强制保留最后 400 行，移除 %d 行",
                    filepath.name, len(all_lines), removed_count,
                )
            else:
                kept_start = entry_starts[-1]
                for idx in reversed(entry_starts):
                    line_count = len(all_lines) - idx
                    if line_count <= 400:
                        kept_start = idx
                    else:
                        break

                kept_lines = all_lines[kept_start:]
                total_entries = len(entry_starts)
                kept_entries = sum(1 for s in entry_starts if s >= kept_start)
                removed_entries = total_entries - kept_entries
                logger.info(
                    "容量管理：%s 超过 500 行（%d 行），保留最近 %d 条条目（%d 行），移除 %d 条旧条目",
                    filepath.name, len(all_lines), kept_entries, len(kept_lines), removed_entries,
                )

        self._atomic_write(filepath, "\n".join(kept_lines))

    def _migrate_layout_if_needed(self) -> None:
        """迁移旧布局到当前版本，并保留备份。"""
        version_path = self._memory_dir / _LAYOUT_VERSION_FILE
        if version_path.exists():
            try:
                if version_path.read_text(encoding="utf-8").strip() == _LAYOUT_VERSION:
                    return
            except OSError:
                logger.warning("读取布局版本文件失败: %s", version_path, exc_info=True)

        candidate_files = {
            CORE_MEMORY_FILE,
            "file_patterns.md",
            "user_prefs.md",
            "error_solutions.md",
            "general.md",
        }
        candidate_files.update(CATEGORY_TOPIC_MAP.values())
        existing_files = [
            filename for filename in sorted(candidate_files)
            if (self._memory_dir / filename).exists()
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
            logger.info(
                "记忆布局迁移完成: version=%s, entries_before=%d, entries_after=%d, backup=%s",
                _LAYOUT_VERSION,
                len(entries),
                len(deduped),
                backup_dir,
            )
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
            src = self._memory_dir / filename
            dst = backup_dir / filename
            shutil.copy2(src, dst)
        return backup_dir

    def _restore_from_backup(self, backup_dir: Path) -> None:
        for item in backup_dir.iterdir():
            if not item.is_file():
                continue
            shutil.copy2(item, self._memory_dir / item.name)

    def _collect_entries_for_migration(self, filenames: list[str]) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for filename in filenames:
            filepath = self._memory_dir / filename
            try:
                raw = filepath.read_text(encoding="utf-8")
            except OSError:
                logger.warning("迁移读取失败: %s", filepath, exc_info=True)
                continue
            parsed = self._parse_entries(raw)
            if parsed:
                entries.extend(parsed)
                continue
            # 兼容历史“非条目化纯文本”文件：按文件名回填为单条记忆，避免迁移丢失。
            normalized = raw.strip()
            if not normalized:
                continue
            inferred = self._infer_category_by_filename(filename)
            if inferred is None:
                logger.warning("迁移跳过无法识别类别的文件: %s", filename)
                continue
            entries.append(
                MemoryEntry(
                    content=normalized,
                    category=inferred,
                    timestamp=datetime.now(),
                )
            )
        return entries

    @staticmethod
    def _infer_category_by_filename(filename: str) -> MemoryCategory | None:
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

    def _dedupe_entries_for_migration(
        self,
        entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        latest_by_key: dict[tuple[str, str], MemoryEntry] = {}
        for entry in sorted(entries, key=lambda item: item.timestamp):
            normalized = self._normalize_content_key(entry.content)
            if not normalized:
                continue
            key = (entry.category.value, normalized)
            prev = latest_by_key.get(key)
            if prev is None or entry.timestamp >= prev.timestamp:
                latest_by_key[key] = entry
        return sorted(latest_by_key.values(), key=lambda item: item.timestamp)

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
            content = self.format_entries(file_entries)
            self._atomic_write(filepath, content)
            self._enforce_capacity(filepath)

    def _write_layout_version(self) -> None:
        version_path = self._memory_dir / _LAYOUT_VERSION_FILE
        self._atomic_write(version_path, _LAYOUT_VERSION + "\n")
