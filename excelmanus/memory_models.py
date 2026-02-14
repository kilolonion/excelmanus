"""记忆数据模型：定义记忆类别、条目结构和类别-主题文件映射。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class MemoryCategory(str, Enum):
    """记忆类别枚举。"""

    FILE_PATTERN = "file_pattern"
    USER_PREF = "user_pref"
    ERROR_SOLUTION = "error_solution"
    GENERAL = "general"


# 类别到主题文件的映射
# 所有类别均有独立主题文件；核心 MEMORY.md 由持久化层统一维护。
CATEGORY_TOPIC_MAP: dict[MemoryCategory, str] = {
    MemoryCategory.FILE_PATTERN: "file_patterns.md",
    MemoryCategory.USER_PREF: "user_prefs.md",
    MemoryCategory.ERROR_SOLUTION: "error_solutions.md",
    MemoryCategory.GENERAL: "general.md",
}


@dataclass
class MemoryEntry:
    """单条记忆条目。"""

    content: str  # 记忆正文
    category: MemoryCategory  # 所属类别
    timestamp: datetime  # 创建时间
    source: str = ""  # 来源描述（可选）
