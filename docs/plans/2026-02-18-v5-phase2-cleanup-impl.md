# v5 Phase 2: 废弃字段清理 + 全链路适配 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 清理 v5 Phase 1 遗留的废弃字段（allowed_tools/triggers/priority/tool_scope），使整个代码库与 v5 三层正交架构完全对齐。

**Architecture:** 分 3 个工作项推进：WI-1 SKILL.md 格式迁移 → WI-2 Model/Loader/Router 瘦身 → WI-3 tool_scope + 旧术语全链路清理。每步 commit + 回归。

**Tech Stack:** Python 3.12, pytest, dataclass, YAML frontmatter

**前置条件:** v5 Phase 1 已完成，全量回归 1448 passed / 0 failed（commit `e7c4b03`）。

---

## WI-1: SKILL.md 格式迁移 + general_excel 删除

### Task 1: 从系统 SKILL.md 中移除废弃 frontmatter 字段

**Files:**
- Modify: `excelmanus/skillpacks/system/data_basic/SKILL.md`
- Modify: `excelmanus/skillpacks/system/chart_basic/SKILL.md`
- Modify: `excelmanus/skillpacks/system/format_basic/SKILL.md`
- Modify: `excelmanus/skillpacks/system/excel_code_runner/SKILL.md`
- Modify: `excelmanus/skillpacks/system/file_ops/SKILL.md`
- Modify: `excelmanus/skillpacks/system/sheet_ops/SKILL.md`

**Step 1: 移除 6 个 SKILL.md 中的 `allowed_tools`、`triggers`、`priority` 字段**

从每个文件的 frontmatter 中删除以下字段块：
- `allowed_tools:` 及其下方所有 `  - xxx` 行
- `triggers:` 及其下方所有 `  - xxx` 行
- `priority: N` 行

保留：`name`、`description`、`file_patterns`、`resources`、`version`、`user_invocable`、`hooks`、`command_dispatch`、`command_tool`、`required_mcp_*`

示例（data_basic 修改后）：
```yaml
---
name: data_basic
description: 数据读取、分析、筛选与转换
file_patterns:
  - "*.xlsx"
version: "1.0.0"
---
```

**Step 2: 运行加载测试确认 SKILL.md 解析不报错**

Run: `uv run pytest tests/test_skillpacks.py -x -q --tb=short`
Expected: 全部 PASS（loader 对缺失的 optional 字段返回空列表/默认值）

**Step 3: Commit**

```
git add excelmanus/skillpacks/system/
git commit -m "chore(v5): remove allowed_tools/triggers/priority from system SKILL.md files"
```

---

### Task 2: 删除 general_excel skillpack

**Files:**
- Delete: `excelmanus/skillpacks/system/general_excel/` (整个目录)

**Step 1: 确认无代码引用**

Run: `grep -r "general_excel" excelmanus/ --include="*.py" | head -20`
Expected: 0 条结果（v5 Phase 1 已清理全部引用）

**Step 2: 删除目录**

```bash
rm -rf excelmanus/skillpacks/system/general_excel/
```

**Step 3: 运行回归确认无破坏**

Run: `uv run pytest tests/ -x -q --tb=line 2>&1 | tail -5`
Expected: 全部 PASS

**Step 4: Commit**

```
git add -A
git commit -m "chore(v5): delete general_excel fallback skillpack (no longer needed)"
```

---

## WI-2: Skillpack Model + Loader + Router 瘦身

### Task 3: Router._build_result() 停止从 allowed_tools 构建 tool_scope

**Files:**
- Modify: `excelmanus/skillpacks/router.py:196-224`
- Test: `tests/test_skillpacks.py`

**Step 1: 修改 `_build_result()` 不再遍历 `skill.allowed_tools`**

将 `excelmanus/skillpacks/router.py` 的 `_build_result` 方法中 tool_scope 构建逻辑替换：

