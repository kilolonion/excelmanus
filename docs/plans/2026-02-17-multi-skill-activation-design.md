# 方案 B：多 Skill 同时激活 — 设计文档

> 日期：2026-02-17
> 状态：待实施

## 1. 问题背景

ExcelManus 的 skillpack 路由系统存在"复合任务盲区"：当用户发出复合请求（如"先画一个柱状图，然后把表头美化一下"），系统只能激活一个 skillpack，无法同时使用 chart_basic 和 format_basic 的工具。

根本原因：`engine.py` 中 `_active_skill: Skillpack | None` 是单值设计，`select_skill` 是覆盖式赋值，切换 skill 时旧 skill 的工具丢失。

## 2. 设计方案

将 `_active_skill: Skillpack | None` 改为 `_active_skills: list[Skillpack]`，支持有序多 skill 同时激活。

### 设计原则

| # | 原则 | 实现策略 |
|---|------|---------|
| 1 | 有序列表，末尾 = 主 skill | `_active_skills[-1]` 即当前主 skill |
| 2 | 追加式 LRU | `select_skill` 时若已存在则移到末尾，否则 append |
| 3 | 工具范围 = 并集 | 遍历所有 `_active_skills` 的 `allowed_tools` 取并集 |
| 4 | System context 只注入主 skill | 仅 `_active_skills[-1].render_context()` |
| 5 | Hook 基于主 skill 触发 | `_active_skills[-1]` 传入 `_run_skill_hook` |
| 6 | `clear_memory` 清空整个列表 | `_active_skills.clear()` |
| 7 | 不设累积上限 | 列表无 maxlen |

### 新增辅助方法

```python
@property
def _primary_skill(self) -> Skillpack | None:
    """当前主 skill（列表末尾），无激活时返回 None。"""
    return self._active_skills[-1] if self._active_skills else None

def _active_skills_tool_union(self) -> list[str]:
    """所有激活 skill 的 allowed_tools 并集（保序去重）。"""
    seen: set[str] = set()
    result: list[str] = []
    for skill in self._active_skills:
        for tool in skill.allowed_tools:
            if tool not in seen:
                seen.add(tool)
                result.append(tool)
    return result
```

## 3. 修改清单

### 3.1 engine.py — 字段声明

```python
# 旧
self._active_skill: Skillpack | None = None

# 新
self._active_skills: list[Skillpack] = []
```

### 3.2 engine.py — 13 处引用替换

| 位置 | 旧代码模式 | 新代码模式 |
|------|-----------|-----------|
| `shutdown_mcp` | `if self._active_skill is not None` | `if self._active_skills` → 取 `[-1]` |
| `chat` 预路由条件 | `self._active_skill is None` | `not self._active_skills` |
| `chat` 补全 context | `self._active_skill.render_context()` | `self._active_skills[-1].render_context()` |
| `_pick_route_skill` | `return self._active_skill` | `return self._active_skills[-1]` |
| `_handle_select_skill` | `self._active_skill = selected` | LRU 追加（移除同名 + append） |
| `_refresh_route_after_skill_switch` ×2 | `self._active_skill.name` / `.render_context()` | `self._active_skills[-1]` |
| `_get_current_tool_scope` | `self._active_skill.allowed_tools` | `self._active_skills_tool_union()` |
| `_delegate_to_subagent` | `hook_skill = self._active_skill` | `self._active_skills[-1] if self._active_skills else None` |
| `_tool_calling_loop` 执行守卫 | `self._active_skill is None` | `not self._active_skills` |
| `clear_memory` ×3 | hook 触发 + `self._active_skill = None` | hook 对 `[-1]` + `self._active_skills.clear()` |
| `_prepare_system_prompts` | `self._active_skill is not None` | `bool(self._active_skills)` |

### 3.3 `_handle_select_skill` 核心变更

```python
# 旧
self._active_skill = selected

# 新：LRU 追加
self._active_skills = [
    s for s in self._active_skills if s.name != selected.name
] + [selected]
```

### 3.4 `_get_current_tool_scope` 核心变更

```python
# 旧
if self._active_skill is not None:
    scope = self._expand_tool_scope_patterns(self._active_skill.allowed_tools)

# 新
if self._active_skills:
    scope = self._expand_tool_scope_patterns(self._active_skills_tool_union())
```

### 3.5 测试文件修改

`tests/test_engine.py` 和 `tests/test_pbt_llm_routing.py` 中约 20 处 `engine._active_skill = ...` 需改为 `engine._active_skills = [...]`，`engine._active_skill is None` 改为 `not engine._active_skills` 或 `engine._active_skills == []`。

## 4. 测试计划

### 新增单元测试

- `test_select_skill_appends_to_list` — 连续 select A → B，验证 `_active_skills == [A, B]`
- `test_select_skill_lru_reorder` — select A → B → A，验证 `_active_skills == [B, A]`
- `test_tool_scope_is_union` — 激活 A(t1,t2) + B(t2,t3)，验证 scope = {t1,t2,t3}
- `test_system_context_only_primary` — 激活 A + B，验证 context 仅含 B
- `test_clear_memory_empties_list` — clear 后验证 `_active_skills == []`
- `test_hook_fires_on_primary_only` — 激活 A + B，shutdown 时 hook 仅对 B 触发

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 工具名冲突 | `_active_skills_tool_union` 已做去重，同名工具指向同一注册实现 |
| 非主 skill 的 hook 未触发 | 当前设计只对主 skill 触发，按需扩展 |
| LRU 列表无限增长 | 不设上限，单会话内 skill 数量有限 |
| 测试文件改动量 | 约 20 处机械替换，风险低 |
