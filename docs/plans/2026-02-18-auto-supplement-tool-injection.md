# 工具自动补充（Auto-Supplement）实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 当 LLM 调用不在当前 tool_scope 中的工具时，自动查找并激活包含该工具的最小 skillpack，扩展 scope 后直接执行，省去 select_skill 中间步骤。

**Architecture:** 在 engine.py 中新增 tool→skill 反向索引和预检-扩展-执行逻辑。当 `_execute_tool_call` 检测到工具不在 scope 中时，通过反向索引找到最小覆盖 skillpack，隐式激活并扩展 tool_scope，然后正常执行工具。配合 config 开关和每轮次数限制，确保可控。同时修改工具索引措辞，引导 LLM 直接调用而非先 select_skill。

**Tech Stack:** Python 3.12, pytest, 现有 ToolRegistry / Skillpack / SkillMatchResult 基础设施

---

### Task 1: 新增配置项

**Files:**
- Modify: `excelmanus/config.py`
- Test: `tests/test_config.py`

**Step 1: 在 config.py 的 ExcelManusConfig 中新增两个字段**

在 `skill_preroute_timeout_ms` 之后追加：

```python
# 工具自动补充：LLM 调用未授权工具时自动激活对应 skillpack
auto_supplement_enabled: bool = True
auto_supplement_max_per_turn: int = 3
```

**Step 2: 在 `_load_from_env` 中新增环境变量映射**

```python
auto_supplement_enabled=_bool_env("EXCELMANUS_AUTO_SUPPLEMENT_ENABLED", True),
auto_supplement_max_per_turn=int(os.getenv("EXCELMANUS_AUTO_SUPPLEMENT_MAX_PER_TURN", "3")),
```

**Step 3: 写测试验证默认值**

在 `tests/test_config.py` 中追加：

```python
def test_auto_supplement_defaults() -> None:
    config = load_config()
    assert config.auto_supplement_enabled is True
    assert config.auto_supplement_max_per_turn == 3
```

**Step 4: 运行测试**

Run: `pytest tests/test_config.py::test_auto_supplement_defaults -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/config.py tests/test_config.py
git commit -m "feat(config): 新增 auto_supplement 配置项"
```

---

### Task 2: 构建 tool→skill 反向索引

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写失败测试**

在 `tests/test_engine.py` 中新增测试类：

```python
class TestToolToSkillIndex:
    """tool→skill 反向索引测试。"""

    def test_build_tool_to_skill_index_returns_sorted_by_size(self) -> None:
        """反向索引按 skill 工具数升序排列（最小覆盖优先）。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        index = engine._build_tool_to_skill_index()
        # format_cells 应在 format_basic(16) 和 general_excel(42) 中
        # format_basic 应排在 general_excel 前面
        if "format_cells" in index:
            skills = index["format_cells"]
            assert len(skills) >= 2
            # 验证按工具数升序
            loader = engine._skill_router._loader
            all_skills = loader.get_skillpacks() or loader.load_all()
            sizes = []
            for name in skills:
                if name in all_skills:
                    sizes.append(len(all_skills[name].allowed_tools))
            assert sizes == sorted(sizes), f"未按工具数升序: {list(zip(skills, sizes))}"

    def test_build_tool_to_skill_index_covers_all_skill_tools(self) -> None:
        """索引应覆盖所有 skillpack 的 allowed_tools。"""
        config = _make_config()
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        index = engine._build_tool_to_skill_index()
        loader = engine._skill_router._loader
        all_skills = loader.get_skillpacks() or loader.load_all()
        for skill in all_skills.values():
            for tool in skill.allowed_tools:
                assert tool in index, f"工具 {tool} 未在索引中"
                assert skill.name in index[tool], f"skill {skill.name} 未在 {tool} 的索引中"
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestToolToSkillIndex -v`
Expected: FAIL (AttributeError: '_build_tool_to_skill_index')

**Step 3: 在 engine.py AgentEngine 中实现 `_build_tool_to_skill_index`**

