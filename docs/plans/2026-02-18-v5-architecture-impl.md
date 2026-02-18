# v5 Architecture Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重构 ExcelManus 的 Skill/Tool 架构为三层正交设计：ToolProfile（工具呈现层）+ Skill（知识注入层，对齐 Agent Skills 开放标准）+ ToolPolicy（安全层，不变）。

**Architecture:** 引入独立的 ToolProfile 层控制工具 schema 分级展示（core=完整/extended=摘要），Skill 层剥离所有工具控制语义仅保留知识注入，删除 PreRouter、auto_supplement、tool_scope 5 分支计算等 ~1800 行旧代码。新增 `tools/profile.py`、`expand_tools` 元工具、`activate_skill` 元工具（替代 `select_skill`/`discover_tools`/`list_skills` 三合一）。

**Tech Stack:** Python 3.12, pytest, existing `AgentEngine` / `SkillRouter` / bench trace pipeline

**Design Doc:** `docs/plans/2026-02-18-skill-tool-architecture-redesign.md`

---

## Phase 1: 引入 ToolProfile + 新元工具（非破坏性，新旧并存）

### Task 1: 创建 `tools/profile.py` — ToolProfile 定义

**Files:**
- Create: `excelmanus/tools/profile.py`
- Test: `tests/test_tool_profile.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool_profile.py
"""ToolProfile 分层 schema 测试。"""
import pytest
from excelmanus.tools.profile import (
    TOOL_PROFILES,
    CORE_TOOLS,
    EXTENDED_CATEGORIES,
    CATEGORY_DESCRIPTIONS,
    get_tier,
    get_category,
    get_tools_in_category,
)


class TestToolProfileDefinitions:
    def test_core_tools_are_in_profiles(self):
        for tool in CORE_TOOLS:
            assert tool in TOOL_PROFILES, f"core tool {tool} missing from TOOL_PROFILES"
            assert TOOL_PROFILES[tool]["tier"] == "core"

    def test_extended_tools_have_category(self):
        for name, profile in TOOL_PROFILES.items():
            if profile["tier"] == "extended":
                assert "category" in profile, f"extended tool {name} missing category"
                assert profile["category"] in EXTENDED_CATEGORIES

    def test_all_categories_have_descriptions(self):
        for cat in EXTENDED_CATEGORIES:
            assert cat in CATEGORY_DESCRIPTIONS, f"category {cat} missing description"

    def test_get_tier_core(self):
        assert get_tier("read_excel") == "core"

    def test_get_tier_extended(self):
        assert get_tier("format_cells") == "extended"

    def test_get_tier_unknown_returns_none(self):
        assert get_tier("nonexistent_tool_xyz") is None

    def test_get_category(self):
        assert get_category("format_cells") == "format"

    def test_get_tools_in_category(self):
        tools = get_tools_in_category("chart")
        assert "create_chart" in tools
        assert "create_excel_chart" in tools

    def test_no_overlap_between_core_and_extended(self):
        for name, profile in TOOL_PROFILES.items():
            if name in CORE_TOOLS:
                assert profile["tier"] == "core"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_profile.py -v`
Expected: FAIL — module `excelmanus.tools.profile` does not exist.

**Step 3: Write minimal implementation**

