# Skill-Tool Logic Deconfusion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate skill/tool routing ambiguity by making `route_mode`, `pre_route`, `active_skills`, and `tool_scope` semantics explicit and consistent in code, prompts, and telemetry.

**Architecture:** Keep runtime behavior stable first (P0), but refactor semantic boundaries: routing decides intent, scope resolver decides authorization, and prompt text reflects actual auto-supplement mode. Then separate “context memory” from “authorization state” to remove cross-turn scope drift side effects. Finally, clean up docs and telemetry to make diagnosis deterministic.

**Tech Stack:** Python 3.11, pytest, existing `AgentEngine` / `SkillRouter` / bench trace pipeline

---

### Task 1: Capability Wording Single Source of Truth (P0)

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
def test_discover_tools_footer_follows_auto_supplement_enabled() -> None:
    config = _make_config(auto_supplement_enabled=True)
    engine = _make_engine(config=config)
    text = engine._handle_discover_tools(category="all")
    assert "可直接调用" in text or "自动激活" in text
    assert "使用 select_skill 激活" not in text


def test_discover_tools_footer_follows_auto_supplement_disabled() -> None:
    config = _make_config(auto_supplement_enabled=False)
    engine = _make_engine(config=config)
    text = engine._handle_discover_tools(category="all")
    assert "select_skill" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine.py -k "discover_tools_footer_follows_auto_supplement" -v`
Expected: FAIL because `_handle_discover_tools()` currently always appends `使用 select_skill 激活...`.

**Step 3: Write minimal implementation**

```python
# excelmanus/engine.py

def _activation_guidance_line(self) -> str:
    if self._config.auto_supplement_enabled:
        return "按需工具可直接调用，系统会自动激活对应技能。"
    return "需要先调用 select_skill 激活对应技能后再调用写入类工具。"
```

Use this helper in all three places:
- `_build_meta_tools()` (`discover_tools` description)
- `_handle_discover_tools()` footer
- `_build_tool_index_notice()` tail warning

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine.py -k "discover_tools_footer_follows_auto_supplement or tool_index_uses_auto_supplement_wording_when_enabled or tool_index_uses_select_skill_wording_when_disabled" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_engine.py excelmanus/engine.py
git commit -m "refactor: unify skill activation wording across tool guidance surfaces"
```

### Task 2: Make Scope Resolution Source Explicit (P0)

**Files:**
- Modify: `excelmanus/engine.py`
- Modify: `excelmanus/bench.py`
- Test: `tests/test_engine.py`
- Test: `tests/test_bench.py`

**Step 1: Write the failing tests**

```python
def test_scope_resolution_source_discovery_when_no_skill() -> None:
    engine = _make_engine()
    source, scope = engine._resolve_scope_with_source(route_result=None)
    assert source == "discovery"
    assert "select_skill" in scope


def test_scope_resolution_source_active_skills_wins() -> None:
    engine = _make_engine()
    asyncio.run(engine._handle_select_skill("data_basic"))
    source, scope = engine._resolve_scope_with_source(route_result=None)
    assert source == "active_skills"
    assert "read_excel" in scope
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine.py -k "scope_resolution_source" -v`
Expected: FAIL because `_resolve_scope_with_source()` does not exist yet.

**Step 3: Write minimal implementation**

```python
# excelmanus/engine.py

def _resolve_scope_with_source(self, route_result: SkillMatchResult | None):
    # returns ("active_skills"|"slash_direct"|"route_scope"|"discovery", scope)
    ...

# _get_current_tool_scope becomes a thin wrapper returning scope only.
```

Also expose `scope_source` in bench tracer event payload for `tool_scope_resolved`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine.py -k "scope_resolution_source or all_tools_route_uses_discovery_set" -v`
Run: `pytest tests/test_bench.py -k "tool_scope_resolved" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_engine.py tests/test_bench.py excelmanus/engine.py excelmanus/bench.py
git commit -m "feat: add explicit scope resolution source for routing diagnostics"
```

### Task 3: Clarify `all_tools` Semantics in Router and Contracts (P0)

**Files:**
- Modify: `excelmanus/skillpacks/router.py`
- Modify: `docs/skillpack_protocol.md`
- Test: `tests/test_skillpacks.py`

**Step 1: Write the failing/guard tests**

```python
async def test_non_slash_all_tools_means_engine_resolved_scope_not_full_access(...):
    result = await router.route("请读取销售数据")
    assert result.route_mode == "all_tools"
    assert result.tool_scope == []
