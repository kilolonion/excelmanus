# 首轮预选 + 缺失时自动补充：深度技术分析报告

> 基于 ExcelManus v4 源码深度研究
> 覆盖 engine.py / registry.py / router.py / pre_router.py / policy.py / models.py 及 7 个内置 Skillpack
> 分析日期：2026-02-17

---

## 0. 现状基线

### 当前架构数据

| 维度 | 数值 |
|------|------|
| 已注册工具总数 | ~50 |
| DISCOVERY_TOOLS（无 skill 时默认 scope） | 12 只读 + focus_window |
| 元工具 | 5（select_skill, delegate_to_subagent, list_subagents, ask_user, discover_tools） |
| Always-Available | 5（task_create, task_update, memory_save, memory_read_topic, list_skills） |
| Skillpack 数量 | 7 |

### Skillpack 工具数分布

| Skillpack | 工具数 | 独占工具 |
|-----------|--------|----------|
| general_excel | 42 | 0（全部与其他 skill 重叠） |
| format_basic | 16 | 0（全部被 general_excel 覆盖） |
| data_basic | 13 | 0（12/13 被 general_excel 覆盖） |
| excel_code_runner | 11 | 2（write_text_file, run_code） |
| file_ops | 8 | 0（全部被 general_excel 覆盖） |
| sheet_ops | 8 | 0（全部被 general_excel 覆盖） |
| chart_basic | 5 | 0（全部被 general_excel 覆盖） |

### 工具重叠关键数据

- `read_excel` 出现在全部 7 个 skillpack 中
- `list_sheets`、`write_excel` 各出现在 4 个 skillpack 中
- 35 个工具出现在 2+ 个 skillpack 中
- `general_excel` 是 `format_basic`（16/16）、`sheet_ops`（8/8）、`file_ops`（8/8）、`chart_basic`（5/5）的完全超集
- 仅 `excel_code_runner` 有 2 个 general_excel 未覆盖的工具：`write_text_file`、`run_code`

### 当前工具注入流程（hybrid 模式）

```
用户消息 → router.route()
  ├─ 斜杠命令 → slash_direct（精确 skill scope）
  └─ 非斜杠 → _build_all_tools_result（tool_scope=[], route_mode="all_tools"）
       ↓
engine.chat() Phase 1: 技能预激活
  ├─ hybrid: pre_route_skill() → 小模型预判 → _handle_select_skill()
  ├─ off: 确定性激活 general_excel
  └─ meta_only: 不预激活
       ↓
_get_current_tool_scope()
  ├─ 有 active_skills → skill.allowed_tools 并集 + select_skill + discover_tools
  ├─ route_result.tool_scope 非空 → 使用路由指定 scope
  └─ 无 skill、无 scope → DISCOVERY_TOOLS(12) + 元工具(5) + always-available(5) ≈ 22
       ↓
_tool_calling_loop() → _execute_tool_call()
  └─ tool_name not in tool_scope → ToolNotAllowedError → JSON 错误返回给 LLM
```

### A/B 测试基线数据

| 方案 | 工具数 | Token/case | 复合任务完成率 |
|------|--------|-----------|---------------|
| 全量注入 | 49 | 43K | 高 |
| 精准路由 | 16-21 | 25K | 低（工具集太窄） |
| 按需激活（当前 hybrid） | 22 | 27K | 中（6/15） |

---

## 1. 自动补充的触发机制

### 1.1 三种策略深度对比

#### 策略 A：拦截-加载-重试

```
LLM 调用 format_cells → _execute_tool_call()
  → tool_name not in tool_scope → 捕获 ToolNotAllowedError
  → 查找包含 format_cells 的 skillpack → format_basic / general_excel
  → 隐式 _handle_select_skill("format_basic")
  → 将 format_basic.allowed_tools 合并到 tool_scope
  → 重试 format_cells 调用
```

**优势：**
- 实现最简单：仅需在 `_execute_tool_call` 的 `except ToolNotAllowedError` 分支加 ~30 行
- 对现有架构侵入最小：不改变 `_get_current_tool_scope` 逻辑
- 语义清晰：LLM 已经"决定"要用这个工具

