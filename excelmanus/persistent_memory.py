"""持久记忆管理器：负责记忆文件的读写、加载与容量控制。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from excelmanus.memory_models import CATEGORY_TOPIC_MAP, MemoryCategory, MemoryEntry

logger = logging.getLogger(__name__)

# 核心记忆文件名
CORE_MEMORY_FILE = "MEMORY.md"


class PersistentMemory:
    """持久记忆管理器。

    管理 MemoryDir 下的核心记忆文件（MEMORY.md）和主题文件的读写。
    """

    def __init__(self, memory_dir: str, auto_load_lines: int = 200) -> None:
        """初始化，确保目录存在。

        Args:
            memory_dir: 记忆存储目录路径，支持 ~ 展开。
            auto_load_lines: 自动加载核心记忆的最大行数，默认 200。
        """
        self._memory_dir = Path(memory_dir).expanduser()
        self._auto_load_lines = auto_load_lines
        # 自动创建目录及所有父目录
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def memory_dir(self) -> Path:
        """返回记忆存储目录路径。"""
        return self._memory_dir

    @property
    def auto_load_lines(self) -> int:
        """返回自动加载行数上限。"""
        return self._auto_load_lines

    def load_core(self) -> str:
        """读取 MEMORY.md 前 auto_load_lines 行，返回文本内容。

        文件不存在或为空时返回空字符串。
        """
        filepath = self._memory_dir / CORE_MEMORY_FILE
        if not filepath.exists():
            return ""
        try:
            with filepath.open("r", encoding="utf-8") as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= self._auto_load_lines:
                        break
                    lines.append(line)
            content = "".join(lines)
            # 去除末尾多余换行，但保留内容本身的格式
            return content.rstrip("\n")
        except OSError:
            logger.warning("读取核心记忆文件失败: %s", filepath, exc_info=True)
            return ""

    # --- 条目头部正则：### [YYYY-MM-DD HH:MM] category ---
    _ENTRY_HEADER_RE = re.compile(
        r"^###\s+\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})]\s+(\S+)\s*$"
    )
    _TIMESTAMP_FMT = "%Y-%m-%d %H:%M"

    def format_entries(self, entries: list[MemoryEntry]) -> str:
        """将 MemoryEntry 列表序列化为 Markdown 文本。

        每条条目格式：
            ### [YYYY-MM-DD HH:MM] category

            content

            ---
        """
        if not entries:
            return ""
        parts: list[str] = []
        for entry in entries:
            ts = entry.timestamp.strftime(self._TIMESTAMP_FMT)
            header = f"### [{ts}] {entry.category.value}"
            parts.append(f"{header}\n\n{entry.content}\n\n---")
        return "\n\n".join(parts)

    def _parse_entries(self, content: str) -> list[MemoryEntry]:
        """将 Markdown 文本解析为 MemoryEntry 列表（内部实现）。

        解析规则：
        - 以 ``### [时间戳] 类别`` 开头的行标记一条新条目
        - 条目正文为该标记行之后、下一个 ``---`` 分隔线之前的所有非空行
        - 格式不合规的条目会被跳过并记录 WARNING 日志
        """
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
        """兼容入口：解析 Markdown 为结构化条目。

        当前运行时主流程仅做写入和按行加载原始文本，不依赖结构化解析。
        本方法主要用于测试 round-trip 验证，以及未来可能的记忆管理能力
        （例如检索、过滤、去重合并）。
        """
        return self._parse_entries(content)

    def load_topic(self, topic_name: str) -> str:
        """按需读取指定主题文件的全部内容。

        Args:
            topic_name: 主题文件名，如 "file_patterns.md"、"user_prefs.md"。

        Returns:
            文件内容字符串，文件不存在时返回空字符串。
        """
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
        """将记忆条目按类别分发写入对应文件。

        分发逻辑：
        - FILE_PATTERN → file_patterns.md
        - USER_PREF → user_prefs.md
        - ERROR_SOLUTION / GENERAL（及其他未映射类别）→ MEMORY.md

        使用临时文件 + 原子重命名确保写入完整性。
        写入失败时捕获 OSError，记录 WARNING 日志，不中断。
        """
        if not entries:
            return

        # 按目标文件名分组
        grouped: dict[str, list[MemoryEntry]] = defaultdict(list)
        for entry in entries:
            filename = CATEGORY_TOPIC_MAP.get(entry.category, CORE_MEMORY_FILE)
            grouped[filename].append(entry)

        # 逐文件追加写入
        for filename, file_entries in grouped.items():
            filepath = self._memory_dir / filename
            try:
                # 读取现有内容
                existing = ""
                if filepath.exists():
                    existing = filepath.read_text(encoding="utf-8")

                # 序列化新条目
                new_content = self.format_entries(file_entries)

                # 拼接：现有内容 + 分隔 + 新条目
                if existing.strip():
                    combined = existing.rstrip("\n") + "\n\n" + new_content
                else:
                    combined = new_content

                # 临时文件 + 原子重命名
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self._memory_dir), suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                        tmp_f.write(combined)
                    os.replace(tmp_path, str(filepath))
                except BaseException:
                    # 清理临时文件
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            except OSError:
                logger.warning(
                    "写入记忆文件失败: %s", filepath, exc_info=True
                )
                continue

            # 写入成功后检查容量
            self._enforce_capacity(filepath)

    def _enforce_capacity(self, filepath: Path) -> None:
        """容量管理：当文件超过 500 行时，保留最近条目使其降至 400 行以内。

        从文件末尾向前保留完整条目（以 ``### [时间戳] 类别`` 开头的条目块），
        使总行数不超过 400 行。使用临时文件 + 原子重命名写回。
        """
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
        # 找到所有条目起始行的索引
        entry_starts: list[int] = []
        for i, line in enumerate(all_lines):
            if self._ENTRY_HEADER_RE.match(line):
                entry_starts.append(i)

        if not entry_starts:
            # 没有有效条目头，无法按条目边界截断，保留最后 400 行
            kept_lines = all_lines[-400:]
            removed_count = len(all_lines) - len(kept_lines)
            logger.info(
                "容量管理：%s 超过 500 行（%d 行），无条目边界，保留最后 400 行，移除 %d 行",
                filepath.name, len(all_lines), removed_count,
            )
        else:
            # 从最后一个条目开始，向前逐个添加条目，直到超过 400 行
            kept_start = entry_starts[-1]
            for idx in reversed(entry_starts):
                # 如果包含从 idx 开始的所有行，总行数是否 ≤ 400
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

        # 使用临时文件 + 原子重命名写回
        new_content = "\n".join(kept_lines)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._memory_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                tmp_f.write(new_content)
            os.replace(tmp_path, str(filepath))
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

