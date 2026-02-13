"""memory_models 模块的单元测试。"""

from datetime import datetime

from excelmanus.memory_models import (
    CATEGORY_TOPIC_MAP,
    MemoryCategory,
    MemoryEntry,
)


class TestMemoryCategory:
    """MemoryCategory 枚举测试。"""

    def test_all_categories_defined(self) -> None:
        """验证四个类别均已定义。"""
        assert set(MemoryCategory) == {
            MemoryCategory.FILE_PATTERN,
            MemoryCategory.USER_PREF,
            MemoryCategory.ERROR_SOLUTION,
            MemoryCategory.GENERAL,
        }

    def test_category_values(self) -> None:
        """验证枚举值为预期的字符串。"""
        assert MemoryCategory.FILE_PATTERN.value == "file_pattern"
        assert MemoryCategory.USER_PREF.value == "user_pref"
        assert MemoryCategory.ERROR_SOLUTION.value == "error_solution"
        assert MemoryCategory.GENERAL.value == "general"

    def test_category_is_str(self) -> None:
        """验证 MemoryCategory 继承 str，可直接用作字符串。"""
        assert isinstance(MemoryCategory.FILE_PATTERN, str)
        assert MemoryCategory.GENERAL == "general"


class TestCategoryTopicMap:
    """CATEGORY_TOPIC_MAP 映射测试。"""

    def test_file_pattern_maps_to_file(self) -> None:
        assert CATEGORY_TOPIC_MAP[MemoryCategory.FILE_PATTERN] == "file_patterns.md"

    def test_user_pref_maps_to_file(self) -> None:
        assert CATEGORY_TOPIC_MAP[MemoryCategory.USER_PREF] == "user_prefs.md"

    def test_error_solution_not_in_map(self) -> None:
        """ERROR_SOLUTION 不在映射中，应写入 MEMORY.md。"""
        assert MemoryCategory.ERROR_SOLUTION not in CATEGORY_TOPIC_MAP

    def test_general_not_in_map(self) -> None:
        """GENERAL 不在映射中，应写入 MEMORY.md。"""
        assert MemoryCategory.GENERAL not in CATEGORY_TOPIC_MAP


class TestMemoryEntry:
    """MemoryEntry 数据类测试。"""

    def test_create_with_required_fields(self) -> None:
        ts = datetime(2025, 1, 15, 14, 30)
        entry = MemoryEntry(
            content="测试内容",
            category=MemoryCategory.GENERAL,
            timestamp=ts,
        )
        assert entry.content == "测试内容"
        assert entry.category == MemoryCategory.GENERAL
        assert entry.timestamp == ts
        assert entry.source == ""

    def test_create_with_source(self) -> None:
        ts = datetime(2025, 6, 1, 10, 0)
        entry = MemoryEntry(
            content="用户偏好深色图表",
            category=MemoryCategory.USER_PREF,
            timestamp=ts,
            source="会话 abc-123",
        )
        assert entry.source == "会话 abc-123"

    def test_equality(self) -> None:
        """验证相同字段的 MemoryEntry 相等。"""
        ts = datetime(2025, 1, 1)
        a = MemoryEntry("内容", MemoryCategory.FILE_PATTERN, ts)
        b = MemoryEntry("内容", MemoryCategory.FILE_PATTERN, ts)
        assert a == b
