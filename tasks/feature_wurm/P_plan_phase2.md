# WURM Phase 2：unified 模式 + 安全阀

> **预计工期**：~3 天
> **前置依赖**：Phase 1 完成（anchored 模式可用 + GPT benchmark 初步通过）
> **目标**：tool_result 彻底移除数据 + 循环检测安全阀 + focus_window 工具 + GPT benchmark 验证

---

## 交付物

- `window_return_mode: "unified"` 可用，tool_result 仅含操作确认（~30 tokens）
- 循环检测安全阀：防止 Agent 重复读取同一范围
- `focus_window` 工具：视口切换、清除筛选、扩展视口、恢复挂起窗口
- GPT benchmark 对比报告：anchored vs unified vs enriched vs OFF

---

## P2-a：unified 模式实现（0.5 天）

### 目标
在 anchored 基础上，tool_result 彻底移除首行预览和额外提示，仅保留纯操作确认。

### 涉及文件
- `excelmanus/window_perception/manager.py`（`generate_confirmation` 新增 unified 格式）
- `excelmanus/engine.py`（分支逻辑扩展）

### 具体任务

1. **`generate_confirmation()` 新增 unified 格式**
   ```
   ✅ read_excel → 150行×5列 → 窗口[W3]
   ```
   - 约 30 tokens，无首行预览，无因果指引
   - 适用于 GPT 等高信任模型

2. **engine 分支扩展**
   - `"unified"` → `ingest_and_confirm()` → unified 确认文本
   - 复用 P1 的 ingest 路径，仅确认文本格式不同

### 验收标准
- [ ] unified 模式下 tool_result ≤ 40 tokens
- [ ] 数据仅在 system prompt Window 区域可见
- [ ] GPT 模型下可正常完成基础读取任务

---

## P2-b：循环检测安全阀（1 天）

### 目标
检测并缓解 Agent 对同一数据的重复读取，三元组粒度，逐级降级。

### 涉及文件
- `excelmanus/window_perception/manager.py`（循环检测状态）
- `excelmanus/window_perception/repeat_detector.py`（**新建**）

### 具体任务

1. **新建 `repeat_detector.py`**

   ```python
   @dataclass
   class RepeatDetector:
       """基于 (file, sheet, range) 三元组检测重复读取。"""
       _counter: dict[tuple[str, str, str], int]
       _last_write_targets: set[tuple[str, str]]  # write 后重置相关计数
   ```

   - `record_read(file, sheet, range) -> int` — 返回当前计数
   - `record_write(file, sheet)` — 重置该 (file, sheet) 下所有 range 的计数
   - 三元组粒度：避免误判合理的范围切换（A1:E25 和 A50:E75 是不同三元组）

2. **降级链实现**

   | 次数 | 处理 |
   |------|------|
   | 第 1 次 | 正常 ingest + 确认 |
   | 第 2 次 | tool_result 附加强提示：`⚠️ 此数据已在窗口 W3 中` |
   | 第 3 次 | 自动退化为 enriched 模式（完整数据在 tool_result 中） |

3. **write 重置逻辑**
   - 两次相同读取之间如果有 write 操作，计数器重置为 0
   - Agent 写入后重新读取验证是合理行为

4. **集成到 `ingest_and_confirm()`**
   - 在 ingest 入口处调用 RepeatDetector
   - 根据计数决定确认格式 / 是否降级

### 验收标准
- [ ] 连续 3 次读取同一 (file, sheet, range) 时第 3 次自动回退到 enriched
- [ ] 读取不同 range 不触发循环检测
- [ ] write 后重读同一 range 计数器已重置
- [ ] 单元测试覆盖：正常计数、write 重置、不同 range 独立计数

---

## P2-c：focus_window 工具（1 天）

### 目标
提供窗口操作工具，让 Agent 可以在不调用 MCP 工具的情况下切换视口、清除筛选、恢复挂起窗口。

### 涉及文件
- `excelmanus/window_perception/focus.py`（**新建**，工具逻辑）
- `excelmanus/tools/`（注册工具定义）
- `excelmanus/window_perception/manager.py`（对接 focus 操作）

### 具体任务

1. **工具定义（通用工具，方案 A）**
   ```python
   focus_window(
       window_id: str,          # "W3"
       action: str,             # "scroll" | "clear_filter" | "expand" | "restore"
       range: str | None,       # action=scroll 时必填
       rows: int | None,        # action=expand 时，追加行数
   )
   ```

2. **各 action 实现**

   | action | 效果 | 是否调用 MCP 工具 |
   |--------|------|------------------|
   | `scroll` | 切换 viewport 到指定 range（从 cached_ranges 中查找） | 否（缓存命中）/ 是（缓存未命中时需 read_excel） |
   | `clear_filter` | 从 unfiltered_buffer 恢复，清除 filter_state | 否 |
   | `expand` | 在当前视口基础上扩展 N 行 | 是（需 read_excel 追加数据） |
   | `restore` | 恢复挂起窗口：detail_level → FULL，state → ACTIVE | 否（内存缓存直接恢复） |

3. **缓存命中判断**
   - scroll 到已缓存的 range → 零工具调用，直接切换 viewport
   - scroll 到未缓存的 range → 自动触发 read_excel（对 Agent 透明）

4. **工具注册**
   - 仅在 `window_return_mode != "enriched"` 时注册（enriched 模式下不需要）
   - 工具描述清晰说明各 action 用途

### 验收标准
- [ ] `focus_window(W3, "scroll", "A50:E75")` 缓存命中时零 MCP 调用
- [ ] `focus_window(W3, "clear_filter")` 正确恢复筛选前数据
- [ ] `focus_window(W3, "restore")` 恢复挂起窗口到 FULL
- [ ] 工具在 enriched 模式下不出现在工具列表中
- [ ] 单元测试覆盖各 action 路径

---

## P2-d：Benchmark 对比验证（0.5 天）

### 目标
在 GPT 模型上对比四种模式的 token 消耗和任务完成质量。

### 涉及文件
- `bench/` 目录下的 benchmark 套件

### 具体任务

1. **运行完整 benchmark 套件**
   - 模式：`anchored` / `unified` / `enriched` / `OFF`
   - 模型：GPT（主要验证对象）
   - 套件：基础读取、数据分析、多轮复杂、超复杂

2. **收集指标**
   - Token 消耗（input/output/total）
   - LLM 调用轮次
   - 任务完成率
   - 数据引用准确性（Agent 是否正确引用 Window 中的数据）

3. **验证假设**
   - H1：anchored 比 HYBRID(flash) 节省 15-20% tokens
   - H3：超复杂场景收益最大（vs OFF 节省 50%+）
   - H6：多范围序列化不导致 Agent 混淆

4. **输出报告**
   - `tasks/feature_wurm/benchmark_phase2_report.md`

### 验收标准
- [ ] 四种模式 benchmark 数据完整
- [ ] unified 在 GPT 上 token 消耗显著低于 enriched
- [ ] 任务完成率无明显下降（unified vs enriched）

---

## P2 整体验收

### 自动化测试
- [ ] `pytest tests/` 全量通过
- [ ] 新增单元测试：RepeatDetector、focus_window 各 action

### 集成测试
- [ ] enriched 模式行为不变（回归）
- [ ] anchored → unified 切换无异常
- [ ] 循环检测 + focus_window 联动：Agent 被提示后使用 focus_window 而非重复 read_excel

### 开放问题（P2 期间决策）
- Q2：子代理 Window 共享 → P2 期间确认只读共享方案是否足够
- Q3：focus_window 通用工具 vs 专用工具 → 倾向通用（方案 A），P2-c 实施时最终确认