```python
# excelmanus/tools/profile.py
"""工具呈现层（ToolProfile）：控制 LLM 看到的 schema 详细度。

与 Skill 层完全解耦。Skill 负责知识注入，ToolProfile 负责 schema 分级展示。
与 ToolPolicy 完全解耦。ToolPolicy 负责安全拦截，ToolProfile 负责呈现控制。
"""
from __future__ import annotations

from typing import Literal

ToolTier = Literal["core", "extended"]

# ── core: 始终展示完整 OpenAI tool schema ──
CORE_TOOLS: frozenset[str] = frozenset({
    # 数据读取
    "read_excel", "analyze_data", "filter_data",
    "group_aggregate", "inspect_excel_files",
    "analyze_sheet_mapping",
    # 结构发现
    "list_sheets", "list_directory", "get_file_info",
    "find_files", "read_text_file", "read_cell_styles",
    # 元工具（由 engine 注册，此处仅声明 tier）
    "activate_skill", "expand_tools",
    "focus_window", "task_create", "task_update",
    "finish_task", "ask_user",
    "delegate_to_subagent", "list_subagents",
    "memory_save", "memory_read_topic",
})

# ── extended 工具类别 ──
EXTENDED_CATEGORIES: frozenset[str] = frozenset({
    "data_write", "format", "advanced_format",
    "chart", "sheet", "code", "file_ops",
})

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "data_write": "数据写入（write_excel, write_cells, transform_data, insert_rows, insert_columns）",
    "format": "基础格式化（format_cells, 列宽行高, 合并/取消合并单元格, read_cell_styles）",
    "advanced_format": "高级格式化（条件格式, 仪表盘主题, 色阶, 数据条, 打印布局等）",
    "chart": "图表生成（create_chart 生成 PNG, create_excel_chart 嵌入原生图表）",
    "sheet": "工作表管理（create/copy/rename/delete_sheet, 跨表复制）",
    "code": "代码执行（write_text_file, run_code, run_shell）",
    "file_ops": "文件操作（copy_file, rename_file, delete_file）",
}

# ── 全量 ToolProfile 定义 ──
TOOL_PROFILES: dict[str, dict] = {}

# 自动填充 core
for _name in CORE_TOOLS:
    TOOL_PROFILES[_name] = {"tier": "core", "category": "meta"}

# extended 工具按类别
_EXTENDED_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "data_write": (
        "write_excel", "write_cells", "transform_data",
        "insert_rows", "insert_columns",
    ),
    "format": (
        "format_cells", "adjust_column_width", "adjust_row_height",
        "merge_cells", "unmerge_cells",
    ),
    "advanced_format": (
        "apply_threshold_icon_format", "style_card_blocks",
        "scale_range_unit", "apply_dashboard_dark_theme",
        "add_color_scale", "add_data_bar", "add_conditional_rule",
        "set_print_layout", "set_page_header_footer",
    ),
    "chart": ("create_chart", "create_excel_chart"),
    "sheet": (
        "create_sheet", "copy_sheet", "rename_sheet",
        "delete_sheet", "copy_range_between_sheets",
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
    "file_ops": ("copy_file", "rename_file", "delete_file"),
}

for _category, _tools in _EXTENDED_TOOL_MAP.items():
    for _tool_name in _tools:
        TOOL_PROFILES[_tool_name] = {"tier": "extended", "category": _category}


def get_tier(tool_name: str) -> ToolTier | None:
    """返回工具的 tier，不存在返回 None。"""
    profile = TOOL_PROFILES.get(tool_name)
    return profile["tier"] if profile else None


def get_category(tool_name: str) -> str | None:
    """返回工具的 category。"""
    profile = TOOL_PROFILES.get(tool_name)
    return profile["category"] if profile else None


def get_tools_in_category(category: str) -> list[str]:
    """返回指定 category 中的所有工具名。"""
    return [
        name for name, profile in TOOL_PROFILES.items()
        if profile.get("category") == category
    ]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_profile.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/tools/profile.py tests/test_tool_profile.py
git commit -m "feat(v5): add ToolProfile layer — core/extended tool tier definitions"
```

---

### Task 2: 为 ToolDef 添加 `to_summary_schema()` 方法

**Files:**
- Modify: `excelmanus/tools/registry.py`
- Test: `tests/test_tool_registry.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool_registry.py — 追加到已有文件
class TestToolDefSummarySchema:
    def test_summary_schema_has_name_and_description(self):
        tool = ToolDef(
            name="format_cells",
            description="对单元格范围应用格式化样式",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "range": {"type": "string"},
                },
                "required": ["file_path", "range"],
            },
            func=lambda **kw: None,
        )
        schema = tool.to_summary_schema()
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "format_cells"
        assert "格式化" in func["description"]
        # 摘要 schema 无参数细节
        assert func["parameters"]["properties"] == {}

    def test_summary_schema_appends_expand_hint(self):
        tool = ToolDef(
            name="write_excel",
            description="写入 Excel",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            func=lambda **kw: None,
        )
        schema = tool.to_summary_schema()
        desc = schema["function"]["description"]
        assert "expand_tools" in desc
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_registry.py::TestToolDefSummarySchema -v`
Expected: FAIL — `to_summary_schema` 不存在。

**Step 3: Write minimal implementation**

在 `ToolDef` 类中添加方法：

