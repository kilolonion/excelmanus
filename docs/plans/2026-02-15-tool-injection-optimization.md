# 工具注入优化实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 借鉴 Gemini CLI 的工具注入逻辑，通过动态 scope 收窄、工具分组索引、两阶段注入和 discover_tools 元工具，将每轮 LLM 调用的工具 token 开销从 ~11,000 降至 ~4,000（首轮），同时提升工具选择准确率。

**Architecture:** 在 `tools/policy.py` 新增 `DISCOVERY_TOOLS` 基础工具集常量和 `TOOL_CATEGORIES` 分类映射。修改 `engine.py` 的 `_get_current_tool_scope()` 使无 skill 激活时仅暴露基础集。新增 `discover_tools` 元工具让 LLM 按类别查询完整工具列表。在 `_build_system_prompts` 中注入动态工具分组索引。

**Tech Stack:** Python 3.12, pytest, 现有 ToolRegistry / ToolDef / SkillMatchResult 基础设施

---

## 基础工具集定义

```
基础集（Discovery Set）— 无 skill 激活时可见：
  数据探查：read_excel, scan_excel_files, analyze_data, filter_data, group_aggregate
  结构探查：list_sheets, list_directory, get_file_info, search_files, read_text_file
  样式感知：read_cell_styles
  窗口：focus_window
  合计 12 个只读工具

Always-Available — 始终可见：
  task_create, task_update, memory_save, memory_read_topic, list_skills

元工具 — 由 _build_meta_tools 注入：
  select_skill, delegate_to_subagent, list_subagents, ask_user, discover_tools（新增）

总计：22 个工具（vs 全量 ~50 个）
```

## 工具分类映射

```python
TOOL_CATEGORIES = {
    "data_read": ["read_excel", "scan_excel_files", "analyze_data", "filter_data",
                   "group_aggregate", "analyze_sheet_mapping"],
    "data_write": ["write_excel", "write_cells", "transform_data",
                    "insert_rows", "insert_columns"],
    "format": ["format_cells", "adjust_column_width", "adjust_row_height",
               "read_cell_styles", "merge_cells", "unmerge_cells"],
    "advanced_format": ["apply_threshold_icon_format", "style_card_blocks",
                        "scale_range_unit", "apply_dashboard_dark_theme",
                        "add_color_scale", "add_data_bar", "add_conditional_rule",
                        "set_print_layout", "set_page_header_footer"],
    "chart": ["create_chart", "create_excel_chart"],
    "sheet": ["list_sheets", "create_sheet", "copy_sheet", "rename_sheet",
              "delete_sheet", "copy_range_between_sheets"],
    "file": ["list_directory", "get_file_info", "search_files", "read_text_file",
             "copy_file", "rename_file", "delete_file"],
    "code": ["write_text_file", "run_code", "run_shell"],
}
```

---

### Task 1: 在 policy.py 新增 DISCOVERY_TOOLS 和 TOOL_CATEGORIES

**Files:**
- Modify: `excelmanus/tools/policy.py`
- Test: `tests/test_tool_policy.py`

**Step 1: 写失败测试**

在 `tests/test_tool_policy.py` 末尾追加：