**劣势：**
- LLM 行为不一致：LLM 看不到 `format_cells` 的 schema（不在 tools 列表中），却能成功调用。违反 OpenAI function calling 的设计假设
- 幻觉风险：LLM 从工具索引知道工具存在但没有 schema 指导，参数可能错误
- 重试开销：每次拦截-重试消耗一次工具调用配额
- 实现复杂：当前 `_execute_tool_call` 的 `except` 块已经很复杂（处理 ValueError、ToolNotAllowedError、通用 Exception），递归重试增加嵌套深度
- instructions 时机：重试在同一轮中，新 skill 的 instructions 要到下一轮才被 LLM 看到

**关键代码位置分析：** `_execute_tool_call`（engine.py L3554）中 `ToolNotAllowedError` 在 `except` 块处理。重试需要递归调用自身或重构为循环。且 `_tool_calling_loop` 中遍历 `tool_calls` 时，后续工具调用的 `tool_scope` 不会自动更新（除非在拦截后调用 `_get_current_tool_scope` 刷新）。

#### 策略 B：预检-扩展-执行

```
LLM 调用 format_cells → _execute_tool_call()
  → 执行前检查: tool_name not in tool_scope?
  → 是 → 查找包含 format_cells 的 skillpack
  → 扩展 tool_scope（合并 skillpack.allowed_tools）
  → 激活 skill（更新 _active_skills）
  → 正常执行 format_cells（无重试）
```

**优势：**
- 无重试开销：一次通过
- 逻辑清晰：预检是显式的，不依赖异常控制流
- 可以在扩展 scope 后立即更新 `tool_scope` 变量，后续同批次工具调用也能受益
- 改动位置精确：在现有 `ToolNotAllowedError` 检查（engine.py L3680 附近）之前插入预检

**劣势：**
- 同样的 LLM 行为不一致问题（但被工具索引缓解，见下文分析）
- 需要通过返回值或副作用通知 `_tool_calling_loop` 更新 `tool_scope`

**对"LLM 行为不一致"的缓解分析：**

在 ExcelManus 中，这个问题被两个现有机制缓解：
1. **工具索引**（`_build_tool_index_notice`）：system prompt 中已列出所有未激活工具的名称和简短描述（`TOOL_SHORT_DESCRIPTIONS`），LLM 知道这些工具存在
2. **select_skill 引导**：LLM 被明确告知"需要更多工具时调用 select_skill"

因此 LLM 调用未激活工具的场景是：LLM 从工具索引知道了工具名，但跳过了 select_skill 直接调用。这是"快捷路径"而非"幻觉"。参数正确性依赖工具索引中的描述质量——当前 `TOOL_SHORT_DESCRIPTIONS` 提供了足够的语义信息让 LLM 推断参数结构。

#### 策略 C：宽松 scope + 延迟加载 context

```
首轮：tool_scope = 全部已注册工具（~50 个 schema 全部可见）
      但 system prompt 中仅注入预选 skill 的 instructions
      ↓
LLM 调用 format_cells（schema 可见，参数正确）→ 执行成功
  → 检测到 format_cells 属于 format_basic
  → 将 format_basic.instructions 注入到下一轮 system_contexts
```

**优势：**
- LLM 行为完全一致：所有工具 schema 可见，参数正确率最高
- 无拦截/重试开销
- 最符合 A/B 测试中"全量注入"方案的优势

**劣势：**
- Token 开销最高：50 个工具 schema ≈ 11K token，回到全量注入水平
- 与当前优化方向矛盾：当前架构核心优化就是通过 scope 收窄减少 token
- A/B 测试已证明全量注入的 token 开销（43K/case）不可接受
- 安全边界模糊：所有工具都"可调用"，LLM 可能在不理解上下文的情况下调用高风险工具

### 1.2 结论

**推荐策略 B（预检-扩展-执行）**