```python
# 旧代码（删除）:
tool_scope: list[str] = []
seen_tools: set[str] = set()
for skill in selected:
    for tool in skill.allowed_tools:
        if tool in seen_tools:
            continue
        seen_tools.add(tool)
        tool_scope.append(tool)

# 新代码（替换为）:
tool_scope: list[str] = []  # v5: engine 使用 _build_v5_tools()，不再依赖 router tool_scope
```

同时更新 `_build_fallback_result` 的 docstring，将 `select_skill` 引用改为 `activate_skill`。

**Step 2: 运行路由测试**

Run: `uv run pytest tests/test_skillpacks.py -x -q --tb=short`
Expected: 全部 PASS

**Step 3: Commit**

```
git add excelmanus/skillpacks/router.py
git commit -m "refactor(v5): router stops building tool_scope from allowed_tools"
```

---

### Task 4: Loader 移除 _validate_allowed_tools_soft

**Files:**
- Modify: `excelmanus/skillpacks/loader.py:280,349-376`

**Step 1: 删除 `_validate_allowed_tools_soft` 方法及其调用**

1. 删除 `loader.py:280` 的调用行：`self._validate_allowed_tools_soft(name=name, allowed_tools=allowed_tools)`
2. 删除 `loader.py:349-376` 的 `_validate_allowed_tools_soft` 方法定义
3. 同时删除 `_is_allowed_tool_selector` 静态方法（仅被 `_validate_allowed_tools_soft` 调用）

**Step 2: 运行加载测试**

Run: `uv run pytest tests/test_skillpacks.py -x -q --tb=short`
Expected: 全部 PASS

**Step 3: Commit**

```
git add excelmanus/skillpacks/loader.py
git commit -m "refactor(v5): remove _validate_allowed_tools_soft from loader (v5 ignores allowed_tools)"
```

---

### Task 5: Engine._adapt_guidance_only_slash_route() 替换 allowed_tools 判断

**Files:**
- Modify: `excelmanus/engine.py` (`_adapt_guidance_only_slash_route` 方法)

**Step 1: 替换 `skill.allowed_tools` 检查**

旧代码：
```python
if skill.command_dispatch == "tool" or skill.allowed_tools:
    return route_result, user_message
```

新代码：
```python
if skill.command_dispatch == "tool":
    return route_result, user_message
```

逻辑说明：v5 中 skill 不再通过 `allowed_tools` 声明是否为"可执行型"。
`command_dispatch == "tool"` 表示该 skill 有绑定的工具命令（直接执行）。
其他 skill 均为"guidance-only"（纯知识注入），需回落到任务路由。

**Step 2: 运行引擎测试**

Run: `uv run pytest tests/test_engine.py -x -q --tb=short`
Expected: 全部 PASS

**Step 3: Commit**

```
git add excelmanus/engine.py
git commit -m "refactor(v5): replace allowed_tools check with command_dispatch in guidance-only detection"
```

---

## WI-3: tool_scope + 旧术语全链路清理

### Task 6: Engine — 清理 _execute_tool_call 中的 tool_scope 参数

**Files:**
- Modify: `excelmanus/engine.py` (多处)

**Step 1: `_execute_tool_call` 签名中 `tool_scope` 改为可选 None**

```python
# 旧
async def _execute_tool_call(self, tc, tool_scope: Sequence[str], ...):
# 新
async def _execute_tool_call(self, tc, tool_scope: Sequence[str] | None = None, ...):
```

**Step 2: 删除 `ToolNotAllowedError` 分支中 `list(tool_scope)` 调用**

`_execute_tool_call` 末尾的 `except ToolNotAllowedError` 分支中：
```python
# 旧
"allowed_tools": list(tool_scope),
# 新
"allowed_tools": list(tool_scope) if tool_scope else [],
```

**Step 3: `_call_registry_tool` 签名也改为可选 None**

```python
# 旧
async def _call_registry_tool(self, *, tool_name, arguments, tool_scope: Sequence[str]) -> Any:
# 新
async def _call_registry_tool(self, *, tool_name, arguments, tool_scope: Sequence[str] | None = None) -> Any:
```

**Step 4: 运行引擎测试**

Run: `uv run pytest tests/test_engine.py -x -q --tb=short`
Expected: 全部 PASS

