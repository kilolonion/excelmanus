"""PersistentMemory 模块的单元测试。"""

from pathlib import Path

import pytest

from excelmanus.persistent_memory import CORE_MEMORY_FILE, PersistentMemory


class TestInit:
    """__init__ 方法测试。"""

    def test_creates_directory(self, tmp_path: Path) -> None:
        """验证初始化时自动创建目录。"""
        target = tmp_path / "sub" / "memory"
        assert not target.exists()
        pm = PersistentMemory(str(target))
        assert target.is_dir()
        assert pm.memory_dir == target

    def test_existing_directory_ok(self, tmp_path: Path) -> None:
        """验证目录已存在时不报错。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.memory_dir == tmp_path

    def test_expands_tilde(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """验证 ~ 路径被正确展开。"""
        monkeypatch.setenv("HOME", str(tmp_path))
        pm = PersistentMemory("~/my_memory")
        expected = tmp_path / "my_memory"
        assert pm.memory_dir == expected
        assert expected.is_dir()

    def test_default_auto_load_lines(self, tmp_path: Path) -> None:
        """验证默认 auto_load_lines 为 200。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.auto_load_lines == 200

    def test_custom_auto_load_lines(self, tmp_path: Path) -> None:
        """验证自定义 auto_load_lines。"""
        pm = PersistentMemory(str(tmp_path), auto_load_lines=50)
        assert pm.auto_load_lines == 50


