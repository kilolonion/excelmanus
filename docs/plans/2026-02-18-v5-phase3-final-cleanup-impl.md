# v5 Phase 3: Bench Tracer 修复 + SkillMatchResult.tool_scope 清理

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复 bench tracer 对已删除 `_get_current_tool_scope` 的引用（运行时崩溃），并将 `SkillMatchResult.tool_scope` 改为默认空列表以消除全代码库的冗余传参。

**Architecture:** 先修复 bench.py 的 critical crash，然后将 SkillMatchResult.tool_scope 改为带默认值的 field，最后清理所有冗余的 `tool_scope=[]` 构造。

**Tech Stack:** Python 3.12, pytest, dataclass

---

### Task 1: 修复 bench.py _EngineTracer 对已删除方法的引用

**Files:**
- Modify: `excelmanus/bench.py:628-761`
- Test: `tests/test_bench.py`

**Step 1: 移除 _EngineTracer 中对 _get_current_tool_scope 的 monkey-patch**

删除 `_traced_scope` 方法及相关代码：
- 删除 `self._orig_scope = engine._get_current_tool_scope`（line 648）
- 删除 `engine._get_current_tool_scope = self._traced_scope`（line 653）
- 删除 `_traced_scope` 方法定义（lines 733-748）
- 删除 `restore()` 中的 `self._engine._get_current_tool_scope = self._orig_scope`（line 761）
- 更新 docstring 移除对 `_get_current_tool_scope` 的引用

**Step 2: 运行 bench 测试**

Run: `uv run pytest tests/test_bench.py -x -q --tb=short`
Expected: PASS

**Step 3: Commit**

```
git add excelmanus/bench.py
git commit -m "fix(v5): remove _EngineTracer reference to deleted _get_current_tool_scope"
```

---

### Task 2: SkillMatchResult.tool_scope 改为默认空列表

**Files:**
- Modify: `excelmanus/skillpacks/models.py:102`
- Test: `tests/test_skillpacks.py`

**Step 1: 将 tool_scope 从必填改为带默认值**

```python
# 旧
tool_scope: list[str]
# 新
tool_scope: list[str] = field(default_factory=list)
```

注意：frozen dataclass 中字段顺序要求带默认值的字段在无默认值字段之后。
当前顺序：skills_used, tool_scope, route_mode → tool_scope 需移到 route_mode 之后。

```python
@dataclass(frozen=True)
class SkillMatchResult:
    skills_used: list[str]
    route_mode: str
    system_contexts: list[str] = field(default_factory=list)
    tool_scope: list[str] = field(default_factory=list)
    parameterized: bool = False
    write_hint: str = "unknown"
```

**Step 2: 运行全量测试确认无破坏**

Run: `uv run pytest tests/ -x -q --tb=line 2>&1 | tail -5`
Expected: 可能有测试因位置参数传参失败

**Step 3: 修复因字段顺序变化导致的位置参数问题**

全代码库搜索 `SkillMatchResult(` 构造，确保都用关键字参数。

**Step 4: Commit**

```
git add excelmanus/skillpacks/models.py
git commit -m "refactor(v5): make SkillMatchResult.tool_scope optional with default empty list"
```

---

### Task 3: 清理所有冗余的 tool_scope=[] 传参

**Files:**
- Modify: `excelmanus/engine.py` (~10 处)
- Modify: `excelmanus/skillpacks/router.py` (~4 处)
- Modify: `tests/test_engine.py` (~60 处)
- Modify: `tests/test_skillpacks.py` (~6 处)
- Modify: `tests/test_write_guard.py` (~13 处)
- Modify: `tests/test_api.py` (~11 处)
- Modify: `tests/test_pbt_unauthorized_tool.py` (~18 处)

**Step 1: 从 engine.py 中的 SkillMatchResult 构造中移除 tool_scope=[]**

所有 `SkillMatchResult(..., tool_scope=[], ...)` 中删除 `tool_scope=[]`。

**Step 2: 从 router.py 中移除 tool_scope=[]**

**Step 3: 从测试文件中移除 tool_scope=[]**

**Step 4: 运行全量测试**

Run: `uv run pytest tests/ -q --tb=line 2>&1 | tail -5`
Expected: 全部 PASS

**Step 5: Commit**

```
git add -A
git commit -m "refactor(v5): remove redundant tool_scope=[] from all SkillMatchResult constructions"
```

---

### Task 4: 全量回归 + 里程碑 Commit

**Step 1: 运行全量测试**

Run: `uv run pytest tests/ -v --tb=short 2>&1 | tail -10`
Expected: 全部 PASS

**Step 2: Commit**

```
git commit --allow-empty -m "milestone(v5-phase3): bench tracer fix + SkillMatchResult.tool_scope cleanup complete"
```
