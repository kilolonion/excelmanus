# Bench 执行效率优化 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 通过三阶段优化（默认技能预激活、Prompt 强化、统一执行守卫），减少 bench 测试中的冗余迭代和 token 消耗，提升任务执行深度。

**Architecture:** Phase 1 在 engine.py 路由完成后自动激活 general_excel 技能，跳过 LLM 调用 select_skill 的开销。Phase 2 在 memory.py 中强化 prompt 约束。Phase 3 在 engine.py 迭代循环中增加两个执行守卫。

**Tech Stack:** Python 3.12, pytest, excelmanus engine/memory/config 模块

---

### Task 1: Phase 1 — 配置项 `auto_activate_default_skill`

**Files:**
- Modify: `excelmanus/config.py:39-117` (ExcelManusConfig dataclass)
- Modify: `excelmanus/config.py:365-762` (load_config function)
- Test: `tests/test_config.py`

**Step 1: Write the failing test**

在 `tests/test_config.py` 中添加：

```python
def test_auto_activate_default_skill_default():
    """auto_activate_default_skill 默认值为 True。"""
    config = load_config()
    assert config.auto_activate_default_skill is True


def test_auto_activate_default_skill_env_false(monkeypatch):
    """环境变量可关闭 auto_activate_default_skill。"""
    monkeypatch.setenv("EXCELMANUS_AUTO_ACTIVATE_DEFAULT_SKILL", "0")
    config = load_config()
    assert config.auto_activate_default_skill is False
```

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_config.py -k "auto_activate" -v`
Expected: FAIL — `ExcelManusConfig` 没有 `auto_activate_default_skill` 属性

**Step 3: Implement config field and env parsing**

在 `excelmanus/config.py` 的 `ExcelManusConfig` dataclass 中（`models` 字段前）添加：

```python
    # 默认技能预激活：非斜杠路由时自动激活 general_excel
    auto_activate_default_skill: bool = True
```

在 `load_config()` 函数中，`window_rule_engine_version` 解析之后、`models` 解析之前添加：

```python
    auto_activate_default_skill = _parse_bool(
        os.environ.get("EXCELMANUS_AUTO_ACTIVATE_DEFAULT_SKILL"),
        "EXCELMANUS_AUTO_ACTIVATE_DEFAULT_SKILL",
        True,
    )
```

在 `return ExcelManusConfig(...)` 中添加 `auto_activate_default_skill=auto_activate_default_skill,`。

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_config.py -k "auto_activate" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/config.py tests/test_config.py
git commit -m "feat(config): add auto_activate_default_skill option"
```

---

### Task 2: Phase 1 — 引擎自动预激活逻辑

**Files:**
- Modify: `excelmanus/engine.py:857-878` (chat 方法中路由完成后)
- Test: `tests/test_engine.py`

**Step 1: Write the failing test**

在 `tests/test_engine.py` 中添加测试类：

```python
class TestAutoActivateDefaultSkill:
    """Phase 1: 非斜杠路由时自动预激活 general_excel。"""

    @pytest.mark.asyncio
    async def test_auto_activate_sets_active_skill(self, engine_with_skills):
        """all_tools 路由完成后，_active_skill 应被自动设置。"""
        # engine_with_skills 是已加载 general_excel skillpack 的引擎实例
        engine = engine_with_skills
        assert engine._active_skill is None  # 初始无激活技能

        # 触发 chat（使用 mock LLM 返回纯文本）
        result = await engine.chat("读取文件 test.xlsx 的内容")

        # 预激活后 _active_skill 应非 None
        assert engine._active_skill is not None
        assert engine._active_skill.name == "general_excel"

    @pytest.mark.asyncio
    async def test_auto_activate_disabled_by_config(self, engine_no_auto_activate):
        """配置关闭时不自动激活。"""
        engine = engine_no_auto_activate
        result = await engine.chat("读取文件 test.xlsx 的内容")
        # 如果 LLM 没有调用 select_skill，active_skill 应为 None
        # （取决于 mock LLM 行为，这里验证逻辑路径不触发预激活）

    @pytest.mark.asyncio
    async def test_slash_command_skips_auto_activate(self, engine_with_skills):
        """斜杠命令路由不触发自动预激活。"""
        engine = engine_with_skills
        result = await engine.chat("/general_excel 写入数据",
                                    slash_command="general_excel")
        # 斜杠路由走自己的路径，不应触发额外预激活
```