```python
# excelmanus/tools/registry.py — ToolDef 类内

def to_summary_schema(self, mode: OpenAISchemaMode = "responses") -> dict[str, Any]:
    """生成摘要 schema：仅 name + description，无参数细节。

    用于 ToolProfile extended 工具的默认呈现。
    LLM 看到工具存在但不知道如何调用，需先 expand_tools 获取完整 schema。
    """
    hint = "（调用 expand_tools 展开此类别获取完整参数）"
    desc = self.description
    if hint not in desc:
        desc = f"{desc}\n{hint}"
    func_def: dict[str, Any] = {
        "name": self.name,
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }
    if mode == "responses":
        return {"type": "function", **func_def}
    return {"type": "function", "function": func_def}
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_registry.py::TestToolDefSummarySchema -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/tools/registry.py tests/test_tool_registry.py
git commit -m "feat(v5): add ToolDef.to_summary_schema() for progressive disclosure"
```

---

### Task 3: ToolRegistry 添加分层 schema 生成方法

**Files:**
- Modify: `excelmanus/tools/registry.py`
- Test: `tests/test_tool_registry.py`

**Step 1: Write the failing tests**

```python
# tests/test_tool_registry.py — 追加

class TestRegistryTieredSchemas:
    def test_get_tiered_schemas_core_gets_full(self):
        from excelmanus.tools.profile import CORE_TOOLS
        registry = ToolRegistry()
        registry.register(ToolDef(
            name="read_excel", description="读取 Excel",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            func=lambda **kw: None,
        ))
        schemas = registry.get_tiered_schemas(expanded_categories=set(), mode="chat_completions")
        assert len(schemas) == 1
        # core tool -> full schema
        assert schemas[0]["function"]["parameters"]["properties"] != {}

    def test_get_tiered_schemas_extended_gets_summary(self):
        registry = ToolRegistry()
        registry.register(ToolDef(
            name="format_cells", description="格式化",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            func=lambda **kw: None,
        ))
        schemas = registry.get_tiered_schemas(expanded_categories=set(), mode="chat_completions")
        assert len(schemas) == 1
        # extended tool, not expanded -> summary
        assert schemas[0]["function"]["parameters"]["properties"] == {}

    def test_get_tiered_schemas_expanded_category_gets_full(self):
        registry = ToolRegistry()
        registry.register(ToolDef(
            name="format_cells", description="格式化",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": []},
            func=lambda **kw: None,
        ))
        schemas = registry.get_tiered_schemas(expanded_categories={"format"}, mode="chat_completions")
        assert len(schemas) == 1
        # expanded -> full schema
        assert schemas[0]["function"]["parameters"]["properties"] != {}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tool_registry.py::TestRegistryTieredSchemas -v`
Expected: FAIL — `get_tiered_schemas` 不存在。

**Step 3: Write minimal implementation**

```python
# excelmanus/tools/registry.py — ToolRegistry 类内

def get_tiered_schemas(
    self,
    expanded_categories: set[str],
    mode: OpenAISchemaMode = "responses",
) -> list[dict[str, Any]]:
    """根据 ToolProfile 生成分层 tool schemas。

    core 工具始终返回完整 schema。
    extended 工具默认返回摘要 schema，除非其 category 在 expanded_categories 中。
    不在 TOOL_PROFILES 中的工具（如动态 MCP 工具）始终返回完整 schema。
    """
    from excelmanus.tools.profile import TOOL_PROFILES

    schemas: list[dict[str, Any]] = []
    for name, tool in self._tools.items():
        profile = TOOL_PROFILES.get(name)
        if profile is None:
            # 未在 profile 中的工具（如 MCP 动态注册）→ 完整 schema
            schemas.append(tool.to_openai_schema(mode=mode))
            continue
        if profile["tier"] == "core":
            schemas.append(tool.to_openai_schema(mode=mode))
        elif profile.get("category") in expanded_categories:
            schemas.append(tool.to_openai_schema(mode=mode))
        else:
            schemas.append(tool.to_summary_schema(mode=mode))
    return schemas
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tool_registry.py::TestRegistryTieredSchemas -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/tools/registry.py tests/test_tool_registry.py
git commit -m "feat(v5): add ToolRegistry.get_tiered_schemas() for progressive disclosure"
```

---

### Task 4: 新增 `expand_tools` 元工具 + `activate_skill` 元工具

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
# tests/test_engine.py — 追加

