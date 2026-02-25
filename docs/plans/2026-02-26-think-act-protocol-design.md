# Think-Act Protocol 全栈深化设计文档

> 日期: 2026-02-26 | 状态: approved | 作者: cascade

## 1. 背景与动机

### 1.1 现状

Agent 的推理行为目前**仅靠 prompt 引导**：

- `10_core_principles.md` 第 7 条定义了 Think-Act Protocol（观察/分析/决策三级推理）
- 7 个策略文件各自内嵌了场景化的 Think-Act 注解
- 子代理 `_base.md` 仅要求"每次工具调用前用一句话说明目的"

**缺失的能力层**：
- **检测**：引擎无法感知 agent 是否在推理（"沉默调用"不可见）
- **度量**：bench 不测推理质量，只看完成率和 token 成本
- **修正**：没有运行时反馈机制纠正沉默调用行为
- **分级信号**：推理深度建议是静态文本，未与任务上下文动态绑定

### 1.2 目标

实现 4 层能力矩阵，让 Think-Act Protocol 从"文档建议"升级为"可检测、可度量、可修正、可引导"的运行时能力：

```
Layer 4: 引导 (Prompt Guidance)         — 已有，需优化
Layer 3: 修正 (Runtime Correction)      — 新增
Layer 2: 度量 (Measurement)             — 新增
Layer 1: 检测 (Detection)               — 新增
```

### 1.3 核心约束

- **零轮次增加**：不注入 user message、不阻断执行、不强制重试
- **最小 token 开销**：正常路径 ≤ +5 tokens/轮
- **向后兼容**：所有改动为增量，不删除/修改现有字段和行为
- **模型无关**：兼容 thinking/non-thinking 模型、流式/非流式

## 2. 设计详情

### 2.1 Phase 1: Engine 层检测与状态记录

#### 2.1.1 沉默调用的精确定义

在 `_tool_calling_loop` 中，当 LLM 返回 tool_calls 时：

```python
# 复用已有变量（engine.py line ~4606）
text_content = (getattr(message, 'content', None) or '').strip()
# thinking_content 已在前面计算
has_reasoning = bool(text_content or thinking_content)
reasoning_chars = len(text_content) + len(thinking_content)
```

**关键**：thinking-capable 模型（Claude/Gemini/DeepSeek）的推理在 `thinking` 字段，`_consume_stream` 已将所有 thinking 变体统一到该字段。`content` 为空但有 `thinking` 不算沉默调用。

#### 2.1.2 SessionState 扩展

文件：`excelmanus/engine_core/session_state.py`

```python
# __init__ 新增
self.silent_call_count: int = 0
self.reasoned_call_count: int = 0
self.reasoning_chars_total: int = 0
```

`reset_loop_stats()` 中新增重置：
```python
self.silent_call_count = 0
self.reasoned_call_count = 0
self.reasoning_chars_total = 0
```

`reset_session()` 中同样新增重置。

#### 2.1.3 TurnDiagnostic 扩展

文件：`excelmanus/engine.py`

```python
@dataclass
class TurnDiagnostic:
    # ... 现有字段 ...
    has_reasoning: bool = True
    reasoning_chars: int = 0
    silent_tool_call_count: int = 0
```

`to_dict()` 中按需输出（非默认值时才输出）。

#### 2.1.4 检测注入点

文件：`excelmanus/engine.py` `_tool_calling_loop`

在 tool_calls 非空分支，`assistant_msg` 添加到 memory 之后、工具遍历之前：

```python
# ── Think-Act 检测（纯记录，不阻断） ──
_tc_count = len(tool_calls)
if has_reasoning:
    self._state.reasoned_call_count += _tc_count
    self._state.reasoning_chars_total += reasoning_chars
else:
    self._state.silent_call_count += _tc_count
```

同时更新当前轮次的 TurnDiagnostic：
```python
diag.has_reasoning = has_reasoning
diag.reasoning_chars = reasoning_chars
diag.silent_tool_call_count = 0 if has_reasoning else _tc_count
```

#### 2.1.5 ChatResult 扩展

文件：`excelmanus/engine.py`

```python
@dataclass
class ChatResult:
    # ... 现有字段 ...
    reasoning_metrics: dict[str, Any] = field(default_factory=dict)
```

