# WURM Phase 3：adaptive 模式 + 跨模型验证

> **预计工期**：~3 天
> **前置依赖**：Phase 2 完成（unified 模式 + 安全阀 + GPT benchmark 通过）
> **目标**：基于模型 ID 自动选择最优模式 + 全模型 benchmark + 参数调优

---

## 交付物

- `window_return_mode: "adaptive"` 可用，自动根据模型选择最优模式
- 跨模型 benchmark 报告（GPT / Kimi / Sonnet / DeepSeek）
- 最终参数调优结果 + 上线建议

---

## P3-a：adaptive 模式实现（1 天）

### 目标
基于模型 ID 自动选择 unified / anchored / enriched，并实现逐级降级链。

### 涉及文件
- `excelmanus/window_perception/adaptive.py`（**新建**，模式选择逻辑）
- `excelmanus/window_perception/manager.py`（集成 adaptive 分支）
- `excelmanus/config.py`（adaptive 相关配置）

### 具体任务

1. **新建 `adaptive.py`** — 模式选择器

   ```python
   class AdaptiveModeSelector:
       """根据模型 ID + 运行时信号选择 window_return_mode。"""

       # 静态映射（初始值，后续可通过 benchmark 数据调整）
       MODEL_MODE_MAP = {
           "gpt-5": "unified",
           "gpt-4": "unified",
           "kimi-k2": "anchored",
           "sonnet-4": "anchored",
           "deepseek": "enriched",
       }
   ```

   - `select_mode(model_id: str) -> str` — 基于模型 ID 前缀匹配
   - `downgrade(current_mode: str) -> str` — 逐级降级：unified → anchored → enriched
   - **绝不跳级**：unified 不能直接降到 enriched

2. **降级触发条件**
   - 循环检测第 3 次 → 当前会话降级一级
   - ingest 连续失败 2 次 → 当前会话降级一级
   - 降级是**会话级**的：一旦降级，本次会话内不再升回

3. **集成到 manager**
   - `window_return_mode: "adaptive"` 时，每次 `ingest_and_confirm` 前查询 AdaptiveModeSelector
   - 将实际使用的模式记录到日志（便于 benchmark 分析）

4. **配置扩展**
   ```python
   # config.py 新增
   adaptive_model_mode_overrides: dict[str, str] = {}  # 用户可覆盖模型-模式映射
   ```

### 验收标准
- [ ] GPT 模型自动选择 unified
- [ ] Kimi/Sonnet 自动选择 anchored
- [ ] DeepSeek 自动选择 enriched
- [ ] 循环检测触发后会话级降级
- [ ] 用户可通过配置覆盖默认映射
- [ ] 单元测试：模式选择、降级链、配置覆盖

---

## P3-b：跨模型 Benchmark（1.5 天）

### 目标
在所有支持模型上运行完整 benchmark，验证 adaptive 模式的跨模型表现。

### 涉及文件
- `bench/` 目录
- `tasks/feature_wurm/benchmark_phase3_report.md`（**新建**，最终报告）

### 具体任务

1. **Benchmark 矩阵**

   | 模型 | 测试模式 | 套件 |
   |------|---------|------|
   | GPT-5.3 | unified / anchored / enriched / OFF | 全套 |
   | Kimi K2.5 | anchored / enriched / OFF | 全套 |
   | Sonnet 4.5 | anchored / enriched / OFF | 全套 |
   | DeepSeek | enriched / OFF | 基础 + 数据分析 |

2. **核心验证假设**

   | 假设 | 验证方法 |
   |------|---------|
   | H2：anchored 在 Kimi/Sonnet 上减少验证性工具调用 | 对比 LLM 调用轮次 |
   | H5：管道格式数据能被各模型正确解析 | 数据引用准确性测试 |
   | H6：多范围序列化不导致混淆 | 非连续读取后引用旧范围准确性 |

3. **收集指标（每个模型 × 模式组合）**
   - Token 消耗（input/output/total）
   - LLM 调用轮次
   - 任务完成率
   - 数据引用准确性
   - 循环检测触发次数
   - focus_window 使用次数

4. **分析维度**
   - 模型间差异：哪些模型受益最大
   - 模式间差异：anchored vs unified 的 token 差异是否值得信任风险
   - 场景间差异：哪类场景收益最大（预期：超复杂 > 多轮 > 单轮）

### 验收标准
- [ ] 至少 3 个模型完成全套 benchmark
- [ ] adaptive 模式在每个模型上 ≥ 对应最优固定模式的 95% 表现
- [ ] 报告包含完整数据表格 + 结论建议

---

## P3-c：参数调优 + 最终报告（0.5 天）

### 目标
根据 benchmark 数据调优关键参数，输出上线建议。

### 涉及文件
- `excelmanus/config.py`（参数默认值调整）
- `excelmanus/window_perception/adaptive.py`（模型映射调整）
- `tasks/feature_wurm/R2_review.md`（**新建**，最终验收报告）

### 具体任务

1. **参数调优**

   | 参数 | 当前默认 | 调优依据 |
   |------|---------|---------|
   | `window_full_max_rows` | 25 | benchmark 中 Agent 数据利用率 |
   | 动态行数阈值 | 1→50, 2→25, 3+→15 | 多 Window 场景的 token 开销 |
   | `max_cached_rows` | 200 | 内存开销 vs 缓存命中率 |
   | RepeatDetector 阈值 | 3 次 | 误判率 vs 漏判率 |

2. **MODEL_MODE_MAP 更新**
   - 根据 P3-b benchmark 结果更新每个模型的最优模式
   - 记录决策依据

3. **最终验收报告** (`R2_review.md`)
   - WURM 整体收益总结
   - 各阶段交付物清单
   - 已知限制与后续优化方向（Idea A-D）
   - 上线建议：推荐 `adaptive` 为默认，`enriched` 为安全回退

### 验收标准
- [ ] 参数默认值已根据 benchmark 数据更新
- [ ] MODEL_MODE_MAP 覆盖所有已测试模型
- [ ] R2_review.md 完成

---

## P3 整体验收

### 自动化测试
- [ ] `pytest tests/` 全量通过
- [ ] adaptive 模式单元测试：模式选择、降级链、配置覆盖
- [ ] 全量回归：enriched 模式行为不变

### 性能验收（基于 benchmark 数据）
- [ ] GPT unified：vs OFF 多轮场景节省 35-40% tokens
- [ ] GPT unified：vs OFF 超复杂场景节省 50%+ tokens
- [ ] Kimi/Sonnet anchored：验证性工具调用减少 ≥ 30%
- [ ] 所有模型：任务完成率无显著下降

### 上线准备
- [ ] `window_return_mode` 默认值更新为 `"adaptive"`（或保持 `"enriched"` 观察期）
- [ ] 文档更新：README / 配置说明
- [ ] 监控指标：token 消耗、循环检测触发率、ingest 失败率

---

## 后续展望（不在 Phase 3 范围内）

以下记录在 index.md 第十五节，作为独立优化项评估：

| 编号 | 方向 | 预期收益 | 风险 |
|------|------|---------|------|
| Idea A | 惰性序列化 | 再省 40-60% Window token | 预判失败时 Agent 看不到数据 |
| Idea B | 数据指纹 | 更紧凑的确认格式 | 信息密度过高可能被忽略 |
| Idea C | Window 引用追踪 | 更精准的生命周期管理 | 实现复杂度 |
| Idea D | Delta 序列化 | 后续轮次大幅减少重复输出 | LLM 增量理解能力待验证 |
