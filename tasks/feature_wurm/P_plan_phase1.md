# WURM Phase 1：anchored 模式（最小风险验证）

> **预计工期**：~5 天
> **目标**：实现 WURM 数据容器核心 + anchored 确认模式，`enriched` 仍为默认，可切换验证
> **前置依赖**：无（当前架构零破坏性改动）

---

## 交付物

- `window_return_mode: "anchored"` 可用，GPT 上初步 benchmark 验证通过
- `enriched` 仍为默认值，100% 向后兼容
- ingest 失败时原子回退到 enriched，零数据丢失风险

---

## P1-a：WindowState 模型扩展（1 天）

### 目标
将 WindowState 从"元数据叠加层"升级为"数据容器"，新增缓冲区、schema、生命周期字段。

### 涉及文件
- `excelmanus/window_perception/models.py`（主改动）

### 具体任务

1. **新增 `DetailLevel` 枚举**
   ```python
   class DetailLevel(str, Enum):
       FULL = "full"
       SUMMARY = "summary"
       ICON = "icon"
       NONE = "none"
   ```

2. **新增 `ColumnDef` 数据类**
   ```python
   @dataclass
   class ColumnDef:
       name: str
       inferred_type: str = "unknown"  # "number" | "date" | "text" | "unknown"
   ```

3. **新增 `CachedRange` 数据类**
   ```python
   @dataclass
   class CachedRange:
       range_ref: str
       rows: list[dict]
       is_current_viewport: bool = False
       added_at_iteration: int = 0
   ```

4. **新增 `OpEntry` 和 `ChangeRecord` 数据类**
   - 参照 index.md 第 137-160 行定义

5. **扩展 `WindowState`** — 新增以下字段（均带默认值，不破坏现有代码）：
   - `columns: list[ColumnDef]` — schema 层
   - `data_buffer: list[dict]` — 行级数据缓冲区
   - `cached_ranges: list[CachedRange]` — 非连续范围支持
   - `viewport_range: str` — 当前序列化输出范围
   - `max_cached_rows: int = 200`
   - `detail_level: DetailLevel = DetailLevel.FULL`
   - `stale_hint: str | None = None`
   - `filter_state: dict | None = None`
   - `unfiltered_buffer: list[dict] | None = None`
   - `operation_history: list[OpEntry]`
   - `max_history_entries: int = 20`
   - `change_log: list[ChangeRecord]`
   - `max_change_records: int = 5`
   - `current_iteration: int = 0`

6. **扩展 `PerceptionBudget`** — 新增配置项：
   - `window_full_max_rows: int = 25`
   - `window_full_total_budget_tokens: int = 500`
   - `window_data_buffer_max_rows: int = 200`

### 验收标准
- [ ] 所有现有测试通过（新字段均有默认值，零破坏）
- [ ] 新数据类可正常实例化，序列化无异常
- [ ] `pytest tests/test_window_perception*.py` 全绿

---

## P1-b：ingest_and_confirm 核心路径（1.5 天）

### 目标
实现工具返回数据 → Window 缓冲区的写入路径，含原子性保障和 enriched 回退。

### 涉及文件
- `excelmanus/window_perception/manager.py`（主改动）
- `excelmanus/window_perception/ingest.py`（**新建**，数据提取 + 合并逻辑）

### 具体任务

1. **新建 `ingest.py`** — 数据提取与合并模块
   - `extract_data_rows(result_json) -> list[dict]` — 从工具返回 JSON 提取行级数据
   - `extract_columns(result_json, rows) -> list[ColumnDef]` — 推断列定义
   - `ingest_read_result(window, new_range, new_rows)` — 连续/非连续范围合并（index.md 第 275-318 行）
   - `ingest_write_result(window, target_range, written_values)` — 写入后原地更新 + stale 标记
   - `ingest_filter_result(window, filter_condition, filtered_rows)` — 筛选写入 + 快照保存
   - 辅助函数：`is_adjacent_or_overlapping()`、`union_range()`、`deduplicated_merge()`

2. **在 `manager.py` 中新增 `ingest_and_confirm()` 方法**
   ```python
   def ingest_and_confirm(self, tool_name, arguments, result_text, success) -> str:
       """WURM 核心入口：数据写入 Window + 生成确认文本。失败时原子回退到 enriched。"""
   ```
   - 成功路径：提取数据 → 合并到 Window → 生成 anchored 确认
   - 失败路径：catch 所有异常 → 回退到 `enrich_tool_result()`（当前逻辑）
   - 记录 `OpEntry` 和 `ChangeRecord`

3. **实现 `generate_confirmation()` — anchored 格式**
   ```
   ✅ read_excel 执行成功 → 150行×5列
     数据已写入窗口[W3]，请在系统上下文「数据窗口」区域查看完整内容。
     首行预览: 2024-01-01 | A | 100 | 50 | 5000
   ```
   - 约 50-60 tokens，含因果指引 + 首行预览 + 维度信息

4. **缓冲区行数上限控制** — 按范围块 LRU 淘汰（非单行），当前视口必须完整保留