```

(If this test already exists, tighten its docstring/assert messages to lock semantics.)

**Step 2: Run test to verify baseline**

Run: `pytest tests/test_skillpacks.py -k "non_slash_returns_all_tools" -v`
Expected: PASS before refactor (behavior unchanged).

**Step 3: Implement textual contract cleanup (no behavior change)**

Update misleading comments/docstrings:
- `"默认全量工具"` → `"默认通用路由，具体工具域由引擎按策略收敛"`
- `"tool_scope 为空表示放开全量工具"` → `"tool_scope 为空表示交由引擎计算有效工具域"`

**Step 4: Re-run tests**

Run: `pytest tests/test_skillpacks.py -k "non_slash_returns_all_tools or non_slash_mutating_intent_still_returns_all_tools" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add excelmanus/skillpacks/router.py docs/skillpack_protocol.md tests/test_skillpacks.py
git commit -m "docs: redefine all_tools as route intent not full tool authorization"
```

### Task 4: Decouple Context Memory From Authorization Scope (P1)

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: Write the failing tests**

```python
def test_recent_skill_context_does_not_expand_authorization_scope() -> None:
    engine = _make_engine()
    # simulate historical loaded skill context decay window
    engine._loaded_skill_names["format_basic"] = engine._session_turn
    route_result = SkillMatchResult(skills_used=[], tool_scope=[], route_mode="all_tools", system_contexts=[])
    scope = engine._get_current_tool_scope(route_result=route_result)
    assert "format_cells" not in scope
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine.py -k "recent_skill_context_does_not_expand_authorization_scope" -v`
Expected: FAIL if historical merge still leaks tools into scope path.

**Step 3: Write minimal implementation**

```python
# Keep _merge_with_loaded_skills for prompt context only.
# Authorization scope must come from active_skills + explicit route_result.tool_scope,
# never from historical context decay entries.
```

Adjust `_merge_with_loaded_skills()` and `_get_current_tool_scope()` boundary to enforce this rule.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_engine.py -k "recent_skill_context_does_not_expand_authorization_scope or get_current_tool_scope" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "refactor: separate context carry-over from tool authorization scope"
```

### Task 5: Write Guard Messaging Must Match Activation Mode (P1)

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_write_guard.py`

**Step 1: Write the failing tests**

```python
async def test_write_guard_prompt_uses_auto_supplement_wording_when_enabled():
    ...
    assert "自动激活" in guard_message
    assert "先调用 select_skill" not in guard_message


async def test_write_guard_prompt_uses_select_skill_wording_when_disabled():
    ...
    assert "先调用 select_skill" in guard_message
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_write_guard.py -k "write_guard_prompt_uses" -v`
Expected: FAIL because current text is hardcoded to `select_skill`.

**Step 3: Write minimal implementation**

```python
# excelmanus/engine.py

def _build_write_guard_message(self) -> str:
    if self._config.auto_supplement_enabled:
        return "你尚未调用任何写入工具完成实际操作。请直接调用对应写入/格式化/图表工具执行..."
    return "你尚未调用任何写入工具完成实际操作。请先调用 select_skill 激活可写技能..."
```

Use this function in `_tool_calling_loop()` write-guard branch.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_write_guard.py -k "write_guard_prompt_uses" -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_write_guard.py
git commit -m "fix: align write guard prompts with auto-supplement mode"
```

### Task 6: Publish a Single State-Machine Doc for Skill/Tool Flow (P2)

**Files:**
- Create: `docs/analysis/2026-02-18-skill-tool-state-machine.md`
- Modify: `docs/analysis/2026-02-18-full-chain-audit-report.md`

**Step 1: Draft the state machine**

Include nodes and transitions:
- `route_result` (intent)
- `pre_route_result` (candidate skills)
- `active_skills` (activated)
- `effective_tool_scope` (authorization)
- `auto_supplement` (runtime expansion)

**Step 2: Add “legacy term mapping” table**

Map old terms to new semantics:
- `all_tools` (legacy name) → “default route, engine-resolved scope”
- `tool_scope=[]` → “defer scope computation”

**Step 3: Link from full-chain audit**

Add a short “Source of Truth” section linking the new doc.

**Step 4: Validate markdown and links**

Run: `rg -n "skill-tool-state-machine|all_tools" docs/analysis -S`
Expected: new doc exists and is referenced.

**Step 5: Commit**

```bash
git add docs/analysis/2026-02-18-skill-tool-state-machine.md docs/analysis/2026-02-18-full-chain-audit-report.md
git commit -m "docs: add unified state machine for skill routing and tool authorization"
```

---

Plan complete and saved to `docs/plans/2026-02-18-skill-tool-logic-deconfusion.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