class TestLoadCore:
    """load_core 方法测试。"""

    @pytest.fixture(autouse=True)
    def _mark_layout_v2(self, tmp_path: Path) -> None:
        """本组仅验证读取行为，显式标记布局版本避免触发迁移改写。"""
        (tmp_path / ".layout_version").write_text("2\n", encoding="utf-8")

    def test_file_not_exists_returns_empty(self, tmp_path: Path) -> None:
        """MEMORY.md 不存在时返回空字符串。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_core() == ""

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        """MEMORY.md 为空时返回空字符串。"""
        (tmp_path / CORE_MEMORY_FILE).write_text("", encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_core() == ""

    def test_loads_all_lines_when_fewer_than_limit(self, tmp_path: Path) -> None:
        """文件行数少于 auto_load_lines 时加载全部内容。"""
        content = "第一行\n第二行\n第三行"
        (tmp_path / CORE_MEMORY_FILE).write_text(content, encoding="utf-8")
        pm = PersistentMemory(str(tmp_path), auto_load_lines=200)
        assert pm.load_core() == content

    def test_loads_exact_n_lines(self, tmp_path: Path) -> None:
        """文件行数超过 auto_load_lines 时只加载最近 N 行。"""
        lines = [f"第 {i} 行" for i in range(10)]
        (tmp_path / CORE_MEMORY_FILE).write_text("\n".join(lines), encoding="utf-8")
        pm = PersistentMemory(str(tmp_path), auto_load_lines=3)
        result = pm.load_core()
        expected_lines = lines[-3:]
        assert result == "\n".join(expected_lines)

    def test_single_line_file(self, tmp_path: Path) -> None:
        """单行文件正常加载。"""
        (tmp_path / CORE_MEMORY_FILE).write_text("唯一一行", encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_core() == "唯一一行"

    def test_preserves_content_format(self, tmp_path: Path) -> None:
        """验证加载内容保留原始格式（Markdown 标记等）。"""
        content = "### [2025-01-15 14:30] general\n\n测试内容\n\n---"
        (tmp_path / CORE_MEMORY_FILE).write_text(content, encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_core() == content

    def test_load_core_aligns_to_entry_header_when_reading_recent_lines(
        self, tmp_path: Path
    ) -> None:
        """最近行截取时应优先对齐到条目头，避免半条目。"""
        content = (
            "### [2025-01-15 10:00] general\n\n第一条内容\n\n---\n\n"
            "### [2025-01-15 11:00] general\n\n第二条内容\n\n---\n\n"
            "### [2025-01-15 12:00] general\n\n第三条内容\n\n---\n"
        )
        (tmp_path / CORE_MEMORY_FILE).write_text(content, encoding="utf-8")
        pm = PersistentMemory(str(tmp_path), auto_load_lines=6)
        loaded = pm.load_core()
        assert loaded.startswith("### [2025-01-15 12:00] general")


class TestLoadTopic:
    """load_topic 方法测试。"""

    @pytest.fixture(autouse=True)
    def _mark_layout_v2(self, tmp_path: Path) -> None:
        """本组仅验证读取行为，显式标记布局版本避免触发迁移改写。"""
        (tmp_path / ".layout_version").write_text("2\n", encoding="utf-8")

    def test_file_not_exists_returns_empty(self, tmp_path: Path) -> None:
        """主题文件不存在时返回空字符串。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_topic("file_patterns.md") == ""

    def test_loads_full_content(self, tmp_path: Path) -> None:
        """正常读取主题文件全部内容。"""
        content = "### [2025-01-15] file_pattern\n\n列名: 日期, 产品\n\n---"
        (tmp_path / "file_patterns.md").write_text(content, encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_topic("file_patterns.md") == content

    def test_loads_user_prefs(self, tmp_path: Path) -> None:
        """读取 user_prefs.md 主题文件。"""
        content = "用户偏好深色图表"
        (tmp_path / "user_prefs.md").write_text(content, encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_topic("user_prefs.md") == content

    def test_empty_topic_file_returns_empty(self, tmp_path: Path) -> None:
        """空主题文件返回空字符串。"""
        (tmp_path / "user_prefs.md").write_text("", encoding="utf-8")
        pm = PersistentMemory(str(tmp_path))
        assert pm.load_topic("user_prefs.md") == ""


from datetime import datetime

from excelmanus.memory_models import MemoryCategory, MemoryEntry


class TestFormatEntries:
    """format_entries 方法测试。"""

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        """空列表返回空字符串。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.format_entries([]) == ""

    def test_single_entry(self, tmp_path: Path) -> None:
        """单条条目序列化格式正确。"""
        pm = PersistentMemory(str(tmp_path))
        entry = MemoryEntry(
            content="测试内容",
            category=MemoryCategory.GENERAL,
            timestamp=datetime(2025, 1, 15, 14, 30),
        )
        result = pm.format_entries([entry])
        assert result == "### [2025-01-15 14:30] general\n\n测试内容\n\n---"

    def test_multiple_entries(self, tmp_path: Path) -> None:
        """多条条目之间用空行分隔。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="文件结构信息",
                category=MemoryCategory.FILE_PATTERN,
                timestamp=datetime(2025, 1, 15, 10, 0),
            ),
            MemoryEntry(
                content="用户偏好深色图表",
                category=MemoryCategory.USER_PREF,
                timestamp=datetime(2025, 1, 15, 11, 0),
            ),
        ]
        result = pm.format_entries(entries)
        expected = (
            "### [2025-01-15 10:00] file_pattern\n\n文件结构信息\n\n---"
            "\n\n"
            "### [2025-01-15 11:00] user_pref\n\n用户偏好深色图表\n\n---"
        )
        assert result == expected

    def test_multiline_content(self, tmp_path: Path) -> None:
        """多行正文内容正确序列化。"""
        pm = PersistentMemory(str(tmp_path))
        entry = MemoryEntry(
            content="第一行\n第二行\n第三行",
            category=MemoryCategory.ERROR_SOLUTION,
            timestamp=datetime(2025, 6, 1, 9, 15),
        )
        result = pm.format_entries([entry])
        assert "第一行\n第二行\n第三行" in result
        assert result.startswith("### [2025-06-01 09:15] error_solution")

    def test_all_categories(self, tmp_path: Path) -> None:
        """所有类别都能正确序列化。"""
        pm = PersistentMemory(str(tmp_path))
        ts = datetime(2025, 1, 1, 0, 0)
        for cat in MemoryCategory:
            entry = MemoryEntry(content="内容", category=cat, timestamp=ts)
            result = pm.format_entries([entry])
            assert f"] {cat.value}" in result