在 `_finalize_result` 中填充：
```python
reasoning_metrics={
    "silent_call_count": self._state.silent_call_count,
    "reasoned_call_count": self._state.reasoned_call_count,
    "reasoning_chars_total": self._state.reasoning_chars_total,
    "silent_call_rate": round(
        self._state.silent_call_count
        / max(1, self._state.silent_call_count + self._state.reasoned_call_count),
        3,
    ),
}
```

### 2.2 Phase 2: 推理分级信号 + meta_cognition 扩展

#### 2.2.1 推理分级信号

文件：`excelmanus/engine_core/context_builder.py`

新增方法：
```python
def _compute_reasoning_level(self, route_result) -> str:
    """根据任务上下文计算推荐推理级别。"""
    if route_result is None:
        return "standard"
    wh = getattr(route_result, "write_hint", "unknown") or "unknown"
    tags = set(getattr(route_result, "task_tags", []) or [])
    if wh == "read_only":
        return "lightweight"
    if tags & {"cross_sheet", "large_data"}:
        return "complete"
    if wh == "may_write":
        return "standard"
    return "lightweight"
```

在 `_build_runtime_metadata_line` 末尾追加：
```python
# 需要从 engine 获取 route_result
reasoning_level = self._compute_reasoning_level(
    getattr(e, '_last_route_result', None)
)
parts.append(f"reasoning={reasoning_level}")
```

#### 2.2.2 meta_cognition 扩展

文件：`excelmanus/engine_core/context_builder.py` `_build_meta_cognition_notice`

新增条件 4（沉默调用检测），并加入优先级上限机制：

```python
def _build_meta_cognition_notice(self) -> str:
    e = self._engine
    state = e.state
    max_iter = e.config.max_iterations
    iteration = state.last_iteration_count
    failures = state.last_failure_count
    successes = state.last_success_count

    parts: list[str] = []
    _MAX_WARNINGS = 2  # 最多 2 条警告

    # 条件 1（优先级最高）：接近迭代上限
    if max_iter > 0 and iteration >= max_iter * 0.6:
        parts.append(...)

    # 条件 2：连续失败 >= 3
    if len(parts) < _MAX_WARNINGS and failures >= 3 and successes == 0:
        parts.append(...)

    # 条件 3：执行守卫曾触发
    if len(parts) < _MAX_WARNINGS and state.execution_guard_fired and not state.has_write_tool_call:
        parts.append(...)

    # 条件 4（优先级最低）：沉默调用
    silent = state.silent_call_count
    reasoned = state.reasoned_call_count
    if len(parts) < _MAX_WARNINGS and silent > 0 and silent >= reasoned:
        parts.append(
            f"⚠️ 本轮已有 {silent} 次工具调用未附带推理文本。"
            "请遵循 Think-Act 协议：工具调用前至少用 1 句话说明意图。"
            "（thinking 模型：推理可在 thinking 块中完成。）"
        )

    if not parts:
        return ""
    return "## 进展反思\n" + "\n".join(parts)
```

**关键设计**：
- `_MAX_WARNINGS = 2` 限制总警告数
- 沉默调用排在最后，优先保证更紧急的警告（迭代上限/连续失败）
- 触发条件 `silent >= reasoned` 意味着沉默调用占比 ≥ 50% 才触发

### 2.3 Phase 3: Prompt 层优化

#### 2.3.1 核心法则优化

文件：`excelmanus/prompts/core/10_core_principles.md` 第 7 条

主要变更：
1. 新增「信号驱动分级」说明：告知 agent 看 Runtime metadata 中的 `reasoning` 字段
2. 明确 thinking 模型的推理等价性
3. 新增批量工具调用的推理合并规则

#### 2.3.2 子代理推理协议对齐

文件：`excelmanus/prompts/subagent/_base.md`

"输出规范"章节升级为"推理与输出协议"：

```markdown
## 6. 推理与输出协议（Think-Act）

每次工具调用前，至少输出：
- **观察**（1 句）：上一步返回了什么关键信息，或当前已知什么
- **决策**（1 句）：选择什么工具/参数，为什么

禁止"沉默调用"——不做任何说明直接调用工具。
效率原则：推理应简短精准（每条 1 句即可），不要写长段分析。

任务完成时用自然语言简要汇报：做了什么、核心结果、涉及哪些文件/区域。
任务失败时说清楚：出了什么错、可能的原因、已完成了哪些步骤。
```