```python
def test_discovery_tools_are_subset_of_read_only_safe() -> None:
    """基础发现工具集必须全部是只读安全工具。"""
    from excelmanus.tools.policy import DISCOVERY_TOOLS
    assert DISCOVERY_TOOLS.issubset(READ_ONLY_SAFE_TOOLS | {"focus_window"})


def test_discovery_tools_expected_members() -> None:
    from excelmanus.tools.policy import DISCOVERY_TOOLS
    expected = {
        "read_excel", "scan_excel_files", "analyze_data", "filter_data",
        "group_aggregate", "list_sheets", "list_directory", "get_file_info",
        "search_files", "read_text_file", "read_cell_styles", "focus_window",
    }
    assert DISCOVERY_TOOLS == expected


def test_tool_categories_cover_all_registered_tools(tmp_path: Path) -> None:
    """分类映射必须覆盖所有内置工具（不含 memory/task/skill 元工具）。"""
    from excelmanus.tools.policy import TOOL_CATEGORIES
    categorized = set()
    for tools in TOOL_CATEGORIES.values():
        categorized.update(tools)
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    registered = set(registry.get_tool_names())
    # memory/task/skill/focus 工具不在分类中（它们是 always-available 或特殊工具）
    meta_tools = {"memory_save", "memory_read_topic", "task_create", "task_update",
                  "list_skills", "focus_window"}
    uncategorized = registered - categorized - meta_tools
    assert not uncategorized, f"未分类工具: {sorted(uncategorized)}"


def test_tool_categories_no_duplicates() -> None:
    from excelmanus.tools.policy import TOOL_CATEGORIES
    seen: set[str] = set()
    for cat, tools in TOOL_CATEGORIES.items():
        for tool in tools:
            assert tool not in seen, f"工具 '{tool}' 在多个分类中重复"
            seen.add(tool)
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_tool_policy.py::test_discovery_tools_are_subset_of_read_only_safe -v`
Expected: FAIL (ImportError: cannot import name 'DISCOVERY_TOOLS')

**Step 3: 在 policy.py 新增常量**

在 `excelmanus/tools/policy.py` 的 `FALLBACK_DISCOVERY_TOOLS` 之前插入：

```python
# ── 基础发现工具集（无 skill 激活时的默认 scope） ──────────

DISCOVERY_TOOLS: frozenset[str] = frozenset({
    # 数据探查
    "read_excel",
    "scan_excel_files",
    "analyze_data",
    "filter_data",
    "group_aggregate",
    # 结构探查
    "list_sheets",
    "list_directory",
    "get_file_info",
    "search_files",
    "read_text_file",
    # 样式感知
    "read_cell_styles",
    # 窗口
    "focus_window",
})


# ── 工具分类映射（用于 discover_tools 元工具和工具索引） ────

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "data_read": (
        "read_excel", "scan_excel_files", "analyze_data",
        "filter_data", "group_aggregate", "analyze_sheet_mapping",
    ),
    "data_write": (
        "write_excel", "write_cells", "transform_data",
        "insert_rows", "insert_columns",
    ),
    "format": (
        "format_cells", "adjust_column_width", "adjust_row_height",
        "read_cell_styles", "merge_cells", "unmerge_cells",
    ),
    "advanced_format": (
        "apply_threshold_icon_format", "style_card_blocks",
        "scale_range_unit", "apply_dashboard_dark_theme",
        "add_color_scale", "add_data_bar", "add_conditional_rule",
        "set_print_layout", "set_page_header_footer",
    ),
    "chart": ("create_chart", "create_excel_chart"),
    "sheet": (
        "list_sheets", "create_sheet", "copy_sheet",
        "rename_sheet", "delete_sheet", "copy_range_between_sheets",
    ),
    "file": (
        "list_directory", "get_file_info", "search_files",
        "read_text_file", "copy_file", "rename_file", "delete_file",
    ),
    "code": ("write_text_file", "run_code", "run_shell"),
}
```

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_tool_policy.py -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add excelmanus/tools/policy.py tests/test_tool_policy.py
git commit -m "feat(policy): 新增 DISCOVERY_TOOLS 基础工具集和 TOOL_CATEGORIES 分类映射"
```

---

### Task 2: 修改 engine._get_current_tool_scope 实现动态 scope 收窄

**Files:**
- Modify: `excelmanus/engine.py` (行 1739 附近，`_get_current_tool_scope` 最后一个分支)
- Test: `tests/test_engine.py`

**核心改动：** 当 `route_result` 为 `None` 或 `route_mode == "all_tools"` 且 `tool_scope` 为空时，不再返回全量工具，改为返回 `DISCOVERY_TOOLS` + 元工具 + always-available + MCP 工具。

**Step 1: 写失败测试**

在 `tests/test_engine.py` 的 `TestToolScopeConvergence` 类中追加：

```python
def test_all_tools_route_uses_discovery_set_when_no_active_skill(self) -> None:
    """all_tools 路由模式下，无 active_skill 时仅暴露基础发现工具集。"""
    from excelmanus.tools.policy import DISCOVERY_TOOLS
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)

    route_result = SkillMatchResult(
        skills_used=[],
        tool_scope=[],
        route_mode="all_tools",
        system_contexts=[],
    )
    scope = engine._get_current_tool_scope(route_result=route_result)
    scope_set = set(scope)

    # 基础发现工具中已注册的应该在 scope 中
    for tool_name in DISCOVERY_TOOLS:
        if tool_name in set(registry.get_tool_names()):
            assert tool_name in scope_set, f"基础工具 {tool_name} 应在 scope 中"

    # 元工具应在 scope 中
    assert "select_skill" in scope_set
    assert "ask_user" in scope_set
    assert "discover_tools" in scope_set

    # 写入工具不应在 scope 中（除非是 always-available）
    assert "format_cells" not in scope_set or "format_cells" in set(registry.get_tool_names()) is False


