"""单元测试：CapabilityMapGenerator 能力图谱生成器。

验证能力图谱的完整性、权限标注正确性、MCP 检测逻辑和边界情况。
"""

from __future__ import annotations

import logging

import pytest

from excelmanus.introspection.capability_map import (
    CATEGORY_DISPLAY_NAMES,
    ICON_AUDIT_ONLY,
    ICON_CONFIRM,
    ICON_DEFAULT,
    ICON_MCP,
    ICON_READ_ONLY,
    INTROSPECTION_GUIDANCE,
    CapabilityMapGenerator,
)
from excelmanus.tools.policy import (
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    TOOL_CATEGORIES,
    TOOL_SHORT_DESCRIPTIONS,
)
from excelmanus.tools.registry import ToolDef, ToolRegistry


# ── Fixtures ──────────────────────────────────────────────


def _make_registry(tool_names: list[str]) -> ToolRegistry:
    """创建包含指定工具名的 ToolRegistry。"""
    reg = ToolRegistry()
    for name in tool_names:
        reg.register_tool(
            ToolDef(
                name=name,
                description=f"desc of {name}",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
            )
        )
    return reg


def _all_categorized_tools() -> list[str]:
    """返回 TOOL_CATEGORIES 中所有工具名。"""
    result = []
    for names in TOOL_CATEGORIES.values():
        result.extend(names)
    return result


# ── 基本生成测试 ──────────────────────────────────────────


class TestGenerate:
    """测试 generate() 输出的完整性和格式。"""

    def test_output_contains_header(self) -> None:
        """输出包含 '## 能力范围' 标题。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        assert "## 能力范围" in output

    def test_all_categories_present(self) -> None:
        """输出包含所有分类段落。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        for cat_name in TOOL_CATEGORIES:
            display = CATEGORY_DISPLAY_NAMES[cat_name]
            assert f"### {display}" in output, f"缺少分类: {cat_name}"

    def test_all_tools_present(self) -> None:
        """输出包含 TOOL_CATEGORIES 中的所有工具名。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        for tool_names in TOOL_CATEGORIES.values():
            for tool_name in tool_names:
                assert tool_name in output, f"缺少工具: {tool_name}"

    def test_descriptions_present(self) -> None:
        """输出包含每个工具的描述。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        for tool_name, desc in TOOL_SHORT_DESCRIPTIONS.items():
            if tool_name in output:
                assert desc in output, f"工具 {tool_name} 缺少描述"

    def test_introspection_guidance_appended(self) -> None:
        """输出末尾包含自省指引段落。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        assert INTROSPECTION_GUIDANCE in output

    def test_output_is_nonempty_string(self) -> None:
        """输出为非空字符串。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        assert isinstance(output, str)
        assert len(output) > 0


# ── 权限标注测试 ──────────────────────────────────────────


