# 子路由预选（Pre-Router）技术分析报告

## 1. 现有预路由机制分析

### 1.1 `skill_preroute_mode` 模式总览

当前配置项 `skill_preroute_mode` 支持 5 种模式：

| 模式 | 行为 | 首轮工具集 | 首轮 instructions |
|------|------|-----------|-------------------|
| `off` | 确定性激活 `general_excel` | general_excel 的 ~40 个工具 + 元工具 | general_excel SKILL.md 全文 |
| `meta_only` | 不预激活任何 skill | DISCOVERY_TOOLS (~13 个只读) + 元工具 | 无 |
| `deepseek` | 小模型预判 → 激活 1 个 skill；失败回退 general_excel | 预判 skill 的 allowed_tools + 元工具 | 预判 skill 的 SKILL.md 全文 |
| `gemini` | 同 deepseek，仅 API 协议不同 | 同上 | 同上 |
| `hybrid` | 小模型预判 → 激活 1 个 skill；失败回退 meta_only（不回退 general_excel） | 预判 skill 的 allowed_tools + `discover_tools` + 元工具 | 预判 skill 的 SKILL.md 全文 |

### 1.2 小模型路由工作流程（deepseek/gemini/hybrid 共用）

```
用户消息
  ↓
pre_route_skill()
  ├─ 短路检测：空消息/闲聊 → skill_name=None, confidence=0.9+
  ├─ 判断 API 协议：_is_gemini_url() → Gemini 原生 / OpenAI 兼容
  ├─ 构建 prompt：_get_system_prompt() 含完整技能目录（含工具明细）
  ├─ 调用小模型，temperature=0, max_tokens=150
  └─ 解析响应 → PreRouteResult(skill_name, skill_names, confidence, reason)
       ↓
_resolve_preroute_target()
  ├─ skill_names 有 2+ 个 → 降级激活 general_excel
  ├─ skill_names 有 1 个 → 精准激活该 skill
  └─ skill_names 为空 → 不激活（闲聊场景）
       ↓
_handle_select_skill(target_skill_name)
  → 将 skill 加入 _active_skills，注入 tools + instructions
```

### 1.3 路由结果传递给 engine 的方式

路由结果通过 `SkillMatchResult` 传递，engine 在 `_get_current_tool_scope()` 中消费：

- `skills_used: list[str]` — 已激活的技能名列表
- `tool_scope: list[str]` — 当前可用工具名列表（空 = 由 engine 根据 _active_skills 计算）
- `system_contexts: list[str]` — 注入 system message 的技能上下文文本
- `write_hint: str` — "may_write" / "read_only" / "unknown"

关键路径：`_handle_select_skill()` 将 skill 加入 `_active_skills` 列表 → `_get_current_tool_scope()` 从 `_active_skills` 的 `allowed_tools` 并集计算 tool_scope → skill 的 `render_context()` 注入 system_contexts。

### 1.4 现有方案的核心问题

当前所有模式都是"全有或全无"：
- 激活 skill = 注入该 skill 的全部 tools + 全部 instructions
- 不激活 = 仅有 DISCOVERY_TOOLS（只读探查）

缺少中间态：**"我大概知道需要这个 skill 的工具，但不确定是否需要它的策略指引"**。

---

## 2. `hybrid_preload` 预选 + 不确定加载设计

### 2.1 核心理念

```
用户消息 → 小模型分类 → 返回 {skill: confidence} 映射
  → confidence >= 0.8 (HIGH)：加载 tools + instructions（完整激活）
  → 0.4 <= confidence < 0.8 (MEDIUM)：仅加载 tools（不注入 instructions）
  → confidence < 0.4 (LOW)：不加载
```

### 2.2 小模型 Prompt 模板

现有 prompt 返回单个 skill + confidence，需要改为返回多 skill 的 confidence 映射：