class TestExpandToolsMeta:
    def test_expand_tools_adds_category(self):
        engine = _make_engine()
        result = engine._handle_expand_tools("format")
        assert "format" in engine._expanded_categories
        assert "format_cells" in result

    def test_expand_tools_invalid_category(self):
        engine = _make_engine()
        result = engine._handle_expand_tools("nonexistent")
        assert "无效" in result or "不支持" in result

    def test_expand_tools_persists_across_calls(self):
        engine = _make_engine()
        engine._handle_expand_tools("format")
        engine._handle_expand_tools("chart")
        assert "format" in engine._expanded_categories
        assert "chart" in engine._expanded_categories


class TestActivateSkillMeta:
    def test_activate_skill_returns_instructions(self):
        engine = _make_engine()
        result = engine._handle_activate_skill("data_basic")
        assert "OK" in result

    def test_activate_skill_not_found(self):
        engine = _make_engine()
        result = engine._handle_activate_skill("nonexistent_skill_xyz")
        assert "未找到" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine.py::TestExpandToolsMeta -v`
Run: `uv run pytest tests/test_engine.py::TestActivateSkillMeta -v`
Expected: FAIL — 方法不存在。

**Step 3: Write minimal implementation**

在 `AgentEngine.__init__` 中添加：
```python
# v5: session 级别已展开的工具类别
self._expanded_categories: set[str] = set()
```

新增方法：
```python
def _handle_expand_tools(self, category: str) -> str:
    """处理 expand_tools 元工具：将指定类别的工具从摘要升级为完整 schema。"""
    from excelmanus.tools.profile import EXTENDED_CATEGORIES, CATEGORY_DESCRIPTIONS, get_tools_in_category

    if category not in EXTENDED_CATEGORIES:
        valid = ", ".join(sorted(EXTENDED_CATEGORIES))
        return f"无效类别 '{category}'。可用类别：{valid}"

    self._expanded_categories.add(category)
    tools = get_tools_in_category(category)
    desc = CATEGORY_DESCRIPTIONS.get(category, category)
    tool_list = ", ".join(tools) if tools else "(无)"
    return (
        f"已展开类别 [{category}]: {desc}\n"
        f"包含工具：{tool_list}\n"
        f"这些工具的完整参数定义已在下一轮对话中可见。"
    )


def _handle_activate_skill(self, skill_name: str) -> str:
    """处理 activate_skill 元工具：注入 Skill 指引到对话历史。

    与 _handle_select_skill 相同的核心逻辑，但命名对齐 Agent Skills 标准。
    """
    return await self._handle_select_skill(skill_name)
```

在 `_build_meta_tools()` 中追加 `expand_tools` 和 `activate_skill` 定义（与现有元工具并存）。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine.py::TestExpandToolsMeta tests/test_engine.py::TestActivateSkillMeta -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(v5): add expand_tools + activate_skill meta tools (coexist with legacy)"
```

---

### Task 5: 全量测试回归验证 Phase 1

**Step 1: 运行全量测试确保新旧并存无破坏**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: 全部 PASS（新代码与旧代码并存，不改变现有行为）。

**Step 2: Commit Phase 1 完成标记**

```bash
git add -A
git commit -m "milestone(v5): Phase 1 complete — ToolProfile + new meta tools coexist with legacy"
```

---

## Phase 2: 切换 Schema 生成路径

### Task 6: Engine 新增 v5 schema 生成路径

**Files:**
- Modify: `excelmanus/engine.py`
- Modify: `excelmanus/config.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
# tests/test_engine.py — 追加

class TestV5SchemaGeneration:
    def test_v5_mode_uses_tiered_schemas(self):
        config = _make_config(use_tool_profile=True)
        engine = _make_engine(config=config)
        schemas = engine._build_v5_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        # core tools present with full params
        assert "read_excel" in names
        # extended tools present but with empty params (summary)
        format_schema = next(s for s in schemas if s["function"]["name"] == "format_cells")
        assert format_schema["function"]["parameters"]["properties"] == {}

    def test_v5_mode_expanded_category_shows_full(self):
        config = _make_config(use_tool_profile=True)
        engine = _make_engine(config=config)
        engine._expanded_categories.add("format")
        schemas = engine._build_v5_tool_schemas()
        format_schema = next(s for s in schemas if s["function"]["name"] == "format_cells")
        assert format_schema["function"]["parameters"]["properties"] != {}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine.py::TestV5SchemaGeneration -v`
Expected: FAIL