> 注意：具体 fixture 需要根据现有 test_engine.py 的 fixture 模式适配。看现有测试是如何创建引擎实例的，然后照搬模式。

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_engine.py -k "TestAutoActivateDefaultSkill" -v`
Expected: FAIL

**Step 3: Implement auto-activation in engine.py**

在 `excelmanus/engine.py` 的 `chat()` 方法中，找到路由完成后、进入 hook/用户消息处理前的位置（约第 868-878 行之间），插入自动预激活逻辑：

```python
        # ── Phase 1: 默认技能预激活 ──
        # 非斜杠的 all_tools 路由且无已激活技能时，自动激活 general_excel
        if (
            self._config.auto_activate_default_skill
            and route_result.route_mode == "all_tools"
            and self._active_skill is None
            and effective_slash_command is None
            and self._skill_router is not None
        ):
            auto_result = await self._handle_select_skill("general_excel")
            if not auto_result.startswith("未找到技能:"):
                logger.info("自动预激活技能: general_excel")
            else:
                logger.debug("自动预激活 general_excel 失败（技能不存在），继续使用全量工具")
```

插入位置：在 `route_result = self._merge_with_loaded_skills(route_result)` 之后、`effective_tool_scope = self._get_current_tool_scope(...)` 之前。

这样 `_get_current_tool_scope` 会自动包含已激活技能的工具。

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_engine.py -k "TestAutoActivateDefaultSkill" -v`
Expected: PASS

**Step 5: Run full test suite to check regression**

Run: `uv run --extra dev pytest tests/test_engine.py -q`
Expected: 全部通过，无回归

**Step 6: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): auto-activate general_excel for all_tools route"
```

---

### Task 3: Phase 2 — Prompt 约束强化

**Files:**
- Modify: `excelmanus/memory.py:20-74` (_SEGMENT_TONE_STYLE 和 _SEGMENT_TOOL_POLICY)
- Test: `tests/test_memory.py` 或 `tests/test_system_prompt.py`

**Step 1: Write the failing test**

```python
def test_system_prompt_contains_first_round_action_constraint():
    """系统提示词必须包含首轮必须行动的约束。"""
    from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT
    assert "第一轮响应必须包含至少一个工具调用" in _DEFAULT_SYSTEM_PROMPT


def test_system_prompt_contains_no_text_transition():
    """系统提示词必须禁止纯文本过渡轮。"""
    from excelmanus.memory import _DEFAULT_SYSTEM_PROMPT
    assert "中间不得有纯文本过渡轮" in _DEFAULT_SYSTEM_PROMPT
```

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_memory.py -k "first_round_action or no_text_transition" -v`
Expected: FAIL

**Step 3: Modify prompt segments**

在 `excelmanus/memory.py` 的 `_SEGMENT_TONE_STYLE`（第 21-36 行）中，在 `"- 不给出时间估算，聚焦于做什么。"` 之前添加两行：

```python
    "- **首轮必须行动**：收到任务后，第一轮响应必须包含至少一个工具调用。"
    "纯文本解释、方案说明不算有效响应，必须同时带上工具调用。\n"
    "- **禁止纯文本过渡**：不得先用一轮纯文本解释方案再执行。"
    "解释和执行必须在同一轮完成。\n"
```

在 `_SEGMENT_TOOL_POLICY`（第 50-73 行）中，在 `"禁止跳过文件操作直接给出文本建议。"` 之后添加：