```python
_USER_PROMPT_TEMPLATE_V2 = (
    '用户消息: "{user_message}"\n\n'
    '请为每个可能相关的技能包评估置信度（0.0~1.0），仅列出置信度 > 0.3 的技能。\n'
    '输出格式: {{"skills": [{{"name": "技能名", "confidence": 0.0到1.0}}], '
    '"reason": "一句话理由"}}\n'
    '规则：\n'
    '- 最多返回 3 个技能\n'
    '- 闲聊/问候返回空 skills 数组\n'
    '- 按 confidence 降序排列'
)
```

**设计理由**：
- 返回数组而非单个值，让小模型表达"可能需要 data_basic，也可能需要 chart_basic"的不确定性
- 限制最多 3 个，避免小模型过度发散
- 阈值 0.3 作为最低门槛，减少噪声

### 2.3 置信度阈值设定

| 阈值 | 名称 | 行为 | 理由 |
|------|------|------|------|
| >= 0.8 | HIGH | tools + instructions | 小模型高度确信，注入完整策略指引可减少主模型试错 |
| 0.4 ~ 0.8 | MEDIUM | 仅 tools | 有一定相关性但不确定，预加载工具避免后续 select_skill 延迟，但不用 instructions 污染上下文 |
| < 0.4 | LOW | 不加载 | 相关性太低，加载会浪费 token |

**阈值选择理由**：
- 0.8 而非 0.9：小模型（DeepSeek/Gemini Flash）的 confidence 校准偏保守，实测中正确分类的 confidence 通常在 0.7~0.9 区间
- 0.4 而非 0.5：宁可多加载几个工具 schema（每个 ~200 token），也不要让主模型因缺工具而多一轮 select_skill 调用（~1000+ token + 延迟）
- 这些阈值应可配置，允许用户根据实际小模型表现调优

### 2.4 新数据结构设计

#### PreRouteResultV2（替代 PreRouteResult）

```python
@dataclass(frozen=True)
class SkillConfidence:
    """单个 skill 的置信度评估。"""
    skill_name: str
    confidence: float  # 0.0 ~ 1.0

@dataclass(frozen=True)
class PreRouteResult:
    """预路由结果（v2：支持多 skill 置信度映射）。"""
    skill_confidences: list[SkillConfidence]  # 按 confidence 降序
    reason: str
    latency_ms: float
    model_used: str
    raw_response: str = ""

    @property
    def primary_skill(self) -> str | None:
        """最高置信度的 skill，None 表示闲聊。"""
        return self.skill_confidences[0].skill_name if self.skill_confidences else None

    @property
    def primary_confidence(self) -> float:
        return self.skill_confidences[0].confidence if self.skill_confidences else 0.0

    # 向后兼容
    @property
    def skill_name(self) -> str | None:
        return self.primary_skill

    @property
    def confidence(self) -> float:
        return self.primary_confidence

    @property
    def skill_names(self) -> list[str]:
        return [sc.skill_name for sc in self.skill_confidences]
```

#### SkillMatchResult 新增字段

```python
@dataclass(frozen=True)
class SkillPreloadEntry:
    """预加载的 skill 条目。"""
    skill_name: str
    confidence: float
    load_level: str  # "full" | "tools_only"

@dataclass(frozen=True)
class SkillMatchResult:
    """Skill 路由结果。"""
    skills_used: list[str]
    tool_scope: list[str]
    route_mode: str
    system_contexts: list[str] = field(default_factory=list)
    parameterized: bool = False
    write_hint: str = "unknown"
    # 新增：预加载信息，供自动补充机制使用
    preloaded_skills: list[SkillPreloadEntry] = field(default_factory=list)
    # 新增：仅加载了 tools 但未注入 instructions 的 skill 名称
    tools_only_skills: list[str] = field(default_factory=list)
```

### 2.5 "仅加载 tools" vs "加载 tools + instructions" 的实现区分

当前 `_handle_select_skill()` 是一体化的：激活 skill → 加入 `_active_skills` → `render_context()` 注入 instructions。

需要新增一个轻量级加载路径：