| 维度 | 策略 A | 策略 B ✓ | 策略 C |
|------|--------|----------|--------|
| 实现复杂度 | 中（异常+重试） | 低（预检+扩展） | 低 |
| Token 开销 | 与当前持平 | 与当前持平 | 回退到全量 |
| LLM 行为一致性 | 差 | 中（工具索引缓解） | 好 |
| 对现有架构侵入 | 中 | 低 | 高 |
| 同批次工具受益 | 否（需额外刷新） | 是 | 是 |

**推荐实现位置：** 在 `_execute_tool_call` 中，现有 `ToolNotAllowedError` 检查之前插入预检逻辑。

---

## 2. 不确定性处理（uncertainty → load）

### 2.1 当前预路由的不确定性现状

`PreRouteResult` 已支持：
- `skill_names: list[str]`（最多 2 个候选）
- `confidence: float`（0.0~1.0，全局置信度）

当前 `_resolve_preroute_target` 的处理：
```python
if len(candidates) >= 2:
    # 复合意图 → 降级激活 general_excel
    return "general_excel", candidates
```

**问题：** 复合意图一律降级到 general_excel（42 工具），丧失精准路由优势。

### 2.2 三种方案对比

#### 方案 a：Top-N union

取置信度最高的 N 个 skillpack 的 allowed_tools 并集。

| 组合示例 | 并集工具数 | 去重后 |
|----------|-----------|--------|
| data_basic ∪ chart_basic | 13+5 | 15（3 个重叠） |
| data_basic ∪ format_basic | 13+16 | 28（1 个重叠） |
| data_basic ∪ sheet_ops | 13+8 | 18（3 个重叠） |
| format_basic ∪ chart_basic | 16+5 | 20（1 个重叠） |

**评估：** N=2 时工具数 15-28，可控。但两份 instructions 可能冲突（如 data_basic 强调"先分析再写入"，chart_basic 强调"先确认数据范围再画图"）。

#### 方案 b：置信度阈值

设定阈值（如 0.3），所有超过阈值的 skillpack 都加载。

**关键障碍：** 当前 `PreRouteResult` 只有一个全局 `confidence`，不支持每个 skill 独立置信度。要实现需要：
1. 修改 pre_router prompt，让小模型输出 `[{"skill": "data_basic", "confidence": 0.6}, ...]`
2. 修改 `_parse_pre_route_response` 解析逻辑
3. 小模型的置信度往往未经校准（overconfident/underconfident），阈值难以调优

**评估：** 理论最优但实现成本高，且依赖小模型的置信度校准质量。

#### 方案 c：分层加载

```
预路由返回 skill_names=["data_basic", "chart_basic"]
→ 第一个（主 skill）: data_basic → 加载 allowed_tools + instructions（full 模式）
→ 第二个（副 skill）: chart_basic → 仅加载 allowed_tools（tools_only 模式，不注入 instructions）
→ 其余 skill → 不加载，依赖 select_skill 或自动补充
```

| 维度 | 方案 a | 方案 b | 方案 c ✓ |
|------|--------|--------|----------|
| 工具数 | 15-28 | 不可预测 | 15-28（同 a） |
| Token 开销 | 高（2 份 instructions） | 不可预测 | 中（1 份 instructions） |
| 实现复杂度 | 低 | 高 | 中 |
| instructions 冲突 | 有风险 | 有风险 | 无（副 skill 不注入） |
| 依赖小模型质量 | 低 | 高 | 低 |

### 2.3 结论

**推荐方案 c（分层加载）**

理由：
1. **Token 效率最优**：比 Top-N union 节省一份 instructions（~1-2K token）
2. **与现有架构契合**：
   - `_active_skills` 列表已支持多个 skill
   - `_active_skills_tool_union` 已实现并集
   - `build_contexts_with_budget` 已支持多 skill 上下文预算分配
   - 只需在 `_handle_select_skill` 中增加 `context_mode` 参数
3. **无 instructions 冲突**：主 skill 提供完整指导，副 skill 仅提供工具能力
4. **不依赖小模型置信度校准**：只需要排序（第一个 vs 第二个），不需要绝对值