```python
    "\n- **操作动词即执行**：用户消息包含操作动词"
    "（删除/替换/写入/创建/修改/格式化/转置/排序/过滤/合并/计算）"
    "加上文件引用时，必须读取并操作该文件直至完成，不得仅给出说明后结束。\n"
    "- **每轮要么行动要么完结**：每轮响应要么包含工具调用推进任务，"
    "要么是最终完成总结。中间不得有纯文本过渡轮。"
```

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_memory.py -k "first_round_action or no_text_transition" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/memory.py tests/test_memory.py
git commit -m "feat(prompt): strengthen first-round-action and no-text-transition constraints"
```

---

### Task 4: Phase 3 — 写入意图检测辅助函数

**Files:**
- Modify: `excelmanus/engine.py` (模块级函数区域，约第 275 行附近)
- Test: `tests/test_engine.py`

**Step 1: Write the failing test**

```python
class TestDetectWriteIntent:
    """写入意图检测启发式。"""

    @pytest.mark.parametrize("msg,expected", [
        ("打开文件 test.xlsx 并删除第一行", True),
        ("替换 data.xlsx 中的 A 列数据", True),
        ("在 report.xlsx 里创建一个汇总表", True),
        ("格式化 output.xlsx 的标题行", True),
        ("转置 input.xlsx 中的数据", True),
        ("读取 test.xlsx 的前10行", False),  # 纯读取，无写入动词
        ("Excel 怎么用公式求和？", False),  # 无文件引用
        ("帮我看看这个文件结构", False),  # 无操作动词
        ("删除表格中多余的行", False),  # 无文件名/路径
    ])
    def test_detect_write_intent(self, msg, expected):
        from excelmanus.engine import _detect_write_intent
        assert _detect_write_intent(msg) is expected
```

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_engine.py::TestDetectWriteIntent -v`
Expected: FAIL — `_detect_write_intent` 不存在

**Step 3: Implement detection function**

在 `excelmanus/engine.py` 中 `_contains_formula_advice` 函数附近（约第 275 行后）添加：

```python
# 写入意图动词集合
_WRITE_ACTION_VERBS = _re.compile(
    r"(删除|替换|写入|创建|修改|格式化|转置|排序|过滤|合并|计算|填充|插入|移动|复制到|粘贴|更新|设置|调整|添加|生成)",
)

# 文件引用模式
_FILE_REFERENCE_PATTERN = _re.compile(
    r"(\.xlsx\b|\.xls\b|\.csv\b|文件\s*[「『"""\w/\\]|[A-Za-z0-9_\-/\\]+\.(xlsx|xls|csv))",
    _re.IGNORECASE,
)


def _detect_write_intent(text: str) -> bool:
    """检测用户消息是否同时包含文件引用和写入动作动词。"""
    if not text:
        return False
    has_file = bool(_FILE_REFERENCE_PATTERN.search(text))
    has_action = bool(_WRITE_ACTION_VERBS.search(text))
    return has_file and has_action
```

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_engine.py::TestDetectWriteIntent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): add _detect_write_intent helper for execution guard"
```

---

### Task 5: Phase 3 — 空响应守卫和读写不匹配守卫

**Files:**
- Modify: `excelmanus/engine.py:2750-2870` (迭代循环中的守卫逻辑)
- Test: `tests/test_engine.py`

**Step 1: Write the failing test**

```python
class TestUnifiedExecutionGuard:
    """Phase 3: 统一执行守卫。"""

    @pytest.mark.asyncio
    async def test_empty_response_guard_triggers_on_first_iteration(self, ...):
        """迭代 0 时 LLM 返回纯文本无工具调用，守卫应触发 nudge。"""
        # Mock LLM: 第 1 轮返回纯文本，第 2 轮返回工具调用
        # 验证最终结果包含工具调用（守卫使循环继续）

    @pytest.mark.asyncio
    async def test_read_write_mismatch_guard(self, ...):
        """LLM 只做了读取就结束，但用户意图是写入时，守卫应触发。"""
        # Mock LLM: 第 1 轮调用 list_sheets，第 2 轮返回纯文本
        # 用户消息: "删除 test.xlsx 中的空行"
        # 验证守卫触发，循环继续

    @pytest.mark.asyncio
    async def test_guard_does_not_trigger_for_pure_read(self, ...):
        """纯读取任务不应触发守卫。"""
        # 用户消息: "读取 test.xlsx 的前10行"
        # LLM 返回 list_sheets + read_excel + 纯文本总结
        # 验证正常结束，无守卫触发