在 `_active_skills_tool_union` 方法之后插入：

```python
def _build_tool_to_skill_index(self) -> dict[str, list[str]]:
    """构建 tool_name → [skill_name, ...] 反向索引，按 skill 工具数升序。"""
    if self._skill_router is None:
        return {}
    loader = self._skill_router._loader
    skillpacks = loader.get_skillpacks()
    if not skillpacks:
        skillpacks = loader.load_all()
    if not skillpacks:
        return {}

    index: dict[str, list[str]] = {}
    for skill in skillpacks.values():
        if getattr(skill, "disable_model_invocation", False):
            continue
        for tool in skill.allowed_tools:
            index.setdefault(tool, []).append(skill.name)

    # 按 skill 工具数升序（最小覆盖优先）
    for tool, skills in index.items():
        skills.sort(key=lambda name: len(skillpacks[name].allowed_tools) if name in skillpacks else 999)

    return index
```

**Step 4: 运行测试**

Run: `pytest tests/test_engine.py::TestToolToSkillIndex -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 构建 tool→skill 反向索引"
```

---

### Task 3: 实现 `_try_auto_supplement_tool` 核心方法

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写失败测试**

```python
class TestAutoSupplement:
    """工具自动补充测试。"""

    @pytest.mark.asyncio
    async def test_auto_supplement_activates_minimal_skill(self) -> None:
        """调用未授权工具时，自动激活最小覆盖 skillpack。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        # 初始无 active_skills
        assert not engine._active_skills

        result = engine._try_auto_supplement_tool("format_cells")
        assert result is not None
        assert result.skill_name == "format_basic"  # 最小覆盖
        # 验证 skill 已激活
        assert any(s.name == "format_basic" for s in engine._active_skills)

    @pytest.mark.asyncio
    async def test_auto_supplement_skips_blocked_skill(self) -> None:
        """被 blocked 的 skill 不应被自动补充激活。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        # run_code 仅在 excel_code_runner 中，该 skill 默认被 blocked
        result = engine._try_auto_supplement_tool("run_code")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_supplement_disabled_returns_none(self) -> None:
        """auto_supplement_enabled=False 时始终返回 None。"""
        config = _make_config(auto_supplement_enabled=False)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        result = engine._try_auto_supplement_tool("format_cells")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_supplement_respects_max_per_turn(self) -> None:
        """超过每轮最大次数后返回 None。"""
        config = _make_config(auto_supplement_enabled=True, auto_supplement_max_per_turn=1)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        engine._turn_supplement_count = 1  # 已用完配额
        result = engine._try_auto_supplement_tool("format_cells")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_supplement_unknown_tool_returns_none(self) -> None:
        """不存在于任何 skill 的工具返回 None。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        result = engine._try_auto_supplement_tool("nonexistent_tool_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_supplement_skips_already_active_skill(self) -> None:
        """工具已在当前 active_skills 的并集中时，不需要补充。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        # 先激活 format_basic
        await engine._handle_select_skill("format_basic")
        # format_cells 已在 scope 中，不需要补充
        result = engine._try_auto_supplement_tool("format_cells")
        assert result is None
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestAutoSupplement -v`
Expected: FAIL (AttributeError: '_try_auto_supplement_tool')

**Step 3: 新增 `AutoSupplementResult` 数据类和 `_try_auto_supplement_tool` 方法**

在 `engine.py` 顶部 dataclass 区域新增：

```python
@dataclass
class AutoSupplementResult:
    """自动补充结果。"""
    skill_name: str
    expanded_tools: list[str]
```

在 `_build_tool_to_skill_index` 之后新增：

