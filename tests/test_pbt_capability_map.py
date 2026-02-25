"""属性测试：能力图谱生成器。

# Feature: capability-introspection, Property 1-3

使用 hypothesis 验证能力图谱的完整性（P1）、权限一致性（P2）、
MCP 检测双向正确性（P3）。

**Validates: Requirements 1.1–1.6, 11.1, 11.2**
"""

from __future__ import annotations

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from excelmanus.introspection.capability_map import (
    ICON_AUDIT_ONLY,
    ICON_CONFIRM,
    ICON_DEFAULT,
    ICON_MCP,
    ICON_READ_ONLY,
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


# ── 辅助函数 ──────────────────────────────────────────────


def _all_categorized_tools() -> list[str]:
    """返回 TOOL_CATEGORIES 中所有工具名（去重保序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for names in TOOL_CATEGORIES.values():
        for n in names:
            if n not in seen:
                seen.add(n)
                result.append(n)
    return result


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


# ── 策略 ──────────────────────────────────────────────────

# 从 TOOL_CATEGORIES 中随机选取一个 (category, tool_name) 对
_all_cat_tool_pairs: list[tuple[str, str]] = [
    (cat, tool)
    for cat, tools in TOOL_CATEGORIES.items()
    for tool in tools
]
_cat_tool_strategy = st.sampled_from(_all_cat_tool_pairs)

# 随机选取一个分类内工具名
_categorized_tool_strategy = st.sampled_from(_all_categorized_tools())

# 随机生成 MCP 工具名（不与已有工具冲突）
_existing_names = set(_all_categorized_tools()) | READ_ONLY_SAFE_TOOLS | MUTATING_CONFIRM_TOOLS | MUTATING_AUDIT_ONLY_TOOLS
_mcp_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
    min_size=5,
    max_size=20,
).filter(lambda s: s not in _existing_names and s.strip())


# ---------------------------------------------------------------------------
# Property 1: 能力图谱完整性
# Feature: capability-introspection, Property 1
# **Validates: Requirements 1.1, 1.5**
# ---------------------------------------------------------------------------


@given(pair=_cat_tool_strategy)
def test_pbt_property_1_capability_map_completeness(pair: tuple[str, str]) -> None:
    """Property 1：对于 TOOL_CATEGORIES 中的任意 (category, tool)，
    该工具必须出现在 generate() 输出中，且附带其描述。

    **Validates: Requirements 1.1, 1.5**
    """
    category, tool_name = pair
    reg = _make_registry(_all_categorized_tools())
    gen = CapabilityMapGenerator(reg)
    output = gen.generate()

    # 工具名必须出现
    assert tool_name in output, f"工具 {tool_name} 未出现在能力图谱中"

    # 描述必须出现（如果有）
    desc = TOOL_SHORT_DESCRIPTIONS.get(tool_name, "")
    if desc:
        assert desc in output, f"工具 {tool_name} 的描述未出现在能力图谱中"


# ---------------------------------------------------------------------------
# Property 2: 权限标注一致性
# Feature: capability-introspection, Property 2
# **Validates: Requirements 1.2, 1.3**
# ---------------------------------------------------------------------------


@given(tool_name=_categorized_tool_strategy)
def test_pbt_property_2_permission_consistency(tool_name: str) -> None:
    """Property 2：对于任意分类内工具，其权限图标必须与 policy 分层一致。

    **Validates: Requirements 1.2, 1.3**
    """
    reg = _make_registry(_all_categorized_tools())
    gen = CapabilityMapGenerator(reg)
    icon = gen._classify_permission(tool_name)

    if tool_name in READ_ONLY_SAFE_TOOLS:
        assert icon == ICON_READ_ONLY, f"{tool_name} 应为 🟢"
    elif tool_name in MUTATING_CONFIRM_TOOLS:
        assert icon == ICON_CONFIRM, f"{tool_name} 应为 🔴"
    elif tool_name in MUTATING_AUDIT_ONLY_TOOLS:
        assert icon == ICON_AUDIT_ONLY, f"{tool_name} 应为 🟡"
    else:
        # 不在三个集合中的工具（如 run_code），使用默认图标
        assert icon == ICON_DEFAULT, f"{tool_name} 应为默认图标 {ICON_DEFAULT}"

    # 验证输出中该工具行确实包含正确图标
    output = gen.generate()
    for line in output.splitlines():
        if f" {tool_name} —" in line:
            assert icon in line, f"工具行中图标不匹配: {line}"
            break


# ---------------------------------------------------------------------------
# Property 3: MCP 检测双向正确性
# Feature: capability-introspection, Property 3
# **Validates: Requirements 1.4, 11.1, 11.2**
# ---------------------------------------------------------------------------


@given(mcp_name=_mcp_name_strategy)
def test_pbt_property_3_mcp_detection_bidirectional(mcp_name: str) -> None:
    """Property 3：工具被标记为 MCP (🔵) 当且仅当它不在 TOOL_CATEGORIES 中。

    正向：注册一个不在分类中的工具 → 应被检测为 MCP
    反向：分类内工具 → 不应被检测为 MCP

    **Validates: Requirements 1.4, 11.1, 11.2**
    """
    categorized = _all_categorized_tools()
    all_tools = categorized + [mcp_name]
    reg = _make_registry(all_tools)
    gen = CapabilityMapGenerator(reg)

    mcp_detected = gen._detect_mcp_tools()

    # 正向：MCP 工具应被检测到
    assert mcp_name in mcp_detected, f"MCP 工具 {mcp_name} 未被检测到"

    # 反向：分类内工具不应被检测为 MCP
    for cat_tool in categorized:
        assert cat_tool not in mcp_detected, f"分类内工具 {cat_tool} 不应被标记为 MCP"


@given(tool_name=_categorized_tool_strategy)
def test_pbt_property_3_categorized_not_mcp(tool_name: str) -> None:
    """Property 3 反向：分类内工具不应被检测为 MCP。

    **Validates: Requirements 11.2**
    """
    reg = _make_registry(_all_categorized_tools())
    gen = CapabilityMapGenerator(reg)
    mcp_detected = gen._detect_mcp_tools()
    assert tool_name not in mcp_detected, f"分类内工具 {tool_name} 不应被标记为 MCP"


# ══════════════════════════════════════════════════════════
# Property 4–10: introspect_capability 工具属性测试
# Feature: capability-introspection, Property 4-10
# **Validates: Requirements 3.1–3.3, 4.1–4.3, 5.1–5.3, 6.1–6.4, 7.1–7.2, 8.1–8.3, 12.1**
# ══════════════════════════════════════════════════════════

import json

import excelmanus.tools.introspection_tools as introspection_mod
from excelmanus.tools.introspection_tools import (
    introspect_capability,
    register_introspection_tools,
)

# ── 辅助 fixture ─────────────────────────────────────────


def _make_introspection_registry() -> ToolRegistry:
    """创建包含常用工具的 ToolRegistry 并注册 introspection 工具。"""
    reg = ToolRegistry()
    for name in _all_categorized_tools():
        desc = TOOL_SHORT_DESCRIPTIONS.get(name, f"desc of {name}")
        reg.register_tool(
            ToolDef(
                name=name,
                description=desc,
                input_schema={
                    "type": "object",
                    "properties": {"file_path": {"type": "string"}},
                },
                func=lambda: None,
            )
        )
    register_introspection_tools(reg)
    return reg


def _ensure_registry() -> ToolRegistry:
    """确保 introspection 模块的 _registry 已初始化，返回 registry。"""
    if introspection_mod._registry is None:
        reg = _make_introspection_registry()
        return reg
    return introspection_mod._registry


# ── 策略：已注册工具名 ───────────────────────────────────

_registered_tool_strategy = st.sampled_from(_all_categorized_tools())

# 有效分类名
_valid_category_strategy = st.sampled_from(list(TOOL_CATEGORIES.keys()))

# 有描述的工具名
_described_tool_strategy = st.sampled_from(list(TOOL_SHORT_DESCRIPTIONS.keys()))

# 有效 query_type
_query_type_strategy = st.sampled_from(
    ["tool_detail", "category_tools", "can_i_do", "related_tools"]
)

# 非空查询字符串
_nonempty_text_strategy = st.text(min_size=1, max_size=100).filter(lambda s: s.strip())

# 不存在的工具名（不在已注册工具中）
_all_known_names = set(_all_categorized_tools()) | {"introspect_capability"}
_nonexistent_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
    min_size=5,
    max_size=30,
).filter(lambda s: s not in _all_known_names and s.strip())

# 不存在的分类名
_nonexistent_category_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
    min_size=5,
    max_size=30,
).filter(lambda s: s not in TOOL_CATEGORIES and s.strip())


# ---------------------------------------------------------------------------
# Property 4: tool_detail 查询正确性
# Feature: capability-introspection, Property 4
# **Validates: Requirements 4.1, 4.2**
# ---------------------------------------------------------------------------


@given(tool_name=_registered_tool_strategy)
def test_pbt_property_4_tool_detail_correctness(tool_name: str) -> None:
    """Property 4：对于任意已注册工具，tool_detail 返回的结果应包含
    与 ToolDef.input_schema 一致的参数 schema。

    **Validates: Requirements 4.1, 4.2**
    """
    reg = _ensure_registry()
    result = introspect_capability("tool_detail", tool_name)

    # 结果应包含工具名
    assert tool_name in result, f"结果中未包含工具名 {tool_name}"

    # 结果应包含 schema 中的关键字段
    tool_def = reg.get_tool(tool_name)
    if tool_def is not None:
        schema = tool_def.input_schema
        # 验证 schema 中的 properties 键出现在结果中
        props = schema.get("properties", {})
        for prop_name in props:
            assert prop_name in result, (
                f"工具 {tool_name} 的参数 {prop_name} 未出现在 tool_detail 结果中"
            )


# ---------------------------------------------------------------------------
# Property 5: category_tools 查询完整性
# Feature: capability-introspection, Property 5
# **Validates: Requirements 5.1, 5.2**
# ---------------------------------------------------------------------------


@given(category=_valid_category_strategy)
def test_pbt_property_5_category_tools_completeness(category: str) -> None:
    """Property 5：对于任意有效分类，category_tools 返回的工具集合
    应与 TOOL_CATEGORIES[category] 一致。

    **Validates: Requirements 5.1, 5.2**
    """
    _ensure_registry()
    result = introspect_capability("category_tools", category)

    expected_tools = TOOL_CATEGORIES[category]
    for tool_name in expected_tools:
        assert tool_name in result, (
            f"分类 {category} 的工具 {tool_name} 未出现在 category_tools 结果中"
        )


# ---------------------------------------------------------------------------
# Property 6: can_i_do 自匹配性
# Feature: capability-introspection, Property 6
# **Validates: Requirement 6.4**
# ---------------------------------------------------------------------------


@given(tool_name=_described_tool_strategy)
def test_pbt_property_6_can_i_do_self_match(tool_name: str) -> None:
    """Property 6：对于任意有描述的工具，使用其完整描述作为 can_i_do 查询
    必须在匹配结果中包含该工具。

    **Validates: Requirement 6.4**
    """
    _ensure_registry()
    desc = TOOL_SHORT_DESCRIPTIONS[tool_name]
    result = introspect_capability("can_i_do", desc)

    assert tool_name in result, (
        f"工具 {tool_name} 的完整描述作为 can_i_do 查询未匹配到自身。"
        f"\n描述: {desc}\n结果: {result}"
    )


# ---------------------------------------------------------------------------
# Property 7: introspect_capability 纯查询无副作用
# Feature: capability-introspection, Property 7
# **Validates: Requirements 8.1, 8.2, 8.3**
# ---------------------------------------------------------------------------


@given(query_type=_query_type_strategy, query=_nonempty_text_strategy)
def test_pbt_property_7_no_side_effects(query_type: str, query: str) -> None:
    """Property 7：对于任意有效 (query_type, query) 对，调用 introspect_capability
    不应修改 ToolRegistry 状态，且必须返回非空字符串。

    **Validates: Requirements 8.1, 8.2, 8.3**
    """
    reg = _ensure_registry()
    tools_before = set(reg.get_tool_names())

    result = introspect_capability(query_type, query)

    # 返回非空字符串
    assert isinstance(result, str), "结果应为字符串"
    assert len(result) > 0, "结果不应为空"

    # ToolRegistry 状态不变
    tools_after = set(reg.get_tool_names())
    assert tools_before == tools_after, "ToolRegistry 状态被修改"


# ---------------------------------------------------------------------------
# Property 9: can_i_do 结果上限
# Feature: capability-introspection, Property 9
# **Validates: Requirement 6.2**
# ---------------------------------------------------------------------------


@given(query=_nonempty_text_strategy)
def test_pbt_property_9_can_i_do_max_results(query: str) -> None:
    """Property 9：对于任意 can_i_do 查询，每层匹配结果不超过 5 个。

    can_i_do 返回多层结果（内置工具/扩展能力/子代理/MCP），
    每层各自限制 _MAX_RESULTS (5) 条。

    **Validates: Requirement 6.2**
    """
    _ensure_registry()
    result = introspect_capability("can_i_do", query)

    # 按层分组计数：遇到不以 "  - " 开头的非空行即进入新层
    layer_counts: list[int] = []
    current_count = 0
    for line in result.splitlines():
        if line.startswith("  - "):
            current_count += 1
        elif line.strip() and not line.startswith("  "):
            if current_count > 0:
                layer_counts.append(current_count)
            current_count = 0
    if current_count > 0:
        layer_counts.append(current_count)

    for i, count in enumerate(layer_counts):
        assert count <= 5, (
            f"can_i_do 第 {i+1} 层返回了 {count} 个结果，超过上限 5"
        )


# ---------------------------------------------------------------------------
# Property 10: 不存在工具/分类的错误处理
# Feature: capability-introspection, Property 10
# **Validates: Requirements 4.3, 5.3**
# ---------------------------------------------------------------------------


@given(name=_nonexistent_name_strategy)
def test_pbt_property_10_nonexistent_tool_detail(name: str) -> None:
    """Property 10a：对于任意不存在的工具名，tool_detail 应返回"工具不存在"提示。

    **Validates: Requirement 4.3**
    """
    _ensure_registry()
    result = introspect_capability("tool_detail", name)
    assert "工具不存在" in result, (
        f"查询不存在的工具 {name} 未返回'工具不存在'提示"
    )


@given(name=_nonexistent_category_strategy)
def test_pbt_property_10_nonexistent_category(name: str) -> None:
    """Property 10b：对于任意不存在的分类名，category_tools 应返回所有可用分类名。

    **Validates: Requirement 5.3**
    """
    _ensure_registry()
    result = introspect_capability("category_tools", name)

    assert "分类不存在" in result, (
        f"查询不存在的分类 {name} 未返回'分类不存在'提示"
    )
    for cat in TOOL_CATEGORIES:
        assert cat in result, (
            f"不存在分类查询结果中缺少可用分类 {cat}"
        )