class TestClassifyPermission:
    """测试 _classify_permission() 权限分类逻辑。"""

    def test_read_only_tool(self) -> None:
        reg = _make_registry([])
        gen = CapabilityMapGenerator(reg)
        assert gen._classify_permission("read_excel") == ICON_READ_ONLY

    def test_confirm_tool(self) -> None:
        reg = _make_registry([])
        gen = CapabilityMapGenerator(reg)
        assert gen._classify_permission("delete_file") == ICON_CONFIRM

    def test_audit_only_tool(self) -> None:
        reg = _make_registry([])
        gen = CapabilityMapGenerator(reg)
        assert gen._classify_permission("copy_file") == ICON_AUDIT_ONLY

    def test_dynamic_tool_gets_default(self) -> None:
        """run_code 不在三个权限集合中，应返回默认图标。"""
        reg = _make_registry([])
        gen = CapabilityMapGenerator(reg)
        assert gen._classify_permission("run_code") == ICON_DEFAULT

    def test_unknown_tool_gets_default(self) -> None:
        """未知工具应返回默认图标。"""
        reg = _make_registry([])
        gen = CapabilityMapGenerator(reg)
        assert gen._classify_permission("nonexistent_tool") == ICON_DEFAULT

    def test_each_tool_has_exactly_one_icon(self) -> None:
        """generate() 输出中每个工具行只有一个权限图标。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        icons = [ICON_READ_ONLY, ICON_AUDIT_ONLY, ICON_CONFIRM]
        for line in output.splitlines():
            # 仅检查工具行（包含 " — " 分隔符的行）
            if line.startswith("- ") and " — " in line:
                count = sum(1 for icon in icons if icon in line)
                assert count == 1, f"工具行应有且仅有一个权限图标: {line}"


# ── MCP 检测测试 ──────────────────────────────────────────


class TestDetectMcpTools:
    """测试 _detect_mcp_tools() MCP 工具检测逻辑。"""

    def test_no_mcp_when_only_categorized(self) -> None:
        """仅注册分类内工具时，无 MCP 工具。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        assert gen._detect_mcp_tools() == []

    def test_detects_unknown_tool_as_mcp(self) -> None:
        """注册不在分类中的工具应被检测为 MCP。"""
        tools = _all_categorized_tools() + ["mcp_weather"]
        reg = _make_registry(tools)
        gen = CapabilityMapGenerator(reg)
        mcp = gen._detect_mcp_tools()
        assert "mcp_weather" in mcp

    def test_mcp_section_in_output(self) -> None:
        """有 MCP 工具时，输出包含扩展能力段落。"""
        tools = _all_categorized_tools() + ["mcp_weather"]
        reg = _make_registry(tools)
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        assert "### 扩展能力 (MCP)" in output
        assert f"{ICON_MCP} mcp_weather" in output

    def test_no_mcp_section_without_mcp_tools(self) -> None:
        """无 MCP 工具时，输出不包含扩展能力段落。"""
        reg = _make_registry(_all_categorized_tools())
        gen = CapabilityMapGenerator(reg)
        output = gen.generate()
        assert "### 扩展能力 (MCP)" not in output

    def test_internal_tools_not_flagged_as_mcp(self) -> None:
        """内部工具（如 memory_read_topic）不应被标记为 MCP。"""
        internal_tools = ["memory_read_topic", "task_create", "task_update"]
        tools = _all_categorized_tools() + internal_tools
        reg = _make_registry(tools)
        gen = CapabilityMapGenerator(reg)
        mcp = gen._detect_mcp_tools()
        for name in internal_tools:
            assert name not in mcp, f"内部工具 {name} 不应被标记为 MCP"


# ── 缺失描述 warning 日志测试 ─────────────────────────────


class TestMissingDescription:
    """测试缺失描述时的 warning 日志。"""

    def test_missing_description_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """TOOL_CATEGORIES 中的工具缺少描述时应记录 warning。"""
        categories = {"test_cat": ("tool_a",)}
        descriptions: dict[str, str] = {}  # tool_a 无描述
        reg = _make_registry(["tool_a"])
        gen = CapabilityMapGenerator(reg, categories=categories, descriptions=descriptions)
        with caplog.at_level(logging.WARNING, logger="excelmanus.introspection"):
            output = gen.generate()
        assert "tool_a" in caplog.text
        # 工具仍应出现在输出中（空描述）
        assert "tool_a —" in output

    def test_empty_description_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """描述为空字符串（但 key 存在）时不应记录 warning。"""
        categories = {"test_cat": ("tool_a",)}
        descriptions = {"tool_a": ""}
        reg = _make_registry(["tool_a"])
        gen = CapabilityMapGenerator(reg, categories=categories, descriptions=descriptions)
        with caplog.at_level(logging.WARNING, logger="excelmanus.introspection"):
            gen.generate()
        # 只检查 WARNING 及以上级别，排除 INFO 注册日志
        warning_text = "\n".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "tool_a" not in warning_text