```python
async def _preload_skill_tools_only(self, skill_name: str) -> bool:
    """仅将 skill 的 allowed_tools 加入可用范围，不注入 instructions。

    用于中置信度 skill 的预加载：工具可用但不占用 system context 预算。
    返回 True 表示成功。
    """
    loader = self._skill_router._loader
    skillpacks = loader.get_skillpacks() or loader.load_all()
    selected = self._skill_router._find_skill_by_name(
        skillpacks=skillpacks, name=skill_name,
    )
    if selected is None:
        return False

    # 记录为"已预加载但未完整激活"
    self._preloaded_tool_only_skills[skill_name] = selected
    # 工具加入可用范围（但不加入 _active_skills）
    self._loaded_skill_names.add(skill_name)
    return True
```

engine 中需要新增：
- `_preloaded_tool_only_skills: dict[str, Skillpack]` — 仅预加载工具的 skill 映射
- `_get_current_tool_scope()` 中合并 `_preloaded_tool_only_skills` 的 allowed_tools

```python
# 在 _get_current_tool_scope() 中追加：
if self._preloaded_tool_only_skills:
    for skill in self._preloaded_tool_only_skills.values():
        for tool in skill.allowed_tools:
            if tool not in scope:
                scope.append(tool)
```

### 2.6 engine 中的预选流程（伪代码）

```python
# Phase 1: hybrid_preload 模式
if preroute_mode == "hybrid_preload":
    pre_route_result = await pre_route_skill_v2(user_message, ...)

    for sc in pre_route_result.skill_confidences:
        if sc.confidence >= HIGH_THRESHOLD:  # >= 0.8
            # 完整激活：tools + instructions
            await self._handle_select_skill(sc.skill_name)
            auto_activated_skill_name = sc.skill_name
            break  # 只完整激活 1 个（最高置信度的）

    for sc in pre_route_result.skill_confidences:
        if sc.skill_name == auto_activated_skill_name:
            continue  # 已完整激活，跳过
        if sc.confidence >= MEDIUM_THRESHOLD:  # >= 0.4
            # 仅加载工具
            await self._preload_skill_tools_only(sc.skill_name)

    # 如果没有任何 skill 达到 HIGH，走 meta_only 路径
    # 但中置信度的工具已经预加载，主模型可以直接使用
```

---

## 3. 与自动补充的衔接

### 3.1 预选结果中支持自动补充的信息

`SkillMatchResult.preloaded_skills` 记录了每个预加载 skill 的置信度和加载级别，供后续判断：

```python
preloaded_skills=[
    SkillPreloadEntry("data_basic", 0.85, "full"),
    SkillPreloadEntry("chart_basic", 0.55, "tools_only"),
]
```

当主模型在执行过程中调用了 `chart_basic` 的工具（如 `create_chart`），engine 可以检测到该工具属于一个 `tools_only` 的预加载 skill，自动补充其 instructions：

```python
async def _maybe_upgrade_preloaded_skill(self, tool_name: str) -> None:
    """当主模型调用了 tools_only skill 的工具时，自动升级为完整激活。"""
    skill_name = self._tool_to_skillpack_index.get(tool_name)
    if skill_name and skill_name in self._preloaded_tool_only_skills:
        skill = self._preloaded_tool_only_skills.pop(skill_name)
        self._active_skills.append(skill)
        # 在下一轮 system message 中注入 instructions
        logger.info("自动升级预加载技能 %s → 完整激活", skill_name)
```

### 3.2 `tool_to_skillpack_index` 反向映射

需要构建工具名 → skillpack 名的反向索引，用于：
1. 自动补充时定位工具所属 skill
2. `discover_tools` 返回结果中标注工具所属 skill

```python
class SkillpackLoader:
    def build_tool_to_skillpack_index(self) -> dict[str, list[str]]:
        """构建工具名 → skillpack 名列表的反向映射。

        一个工具可能属于多个 skillpack（如 read_excel 属于几乎所有 skill），
        因此值为列表，按 skill priority 降序排列。
        """
        index: dict[str, list[str]] = {}
        skillpacks = self.get_skillpacks() or self.load_all()
        # 按 priority 降序排列，确保高优先级 skill 排在前面
        sorted_skills = sorted(
            skillpacks.values(),
            key=lambda s: (-s.priority, s.name),
        )
        for skill in sorted_skills:
            for tool in skill.allowed_tools:
                if tool not in index:
                    index[tool] = []
                if skill.name not in index[tool]:
                    index[tool].append(skill.name)
        return index
```