### 验收标准
- [ ] `ingest_and_confirm` 对 read_excel 返回可正确提取数据并写入 Window
- [ ] 连续范围自动合并，非连续范围创建新 CachedRange
- [ ] write_cells 后 buffer 内单元格原地更新 + stale_hint 设置
- [ ] JSON 解析异常时 100% 回退到 enriched，无数据丢失
- [ ] 单元测试覆盖：正常 ingest、合并去重、异常回退、缓冲区溢出淘汰

---

## P1-c：FULL 序列化渲染（1.5 天）

### 目标
新增基于 data_buffer 的 FULL 模式渲染——管道分隔格式、多范围输出、stale 提示、动态行数分配。

### 涉及文件
- `excelmanus/window_perception/renderer.py`（主改动）
- `excelmanus/window_perception/budget.py`（动态行数分配）

### 具体任务

1. **新增 `render_window_wurm_full()` 函数**
   - 管道分隔格式渲染（列头在 schema 行，数据行不重复键名）
   - 单范围 vs 多范围场景（index.md 第 364-398 行）
   - stale_hint 提示输出
   - 变更摘要头（`📝 最近: ...`）
   - 新鲜度感知渲染：本轮新增行完整输出，旧行首尾+省略
   - 操作链输出（可选，~15 tokens）

2. **动态行数分配**（`budget.py` 扩展）
   - 根据 ACTIVE Window 数量动态调整每个 Window 的 max_rows
   - 1 个 ACTIVE → 50 行；2 个 → 25 行；3+ → 15 行
   - 总预算恒定 ~500 tokens

3. **SUMMARY / ICON 模式调整**
   - SUMMARY：复用现有 `render_window_background()`，微调格式对齐 WURM 定义
   - ICON：复用现有 `render_window_minimized()`，微调格式

4. **序列化入口分支**
   - `render_window_keep()` 中根据 `window.detail_level` 分支：
     - `FULL` + 有 `data_buffer` → `render_window_wurm_full()`
     - 其他 → 现有逻辑（兼容）

### 验收标准
- [ ] FULL 渲染输出格式正确（管道分隔、schema 行、多范围块标记）
- [ ] stale_hint 正确出现在 Window 头部
- [ ] 动态行数分配：2 个 ACTIVE Window 时每个 ≤25 行
- [ ] 变更摘要头正确显示最近操作
- [ ] 新鲜度感知：旧数据被压缩为首尾行+省略
- [ ] 无 data_buffer 时退化为现有渲染逻辑

---

## P1-d：engine 集成 + 配置开关（1 天）

### 目标
在 engine 层接入 WURM 路径，通过 `window_return_mode` 配置切换。

### 涉及文件
- `excelmanus/engine.py`（分支逻辑）
- `excelmanus/config.py`（新增配置项）
- `excelmanus/window_perception/manager.py`（system prompt 注入位置调整）

### 具体任务

1. **`config.py` 新增配置项**
   ```python
   window_return_mode: str = "enriched"  # "unified" | "anchored" | "enriched" | "adaptive"
   window_full_max_rows: int = 25
   window_full_total_budget_tokens: int = 500
   window_data_buffer_max_rows: int = 200
   ```

2. **`engine.py` 工具结果处理分支**
   - `_enrich_tool_result_with_window_perception()` 改为模式分支入口：
     - `"enriched"` → 现有逻辑（零改动）
     - `"anchored"` → `ingest_and_confirm()` → anchored 确认文本
   - 失败时自动回退到 enriched

3. **Window 注入位置调整**
   - 当前：`window_perception_context` 在 skill_contexts **之前**
   - WURM：调整为 system prompt **最后**（注意力高峰位置）
   - 仅在 `window_return_mode != "enriched"` 时调整位置

4. **system prompt Window 区域标题调整**
   - 标题从"窗口感知上下文"改为"数据窗口"
   - 添加因果指引文案（index.md 第 434-448 行）
   - 仅在非 enriched 模式下生效

### 验收标准
- [ ] `window_return_mode: "enriched"` 时行为与当前完全一致
- [ ] `window_return_mode: "anchored"` 时 tool_result 为 anchored 确认格式（~60 tokens）
- [ ] Window 数据在 system prompt 末尾正确渲染
- [ ] 配置可通过 config 文件 / 环境变量切换
- [ ] 端到端手动测试：GPT 模型下 anchored 模式可正常完成"读取前10行"任务

---

## P1 整体验收

### 自动化测试
- [ ] `pytest tests/` 全量通过
- [ ] 新增单元测试覆盖率 > 85%（ingest、合并、渲染、回退路径）

### Benchmark 验证
- [ ] GPT 模型 benchmark：anchored vs enriched vs OFF
- [ ] 验证假设 H1：anchored 在 GPT 上比 HYBRID(flash) 进一步节省 15-20% tokens
- [ ] 验证假设 H4：单轮简单场景无负面影响
- [ ] 验证假设 H5：管道格式数据能被 Agent 正确解析
- [ ] 验证假设 H7：ingest 原子性保障不产生数据丢失（单元测试）

### 开放问题（P1 期间决策）
- Q1：data_buffer 格式 → 倾向行级 dict，P1-b 开始时确认