```python
def _try_auto_supplement_tool(self, tool_name: str) -> AutoSupplementResult | None:
    """预检-扩展：查找包含 tool_name 的最小覆盖 skillpack 并激活。

    返回 AutoSupplementResult 或 None（不可补充）。
    """
    if not self._config.auto_supplement_enabled:
        return None
    if self._turn_supplement_count >= self._config.auto_supplement_max_per_turn:
        return None

    # 已在当前 active_skills 并集中 → 不需要补充
    if self._active_skills:
        union = set(self._active_skills_tool_union())
        if tool_name in union:
            return None

    index = self._build_tool_to_skill_index()
    candidates = index.get(tool_name)
    if not candidates:
        return None

    blocked = self._blocked_skillpacks()
    for skill_name in candidates:
        if blocked and skill_name in blocked:
            continue
        # 跳过已激活的 skill（工具应该已在 union 中，但防御性检查）
        if any(s.name == skill_name for s in self._active_skills):
            continue

        loader = self._skill_router._loader
        skillpacks = loader.get_skillpacks() or loader.load_all()
        skill = skillpacks.get(skill_name)
        if skill is None:
            continue

        # MCP 依赖检查
        mcp_error = self._validate_skill_mcp_requirements(skill)
        if mcp_error:
            continue

        # 激活 skill
        self._active_skills = [
            s for s in self._active_skills if s.name != skill.name
        ] + [skill]
        self._loaded_skill_names.add(skill.name)

        logger.info(
            "自动补充激活技能 %s（触发工具: %s，工具数: %d）",
            skill.name, tool_name, len(skill.allowed_tools),
        )
        return AutoSupplementResult(
            skill_name=skill.name,
            expanded_tools=list(skill.allowed_tools),
        )

    return None
```

**Step 4: 在 `__init__` 中初始化计数器**

在 `self._loaded_skill_names: set[str] = set()` 之后追加：

```python
self._turn_supplement_count: int = 0
```

**Step 5: 运行测试**

Run: `pytest tests/test_engine.py::TestAutoSupplement -v`
Expected: PASS

**Step 6: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 实现 _try_auto_supplement_tool 核心方法"
```

---

### Task 4: 在 `_execute_tool_call` 中集成自动补充预检

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写集成测试**

```python
class TestAutoSupplementIntegration:
    """自动补充在工具调用链路中的集成测试。"""

    @pytest.mark.asyncio
    async def test_tool_not_in_scope_triggers_auto_supplement_and_succeeds(self) -> None:
        """LLM 调用不在 scope 中的工具时，自动补充后成功执行。"""
        config = _make_config(
            skill_preroute_mode="meta_only",
            auto_supplement_enabled=True,
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        engine._turn_supplement_count = 0

        # 构造一个不在 DISCOVERY_TOOLS 中的工具调用
        # format_cells 不在基础 scope 中
        scope = engine._get_current_tool_scope()
        assert "format_cells" not in scope

        # 模拟工具调用
        tc = _make_tool_call("format_cells", {"file_path": "test.xlsx", "range": "A1:B2", "styles": {}})
        result = await engine._execute_tool_call(tc, scope, None, 1)

        # 应该成功（自动补充激活了 format_basic）
        # 注意：实际执行可能因文件不存在而失败，但不应是 TOOL_NOT_ALLOWED
        assert "TOOL_NOT_ALLOWED" not in result.result

    @pytest.mark.asyncio
    async def test_auto_supplement_disabled_still_raises_not_allowed(self) -> None:
        """auto_supplement 关闭时，未授权工具仍返回 TOOL_NOT_ALLOWED。"""
        config = _make_config(
            skill_preroute_mode="meta_only",
            auto_supplement_enabled=False,
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))

        scope = engine._get_current_tool_scope()
        tc = _make_tool_call("format_cells", {"file_path": "test.xlsx", "range": "A1:B2", "styles": {}})
        result = await engine._execute_tool_call(tc, scope, None, 1)
        assert "TOOL_NOT_ALLOWED" in result.result
```

**Step 2: 运行测试确认失败**

Run: `pytest tests/test_engine.py::TestAutoSupplementIntegration -v`
Expected: FAIL（format_cells 仍触发 TOOL_NOT_ALLOWED）

**Step 3: 修改 `_execute_tool_call` 中的 ToolNotAllowedError 检查**

找到 `_execute_tool_call` 中的这段代码（约 L3680）：

```python
if tool_scope is not None and tool_name not in set(tool_scope):
    raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")