**具体实现：**

```python
# chat() Phase 1 中替换当前的 _resolve_preroute_target 降级逻辑
target_skill_name, skill_candidates = _resolve_preroute_target(pre_route_result)
if target_skill_name is not None:
    # 主 skill：full 模式（tools + instructions）
    await self._handle_select_skill(target_skill_name)
    
    # 副 skill：tools_only 模式（仅 allowed_tools）
    if len(skill_candidates) >= 2:
        secondary = skill_candidates[1]
        if secondary != target_skill_name:
            self._load_skill_tools_only(secondary)
```

---

## 3. 与现有机制的协同

### 3.1 select_skill 元工具

**关系：互补，非替代。** 自动补充是 select_skill 的"快捷路径"。

- 自动补充：LLM 从工具索引识别工具 → 直接调用 → 系统自动激活（1 步）
- select_skill：LLM 判断需要能力 → 调用 select_skill → 再调用目标工具（2 步）

**交互安全性：**
- 自动补充激活 skill 后，`_active_skills` 更新，后续 `_get_current_tool_scope` 返回扩展 scope
- LLM 之后调用 `select_skill` 激活同一 skill 时，`_handle_select_skill` 的去重逻辑正确处理：`self._active_skills = [s for s in self._active_skills if s.name != selected.name] + [selected]`

**建议：** 自动补充成功后，在工具结果中附加提示让 LLM 知道 scope 已扩展：
```
[系统已自动激活技能 format_basic，后续可直接使用该技能的工具]
```

### 3.2 discover_tools 元工具

**关系：discover_tools 是"查询"，自动补充是"执行"。**

自动补充使 discover_tools 的使用频率自然降低，但 discover_tools 在以下场景仍有价值：
- LLM 不确定任务需要哪类工具时（探索性查询）
- 用户问"你能做什么"时
- 工具索引中的简短描述不够详细时

**建议：** 保留 discover_tools 不变。

### 3.3 工具索引（_build_tool_index_notice）

**关系：工具索引是自动补充的"触发源"。**

工具索引在 system prompt 中列出未激活工具的名称和描述，是 LLM 知道"存在但不可用"工具的唯一途径。自动补充使这些工具变为"可自动激活"。

**建议修改措辞：**

当前：`未激活（需 select_skill 激活对应技能后可用）`
改为：`按需可用（直接调用即可，系统会自动激活对应技能）`

这样 LLM 会更自信地直接调用，减少不必要的 select_skill 中间步骤。

### 3.4 write_hint 分类

**关系：正交，但有一个边界情况。**

`write_hint` 由 router 的 `_classify_write_hint` 在路由阶段确定，影响 `finish_task` 注入和写入门禁。自动补充不改变 `write_hint`。

**边界情况：** write_hint="read_only"，但 LLM 调用了 write_excel → 自动补充激活 data_basic → 执行成功。此时 finish_task 未注入，写入门禁未生效。

**建议：** 自动补充激活包含写入工具的 skill 时，升级 write_hint：
```python
if tool_name in MUTATING_ALL_TOOLS:
    self._current_write_hint = "may_write"
```

### 3.5 执行守卫

**关系：自动补充减少执行守卫触发。**

执行守卫在 LLM 输出公式建议但未写入时触发。自动补充使 LLM 更容易直接调用写入工具，减少"仅建议不执行"。

**建议：** 保留执行守卫作为兜底，预期触发频率降低。

### 3.6 熔断机制

**关系：自动补充减少 ToolNotAllowedError 导致的误熔断。**

当前 `ToolNotAllowedError` 计入 `consecutive_failures`，连续 6 次触发熔断。自动补充将这类错误转化为成功。

**建议：** 自动补充失败时（找不到包含该工具的 skillpack），仍返回 ToolNotAllowedError 并计入失败，但优化错误消息：
```json
{
    "error_code": "TOOL_NOT_ALLOWED",
    "tool": "some_tool",
    "message": "工具 'some_tool' 不在任何已注册技能包中，无法自动激活。",
    "suggestion": "请检查工具名称是否正确，或使用 discover_tools 查询可用工具。"
}
```

