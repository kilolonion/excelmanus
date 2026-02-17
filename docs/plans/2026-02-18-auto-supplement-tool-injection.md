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
