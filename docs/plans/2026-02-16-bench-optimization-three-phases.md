# ExcelManus Bench 优化方案：三阶段执行效率提升

> 日期：2026-02-16
> 来源：bench_skill_20260216T004700 测试结果分析

---

## 背景

5 题 SpreadsheetBench 随机抽样测试（seed=20260216）全部通过，但暴露三类效率问题：

| 问题 | 影响 case | 浪费 |
|------|----------|------|
| `select_skill` 路由开销 | sb_24_23, sb_105_24, sb_56599 (3/5) | ~1轮 + ~6K tokens/case |
| 执行深度不足（只读+文字说明） | sb_24_23, sb_147_48 (2/5) | 任务未实际完成 |
| 空承诺/多余说明轮 | sb_56599 (1/5) | 额外 1 轮纯文本 |

---

## Phase 1: 默认技能预激活

### 问题

当前 `all_tools` 路由只给只读工具 + `select_skill` 元工具。60%+ 的任务需要写入，每次浪费 1 轮 LLM 调用激活技能。

### 方案

非斜杠路由时，自动预激活 `general_excel` 技能，让写入工具从首轮可用。

### 改动点

- **`excelmanus/engine.py`** — `chat()` 方法中，路由完成后、进入 LLM 循环前：
  - 当 `route_mode == "all_tools"` 且 `self._active_skill is None` 时
  - 自动调用内部激活逻辑（等效 `_handle_select_skill("general_excel")`）
  - 跳过 LLM 调用，直接设置 `_active_skill` 和扩展 tool_scope
- **`excelmanus/config.py`** — 新增配置项 `auto_activate_default_skill: bool = True`
- 保留 `select_skill` 工具用于切换到其他技能

### 预期收益

- 每个写入任务省 ~1 轮迭代 + ~6K tokens + ~2-4s
- 纯读取任务仅多加载技能上下文（~几百 tokens），远低于激活开销

### 风险

- 低 — 加载的内容本来就会被加载，只是提前了

---

## Phase 2: Prompt 约束强化

### 问题

现有 prompt 已有"禁止空承诺""执行优先"约束，但表述分散、力度不够，LLM 仍然违反。

### 方案

在 `excelmanus/memory.py` 的 prompt 模板中强化关键规则：

### 改动点

**`_SEGMENT_TONE_STYLE`** 新增：
- "收到任务后，第一轮响应**必须**包含至少一个工具调用。纯文本解释不算有效响应。"
- "不得先用一轮纯文本解释方案再执行。解释和执行必须在同一轮完成。"

**`_SEGMENT_TOOL_POLICY`** 强化：
- "用户消息提到文件 + 操作动词（删除/替换/写入/创建/修改/格式化/转置/排序等）时，必须读取并操作该文件直至完成，不得仅给出说明后结束。"
- "每轮响应要么包含工具调用推进任务，要么是最终完成总结。中间不得有纯文本过渡轮。"

### 预期收益

- 减少 "先解释后做" 和 "只读不做" 模式
- 与 Phase 1 协同：有工具可用 + 有约束要求用 = 更高执行率

### 风险

- 低 — 纯文本修改，可通过 bench 立即验证

---

## Phase 3: 统一执行守卫

### 问题

Prompt 约束是软约束，LLM 可能仍然违反。现有 `_execution_guard_fired` 只检测公式文本模式，覆盖面太窄。

### 方案

增强执行守卫，在每轮 LLM 响应后检测两种失败模式：

1. **空响应守卫**: 迭代 0 时 LLM 返回有文本但无 tool_calls → 注入 nudge 继续循环
2. **读写不匹配守卫**: LLM 准备结束（无 tool_calls），但整个会话只有只读工具调用，而用户消息暗示写入意图 → 注入 nudge 继续

### 写入意图检测

用户消息同时满足：
- (a) 包含文件路径或文件名（`.xlsx` / `文件` / 路径模式）
- (b) 包含动作动词（删除/替换/写入/创建/修改/转置/格式化/排序/过滤/合并 等）

### 改动点

- **`excelmanus/engine.py`** — 迭代循环中，在现有 `_execution_guard_fired` 逻辑旁增加两个守卫
- 新增 `_detect_write_intent(user_message: str) -> bool` 辅助方法
- 守卫触发时注入 nudge 消息并继续循环（最多触发 1 次，避免无限循环）

### 预期收益

- 作为 Phase 2 的安全网，确保 prompt 失效时也能补救

### 风险

- 中等 — 启发式可能误触发纯问答场景
- 缓解：仅在检测到写入意图时激活，纯问答不触发；每个守卫最多触发 1 次

---

## 验证策略

每个 Phase 实施后用 **同一 seed=20260216 的 bench suite** 重跑对比：

```bash
.venv/bin/python -m excelmanus.bench \
    --suite bench/cases/suite_bench_skill_20260216T004700.json \
    --output-dir outputs/bench_opt_phase<N>_<timestamp> \
    --concurrency 1 --trace
```

对比指标：
- 平均迭代数（目标：Phase 1 后从 3.8 降至 ~2.5）
- 平均 token 消耗（目标：Phase 1 后从 30K 降至 ~24K）
- 执行深度（目标：Phase 2 后 5/5 case 都有实际写入操作）
- 空承诺率（目标：Phase 2+3 后降至 0）

## 实施顺序

1. **Phase 1** → bench 验证 → 合并
2. **Phase 2** → bench 验证 → 合并
3. **Phase 3** → 单测 + bench 验证 → 合并