---

## 4. 边界情况分析

### 4.1 一个工具属于多个 skillpack

**数据：** 35 个工具出现在 2+ 个 skillpack 中。`read_excel` 出现在全部 7 个中。

**选择策略：** 当 `format_cells` 被调用时，应激活 `format_basic`（16 工具）还是 `general_excel`（42 工具）？

**推荐：最小覆盖优先**

理由：
- 最小覆盖 = 最少额外工具 = 最少 token 开销
- 专业 skill 的 instructions 更精准
- `general_excel` 作为兜底应是最后选择

**实现：** 构建反向索引 `tool_name → [skill_name, ...]`，按 `len(allowed_tools)` 升序排列：

```python
self._tool_to_skill_index: dict[str, list[str]] = {}
for skill in all_skillpacks.values():
    for tool in skill.allowed_tools:
        self._tool_to_skill_index.setdefault(tool, []).append(skill.name)
# 按工具数升序排列（最小覆盖优先）
for tool, skills in self._tool_to_skill_index.items():
    skills.sort(key=lambda name: len(all_skillpacks[name].allowed_tools))
```

**示例：** `format_cells` → `["format_basic"(16), "general_excel"(42)]` → 选择 `format_basic`

**特殊处理：** 如果已有 active_skill 包含目标工具，不需要额外激活（`_active_skills_tool_union` 已处理）。

### 4.2 instructions 注入时机（当前轮 vs 下一轮）

**分析：**

```
iteration N:
  1. _prepare_system_prompts_for_request() → 构建 system prompt（不含新 skill instructions）
  2. LLM 返回 tool_calls: [format_cells, adjust_column_width]
  3. _execute_tool_call(format_cells) → 自动补充激活 format_basic
  4. _execute_tool_call(adjust_column_width) → 已在扩展后的 scope 中，直接执行
  5. 工具结果返回给 LLM

iteration N+1:
  1. _prepare_system_prompts_for_request() → 包含 format_basic.instructions ✓
  2. LLM 基于 instructions 指导执行后续操作
```

**结论：当前轮工具执行不受 instructions 影响，下一轮开始受益。这是可接受的。**

理由：
- 当前轮 LLM 已"决定"了工具和参数，instructions 不改变这个决策
- instructions 的主要价值是指导"如何组合使用工具"和"注意事项"，在后续轮次发挥作用
- 如果当前轮参数有误，工具返回错误，LLM 在下一轮（有 instructions）可修正

**建议：** 不在当前轮注入 instructions。在自动补充的工具结果中附加提示即可。

### 4.3 自动补充次数限制

**分析：**
- 7 个 skillpack 中，general_excel 覆盖 84%。极端情况下一轮最多触发 2-3 次
- 每次增加 ~1-2K token（instructions），3 次 = ~3-6K token，可接受
- 用户自定义 skillpack 增多时，理论上可能触发更多次

**推荐：每轮最大 3 次**

```python
AUTO_SUPPLEMENT_MAX_PER_TURN = 3  # 配置项
```

超过限制后回退到 ToolNotAllowedError，提示 LLM 使用 select_skill。

### 4.4 fullAccess 模式

**行为应一致：**
- fullAccess 解除"确认门禁"，不影响"工具可见性"
- 自动补充解决"工具可见性"，不影响"确认门禁"
- 两者正交

**例外：** `excel_code_runner` 的 `run_code`/`write_text_file` 在非 fullAccess 下被 `_blocked_skillpacks` 限制。自动补充时需检查：

```python
def _try_auto_supplement_tool(self, tool_name: str) -> ...:
    candidate_skill = self._find_skill_for_tool(tool_name)
    if candidate_skill is None:
        return None
    blocked = self._blocked_skillpacks()
    if blocked and candidate_skill.name in blocked:
        return None  # 不自动补充被限制的 skill
```

### 4.5 bench 测试模式