**Step 3: Write minimal implementation**

`config.py` 添加：
```python
use_tool_profile: bool = False  # v5 开关
```

`engine.py` 添加：
```python
def _build_v5_tool_schemas(self) -> list[dict[str, Any]]:
    """v5: 根据 ToolProfile + expanded_categories 生成分层 tool schemas。"""
    domain_schemas = self._registry.get_tiered_schemas(
        expanded_categories=self._expanded_categories,
        mode="chat_completions",
    )
    meta_schemas = self._build_meta_tools_v5()
    return meta_schemas + domain_schemas
```

在 `_tool_calling_loop` 中根据 `self._config.use_tool_profile` 决定用新路径还是旧路径生成 schemas。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine.py::TestV5SchemaGeneration -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py excelmanus/config.py tests/test_engine.py
git commit -m "feat(v5): add v5 schema generation path with config switch"
```

---

### Task 7: 重写 `_build_meta_tools_v5()`

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
class TestMetaToolsV5:
    def test_v5_meta_tools_has_activate_skill(self):
        engine = _make_engine()
        tools = engine._build_meta_tools_v5()
        names = [t["function"]["name"] for t in tools]
        assert "activate_skill" in names

    def test_v5_meta_tools_has_expand_tools(self):
        engine = _make_engine()
        tools = engine._build_meta_tools_v5()
        names = [t["function"]["name"] for t in tools]
        assert "expand_tools" in names

    def test_v5_meta_tools_no_legacy_select_skill(self):
        engine = _make_engine()
        tools = engine._build_meta_tools_v5()
        names = [t["function"]["name"] for t in tools]
        assert "select_skill" not in names
        assert "discover_tools" not in names
        assert "list_skills" not in names
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine.py::TestMetaToolsV5 -v`
Expected: FAIL

**Step 3: Write minimal implementation**

新的 `_build_meta_tools_v5()` 仅包含：
- `activate_skill` — 技能列表嵌入 description
- `expand_tools` — 类别列表嵌入 description
- `delegate_to_subagent` — 保持不变
- `list_subagents` — 保持不变
- `ask_user` — 保持不变
- `finish_task` — 条件注入，保持不变

不再包含 `select_skill`、`discover_tools`、`list_skills`。

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine.py::TestMetaToolsV5 -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(v5): implement _build_meta_tools_v5 replacing legacy 3 meta tools"
```

---

### Task 8: v5 模式下 tool call 处理适配

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
class TestV5ToolCallHandling:
    async def test_v5_expand_tools_call_handled(self):
        config = _make_config(use_tool_profile=True)
        engine = _make_engine(config=config)
        # 模拟 LLM 调用 expand_tools
        result = engine._handle_expand_tools("format")
        assert "已展开" in result

    async def test_v5_activate_skill_call_handled(self):
        config = _make_config(use_tool_profile=True)
        engine = _make_engine(config=config)
        result = await engine._handle_activate_skill("data_basic")
        assert "OK" in result or "未找到" in result

    async def test_v5_no_tool_scope_check(self):
        """v5 模式下不做 tool_scope 检查，所有注册工具均可调用。"""
        config = _make_config(use_tool_profile=True)
        engine = _make_engine(config=config)
        # 在 v5 模式下，即使未 expand，工具也应该可以被调用
        # （只是 LLM 不知道参数而已，但如果它猜对了参数也应该放行）
```

**Step 2: Run test to verify it fails / passes as expected**

**Step 3: 在 `_tool_calling_loop` 的 tool dispatch 中，v5 模式跳过 `tool_scope` 检查和 `auto_supplement`**

关键改动：
```python
# v5 模式：不做 tool_scope 检查
if self._config.use_tool_profile:
    # 所有注册工具均可调用，不需要 scope 检查
    if tool_name == "expand_tools":
        result_str = self._handle_expand_tools(arguments.get("category", ""))
        # ... 处理
    elif tool_name == "activate_skill":
        result_str = await self._handle_activate_skill(arguments.get("name", ""))
        # ... 处理
    else:
        # 直接调用，不做 scope 限制
        result_str = self._registry.call_tool(tool_name, arguments)
else:
    # 旧路径：tool_scope 检查 + auto_supplement
    ...
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_engine.py::TestV5ToolCallHandling -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(v5): adapt tool call dispatch for v5 mode (no scope check)"
```

---