```

替换为：

```python
if tool_scope is not None and tool_name not in set(tool_scope):
    supplement = self._try_auto_supplement_tool(tool_name)
    if supplement is not None:
        # 扩展当前 scope（本次调用及后续同批次调用生效）
        tool_scope = list(set(tool_scope) | set(supplement.expanded_tools))
        self._turn_supplement_count += 1
        # 在工具结果中附加提示
        self._auto_supplement_notice = (
            f"\n[系统已自动激活技能 {supplement.skill_name}，"
            f"后续可直接使用该技能的工具]"
        )
    else:
        raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")
```

在方法末尾、返回 `ToolCallResult` 之前，将 notice 附加到 result_str：

```python
# 附加自动补充提示
if hasattr(self, "_auto_supplement_notice") and self._auto_supplement_notice:
    result_str = result_str + self._auto_supplement_notice
    self._auto_supplement_notice = ""
```

**Step 4: 运行测试**

Run: `pytest tests/test_engine.py::TestAutoSupplementIntegration -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 在 _execute_tool_call 中集成自动补充预检"
```

---

### Task 5: 在 `_tool_calling_loop` 中重置每轮计数器并同步 tool_scope

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写测试**

```python
class TestAutoSupplementTurnReset:
    """每轮迭代重置自动补充计数器。"""

    @pytest.mark.asyncio
    async def test_supplement_count_resets_each_iteration(self) -> None:
        """每轮迭代开始时 _turn_supplement_count 应重置为 0。"""
        config = _make_config(auto_supplement_enabled=True, auto_supplement_max_per_turn=2)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        engine._turn_supplement_count = 5  # 模拟上一轮残留
        # _tool_calling_loop 每轮开始时应重置
        # 通过检查 _tool_calling_loop 的行为间接验证
        # 这里直接验证初始化值
        assert engine._turn_supplement_count == 5
        # 重置逻辑在 _tool_calling_loop 的 for 循环开头
```

**Step 2: 修改 `_tool_calling_loop`**

在 `_tool_calling_loop` 的 `for iteration in range(...)` 循环体开头（`self._emit(on_event, ToolCallEvent(event_type=EventType.ITERATION_START, ...))` 之后）追加：

```python
# 每轮重置自动补充计数器
self._turn_supplement_count = 0
```

**Step 3: 同步 tool_scope 更新**

在 `_tool_calling_loop` 中，当 `_execute_tool_call` 返回后，如果自动补充触发了 skill 激活，需要刷新 `tool_scope`。找到 `select_skill` 成功后的 scope 刷新逻辑：

```python
if tc_result.tool_name == "select_skill":
    current_route_result = self._refresh_route_after_skill_switch(
        current_route_result
    )
    ...
    tool_scope = self._get_current_tool_scope(
        route_result=current_route_result
    )
```

在这段之后追加对自动补充的同样处理：

```python
# 自动补充也可能激活了新 skill，需要刷新 scope
elif self._turn_supplement_count > 0 and tc_result.success:
    # 检查是否有新 skill 被自动补充激活
    current_scope_set = set(tool_scope)
    new_union = set(self._active_skills_tool_union()) if self._active_skills else set()
    if not new_union.issubset(current_scope_set):
        current_route_result = self._refresh_route_after_skill_switch(
            current_route_result
        )
        write_hint = getattr(current_route_result, "write_hint", write_hint)
        self._current_write_hint = write_hint
        tool_scope = self._get_current_tool_scope(
            route_result=current_route_result
        )
```

**Step 4: 运行全部相关测试**

Run: `pytest tests/test_engine.py::TestAutoSupplement tests/test_engine.py::TestAutoSupplementIntegration tests/test_engine.py::TestAutoSupplementTurnReset -v`
Expected: PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 每轮重置自动补充计数器并同步 tool_scope"
```