**影响：**
1. `enable_bench_sandbox()` 设置 fullAccess=True，不影响自动补充逻辑
2. 自动补充是确定性的（给定工具名 → 确定的 skillpack），不影响可重现性
3. `_EngineTracer` 需要能捕获轮次中间的 tool_scope 变化

**建议：**
- `TurnResult` 新增 `auto_supplement_events` 字段
- bench 对比分析时将自动补充次数作为维度
- 通过 `auto_supplement_enabled` 配置项控制，bench 对比时可固定配置

---

## 5. 实现路径建议

### 5.1 整体架构变更

```
                    ┌─────────────────────────────────────────┐
                    │           chat() Phase 1                │
                    │  小模型预路由 → 分层加载（主+副 skill）   │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │      _tool_calling_loop()               │
                    │  tool_scope = 主skill + 副skill tools   │
                    │  + DISCOVERY_TOOLS + 元工具              │
                    └──────────────┬──────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────┐
                    │      _execute_tool_call()               │
                    │  tool not in scope?                     │
                    │  ├─ YES → _try_auto_supplement()        │
                    │  │   ├─ 找到 skill → 激活 + 扩展 scope  │
                    │  │   └─ 未找到 → ToolNotAllowedError    │
                    │  └─ NO → 正常执行                       │
                    └─────────────────────────────────────────┘
```

### 5.2 需要修改的文件和方法

#### engine.py（主要改动）

**新增方法：**

| 方法 | 职责 |
|------|------|
| `_build_tool_to_skill_index()` | 构建 tool→skill 反向索引，按 skill 工具数升序 |
| `_try_auto_supplement_tool()` | 预检+扩展：查找最小覆盖 skillpack 并激活 |
| `_load_skill_tools_only()` | 分层加载副 skill：仅 allowed_tools，不注入 instructions |

**修改方法：**

| 方法 | 改动 |
|------|------|
| `__init__` | 初始化 `_tool_to_skill_index`、`_turn_supplement_count` |
| `_execute_tool_call` | ToolNotAllowedError 检查前增加自动补充预检 |
| `_tool_calling_loop` | 增加 `_turn_supplement_count` 每轮重置 |
| `chat` Phase 1 | 修改 `_resolve_preroute_target` 支持分层加载 |
| `_build_tool_index_notice` | 修改措辞 |

**`_execute_tool_call` 具体改动（L3680 附近）：**

```python
# 当前代码：
if tool_scope is not None and tool_name not in set(tool_scope):
    raise ToolNotAllowedError(...)

# 改为：
if tool_scope is not None and tool_name not in set(tool_scope):
    supplement = self._try_auto_supplement_tool(
        tool_name,
        current_count=self._turn_supplement_count,
        max_count=self._config.auto_supplement_max,
    )
    if supplement is not None:
        tool_scope = list(set(tool_scope) | set(supplement.expanded_tools))
        self._turn_supplement_count += 1
    else:
        raise ToolNotAllowedError(...)
```

#### skillpacks/models.py

**新增：**
```python
@dataclass(frozen=True)
class AutoSupplementResult:
    skill_name: str
    expanded_tools: list[str]
    context_injected: bool
```

#### config.py

**新增配置项：**
```python
auto_supplement_enabled: bool = True
auto_supplement_max: int = 3
auto_supplement_prefer_minimal: bool = True
```

环境变量：`EXCELMANUS_AUTO_SUPPLEMENT_ENABLED`、`EXCELMANUS_AUTO_SUPPLEMENT_MAX`

#### bench.py

**修改：** `TurnResult` 新增 `auto_supplement_events` 字段，`_EngineTracer` 追踪事件。

#### tools/policy.py

**无需修改。**

### 5.3 与 skill_preroute_mode 的关系

| preroute_mode | 首轮预选 | 自动补充触发频率 |
|---------------|---------|-----------------|
| `off` | general_excel（42 工具） | 极低（84% 覆盖） |
| `hybrid` | 小模型预选 + 分层加载 | 偶尔（补充遗漏） |
| `deepseek`/`gemini` | 小模型预选 1 个 | 较常（单 skill 不足时） |
| `meta_only` | 不预选 | 频繁（LLM 从索引直接调用） |