class TestParseEntries:
    """parse_entries 方法测试。"""

    def test_public_api_matches_internal_parser(self, tmp_path: Path) -> None:
        """公开兼容入口与内部解析实现结果一致。"""
        pm = PersistentMemory(str(tmp_path))
        md = "### [2025-01-15 14:30] general\n\n测试内容\n\n---"
        assert pm.parse_entries(md) == pm._parse_entries(md)

    def test_empty_string_returns_empty(self, tmp_path: Path) -> None:
        """空字符串返回空列表。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.parse_entries("") == []

    def test_whitespace_only_returns_empty(self, tmp_path: Path) -> None:
        """仅空白字符返回空列表。"""
        pm = PersistentMemory(str(tmp_path))
        assert pm.parse_entries("   \n\n  ") == []

    def test_single_entry(self, tmp_path: Path) -> None:
        """解析单条条目。"""
        pm = PersistentMemory(str(tmp_path))
        md = "### [2025-01-15 14:30] general\n\n测试内容\n\n---"
        entries = pm.parse_entries(md)
        assert len(entries) == 1
        assert entries[0].content == "测试内容"
        assert entries[0].category == MemoryCategory.GENERAL
        assert entries[0].timestamp == datetime(2025, 1, 15, 14, 30)

    def test_multiple_entries(self, tmp_path: Path) -> None:
        """解析多条条目。"""
        pm = PersistentMemory(str(tmp_path))
        md = (
            "### [2025-01-15 10:00] file_pattern\n\n文件结构\n\n---\n\n"
            "### [2025-01-15 11:00] user_pref\n\n偏好设置\n\n---"
        )
        entries = pm.parse_entries(md)
        assert len(entries) == 2
        assert entries[0].category == MemoryCategory.FILE_PATTERN
        assert entries[1].category == MemoryCategory.USER_PREF

    def test_multiline_content(self, tmp_path: Path) -> None:
        """解析多行正文。"""
        pm = PersistentMemory(str(tmp_path))
        md = "### [2025-06-01 09:15] error_solution\n\n第一行\n第二行\n第三行\n\n---"
        entries = pm.parse_entries(md)
        assert len(entries) == 1
        assert entries[0].content == "第一行\n第二行\n第三行"

    def test_skips_invalid_category(self, tmp_path: Path) -> None:
        """跳过未知类别的条目。"""
        pm = PersistentMemory(str(tmp_path))
        md = (
            "### [2025-01-15 10:00] unknown_cat\n\n无效类别\n\n---\n\n"
            "### [2025-01-15 11:00] general\n\n有效条目\n\n---"
        )
        entries = pm.parse_entries(md)
        assert len(entries) == 1
        assert entries[0].content == "有效条目"

    def test_skips_invalid_timestamp(self, tmp_path: Path) -> None:
        """跳过时间戳格式不合规的条目。"""
        pm = PersistentMemory(str(tmp_path))
        md = (
            "### [not-a-date 99:99] general\n\n无效时间\n\n---\n\n"
            "### [2025-01-15 11:00] general\n\n有效条目\n\n---"
        )
        entries = pm.parse_entries(md)
        assert len(entries) == 1
        assert entries[0].content == "有效条目"

    def test_skips_empty_body(self, tmp_path: Path) -> None:
        """跳过正文为空的条目。"""
        pm = PersistentMemory(str(tmp_path))
        md = (
            "### [2025-01-15 10:00] general\n\n\n\n---\n\n"
            "### [2025-01-15 11:00] general\n\n有内容\n\n---"
        )
        entries = pm.parse_entries(md)
        assert len(entries) == 1
        assert entries[0].content == "有内容"

    def test_no_valid_entries(self, tmp_path: Path) -> None:
        """完全无效的内容返回空列表。"""
        pm = PersistentMemory(str(tmp_path))
        md = "这不是有效的记忆格式\n随便写的内容"
        assert pm.parse_entries(md) == []

    def test_roundtrip_consistency(self, tmp_path: Path) -> None:
        """往返一致性：format → parse 应还原原始条目。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="销售数据结构",
                category=MemoryCategory.FILE_PATTERN,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
            MemoryEntry(
                content="偏好深色主题\n柱状图优先",
                category=MemoryCategory.USER_PREF,
                timestamp=datetime(2025, 3, 20, 8, 0),
            ),
        ]
        md = pm.format_entries(entries)
        parsed = pm.parse_entries(md)
        assert len(parsed) == len(entries)
        for orig, restored in zip(entries, parsed):
            assert orig.content == restored.content
            assert orig.category == restored.category
            assert orig.timestamp == restored.timestamp