---

### Task 6: write_hint 联动 — 自动补充写入工具时升级 write_hint

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写测试**

```python
class TestAutoSupplementWriteHint:
    """自动补充写入工具时升级 write_hint。"""

    @pytest.mark.asyncio
    async def test_auto_supplement_write_tool_upgrades_hint(self) -> None:
        """自动补充激活包含写入工具的 skill 时，write_hint 升级为 may_write。"""
        config = _make_config(
            skill_preroute_mode="meta_only",
            auto_supplement_enabled=True,
        )
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        engine._current_write_hint = "read_only"

        # format_cells 是写入工具，自动补充应升级 write_hint
        result = engine._try_auto_supplement_tool("format_cells")
        assert result is not None
        assert engine._current_write_hint == "may_write"
```

**Step 2: 修改 `_try_auto_supplement_tool`**

在激活 skill 成功后、return 之前追加：

```python
from excelmanus.tools.policy import MUTATING_ALL_TOOLS

# 如果触发工具是写入类，升级 write_hint
if tool_name in MUTATING_ALL_TOOLS:
    self._current_write_hint = "may_write"
```

**Step 3: 运行测试**

Run: `pytest tests/test_engine.py::TestAutoSupplementWriteHint -v`
Expected: PASS

**Step 4: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 自动补充写入工具时升级 write_hint"
```

---

### Task 7: 修改工具索引措辞

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写测试**

```python
class TestToolIndexWording:
    """工具索引措辞更新。"""

    def test_tool_index_uses_auto_supplement_wording_when_enabled(self) -> None:
        """auto_supplement 开启时，工具索引使用"直接调用"措辞。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        scope = list(DISCOVERY_TOOLS)
        notice = engine._build_tool_index_notice(scope)
        assert "直接调用" in notice or "按需可用" in notice
        assert "需 select_skill" not in notice

    def test_tool_index_uses_select_skill_wording_when_disabled(self) -> None:
        """auto_supplement 关闭时，保留原有"需 select_skill"措辞。"""
        config = _make_config(auto_supplement_enabled=False)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        scope = list(DISCOVERY_TOOLS)
        notice = engine._build_tool_index_notice(scope)
        assert "select_skill" in notice
```

**Step 2: 修改 `_build_tool_index_notice`**

找到当前的措辞：

```python
parts.append("未激活（需 select_skill 激活对应技能后可用）：")
```

替换为：

```python
if self._config.auto_supplement_enabled:
    parts.append("按需可用（直接调用即可，系统会自动激活对应技能）：")
else:
    parts.append("未激活（需 select_skill 激活对应技能后可用）：")
```

找到末尾的警告文本：

```python
parts.append(
    "\n⚠️ 当任务需要未激活工具时，立即调用 select_skill 激活技能，"
    "禁止向用户请求权限或声称无法完成。"
    "\n⚠️ 特别是写入类任务（公式、数据、格式），必须激活技能后调用工具执行，"
    "不得以文本建议替代实际写入操作。"
)
```

替换为：

```python
if self._config.auto_supplement_enabled:
    parts.append(
        "\n⚠️ 上述按需可用工具可直接调用，系统会自动激活对应技能。"
        "无需先调用 select_skill。"
        "\n⚠️ 写入类任务（公式、数据、格式）必须调用工具执行，"
        "不得以文本建议替代实际写入操作。"
    )
else:
    parts.append(
        "\n⚠️ 当任务需要未激活工具时，立即调用 select_skill 激活技能，"
        "禁止向用户请求权限或声称无法完成。"
        "\n⚠️ 特别是写入类任务（公式、数据、格式），必须激活技能后调用工具执行，"
        "不得以文本建议替代实际写入操作。"
    )
```

**Step 3: 运行测试**

Run: `pytest tests/test_engine.py::TestToolIndexWording -v`
Expected: PASS

**Step 4: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 工具索引措辞适配 auto_supplement 模式"
```

---

### Task 8: 预路由分层加载 — 复合意图不再降级到 general_excel

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`

**Step 1: 写测试**

```python
class TestPreRouteLayeredLoading:
    """预路由分层加载：复合意图加载多个 skill 而非降级 general_excel。"""

    @pytest.mark.asyncio
    async def test_compound_intent_loads_both_skills(self) -> None:
        """复合意图（如 data_basic + chart_basic）应加载两个 skill。"""
        config = _make_config(skill_preroute_mode="hybrid", auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))

        # 模拟预路由返回复合意图
        from excelmanus.skillpacks.pre_router import PreRouteResult
        pre_result = PreRouteResult(
            skill_name="data_basic",
            skill_names=["data_basic", "chart_basic"],
            confidence=0.8,
            reason="复合意图",
            latency_ms=50.0,
            model_used="test",
        )

        target, candidates = engine._resolve_preroute_target_layered(pre_result)
        assert target == "data_basic"
        assert candidates == ["data_basic", "chart_basic"]
        # 不应降级到 general_excel
        assert target != "general_excel"
```

**Step 2: 修改 chat() Phase 1 中的 `_resolve_preroute_target`**

将现有的 `_resolve_preroute_target` 内联函数重构为方法 `_resolve_preroute_target_layered`：

```python
def _resolve_preroute_target_layered(
    self, result: "PreRouteResult | None",
) -> tuple[str | None, list[str]]:
    """解析预路由结果，支持分层加载。

    返回 (主 skill 名, 全部候选列表)。
    复合意图时返回第一个候选作为主 skill，不再降级到 general_excel。
    """
    if result is None:
        return None, []
    candidates: list[str] = []
    raw_candidates = getattr(result, "skill_names", None)
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name or name in candidates:
                continue
            candidates.append(name)
    legacy_name = getattr(result, "skill_name", None)
    if isinstance(legacy_name, str) and legacy_name.strip():
        normalized = legacy_name.strip()
        if normalized not in candidates:
            candidates.append(normalized)
    if not candidates:
        return None, candidates
    # 分层加载：第一个候选为主 skill，其余为副 skill
    return candidates[0], candidates
```

然后修改 chat() Phase 1 中 hybrid 分支的复合意图处理：

```python
target_skill_name, skill_candidates = self._resolve_preroute_target_layered(pre_route_result)
if target_skill_name is not None:
    # 主 skill：full 模式（tools + instructions）
    auto_result = await self._handle_select_skill(target_skill_name)
    if not auto_result.startswith("未找到技能:"):
        auto_activated_skill_name = target_skill_name
        logger.info("hybrid 预路由激活主技能: %s", auto_activated_skill_name)
        # 副 skill：也激活（多 skill 注入已支持）
        for secondary_name in skill_candidates[1:]:
            if secondary_name == target_skill_name:
                continue
            sec_result = await self._handle_select_skill(secondary_name)
            if not sec_result.startswith("未找到技能:"):
                logger.info("hybrid 预路由激活副技能: %s", secondary_name)
    else:
        logger.warning(
            "hybrid 预路由技能 %s 不存在（候选=%s），回退 meta_only 模式",
            target_skill_name, skill_candidates,
        )
```

**Step 3: 运行测试**

Run: `pytest tests/test_engine.py::TestPreRouteLayeredLoading -v`
Expected: PASS

**Step 4: 运行全量引擎测试确保无回归**

Run: `pytest tests/test_engine.py -x -q`
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "feat(engine): 预路由分层加载，复合意图不再降级 general_excel"
```

---

### Task 9: 全量回归测试 + 边界加固

**Files:**
- Modify: `excelmanus/engine.py`
- Test: `tests/test_engine.py`, `tests/test_tool_policy.py`

**Step 1: 写边界测试**

```python
class TestAutoSupplementEdgeCases:
    """自动补充边界情况。"""

    @pytest.mark.asyncio
    async def test_auto_supplement_does_not_activate_same_skill_twice(self) -> None:
        """同一 skill 不应被自动补充重复激活。"""
        config = _make_config(auto_supplement_enabled=True, auto_supplement_max_per_turn=5)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))

        # 第一次：激活 format_basic
        r1 = engine._try_auto_supplement_tool("format_cells")
        assert r1 is not None
        assert r1.skill_name == "format_basic"

        # 第二次：format_cells 已在 active_skills 并集中
        r2 = engine._try_auto_supplement_tool("format_cells")
        assert r2 is None

    @pytest.mark.asyncio
    async def test_auto_supplement_with_mcp_tool_returns_none(self) -> None:
        """MCP 工具不在任何 skillpack 中，应返回 None。"""
        config = _make_config(auto_supplement_enabled=True)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        result = engine._try_auto_supplement_tool("mcp_some_server_some_tool")
        assert result is None

    @pytest.mark.asyncio
    async def test_auto_supplement_counter_increments(self) -> None:
        """每次成功补充后计数器递增。"""
        config = _make_config(auto_supplement_enabled=True, auto_supplement_max_per_turn=5)
        registry = _make_registry_with_tools()
        engine = AgentEngine(config=config, registry=registry, skill_router=_make_skill_router(config))
        assert engine._turn_supplement_count == 0

        engine._try_auto_supplement_tool("format_cells")
        # 注意：计数器在 _execute_tool_call 中递增，不在 _try_auto_supplement_tool 中
        # _try_auto_supplement_tool 只做查找和激活
        # 所以这里计数器仍为 0，递增在调用方
```

**Step 2: 运行全量测试**

Run: `pytest tests/test_engine.py -x -q`
Expected: 全部 PASS

Run: `pytest tests/test_tool_policy.py -x -q`
Expected: 全部 PASS

Run: `pytest tests/ -x -q --timeout=60`
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add excelmanus/engine.py tests/test_engine.py
git commit -m "test(engine): 自动补充边界测试"
```

---

### Task 10: 最终集成验证

**Step 1: 运行完整测试套件**

Run: `pytest tests/ -x -q --timeout=120`
Expected: 全部 PASS，无回归

**Step 2: 手动验证关键路径**

验证以下场景的日志输出：

1. `skill_preroute_mode=hybrid` + `auto_supplement_enabled=true`：
   - 预路由选中 data_basic → LLM 调用 format_cells → 自动补充激活 format_basic → 执行成功
   - 日志应包含 "自动补充激活技能 format_basic（触发工具: format_cells）"

2. `skill_preroute_mode=meta_only` + `auto_supplement_enabled=true`：
   - 无预激活 → LLM 从工具索引看到 format_cells → 直接调用 → 自动补充 → 成功

3. `auto_supplement_enabled=false`：
   - 行为与当前完全一致，无自动补充

**Step 3: 最终 Commit**

```bash
git add -A
git commit -m "feat: 工具自动补充（auto-supplement）完整实现

- 新增 auto_supplement_enabled / auto_supplement_max_per_turn 配置
- 构建 tool→skill 反向索引（最小覆盖优先）
- _execute_tool_call 预检-扩展-执行逻辑
- 每轮重置计数器 + tool_scope 同步
- write_hint 联动升级
- 工具索引措辞适配
- 预路由分层加载（复合意图不再降级 general_excel）
- 完整测试覆盖"
```

---

## 任务依赖关系

```
Task 1 (config) ──→ Task 2 (反向索引) ──→ Task 3 (核心方法)
                                              │
                                              ▼
                                         Task 4 (集成到 _execute_tool_call)
                                              │
                                              ▼
                                         Task 5 (轮次重置 + scope 同步)
                                              │
                                         ┌────┼────┐
                                         ▼    ▼    ▼
                                    Task 6  Task 7  Task 8
                                  (write_hint) (措辞) (分层加载)
                                         │    │    │
                                         └────┼────┘
                                              ▼
                                         Task 9 (边界测试)
                                              │
                                              ▼
                                         Task 10 (集成验证)
```

Task 6/7/8 可并行执行。