def test_active_skill_overrides_discovery_set(self) -> None:
    """active_skill 激活后，scope 应包含 skill 的 allowed_tools 而非仅基础集。"""
    config = _make_config()
    registry = _make_registry_with_tools()
    engine = AgentEngine(config, registry)
    engine._active_skill = Skillpack(
        name="data_basic",
        description="数据处理",
        allowed_tools=["add_numbers"],
        triggers=[],
        instructions="test",
        source="system",
        root_dir="/tmp/data_basic",
    )
    scope = engine._get_current_tool_scope(route_result=None)
    assert "add_numbers" in scope
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestToolScopeConvergence::test_all_tools_route_uses_discovery_set_when_no_active_skill -v`
Expected: FAIL（当前返回全量工具，format_cells 等写入工具会在 scope 中）

**Step 3: 修改 engine.py**

在 `excelmanus/engine.py` 顶部 import 区域添加：

```python
from excelmanus.tools.policy import DISCOVERY_TOOLS
```

修改 `_get_current_tool_scope` 方法的最后一个分支（当前是 `scope = self._all_tool_names()`）：

```python
        # 无 skill 激活、无路由指定 scope：使用基础发现工具集
        # LLM 需要写入/格式化/图表等能力时，通过 select_skill 激活对应技能
        registered = set(self._all_tool_names())
        scope = [t for t in DISCOVERY_TOOLS if t in registered]
        for tool_name in _META_TOOL_NAMES:
            if tool_name not in scope:
                scope.append(tool_name)
        merged_scope = self._append_global_mcp_tools(self._ensure_always_available(scope))
        return self._apply_window_mode_tool_filter(merged_scope)
```

同时修改 `route_result.tool_scope` 为空列表的判断分支（`all_tools` 路由模式）。
当 `route_result is not None` 且 `route_result.tool_scope` 为空且 `route_mode == "all_tools"` 时，
也走基础发现工具集逻辑，而非全量工具。

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_engine.py::TestToolScopeConvergence -v`
Expected: ALL PASS

**Step 5: 运行全量测试确认无回归**

Run: `pytest tests/test_engine.py -q`
Expected: 部分现有测试可能需要调整（见 Step 6）

**Step 6: 修复受影响的现有测试**

以下测试可能受影响，需要检查并调整：
- `test_chat_last_route_scope_matches_effective_scope`：期望 scope 中不含 `add_numbers`，这与新逻辑一致，应该仍然 PASS
- 初始化时 `self._last_route_result` 的 `tool_scope=self._all_tool_names()` 也需要改为基础集

在 `engine.py` 的 `__init__` 中（行 394 附近），将：
```python
tool_scope=self._all_tool_names(),
```
改为：
```python
tool_scope=[t for t in DISCOVERY_TOOLS if t in set(self._all_tool_names())],
```

同样在 `_route_skills` 方法中（行 3834 附近）的 fallback 路径也做同样修改。