### 2.4 Phase 4: Bench 推理质量度量

#### 2.4.1 BenchResult 扩展

文件：`excelmanus/bench.py`

在 `BenchResult.to_dict()` 的 `stats` 区块新增：
```python
"reasoning_metrics": chat_result.reasoning_metrics,
```

在 `TurnResult.to_dict()` 的 `stats` 区块同样新增。

#### 2.4.2 报告输出

文件：`excelmanus/bench_reporter.py`

报告新增"推理质量"章节：
```markdown
## 推理质量
| 指标 | 值 |
|------|-----|
| 沉默调用率 | X% (N/M) |
| 有推理的调用 | N/M |
| 平均推理字符/调用 | X chars |
```

### 2.5 Phase 5: 测试

| 测试文件 | 覆盖内容 |
|----------|---------|
| `tests/test_think_act.py` (新建) | 沉默调用检测逻辑、state 计数、推理分级信号、ChatResult 携带 metrics |
| `tests/test_context_builder.py` (扩展) | meta_cognition 沉默调用条件、优先级上限机制 |

## 3. 兼容性验证

| 模块 | 交互方式 | 结论 |
|------|---------|------|
| Compaction | 推理文本增加 assistant msg token | 安全 — <1% 上下文预算增量 |
| Prompt Cache | reasoning 字段在 runtime_metadata | 安全 — 已在动态区域 |
| Streaming | content/thinking 提取 | 安全 — `_consume_stream` 已统一 |
| Stuck Detection | 可能同时触发 | 安全 — 优先级上限机制 |
| Execution Guard | 代码路径 | 安全 — 互斥路径 |
| Resume（续跑） | state 保持 | 安全 — reset_loop_stats 中重置 |
| Parallel Batches | 检测粒度 | 安全 — per-response 检测 |
| Bench | artifact 收集 | 安全 — 纯增量字段 |
| Thinking 模型 | DeepSeek/Claude/Gemini | 安全 — thinking 已统一提取 |

## 4. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 迭代轮次增加 | 极低 | 高 | 不阻断/不注入 user msg |
| Token 成本飙升 | 低 | 中 | 分级控制 + 上限 2 条 |
| Stuck Detection 冲突 | 低 | 低 | 优先级机制 |
| 子代理迭代增加 | 低 | 中 | 仅"标准"级（各1句） |

## 5. Token 开销量化

| 场景 | 新增 tokens/轮 |
|------|---------------|
| 正常路径（agent 有推理） | +5 |
| 沉默调用触发 meta_cognition | +55~105 |
| Core prompt 常驻 | +25 |
| 子代理常驻 | +100 |

## 6. 改动文件清单

```
Phase 1: Engine 层检测
├── excelmanus/engine_core/session_state.py  → +3 字段, reset 扩展
├── excelmanus/engine.py (TurnDiagnostic)    → +3 字段
├── excelmanus/engine.py (_tool_calling_loop) → +6 行检测
└── excelmanus/engine.py (ChatResult)        → +1 字段

Phase 2: 分级信号 + 修正
├── excelmanus/engine_core/context_builder.py (_build_runtime_metadata_line) → +1 字段
├── excelmanus/engine_core/context_builder.py (_build_meta_cognition_notice) → +1 条件 + 上限
└── excelmanus/engine_core/context_builder.py (_compute_reasoning_level)     → 新方法

Phase 3: Prompt 层
├── excelmanus/prompts/core/10_core_principles.md  → 第7条精炼
└── excelmanus/prompts/subagent/_base.md           → 推理协议章节

Phase 4: Bench 度量
├── excelmanus/bench.py (BenchResult/TurnResult)   → stats 新增 reasoning_metrics
└── excelmanus/bench_reporter.py                    → 报告新增推理质量章节

Phase 5: 测试
├── tests/test_think_act.py      → 新建
└── tests/test_context_builder.py → 扩展
```

## 7. 实施顺序

```
Phase 1 (检测) → Phase 2 (修正) → Phase 3 (prompt) → Phase 4 (度量) → Phase 5 (测试)
```

Phase 1-2 为基础设施，Phase 3 可独立实施，Phase 4 依赖 Phase 1 的 ChatResult 扩展。