### Task 9: v5 模式下 chat() 入口适配

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 在 `chat()` 方法中，v5 模式跳过 PreRouter 并行调用和 Phase 1 预激活**

关键改动：
```python
if self._config.use_tool_profile:
    # v5: 直接走路由（仅处理斜杠命令 + write_hint 分类）
    route_result = await self._route_skills(
        user_message,
        slash_command=effective_slash_command,
        raw_args=effective_raw_args if effective_slash_command else None,
    )
    # 不做 pre_route、不做 preroute_candidates、不做 fallback
    # tool schemas 由 _build_v5_tool_schemas() 生成
else:
    # 旧路径：保持现有逻辑不变
    ...
```

**Step 2: Write tests confirming v5 chat skips preroute**

**Step 3: Implement**

**Step 4: Run tests**

Run: `uv run pytest tests/test_engine.py -x -q --tb=short`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(v5): chat() entry point v5 path skips preroute and scope computation"
```

---

### Task 10: 全量测试 + bench 对比

**Step 1: 全量测试**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: PASS（v5 开关默认 off，旧路径不受影响）

**Step 2: 手动 bench 验证（v5 开关 on）**

```bash
EXCELMANUS_USE_TOOL_PROFILE=true uv run python -m excelmanus.bench ...
```

对比新旧路径的行为差异。

**Step 3: Commit Phase 2 完成标记**

```bash
git commit --allow-empty -m "milestone(v5): Phase 2 complete — v5 schema path functional behind config flag"
```

---

## Phase 3: SKILL.md 格式迁移

### Task 11: Skillpack model 简化

**Files:**
- Modify: `excelmanus/skillpacks/models.py`
- Modify: `excelmanus/skillpacks/loader.py`
- Test: `tests/test_skillpacks.py`

**Step 1: 使 `allowed_tools`、`triggers`、`priority` 等字段可选（向后兼容）**

```python
# models.py — Skillpack 新增默认值使字段可选
allowed_tools: list[str] = field(default_factory=list)  # v5: 已废弃，保留兼容
triggers: list[str] = field(default_factory=list)        # v5: 已废弃
```

`loader.py` 中解析时对缺失字段给默认值（不再 warn）。

**Step 2: Write guard tests**

确保无 `allowed_tools` 的 SKILL.md 可以正常加载。

**Step 3: Implement**

**Step 4: Test**

Run: `uv run pytest tests/test_skillpacks.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/skillpacks/models.py excelmanus/skillpacks/loader.py tests/test_skillpacks.py
git commit -m "feat(v5): make Skillpack.allowed_tools/triggers optional for v5 compatibility"
```

---

### Task 12: 迁移所有系统 SKILL.md 到新格式

**Files:**
- Modify: `excelmanus/skillpacks/system/*/SKILL.md` (6 files)
- Delete: `excelmanus/skillpacks/system/general_excel/` (v5 不需要兜底)

**Step 1: 重写每个 SKILL.md**

移除 `allowed_tools`、`triggers`、`priority`、`user_invocable`。
仅保留 `name`、`description` + markdown body。

示例 `data-basic/SKILL.md`：
```yaml
---
name: data-basic
description: 数据读取、分析、筛选与转换。当用户需要读取 Excel、做统计分析、筛选排序时使用。
---
优先使用结构化方式处理数据：
1. 明确列名与过滤条件。
2. 先分析后修改，避免直接覆盖。
3. 需要改写时建议输出新文件路径。
...
```

**Step 2: 删除 `general_excel/`**

**Step 3: Test loader 能加载新格式**

Run: `uv run pytest tests/test_skillpacks.py -v`

**Step 4: Commit**

```bash
git add excelmanus/skillpacks/system/
git commit -m "feat(v5): migrate all system SKILL.md to Agent Skills standard format"
```

---

### Task 13: SkillRouter 简化

**Files:**
- Modify: `excelmanus/skillpacks/router.py`
- Test: `tests/test_skillpacks.py`

**Step 1: 简化 `route()` 方法**

v5 模式下 router 仅需要：
1. 斜杠命令 → 直接找 Skill 并注入 instructions
2. 非斜杠 → 返回空结果（不计算 tool_scope）
3. write_hint 分类保留（安全层需要）

删除 `_build_result()` 中的 tool_scope 计算、`_build_fallback_result()` 中的 DISCOVERY_TOOLS 注入。

**Step 2: Test**

**Step 3: Commit**

```bash
git add excelmanus/skillpacks/router.py tests/test_skillpacks.py
git commit -m "refactor(v5): simplify SkillRouter — remove tool_scope computation"
```

---

## Phase 4: 删除旧代码

### Task 14: 删除 PreRouter

**Files:**
- Delete: `excelmanus/skillpacks/pre_router.py`
- Delete: `tests/test_pre_router.py`
- Modify: `excelmanus/engine.py` — 移除所有 pre_route import 和调用
- Modify: `excelmanus/skillpacks/manager.py` — 移除 `invalidate_pre_route_cache` 调用
- Modify: `excelmanus/config.py` — 移除 `skill_preroute_*` 配置项

**Step 1: 删除文件和引用**

**Step 2: Test**

Run: `uv run pytest tests/ -x -q --tb=short`

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor(v5): delete PreRouter (493 lines) and related config"
```

---

### Task 15: 删除 engine 中旧的 scope/supplement 代码

**Files:**
- Modify: `excelmanus/engine.py`

删除以下方法和相关代码：
- `_get_current_tool_scope()` (~150 行)
- `_activate_preroute_candidates()` (~40 行)
- `_apply_preroute_fallback()` (~20 行)
- `_try_auto_supplement_tool()` (~60 行)
- `_build_tool_to_skill_index()` (~40 行)
- `_expand_tool_scope_patterns()` (~50 行)
- `_merge_with_loaded_skills()` (~30 行)
- `_resolve_preroute_target_layered()` (~30 行)
- `_refresh_route_after_skill_switch()` (~50 行)
- `_ensure_always_available()` (~10 行)
- `_append_global_mcp_tools()` (~10 行)
- Phase 1 预激活逻辑块 (~100 行)
- `AutoSupplementResult` dataclass
- `_META_TOOL_NAMES`、`_ALWAYS_AVAILABLE_TOOLS` 常量
- `_build_meta_tools()` 旧版（替换为 `_build_meta_tools_v5()`）

删除旧元工具处理分支：
- `select_skill` 调用分支
- `discover_tools` 调用分支

**Step 1: 逐块删除，每删一块跑一次测试**

**Step 2: 适配测试（大量测试需要更新）**

受影响的测试文件：
- `tests/test_engine.py` (~307 matches)
- `tests/test_pbt_llm_routing.py`
- `tests/test_pbt_unauthorized_tool.py`
- `tests/test_write_guard.py`
- `tests/test_approval.py`
- `tests/test_api.py`
- `tests/test_config.py`

策略：删除与旧 scope/preroute/supplement 相关的测试，保留与 v5 行为对应的测试。

**Step 3: Test**

Run: `uv run pytest tests/ -x -q --tb=short`

**Step 4: Commit**

```bash
git add -A
git commit -m "refactor(v5): delete legacy scope/supplement/preroute code (~800 lines from engine)"
```

---

### Task 16: 删除旧的 `skill_tools.py` + `list_skills` 工具

**Files:**
- Delete: `excelmanus/tools/skill_tools.py`
- Delete: `tests/test_skill_tools.py`
- Modify: engine 中的 `list_skills` 注册引用

**Step 1: 删除文件和引用**

**Step 2: Test**

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor(v5): delete skill_tools.py (list_skills replaced by activate_skill)"
```

---

### Task 17: 清理 config.py 废弃配置项

**Files:**
- Modify: `excelmanus/config.py`
- Modify: `tests/test_config.py`

删除：
- `skill_preroute_mode`
- `skill_preroute_api_key` / `base_url` / `model` / `timeout_ms`
- `auto_supplement_enabled` / `auto_supplement_max_per_turn`
- `auto_activate_default_skill`
- 相关环境变量解析和校验

将 `use_tool_profile` 默认值改为 `True`（v5 成为默认行为）。

**Step 1: 删除配置项**

**Step 2: 更新 test_config.py**

**Step 3: Test**

Run: `uv run pytest tests/test_config.py -v`

**Step 4: Commit**

```bash
git add excelmanus/config.py tests/test_config.py
git commit -m "refactor(v5): remove deprecated config (preroute/supplement/auto_activate)"
```

---

### Task 18: 清理 policy.py 废弃概念

**Files:**
- Modify: `excelmanus/tools/policy.py`
- Modify: `tests/test_tool_policy.py`

删除：
- `DISCOVERY_TOOLS`（被 `CORE_TOOLS` in `profile.py` 替代）
- `FALLBACK_DISCOVERY_TOOLS`

保留：
- `TOOL_CATEGORIES`（被 `expand_tools` 元工具 description 引用）
- `TOOL_SHORT_DESCRIPTIONS`（被 summary schema description 使用）
- 所有安全层定义（MUTATING_*、READ_ONLY_SAFE_TOOLS、审计路径映射）
- `SUBAGENT_*` 工具域定义

**Step 1: 删除废弃常量**

**Step 2: 更新引用**

**Step 3: Test**

Run: `uv run pytest tests/test_tool_policy.py -v`

**Step 4: Commit**

```bash
git add excelmanus/tools/policy.py tests/test_tool_policy.py
git commit -m "refactor(v5): remove DISCOVERY_TOOLS from policy (replaced by ToolProfile.CORE_TOOLS)"
```

---

### Task 19: SkillMatchResult 简化

**Files:**
- Modify: `excelmanus/skillpacks/models.py`
- Modify: 所有引用 `SkillMatchResult.tool_scope` / `route_mode` 的地方

`SkillMatchResult` 简化为：
```python
@dataclass(frozen=True)
class SkillMatchResult:
    skills_used: list[str]
    system_contexts: list[str] = field(default_factory=list)
    write_hint: str = "unknown"
    parameterized: bool = False
```

移除 `tool_scope` 和 `route_mode`。

**Step 1: 修改 model**

**Step 2: 全量搜索并修复所有引用**

**Step 3: Test**

Run: `uv run pytest tests/ -x -q --tb=short`

**Step 4: Commit**

```bash
git add -A
git commit -m "refactor(v5): simplify SkillMatchResult — remove tool_scope and route_mode"
```

---

### Task 20: 全量回归测试 + bench

**Step 1: 全量测试**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: 全部 PASS

**Step 2: bench 全面回归**

```bash
uv run python scripts/bench_pick.py --suite all --run
```

对比 v4 基线和 v5 结果。

**Step 3: 最终 commit**

```bash
git add -A
git commit -m "milestone(v5): architecture redesign complete — 3-layer orthogonal design"
```

---

## 适配清单（非 engine 核心的周边组件）

以下组件在上述 Tasks 中被隐含涉及，但需要特别注意适配：

| 组件 | 文件 | 适配要点 |
|------|------|---------|
| **API 层** | `excelmanus/api.py` | 检查是否有 `tool_scope` / `active_skills` 暴露到 API 响应中 |
| **Bench 追踪** | `excelmanus/bench.py` | 移除 `pre_route_result` / `auto_supplement` 事件；新增 `expand_tools` 事件 |
| **子代理** | `excelmanus/subagent/executor.py` | `FilteredToolRegistry` 不变；确认子代理不依赖主 agent 的 `tool_scope` |
| **Hooks** | `excelmanus/hooks/` | 检查 hook context 中是否传递了 `active_skills` / `tool_scope` |
| **Window Perception** | `excelmanus/window_perception/` | 不依赖 skill routing，无需适配 |
| **System Prompt** | `excelmanus/memory.py` | 检查 system prompt 中是否硬编码了 `select_skill` / `discover_tools` 的使用指引 |
| **CLI** | `excelmanus/__main__.py` | 检查 `/skill` 命令是否需要适配 |
| **Frontmatter 解析** | `excelmanus/skillpacks/frontmatter.py` | 不需要改，它是通用 YAML 解析 |
| **Context Builder** | `excelmanus/skillpacks/context_builder.py` | 保留（Skill 知识注入仍需要 budget 控制） |
| **Approval** | `excelmanus/approval.py` | 不依赖 Skill，无需适配 |
| **Security** | `excelmanus/security/` | 不依赖 Skill，无需适配 |

---

## 总结

| 指标 | 数值 |
|------|------|
| 总 Tasks | 20 |
| 预计删除代码 | ~1800 行 |
| 预计新增代码 | ~280 行 |
| 受影响测试文件 | ~22 个 |
| 新增测试 | ~15 个测试类 |
| Phase 1 (非破坏性) | Tasks 1-5 |
| Phase 2 (新路径) | Tasks 6-10 |
| Phase 3 (格式迁移) | Tasks 11-13 |
| Phase 4 (删除旧代码) | Tasks 14-20 |