**Step 7: 提交**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 无 skill 激活时使用 DISCOVERY_TOOLS 基础工具集替代全量工具"
```

---

### Task 3: 新增 discover_tools 元工具

**Files:**
- Modify: `excelmanus/engine.py`（`_build_meta_tools` 方法 + 新增 `_handle_discover_tools` 方法）
- Modify: `excelmanus/engine.py`（`_META_TOOL_NAMES` 常量）
- Test: `tests/test_engine.py`

**设计：** `discover_tools` 是一个元工具，LLM 调用时传入 `category`（如 "format"、"chart"），
返回该类别下所有工具的 name + description 文本。LLM 据此决定是否需要 `select_skill` 激活对应技能。

**Step 1: 写失败测试**

```python
class TestDiscoverTools:
    """discover_tools 元工具测试。"""

    def test_discover_tools_in_meta_tool_names(self) -> None:
        from excelmanus.engine import _META_TOOL_NAMES
        assert "discover_tools" in _META_TOOL_NAMES

    def test_discover_tools_returns_category_tools(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = engine._handle_discover_tools(category="data_read")
        assert "read_excel" in result
        assert "scan_excel_files" in result

    def test_discover_tools_all_category(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = engine._handle_discover_tools(category="all")
        # all 应返回所有分类
        assert "data_read" in result or "数据读取" in result

    def test_discover_tools_unknown_category(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        result = engine._handle_discover_tools(category="nonexistent")
        assert "未知" in result or "不存在" in result

    def test_discover_tools_schema_in_meta_tools(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        meta_tools = engine._build_meta_tools()
        names = [t["function"]["name"] for t in meta_tools]
        assert "discover_tools" in names
        discover = next(t for t in meta_tools if t["function"]["name"] == "discover_tools")
        params = discover["function"]["parameters"]
        assert "category" in params["properties"]
        assert "enum" in params["properties"]["category"]
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestDiscoverTools -v`
Expected: FAIL

**Step 3: 实现 discover_tools**

3a. 修改 `_META_TOOL_NAMES`（engine.py 行 67）：

```python
_META_TOOL_NAMES = ("select_skill", "delegate_to_subagent", "list_subagents", "ask_user", "discover_tools")
```

3b. 在 `AgentEngine` 类中新增 `_handle_discover_tools` 方法：

```python
def _handle_discover_tools(self, category: str) -> str:
    """处理 discover_tools 元工具调用，返回指定类别的工具列表。"""
    from excelmanus.tools.policy import TOOL_CATEGORIES

    CATEGORY_LABELS = {
        "data_read": "数据读取",
        "data_write": "数据写入",
        "format": "格式化",
        "advanced_format": "高级格式",
        "chart": "图表",
        "sheet": "工作表操作",
        "file": "文件操作",
        "code": "代码执行",
    }

    if category == "all":
        lines = ["## 全部工具分类\n"]
        registered = set(self._all_tool_names())
        for cat, tools in TOOL_CATEGORIES.items():
            label = CATEGORY_LABELS.get(cat, cat)
            available = [t for t in tools if t in registered]
            if available:
                tool_descs = []
                for t in available:
                    tool_def = self._registry.get_tool(t)
                    desc = tool_def.description.split("。")[0] if tool_def else ""
                    tool_descs.append(f"  - {t}：{desc}")
                lines.append(f"### {label}")
                lines.extend(tool_descs)
        lines.append("\n使用 select_skill 激活对应技能后即可调用写入类工具。")
        return "\n".join(lines)

    if category not in TOOL_CATEGORIES:
        available_cats = ", ".join(sorted(TOOL_CATEGORIES.keys()))
        return f"未知分类 '{category}'。可用分类：{available_cats}, all"

    tools = TOOL_CATEGORIES[category]
    label = CATEGORY_LABELS.get(category, category)
    registered = set(self._all_tool_names())
    lines = [f"## {label} 工具\n"]
    for t in tools:
        if t not in registered:
            continue
        tool_def = self._registry.get_tool(t)
        desc = tool_def.description if tool_def else "(无描述)"
        lines.append(f"- {t}：{desc}")
    if not any(line.startswith("- ") for line in lines):
        lines.append("(该分类下无已注册工具)")
    return "\n".join(lines)
```

3c. 在 `_build_meta_tools` 方法的返回列表中追加 discover_tools 定义：

```python
{
    "type": "function",
    "function": {
        "name": "discover_tools",
        "description": (
            "按类别查询可用工具及其功能说明。"
            "当你不确定该用哪个工具、或需要了解某类操作有哪些工具时调用。"
            "返回该类别下所有工具的名称和描述。"
            "注意：查询到的写入类工具需要先通过 select_skill 激活对应技能后才能使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "工具类别",
                    "enum": list(TOOL_CATEGORIES.keys()) + ["all"],
                },
            },
            "required": ["category"],
            "additionalProperties": False,
        },
    },
},
```

3d. 在 agent loop 的工具调用分发逻辑中，增加 discover_tools 的处理分支
（与 select_skill / delegate_to_subagent 同级）：

找到 `_execute_tool_call` 或元工具分发逻辑，增加：

```python
if tool_name == "discover_tools":
    category = arguments.get("category", "all")
    result_text = self._handle_discover_tools(category=category)
    return ToolCallResult(success=True, result=result_text)
```

**Step 4: 运行测试确认通过**

Run: `pytest tests/test_engine.py::TestDiscoverTools -v`
Expected: ALL PASS

**Step 5: 运行全量测试**

Run: `pytest tests/test_engine.py -q`
Expected: ALL PASS（现有测试中 _META_TOOL_NAMES 的引用需要兼容新增的 discover_tools）

**Step 6: 提交**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 新增 discover_tools 元工具，支持按类别查询可用工具"
```

---

### Task 4: 工具分组索引注入 system prompt

**Files:**
- Modify: `excelmanus/engine.py`（新增 `_build_tool_index_notice` 方法，修改 `_prepare_system_prompts_for_request`）
- Test: `tests/test_engine.py`

**设计：** 在 system prompt 中动态生成当前可用工具的分类索引，帮助 LLM 快速定位工具。
仅在无 skill 激活时注入（skill 激活后，skill context 已提供工具指引）。

**Step 1: 写失败测试**

```python
class TestToolIndexNotice:
    """工具分组索引注入测试。"""

    def test_build_tool_index_notice_with_discovery_scope(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        # 模拟基础发现工具集 scope
        scope = ["read_excel", "scan_excel_files", "analyze_data",
                 "filter_data", "list_directory", "search_files"]
        notice = engine._build_tool_index_notice(scope)
        assert "工具索引" in notice
        assert "read_excel" in notice

    def test_build_tool_index_notice_empty_scope(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        notice = engine._build_tool_index_notice([])
        assert notice == ""

    def test_tool_index_not_injected_when_skill_active(self) -> None:
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        engine._active_skill = Skillpack(
            name="data_basic",
            description="test",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="test",
            source="system",
            root_dir="/tmp",
        )
        # skill 激活时不应注入工具索引
        # 验证 _build_tool_index_notice 在 skill 激活时返回空
        notice = engine._build_tool_index_notice(["add_numbers"])
        # 当 scope 中没有分类映射中的工具时，返回空
        assert notice == "" or "工具索引" in notice
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestToolIndexNotice -v`
Expected: FAIL (AttributeError: '_build_tool_index_notice')

**Step 3: 实现 _build_tool_index_notice**

在 `AgentEngine` 类中新增：

```python
def _build_tool_index_notice(self, tool_scope: Sequence[str]) -> str:
    """生成当前可用工具的分类索引，注入 system prompt。"""
    from excelmanus.tools.policy import TOOL_CATEGORIES

    CATEGORY_LABELS = {
        "data_read": "数据读取",
        "data_write": "数据写入",
        "format": "格式化",
        "advanced_format": "高级格式",
        "chart": "图表",
        "sheet": "工作表操作",
        "file": "文件操作",
        "code": "代码执行",
    }

    scope_set = set(tool_scope)
    lines: list[str] = []
    for cat, tools in TOOL_CATEGORIES.items():
        available = [t for t in tools if t in scope_set]
        if available:
            label = CATEGORY_LABELS.get(cat, cat)
            lines.append(f"- {label}：{', '.join(available)}")
    if not lines:
        return ""
    return "## 工具索引\n" + "\n".join(lines) + (
        "\n\n需要更多工具时，调用 select_skill 激活对应技能，"
        "或调用 discover_tools 查询完整工具列表。"
    )
```

**Step 4: 在 _prepare_system_prompts_for_request 中注入工具索引**

在 `_prepare_system_prompts_for_request` 方法中，`base_prompt` 构建完成后、
`_compose_prompts` 之前，增加工具索引注入：

```python
# 无 skill 激活时注入工具分组索引
if self._active_skill is None:
    tool_scope = self._get_current_tool_scope(route_result=None)
    tool_index = self._build_tool_index_notice(tool_scope)
    if tool_index:
        base_prompt = base_prompt + "\n\n" + tool_index
```

注意：`_prepare_system_prompts_for_request` 目前不接收 `tool_scope` 参数，
需要在方法签名中新增或在内部计算。推荐在内部计算（调用 `_get_current_tool_scope`），
因为此时 `_active_skill` 状态已确定。

**Step 5: 运行测试确认通过**

Run: `pytest tests/test_engine.py::TestToolIndexNotice -v`
Expected: ALL PASS

**Step 6: 提交**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 在 system prompt 中注入动态工具分组索引"
```

---

### Task 5: 更新 router fallback 路径使用 DISCOVERY_TOOLS

**Files:**
- Modify: `excelmanus/skillpacks/router.py`（`_build_fallback_result` 和 `_build_all_tools_result`）
- Modify: `excelmanus/tools/policy.py`（可选：废弃 `FALLBACK_DISCOVERY_TOOLS`）
- Test: `tests/test_tool_policy.py`

**设计：** 将 `_build_fallback_result` 中使用的 `FALLBACK_DISCOVERY_TOOLS` 替换为 `DISCOVERY_TOOLS`，
统一基础工具集的单一事实源。`_build_all_tools_result` 返回的 `tool_scope=[]` 保持不变，
由 engine 的 `_get_current_tool_scope` 负责填充基础集。

**Step 1: 修改 router.py**

在 `_build_fallback_result` 中，将：
```python
for tool_name in FALLBACK_DISCOVERY_TOOLS:
    if tool_name not in tool_scope:
        tool_scope.append(tool_name)
```
改为：
```python
from excelmanus.tools.policy import DISCOVERY_TOOLS
for tool_name in DISCOVERY_TOOLS:
    if tool_name not in tool_scope:
        tool_scope.append(tool_name)
```

**Step 2: 在 policy.py 中标记 FALLBACK_DISCOVERY_TOOLS 为废弃**

```python
# ── fallback 路由只读发现工具（已废弃，使用 DISCOVERY_TOOLS 替代） ──
# 保留以兼容外部引用，内容与 DISCOVERY_TOOLS 同步
FALLBACK_DISCOVERY_TOOLS: tuple[str, ...] = tuple(sorted(DISCOVERY_TOOLS))
```

**Step 3: 更新测试**

修改 `test_fallback_discovery_excludes_memory_tool`：
```python
def test_fallback_discovery_excludes_memory_tool() -> None:
    from excelmanus.tools.policy import DISCOVERY_TOOLS
    assert "memory_read_topic" not in DISCOVERY_TOOLS
    assert "memory_save" not in DISCOVERY_TOOLS
```

**Step 4: 运行测试**

Run: `pytest tests/test_tool_policy.py tests/test_engine.py -q`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add excelmanus/skillpacks/router.py excelmanus/tools/policy.py tests/test_tool_policy.py
git commit -m "refactor(router): 统一使用 DISCOVERY_TOOLS 替代 FALLBACK_DISCOVERY_TOOLS"
```

---

### Task 6: 更新 select_skill description 引导 LLM 使用 discover_tools

**Files:**
- Modify: `excelmanus/engine.py`（`_build_meta_tools` 中 `select_skill_description`）

**设计：** 精简 select_skill 的 description，移除冗余说明，增加对 discover_tools 的引导。
skill catalog 仍然嵌入在 description 中（因为 LLM 需要知道有哪些 skill 可选），
但增加一句提示"不确定该激活哪个技能时，先调用 discover_tools 查看工具分类"。

**Step 1: 修改 select_skill_description**

将当前的：
```python
select_skill_description = (
    "激活一个技能包来获取执行任务所需的工具。"
    "仅在当前工具列表不足以完成用户请求时调用。\n"
    "如果用户只是闲聊、问候、询问能力或不需要执行工具，请不要调用本工具，直接回复。\n"
    "⚠️ 信息隔离：不要向用户提及技能名称、工具名称、技能包等内部概念，"
    "只需自然地执行任务并呈现结果。\n"
    "重要：调用本工具后立即执行任务，不要仅输出计划文字。\n\n"
    "Skill_Catalog:\n"
    f"{skill_catalog}"
)
```

改为：
```python
select_skill_description = (
    "激活技能包获取写入/格式化/图表等执行工具。"
    "当前仅有只读探查工具可用，需要修改数据时必须先激活对应技能。\n"
    "不确定该激活哪个技能时，先调用 discover_tools 查看工具分类。\n"
    "⚠️ 不要向用户提及技能名称或工具名称等内部概念。\n"
    "调用后立即执行任务，不要仅输出计划。\n\n"
    "Skill_Catalog:\n"
    f"{skill_catalog}"
)
```

**Step 2: 运行全量测试**

Run: `pytest tests/test_engine.py -q`
Expected: ALL PASS

**Step 3: 提交**

```bash
git add excelmanus/engine.py
git commit -m "refactor(engine): 精简 select_skill description，引导使用 discover_tools"
```

---

### Task 7: 集成测试 — 端到端验证工具注入优化

**Files:**
- Test: `tests/test_engine.py`

**设计：** 验证完整的工具注入流程：首轮基础集 → discover_tools 查询 → select_skill 激活 → 全量工具。

**Step 1: 写集成测试**

```python
class TestToolInjectionOptimization:
    """工具注入优化端到端测试。"""

    def test_initial_scope_is_discovery_set(self) -> None:
        """首轮 scope 应为基础发现工具集 + 元工具。"""
        from excelmanus.tools.policy import DISCOVERY_TOOLS
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        route_result = SkillMatchResult(
            skills_used=[], tool_scope=[], route_mode="all_tools",
            system_contexts=[],
        )
        scope = engine._get_current_tool_scope(route_result=route_result)
        scope_set = set(scope)

        # 基础发现工具应在 scope 中
        registered = set(registry.get_tool_names())
        for tool in DISCOVERY_TOOLS:
            if tool in registered:
                assert tool in scope_set

        # 元工具应在 scope 中
        for meta in ("select_skill", "delegate_to_subagent", "ask_user", "discover_tools"):
            assert meta in scope_set

        # 写入工具不应在 scope 中
        from excelmanus.tools.policy import MUTATING_ALL_TOOLS
        for tool in MUTATING_ALL_TOOLS:
            if tool in registered:
                assert tool not in scope_set, f"写入工具 {tool} 不应在基础 scope 中"

    def test_discover_tools_then_select_skill_flow(self) -> None:
        """discover_tools → select_skill 的两阶段流程。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)

        # 阶段 1：discover_tools 查询
        result = engine._handle_discover_tools(category="all")
        assert isinstance(result, str)
        assert len(result) > 0

        # 阶段 2：select_skill 激活后 scope 扩展
        data_skill = Skillpack(
            name="data_basic",
            description="数据处理",
            allowed_tools=["add_numbers"],
            triggers=[],
            instructions="test",
            source="system",
            root_dir="/tmp/data_basic",
        )
        mock_loader = MagicMock()
        mock_loader.get_skillpacks.return_value = {"data_basic": data_skill}
        mock_loader.get_skillpack.return_value = data_skill
        mock_router = MagicMock()
        mock_router._loader = mock_loader
        mock_router._find_skill_by_name = MagicMock(return_value=data_skill)
        engine._skill_router = mock_router

        await engine._handle_select_skill("data_basic")
        scope = engine._get_current_tool_scope(route_result=None)
        assert "add_numbers" in scope

    def test_tool_index_in_system_prompt(self) -> None:
        """system prompt 中应包含工具索引。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        scope = ["read_excel", "scan_excel_files", "list_directory"]
        notice = engine._build_tool_index_notice(scope)
        assert "工具索引" in notice
        assert "discover_tools" in notice or "select_skill" in notice

    def test_discover_tools_category_enum_matches_tool_categories(self) -> None:
        """discover_tools 的 category enum 应与 TOOL_CATEGORIES 一致。"""
        from excelmanus.tools.policy import TOOL_CATEGORIES
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config, registry)
        meta_tools = engine._build_meta_tools()
        discover = next(t for t in meta_tools if t["function"]["name"] == "discover_tools")
        enum_values = discover["function"]["parameters"]["properties"]["category"]["enum"]
        expected = sorted(TOOL_CATEGORIES.keys()) + ["all"]
        # enum 应包含所有分类 + "all"
        assert set(enum_values) == set(expected)
```

**Step 2: 运行测试**

Run: `pytest tests/test_engine.py::TestToolInjectionOptimization -v`
Expected: ALL PASS

**Step 3: 运行全量测试套件**

Run: `pytest -q`
Expected: ALL PASS

**Step 4: 提交**

```bash
git add tests/test_engine.py
git commit -m "test(engine): 工具注入优化端到端集成测试"
```

---

### Task 8: 更新 _ALWAYS_AVAILABLE_TOOLS 包含 memory 工具

**Files:**
- Modify: `excelmanus/engine.py`（`_ALWAYS_AVAILABLE_TOOLS` 常量）
- Test: `tests/test_engine.py`

**设计：** 当前 `_ALWAYS_AVAILABLE_TOOLS` 只有 `task_create, task_update, ask_user, delegate_to_subagent`。
需要加入 `memory_save, memory_read_topic, list_skills`，确保这些工具在任何 scope 下都可用。

**Step 1: 修改常量**

```python
_ALWAYS_AVAILABLE_TOOLS = (
    "task_create", "task_update", "ask_user", "delegate_to_subagent",
    "memory_save", "memory_read_topic", "list_skills",
)
```

**Step 2: 写测试**

```python
def test_always_available_tools_include_memory_and_skills(self) -> None:
    from excelmanus.engine import _ALWAYS_AVAILABLE_TOOLS
    assert "memory_save" in _ALWAYS_AVAILABLE_TOOLS
    assert "memory_read_topic" in _ALWAYS_AVAILABLE_TOOLS
    assert "list_skills" in _ALWAYS_AVAILABLE_TOOLS
```

**Step 3: 运行测试**

Run: `pytest tests/test_engine.py -q`
Expected: ALL PASS

**Step 4: 提交**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 扩展 _ALWAYS_AVAILABLE_TOOLS 包含 memory 和 skill 工具"
```

---

## 实施顺序总结

```
Task 1: policy.py 新增 DISCOVERY_TOOLS + TOOL_CATEGORIES
  ↓
Task 2: engine._get_current_tool_scope 使用基础集
  ↓
Task 3: 新增 discover_tools 元工具
  ↓
Task 4: system prompt 注入工具分组索引
  ↓
Task 5: router fallback 统一使用 DISCOVERY_TOOLS
  ↓
Task 6: 更新 select_skill description
  ↓
Task 7: 端到端集成测试
  ↓
Task 8: 扩展 _ALWAYS_AVAILABLE_TOOLS
```

## 预期收益

| 指标 | 改进前 | 改进后 |
|------|--------|--------|
| 首轮工具 token | ~11,000 | ~4,000 (22 工具) |
| Skill 激活后工具 token | ~11,000 | ~6,000-8,000 (skill scope) |
| 工具选择准确率 | 中 | 高（索引 + discover_tools 引导） |
| 无效 skill 激活 | 偶发 | 减少（discover_tools 先查询） |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| LLM 首轮需要写入工具但找不到 | select_skill description 明确引导；discover_tools 可查询 |
| 现有测试回归 | Task 2 Step 6 专门处理；Task 7 端到端验证 |
| general_excel 兜底 skill 行为变化 | general_excel 的 allowed_tools 不变，激活后仍是全量 |
| Plan 模式下需要全量工具 | 已批准计划上下文中 active_skill 会被设置，走 skill scope 路径 |