**数据示例**：
```python
{
    "read_excel": ["excel_code_runner", "sheet_ops", "chart_basic", ...],
    "create_chart": ["chart_basic"],
    "format_cells": ["format_basic"],
    "write_text_file": ["excel_code_runner"],
    "run_code": ["excel_code_runner"],
    ...
}
```

### 3.3 自动补充触发逻辑

当主模型请求调用一个不在当前 `tool_scope` 中的工具时：

```python
async def _handle_tool_not_in_scope(self, tool_name: str) -> str | None:
    """工具不在当前 scope 中时的自动补充逻辑。

    返回 None 表示无法补充，返回字符串表示补充成功的提示。
    """
    candidates = self._tool_to_skillpack_index.get(tool_name, [])
    if not candidates:
        return None

    # 优先选择已预加载（tools_only）的 skill
    for skill_name in candidates:
        if skill_name in self._preloaded_tool_only_skills:
            await self._maybe_upgrade_preloaded_skill(tool_name)
            return f"已自动激活技能 {skill_name}"

    # 其次选择 priority 最高的 skill
    best = candidates[0]
    result = await self._handle_select_skill(best)
    if not result.startswith("未找到技能:"):
        return f"已自动激活技能 {best}"

    return None
```

### 3.4 自动补充的次数限制

为防止无限补充导致上下文膨胀，需要限制：

```python
# engine 中新增状态
_auto_supplement_count: int = 0
_auto_supplement_max: int = 2  # 可配置

async def _handle_tool_not_in_scope(self, tool_name: str) -> str | None:
    if self._auto_supplement_count >= self._auto_supplement_max:
        logger.warning("自动补充次数已达上限 %d", self._auto_supplement_max)
        return None
    # ... 补充逻辑 ...
    self._auto_supplement_count += 1
```

---

## 4. Token 开销估算

### 4.1 基础数据

| 组件 | 估算 token 数 |
|------|-------------|
| 单个工具 schema（JSON function definition） | ~200 token |
| 单个 skill instructions（SKILL.md 正文） | 500~1000 token |
| select_skill 元工具（含 Skill_Catalog） | ~400 token |
| discover_tools 元工具 | ~150 token |
| delegate_to_subagent 元工具 | ~300 token |
| DISCOVERY_TOOLS（13 个只读工具 schema） | ~2600 token |

### 4.2 方案对比

| 方案 | 工具 schema | instructions | 元工具 | 总计 |
|------|-----------|-------------|--------|------|
| **A. off（general_excel）** | ~40 × 200 = 8000 | ~800 | ~850 | **~9650** |
| **B. meta_only** | ~13 × 200 = 2600 | 0 | ~850 | **~3450** |
| **C. hybrid（现有，预选 1 skill）** | ~12 × 200 = 2400 + discover_tools | ~700 | ~850 | **~4100** |
| **D. hybrid_preload（预选 1 HIGH）** | ~12 × 200 = 2400 | ~700 | ~850 | **~3950** |
| **E. hybrid_preload（1 HIGH + 1 MEDIUM tools_only）** | ~(12+8) × 200 = 4000 | ~700 (仅 HIGH 的) | ~850 | **~5550** |
| **F. hybrid_preload（2 HIGH）** | ~(12+8) × 200 = 4000 | ~700 × 2 = 1400 | ~850 | **~6250** |

### 4.3 分析

- **方案 D vs B**：多 ~500 token（1 个 skill instructions），但首轮即可执行写入操作，省去 select_skill 的一轮往返（~1000+ token + 延迟）
- **方案 E vs D**：多 ~1600 token（8 个额外工具 schema），但中置信度 skill 的工具已可用，如果主模型需要用到，无需额外 select_skill
- **方案 E vs A**：少 ~4100 token（约 43% 节省），同时保留了大部分执行能力
- **方案 F**（最坏情况）：仍比方案 A 少 ~3400 token