```

> 注意：具体 fixture 和 mock 模式需要参照现有 test_engine.py 中的测试写法。

**Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_engine.py::TestUnifiedExecutionGuard -v`
Expected: FAIL

**Step 3: Implement guards in iteration loop**

在 `excelmanus/engine.py` 的迭代循环中（约第 2750 行 `_iteration_loop` 方法），修改两处：

1. 在执行守卫标记重置处（第 2754 行）增加新标记：

```python
        self._execution_guard_fired = False  # type: ignore[attr-defined]
        self._empty_response_guard_fired = False  # type: ignore[attr-defined]
        self._read_write_guard_fired = False  # type: ignore[attr-defined]
        # 跟踪是否有写入类工具调用
        has_write_tool_call = False
```

2. 在现有公式守卫之后（约第 2869 行 `continue` 之后），添加两个新守卫：

```python
                # ── 空响应守卫：首轮纯文本无工具调用 ──
                if (
                    iteration == 1
                    and not getattr(self, "_empty_response_guard_fired", False)
                    and _detect_write_intent(self._memory._messages[0].get("content", "")
                        if self._memory._messages else "")
                ):
                    self._empty_response_guard_fired = True  # type: ignore[attr-defined]
                    guard_msg = (
                        "⚠️ 你返回了纯文本但没有调用任何工具。"
                        "用户请求涉及文件操作，请立即调用工具执行。"
                    )
                    self._memory.add_user_message(guard_msg)
                    logger.info("空响应守卫触发：首轮无工具调用，注入继续执行提示")
                    continue

                # ── 读写不匹配守卫：只做了读取但任务需要写入 ──
                if (
                    not has_write_tool_call
                    and not getattr(self, "_read_write_guard_fired", False)
                    and _detect_write_intent(self._memory._messages[0].get("content", "")
                        if self._memory._messages else "")
                    and iteration < max_iter - 1
                ):
                    self._read_write_guard_fired = True  # type: ignore[attr-defined]
                    guard_msg = (
                        "⚠️ 你只执行了读取操作但没有完成写入。"
                        "用户请求需要修改文件，请继续调用写入工具完成任务。"
                    )
                    self._memory.add_user_message(guard_msg)
                    logger.info("读写不匹配守卫触发：仅读取未写入，注入继续执行提示")
                    continue
```

3. 在工具执行成功后（约第 2983 行 `if tc_result.success:` 分支），添加写入工具跟踪：

```python
                    if tc_result.tool_name in (
                        "write_cells", "write_excel", "format_cells",
                        "create_chart", "set_column_width", "merge_cells",
                        "write_text_file", "filter_data",
                    ):
                        has_write_tool_call = True
```

**Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_engine.py::TestUnifiedExecutionGuard -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run --extra dev pytest tests/ -q --tb=short`
Expected: 全部通过

**Step 6: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): add empty-response and read-write-mismatch execution guards"
```

---

### Task 6: Bench 验证

**Files:**
- No code changes, verification only

**Step 1: 重跑同一 bench suite**

```bash
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_bench_skill_20260216T004700.json \
    --output-dir outputs/bench_opt_all_phases_$(date +%Y%m%dT%H%M%S) \
    --concurrency 1 --trace
```

**Step 2: 使用 bench_inspect 分析结果**

```bash
# 对每个 run JSON 执行 overview
for f in outputs/bench_opt_all_phases_*/run_*.json; do
    .venv/bin/python scripts/bench_inspect.py overview "$f"
    echo "---"
done
```

**Step 3: 对比指标**

| 指标 | 优化前 | 优化后目标 |
|------|--------|-----------|
| 平均迭代数 | 3.8 | ≤ 2.5 |
| 平均 token | 30,558 | ≤ 24,000 |
| select_skill 调用数 | 3/5 | 0/5 |
| 执行深度（有写入操作） | 3/5 | 5/5 |
| 空承诺轮 | 1/5 | 0/5 |

**Step 4: Commit verification results**

```bash
git add docs/plans/2026-02-16-bench-optimization-three-phases.md
git commit -m "docs: add bench optimization verification results"
```