**Step 5: Commit**

```
git add excelmanus/engine.py
git commit -m "refactor(v5): make tool_scope optional in _execute_tool_call and _call_registry_tool"
```

---

### Task 7: Engine — 重命名 _handle_select_skill → _handle_activate_skill

**Files:**
- Modify: `excelmanus/engine.py` (3 处引用)

**Step 1: 全文替换**

- `_handle_select_skill` → `_handle_activate_skill` (方法定义 + 2 处调用)
- `_is_select_skill_ok` → `_is_activate_skill_ok` (方法定义 + 调用)
- dispatch 分支注释 `"select_skill"` 兼容逻辑保留（旧版 LLM 可能仍发送 select_skill）

**Step 2: 运行测试**

Run: `uv run pytest tests/test_engine.py -x -q --tb=short`
Expected: 全部 PASS

**Step 3: Commit**

```
git add excelmanus/engine.py
git commit -m "refactor(v5): rename _handle_select_skill → _handle_activate_skill"
```

---

### Task 8: Subagent — 更新旧术语引用

**Files:**
- Modify: `excelmanus/subagent/executor.py:25`
- Modify: `excelmanus/subagent/builtin.py:176`

**Step 1: executor.py 更新 blocked meta tools**

```python
# 旧
_SUBAGENT_BLOCKED_META_TOOLS = {"select_skill", "delegate_to_subagent", "list_subagents"}
# 新
_SUBAGENT_BLOCKED_META_TOOLS = {"activate_skill", "expand_tools", "delegate_to_subagent", "list_subagents"}
```

**Step 2: builtin.py 更新 full 子代理系统提示**

```python
# 旧
"- 优先使用 select_skill 激活合适的技能包来获取领域知识和工具授权。\n"
# 新
"- 优先使用 activate_skill 激活合适的技能包来获取领域知识和操作指引。\n"
```

**Step 3: 运行子代理测试**

Run: `uv run pytest tests/test_subagent_executor.py -x -q --tb=short`
Expected: 全部 PASS

**Step 4: Commit**

```
git add excelmanus/subagent/
git commit -m "refactor(v5): update subagent meta tool references (select_skill → activate_skill)"
```

---

### Task 9: Renderer — 更新元工具显示映射

**Files:**
- Modify: `excelmanus/renderer.py:33-38,537`

**Step 1: 更新 `_META_TOOL_DISPLAY` 映射**

```python
# 旧
_META_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "select_skill": ("⚙️", "准备工具"),
    "delegate_to_subagent": ("🧵", "委派子任务"),
    "list_subagents": ("📋", "查询可用助手"),
    "list_skills": ("📋", "查询可用能力"),
}
# 新
_META_TOOL_DISPLAY: dict[str, tuple[str, str]] = {
    "activate_skill": ("⚙️", "激活技能指引"),
    "expand_tools": ("🔧", "展开工具参数"),
    "delegate_to_subagent": ("🧵", "委派子任务"),
    "list_subagents": ("📋", "查询可用助手"),
}
```

**Step 2: 更新 `_meta_tool_hint` 方法中的 `select_skill` 引用**

```python
# 旧
if tool_name == "select_skill":
# 新
if tool_name == "activate_skill":
```

**Step 3: Commit**

```
git add excelmanus/renderer.py
git commit -m "refactor(v5): update renderer meta tool display (activate_skill + expand_tools)"
```

---

### Task 10: Memory — 更新 system prompt 中的旧术语

**Files:**
- Modify: `excelmanus/memory.py:66`

**Step 1: 替换 system prompt 中的 `select_skill` 引用**

```python
# 旧
"必须先调用 select_skill 激活对应技能，然后立即使用激活的工具完成操作。"
# 新
"必须先调用 activate_skill 激活对应技能获取操作指引，或调用 expand_tools 展开对应类别获取完整工具参数。"
```

**Step 2: Commit**

```
git add excelmanus/memory.py
git commit -m "refactor(v5): update system prompt terminology (select_skill → activate_skill/expand_tools)"
```

---