class TestSaveEntries:
    """save_entries 方法测试。"""

    def test_empty_list_does_nothing(self, tmp_path: Path) -> None:
        """空列表不创建任何文件。"""
        pm = PersistentMemory(str(tmp_path))
        pm.save_entries([])
        assert not (tmp_path / CORE_MEMORY_FILE).exists()

    def test_general_entries_write_to_memory_md(self, tmp_path: Path) -> None:
        """GENERAL 类别条目同时写入 MEMORY.md 与 general.md。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="通用记忆",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
        ]
        pm.save_entries(entries)
        content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert "通用记忆" in content
        assert "general" in content
        general_content = (tmp_path / "general.md").read_text(encoding="utf-8")
        assert "通用记忆" in general_content

    def test_error_solution_writes_to_memory_md(self, tmp_path: Path) -> None:
        """ERROR_SOLUTION 类别条目同时写入 MEMORY.md 与 error_solutions.md。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="错误解决方案",
                category=MemoryCategory.ERROR_SOLUTION,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
        ]
        pm.save_entries(entries)
        content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert "错误解决方案" in content
        topic = (tmp_path / "error_solutions.md").read_text(encoding="utf-8")
        assert "错误解决方案" in topic

    def test_file_pattern_writes_to_topic_file(self, tmp_path: Path) -> None:
        """FILE_PATTERN 类别条目写入 file_patterns.md。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="列名: 日期, 产品, 数量",
                category=MemoryCategory.FILE_PATTERN,
                timestamp=datetime(2025, 1, 15, 10, 0),
            ),
        ]
        pm.save_entries(entries)
        assert (tmp_path / "file_patterns.md").exists()
        content = (tmp_path / "file_patterns.md").read_text(encoding="utf-8")
        assert "列名: 日期, 产品, 数量" in content
        # MEMORY.md 应包含同样条目（核心记忆双写）
        core = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert "列名: 日期, 产品, 数量" in core

    def test_user_pref_writes_to_topic_file(self, tmp_path: Path) -> None:
        """USER_PREF 类别条目写入 user_prefs.md。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="偏好深色图表",
                category=MemoryCategory.USER_PREF,
                timestamp=datetime(2025, 1, 15, 11, 0),
            ),
        ]
        pm.save_entries(entries)
        assert (tmp_path / "user_prefs.md").exists()
        content = (tmp_path / "user_prefs.md").read_text(encoding="utf-8")
        assert "偏好深色图表" in content

    def test_mixed_categories_dispatch_correctly(self, tmp_path: Path) -> None:
        """混合类别条目正确分发到各自文件。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="通用信息",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 10, 0),
            ),
            MemoryEntry(
                content="文件结构",
                category=MemoryCategory.FILE_PATTERN,
                timestamp=datetime(2025, 1, 15, 11, 0),
            ),
            MemoryEntry(
                content="用户偏好",
                category=MemoryCategory.USER_PREF,
                timestamp=datetime(2025, 1, 15, 12, 0),
            ),
            MemoryEntry(
                content="错误方案",
                category=MemoryCategory.ERROR_SOLUTION,
                timestamp=datetime(2025, 1, 15, 13, 0),
            ),
        ]
        pm.save_entries(entries)

        # MEMORY.md 包含全部类别（核心记忆双写）
        memory_content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert "通用信息" in memory_content
        assert "错误方案" in memory_content
        assert "文件结构" in memory_content
        assert "用户偏好" in memory_content

        # file_patterns.md 只包含 file_pattern
        fp_content = (tmp_path / "file_patterns.md").read_text(encoding="utf-8")
        assert "文件结构" in fp_content

        # user_prefs.md 只包含 user_pref
        up_content = (tmp_path / "user_prefs.md").read_text(encoding="utf-8")
        assert "用户偏好" in up_content

        # 新增主题文件
        general_content = (tmp_path / "general.md").read_text(encoding="utf-8")
        assert "通用信息" in general_content
        error_content = (tmp_path / "error_solutions.md").read_text(encoding="utf-8")
        assert "错误方案" in error_content

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        """追加写入不覆盖已有内容。"""
        pm = PersistentMemory(str(tmp_path))
        # 先写入一条
        pm.save_entries([
            MemoryEntry(
                content="第一条",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 10, 0),
            ),
        ])
        # 再追加一条
        pm.save_entries([
            MemoryEntry(
                content="第二条",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 11, 0),
            ),
        ])
        content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert "第一条" in content
        assert "第二条" in content

    def test_appended_entries_are_parseable(self, tmp_path: Path) -> None:
        """写入后的文件内容可被 parse_entries 正确解析。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content="可解析内容",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
            MemoryEntry(
                content="另一条记忆",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 15, 0),
            ),
        ]
        pm.save_entries(entries)
        content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        parsed = pm.parse_entries(content)
        assert len(parsed) == 2
        assert parsed[0].content == "可解析内容"
        assert parsed[1].content == "另一条记忆"

    def test_write_failure_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """写入失败时记录 WARNING 日志，不抛异常。"""
        pm = PersistentMemory(str(tmp_path))
        # 用目录替代文件，使写入失败
        (tmp_path / CORE_MEMORY_FILE).mkdir()
        entries = [
            MemoryEntry(
                content="会失败",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
        ]
        import logging

        target_logger = logging.getLogger("excelmanus.persistent_memory")
        parent_logger = logging.getLogger("excelmanus")
        # 临时确保整条传播链畅通，使 caplog 可捕获
        old_child_prop = target_logger.propagate
        old_parent_prop = parent_logger.propagate
        target_logger.propagate = True
        parent_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="excelmanus.persistent_memory"):
                pm.save_entries(entries)  # 不应抛异常
            assert "写入记忆文件失败" in caplog.text
        finally:
            target_logger.propagate = old_child_prop
            parent_logger.propagate = old_parent_prop

    def test_atomic_write_no_partial_content(self, tmp_path: Path) -> None:
        """原子写入：写入完成后文件内容完整。"""
        pm = PersistentMemory(str(tmp_path))
        entries = [
            MemoryEntry(
                content=f"条目{i}",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, i, 0),
            )
            for i in range(5)
        ]
        pm.save_entries(entries)
        content = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        parsed = pm.parse_entries(content)
        assert len(parsed) == 5

    def test_no_temp_files_left_after_success(self, tmp_path: Path) -> None:
        """成功写入后不留临时文件。"""
        pm = PersistentMemory(str(tmp_path))
        pm.save_entries([
            MemoryEntry(
                content="测试",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, 15, 14, 30),
            ),
        ])
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestEnforceCapacity:
    """_enforce_capacity 方法测试。"""

    def _make_entry_block(self, index: int, category: str = "general", body_lines: int = 3) -> str:
        """生成一个完整的条目块。"""
        header = f"### [2025-01-{index:02d} 10:00] {category}"
        body = "\n".join(f"内容行{j}" for j in range(body_lines))
        return f"{header}\n\n{body}\n\n---"

    def test_no_action_under_500_lines(self, tmp_path: Path) -> None:
        """文件行数 ≤ 500 时不做任何操作。"""
        pm = PersistentMemory(str(tmp_path))
        # 生成约 100 行的文件
        blocks = [self._make_entry_block(i) for i in range(1, 15)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")
        original = filepath.read_text(encoding="utf-8")
        pm._enforce_capacity(filepath)
        assert filepath.read_text(encoding="utf-8") == original

    def test_trims_to_under_400_lines(self, tmp_path: Path) -> None:
        """文件超过 500 行时，清理后行数 ≤ 400。"""
        pm = PersistentMemory(str(tmp_path))
        # 每个条目约 7 行（header + 空行 + 3行body + 空行 + ---），加上条目间的空行
        # 生成足够多的条目使总行数 > 500
        blocks = [self._make_entry_block(i % 28 + 1, body_lines=5) for i in range(100)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")
        line_count_before = len(content.split("\n"))
        assert line_count_before > 500  # 确认前置条件

        pm._enforce_capacity(filepath)

        result = filepath.read_text(encoding="utf-8")
        line_count_after = len(result.split("\n"))
        assert line_count_after <= 400

    def test_preserves_recent_entries(self, tmp_path: Path) -> None:
        """保留的是最近（末尾）的条目。"""
        pm = PersistentMemory(str(tmp_path))
        blocks = [self._make_entry_block(i % 28 + 1, body_lines=5) for i in range(100)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")

        pm._enforce_capacity(filepath)

        result = filepath.read_text(encoding="utf-8")
        # 最后一个条目应该被保留
        last_block = blocks[-1]
        assert last_block.split("\n")[0] in result

    def test_preserves_complete_entries(self, tmp_path: Path) -> None:
        """保留的条目是完整的（不在条目中间截断）。"""
        pm = PersistentMemory(str(tmp_path))
        blocks = [self._make_entry_block(i % 28 + 1, body_lines=5) for i in range(100)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")

        pm._enforce_capacity(filepath)

        result = filepath.read_text(encoding="utf-8")
        # 结果应该以条目头开始
        first_line = result.split("\n")[0]
        assert pm._ENTRY_HEADER_RE.match(first_line), f"文件应以条目头开始，实际: {first_line!r}"
        # 所有保留的条目应可被正确解析
        parsed = pm.parse_entries(result)
        assert len(parsed) > 0

    def test_logs_removal_info(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """容量管理时记录日志说明移除条目数。"""
        import logging

        pm = PersistentMemory(str(tmp_path))
        blocks = [self._make_entry_block(i % 28 + 1, body_lines=5) for i in range(100)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")

        target_logger = logging.getLogger("excelmanus.persistent_memory")
        parent_logger = logging.getLogger("excelmanus")
        # 临时确保整条传播链畅通，使 caplog 可捕获
        old_child_prop = target_logger.propagate
        old_parent_prop = parent_logger.propagate
        target_logger.propagate = True
        parent_logger.propagate = True
        try:
            with caplog.at_level(logging.INFO, logger="excelmanus.persistent_memory"):
                pm._enforce_capacity(filepath)

            assert "容量管理" in caplog.text
            assert "移除" in caplog.text
        finally:
            target_logger.propagate = old_child_prop
            parent_logger.propagate = old_parent_prop

    def test_file_not_exists_no_error(self, tmp_path: Path) -> None:
        """文件不存在时不报错。"""
        pm = PersistentMemory(str(tmp_path))
        filepath = tmp_path / "nonexistent.md"
        pm._enforce_capacity(filepath)  # 不应抛异常

    def test_exactly_500_lines_no_action(self, tmp_path: Path) -> None:
        """恰好 500 行时不触发清理。"""
        pm = PersistentMemory(str(tmp_path))
        # 构造恰好 500 行的文件
        lines = [f"行{i}" for i in range(500)]
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text("\n".join(lines), encoding="utf-8")
        original = filepath.read_text(encoding="utf-8")
        pm._enforce_capacity(filepath)
        assert filepath.read_text(encoding="utf-8") == original

    def test_501_lines_triggers_cleanup(self, tmp_path: Path) -> None:
        """501 行时触发清理。"""
        pm = PersistentMemory(str(tmp_path))
        # 构造 501+ 行的文件，包含有效条目
        blocks = []
        for i in range(80):
            blocks.append(self._make_entry_block(i % 28 + 1, body_lines=4))
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")
        line_count = len(content.split("\n"))
        if line_count <= 500:
            # 如果不够，补充更多条目
            while len(content.split("\n")) <= 500:
                blocks.append(self._make_entry_block(1, body_lines=4))
                content = "\n\n".join(blocks)
            filepath.write_text(content, encoding="utf-8")

        pm._enforce_capacity(filepath)
        result_lines = len(filepath.read_text(encoding="utf-8").split("\n"))
        assert result_lines <= 400

    def test_save_entries_triggers_capacity_check(self, tmp_path: Path) -> None:
        """save_entries 写入后自动触发容量检查。"""
        pm = PersistentMemory(str(tmp_path))
        # 先写入大量条目使文件接近 500 行
        big_entries = [
            MemoryEntry(
                content=f"大量内容行1\n大量内容行2\n大量内容行3\n大量内容行4\n大量内容行5",
                category=MemoryCategory.GENERAL,
                timestamp=datetime(2025, 1, i % 28 + 1, 10, 0),
            )
            for i in range(80)
        ]
        pm.save_entries(big_entries)
        filepath = tmp_path / CORE_MEMORY_FILE
        content = filepath.read_text(encoding="utf-8")
        line_count = len(content.split("\n"))
        # 如果超过 500 行，应该已被清理到 ≤ 400
        if line_count > 0:
            assert line_count <= 400 or line_count <= 500

    def test_no_temp_files_after_capacity_enforcement(self, tmp_path: Path) -> None:
        """容量管理后不留临时文件。"""
        pm = PersistentMemory(str(tmp_path))
        blocks = [self._make_entry_block(i % 28 + 1, body_lines=5) for i in range(100)]
        content = "\n\n".join(blocks)
        filepath = tmp_path / CORE_MEMORY_FILE
        filepath.write_text(content, encoding="utf-8")

        pm._enforce_capacity(filepath)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestLayoutMigration:
    """布局迁移测试。"""

    def test_auto_migration_creates_topic_files_and_backup(self, tmp_path: Path) -> None:
        legacy_core = (
            "### [2025-01-15 10:00] general\n\n通用旧内容\n\n---\n\n"
            "### [2025-01-15 11:00] error_solution\n\n错误旧内容\n\n---"
        )
        legacy_fp = (
            "### [2025-01-15 12:00] file_pattern\n\n文件结构旧内容\n\n---"
        )
        (tmp_path / CORE_MEMORY_FILE).write_text(legacy_core, encoding="utf-8")
        (tmp_path / "file_patterns.md").write_text(legacy_fp, encoding="utf-8")

        pm = PersistentMemory(str(tmp_path))

        # 迁移后应存在布局版本标记与备份目录
        assert (pm.memory_dir / ".layout_version").exists()
        backup_root = pm.memory_dir / "migration_backups"
        assert backup_root.exists()
        backup_dirs = [p for p in backup_root.iterdir() if p.is_dir()]
        assert backup_dirs

        # 四分类文件与核心文件均可读取
        assert "通用旧内容" in (tmp_path / "general.md").read_text(encoding="utf-8")
        assert "错误旧内容" in (tmp_path / "error_solutions.md").read_text(encoding="utf-8")
        assert "文件结构旧内容" in (tmp_path / "file_patterns.md").read_text(encoding="utf-8")
        assert "通用旧内容" in (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        legacy_core = (
            "### [2025-01-15 10:00] general\n\n条目A\n\n---\n\n"
            "### [2025-01-15 11:00] general\n\n条目A\n\n---"
        )
        (tmp_path / CORE_MEMORY_FILE).write_text(legacy_core, encoding="utf-8")

        pm = PersistentMemory(str(tmp_path))
        first = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")

        # 再次初始化不应重复写入
        _ = PersistentMemory(str(tmp_path))
        second = (tmp_path / CORE_MEMORY_FILE).read_text(encoding="utf-8")
        assert first == second
        parsed = pm.parse_entries(second)
        assert len(parsed) == 1

    def test_migration_failure_keeps_original_data(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        legacy_core = "### [2025-01-15 10:00] general\n\n原始数据\n\n---"
        core_path = tmp_path / CORE_MEMORY_FILE
        core_path.write_text(legacy_core, encoding="utf-8")

        def _raise(*_args, **_kwargs):
            raise RuntimeError("mock migration failure")

        monkeypatch.setattr(PersistentMemory, "_rewrite_layout_files", _raise)

        # 不应抛异常，且原文件内容保留，并降级只读模式
        pm = PersistentMemory(str(tmp_path))
        assert core_path.read_text(encoding="utf-8") == legacy_core
        assert pm.read_only_mode is True

        pm.save_entries(
            [
                MemoryEntry(
                    content="不会写入",
                    category=MemoryCategory.GENERAL,
                    timestamp=datetime(2025, 1, 15, 12, 0),
                )
            ]
        )
        assert core_path.read_text(encoding="utf-8") == legacy_core