**结论：** `auto_supplement_enabled` 默认 `true`，在所有 preroute_mode 下生效。在 `off` 模式下实际触发极少，不增加开销。在 `meta_only` 模式下价值最大。

### 5.4 分阶段实施计划

| Phase | 内容 | 改动量 | 耗时 |
|-------|------|--------|------|
| 1 | 自动补充核心（反向索引 + 预检 + 配置） | ~150 行新增 + ~30 行修改 | 2-3 天 |
| 2 | 分层加载（主 skill full + 副 skill tools_only） | ~80 行新增 + ~20 行修改 | 1-2 天 |
| 3 | bench 集成与可观测性 | ~60 行新增 + ~20 行修改 | 1 天 |
| 4 | write_hint 联动 + blocked_skillpacks 检查 + 边界加固 | ~40 行新增 + ~15 行修改 | 0.5 天 |

---

## 6. 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 幻觉工具名 | 低 | 低 | 查找失败 → 回退 ToolNotAllowedError |
| 激活错误 skill | 中 | 中 | 最小覆盖策略 + 次数限制 + 下轮 LLM 可修正 |
| Token 增加 | 中 | 低 | 次数限制(3) + 分层加载 |
| Hook 冲突 | 低 | 中 | 自动补充在 pre_hook 后、执行前，不影响 Hook |
| 安全边界突破 | 极低 | 高 | blocked_skillpacks 检查 + Tier A 门禁不变 |
| bench 不可比 | 低 | 中 | 配置项控制开关 |

**回滚策略：** `auto_supplement_enabled=false` 即可完全禁用，无需代码回滚。

---

## 7. 预期收益

### Token 开销

| 场景 | 当前 hybrid | 预选+自动补充 |
|------|------------|--------------|
| 简单读取 | ~22 工具 | 无变化 |
| 单领域写入 | 22→select_skill→16-42 | 22→自动补充→22+16（省 1 轮 LLM） |
| 复合任务 | 22→select_skill(general)→42 | 22→预选(13)+自动补充(16)→30（少 12 工具） |

### LLM 调用轮次

| 场景 | 当前 | 改进后 | 节省 |
|------|------|--------|------|
| "把 A1 改成 100" | 3 轮 | 2 轮 | 1 轮 |
| "分析数据并画图" | 4 轮 | 3 轮 | 1 轮 |
| "读取文件" | 1 轮 | 1 轮 | 0 |

### bench 指标预期

| 指标 | 当前 | 预期 |
|------|------|------|
| 平均 token/case | ~27K | ~24K |
| 复合任务完成率 | 4/15 | 6-8/15 |
| 平均轮次/case | ~5.2 | ~4.5 |
| ToolNotAllowedError/case | ~0.8 | ~0.1 |

---

## 8. 总结

### 核心推荐

| 维度 | 推荐 | 理由 |
|------|------|------|
| 触发机制 | 策略 B：预检-扩展-执行 | 无重试开销，逻辑清晰，侵入最小 |
| 不确定性 | 方案 c：分层加载 | Token 最优，无 instructions 冲突 |
| Skill 选择 | 最小覆盖优先 | 专业 skill 优于 general_excel |
| 次数限制 | 每轮最多 3 次 | 防止无限扩展 |
| 配置 | 默认开启，可关闭 | 所有 preroute_mode 下生效 |

### 关键设计决策

1. **自动补充 ≠ 全量注入**：按需加载，每次只加载一个 skillpack
2. **instructions 延迟注入可接受**：当前轮工具调用已由 LLM 决定，instructions 下一轮生效不影响正确性
3. **最小覆盖优先于 general_excel**：专业 skill 的 instructions 更精准，工具集更小
4. **自动补充与 select_skill 互补**：快捷路径 vs 显式路径，两者共存
5. **安全边界完整保留**：blocked_skillpacks + Tier A 门禁在自动补充流程中不受影响