### Task 11: 测试全链路适配 — 批量替换旧术语

**Files:**
- Modify: `tests/test_engine.py` (~8 处 `select_skill` 引用)
- Modify: `tests/test_pbt_llm_routing.py` (~10 处)
- Modify: `tests/test_skillpacks.py` (~1 处)
- Modify: `tests/test_write_guard.py` (~1 处)
- Modify: `tests/test_bench_validator.py` (~4 处)
- Modify: `tests/test_mcp_client.py` (~21 处 `discover_tools` — 注意：MCP 的 discover_tools 是不同概念，**不要改**)

**Step 1: 替换 test_engine.py 中的 `select_skill` → `activate_skill`**

注意：
- `_handle_select_skill` → `_handle_activate_skill`
- `"select_skill"` 工具名 → `"activate_skill"`（在 dispatch 测试中）
- 保留 dispatch 兼容分支 `tool_name in ("activate_skill", "select_skill")` 的测试

**Step 2: 替换 test_pbt_llm_routing.py 中的旧引用**

- `_handle_select_skill` → `_handle_activate_skill`
- `select_skill` 工具名引用 → `activate_skill`

**Step 3: 确认 test_mcp_client.py 中的 `discover_tools` 不需要修改**

MCP 的 `discover_tools` 是 MCP 协议层的方法（发现远程工具），与旧的 `discover_tools` 元工具完全不同。**不要修改**。

**Step 4: 运行全量回归**

Run: `uv run pytest tests/ -q --tb=line 2>&1 | tail -5`
Expected: 全部 PASS（1448+）

**Step 5: Commit**

```
git add tests/
git commit -m "test(v5): update test assertions for activate_skill/expand_tools terminology"
```

---

### Task 12: 全量回归 + 里程碑 Commit

**Step 1: 运行全量测试**

Run: `uv run pytest tests/ -v --tb=short 2>&1 | tail -20`
Expected: 全部 PASS，0 failed

**Step 2: 里程碑 Commit**

```
git add -A
git commit -m "milestone(v5-phase2): complete field cleanup + full chain terminology alignment"
```

---

## 影响面汇总

### 源码文件改动清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `skillpacks/system/*/SKILL.md` ×6 | 内容修改 | 移除 allowed_tools/triggers/priority |
| `skillpacks/system/general_excel/` | 删除 | 废弃兜底 skillpack |
| `skillpacks/router.py` | 代码修改 | _build_result 不再构建 tool_scope |
| `skillpacks/loader.py` | 代码修改 | 删除 _validate_allowed_tools_soft |
| `engine.py` | 代码修改 | guidance-only 判断、方法重命名、tool_scope 可选化 |
| `subagent/executor.py` | 代码修改 | blocked meta tools 更新 |
| `subagent/builtin.py` | 代码修改 | full 子代理系统提示更新 |
| `renderer.py` | 代码修改 | 元工具显示映射更新 |
| `memory.py` | 代码修改 | system prompt 术语更新 |

### 测试文件改动清单

| 文件 | 匹配数 | 改动说明 |
|---|---|---|
| `test_engine.py` | ~8 | select_skill → activate_skill |
| `test_pbt_llm_routing.py` | ~10 | 同上 |
| `test_skillpacks.py` | ~1 | 同上 |
| `test_write_guard.py` | ~1 | 同上 |
| `test_bench_validator.py` | ~4 | 同上 |
| `test_mcp_client.py` | 0 | **不改**（MCP discover_tools 是不同概念）|

### 不动的文件

| 文件 | 原因 |
|---|---|
| `subagent/models.py` | SubagentConfig.allowed_tools 是子代理隔离概念，与 Skill 无关 |
| `subagent/tool_filter.py` | 子代理 tool_scope 是运行期动态限制，与 SkillMatchResult.tool_scope 无关 |
| `approval.py` | tool_scope 在审计记录中保留（已兼容 None） |
| `mcp/client.py` | discover_tools 是 MCP 协议方法，不是旧元工具 |
| `bench.py` | tool_scope/route_mode 用于 bench 指标记录，保留可观测性 |