**结论**：hybrid_preload 方案 E（1 HIGH + 1 MEDIUM）是最佳平衡点，比 off 模式节省 ~40% token，比 meta_only 多 ~2000 token 但大幅减少后续交互轮次。

---

## 5. 配置项设计

### 5.1 新增配置项

```python
# ExcelManusConfig 新增字段

# 预选模式（扩展现有 skill_preroute_mode）
skill_preroute_mode: str = "hybrid"
# 新增可选值: "hybrid_preload"

# 置信度阈值
skill_preroute_high_threshold: float = 0.8    # HIGH: tools + instructions
skill_preroute_medium_threshold: float = 0.4  # MEDIUM: tools only

# 最大预加载 skill 数（含 HIGH + MEDIUM）
skill_preroute_max_preload: int = 3

# 自动补充开关和次数限制
skill_auto_supplement_enabled: bool = True
skill_auto_supplement_max: int = 2
```

### 5.2 环境变量映射

```bash
EXCELMANUS_SKILL_PREROUTE_MODE=hybrid_preload
EXCELMANUS_SKILL_PREROUTE_HIGH_THRESHOLD=0.8
EXCELMANUS_SKILL_PREROUTE_MEDIUM_THRESHOLD=0.4
EXCELMANUS_SKILL_PREROUTE_MAX_PRELOAD=3
EXCELMANUS_SKILL_AUTO_SUPPLEMENT_ENABLED=true
EXCELMANUS_SKILL_AUTO_SUPPLEMENT_MAX=2
```

### 5.3 配置校验规则

```python
# 在 load_config() 中添加
if skill_preroute_high_threshold <= skill_preroute_medium_threshold:
    raise ConfigError(
        "SKILL_PREROUTE_HIGH_THRESHOLD 必须大于 MEDIUM_THRESHOLD"
    )
if skill_preroute_max_preload < 1:
    raise ConfigError("SKILL_PREROUTE_MAX_PRELOAD 必须 >= 1")
```

---

## 6. 实现路线图

### Phase 1：数据结构升级（低风险）
1. 扩展 `PreRouteResult` 支持多 skill confidence 映射
2. 扩展 `SkillMatchResult` 新增 `preloaded_skills` 和 `tools_only_skills`
3. 新增配置项和环境变量解析

### Phase 2：预加载机制（中风险）
4. 实现 `_preload_skill_tools_only()` 方法
5. 修改 `_get_current_tool_scope()` 合并预加载工具
6. 在 engine Phase 1 中实现 `hybrid_preload` 分支

### Phase 3：自动补充（中风险）
7. 构建 `tool_to_skillpack_index` 反向映射
8. 实现 `_maybe_upgrade_preloaded_skill()` 自动升级
9. 实现 `_handle_tool_not_in_scope()` 自动补充

### Phase 4：Prompt 优化（低风险）
10. 更新小模型 prompt 模板支持多 skill confidence 输出
11. 更新 `_parse_pre_route_response()` 解析新格式

### Phase 5：测试与调优
12. 属性基测试：阈值边界、多 skill 预加载正确性
13. 集成测试：端到端预选 → 执行 → 自动补充流程
14. Bench 对比：hybrid_preload vs hybrid vs meta_only 的 token 消耗和任务成功率

---

## 7. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 小模型 confidence 校准不准 | 错误的 HIGH/MEDIUM 分类 | 阈值可配置 + 日志监控 + 定期 bench 校准 |
| 中置信度工具预加载浪费 | 多 ~1600 token 但未使用 | 统计实际使用率，动态调整 MEDIUM 阈值 |
| 自动补充导致上下文膨胀 | 多轮补充累积 instructions | 次数限制 + 仅升级已预加载的 skill |
| 向后兼容性 | 现有 hybrid 模式行为变化 | 新模式名 `hybrid_preload`，不修改现有 `hybrid` |
| tool_to_skillpack_index 一致性 | 工具名变更后索引过期 | 索引在 loader.load_all() 后重建，与 SKILL.md 保持 SSOT |
