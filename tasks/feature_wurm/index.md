# Window Unified Return Model (WURM) — 设计方案

> **创建日期**：2026-02-15
> **状态**：设计阶段
> **关联**：Phase 2 Window Perception Benchmark

---

## 一、核心命题

**Window 从"工具结果的元数据叠加层"升级为"工具结果的统一数据容器"。**

工具不再向对话历史返回数据，而是将数据写入 Window；Agent 通过读取 Window 获取所有信息。对话历史只保留操作日志，不保留数据本身。

### 范式转换

```
当前范式：Agent → 调用工具 → 工具返回数据（写入对话历史）→ Agent 看到数据
                               ↑
                          数据在对话历史中，append-only，不可压缩

WURM 范式：Agent → 调用工具 → 数据写入 Window → Agent 读取 Window
                               ↑
                          数据在 Window 中，可增量更新、可降级压缩
```

### 人类操作 Excel 的类比

```
1. 打开文件夹 → 看到文件列表           → FolderWindow 创建
2. 打开 xlsx  → 看到默认区域            → SheetWindow 创建（初始视口）
3. 数据不够   → 翻页/滚动              → SheetWindow.data_buffer 增量扩展
4. 切换 Sheet → 看到另一个 Sheet        → 新 SheetWindow 创建
5. 查看样式   → 格式信息出现            → SheetWindow.metadata 更新
```

Agent 的每次工具调用等价于"操作电脑"，Window 是操作后的认知快照。

---

## 二、当前架构的核心问题

### 2.1 结构性矛盾

当前窗口感知存在一个**双信道**问题：

| 信道 | 载体 | 特性 | Agent 信任度 |
|------|------|------|-------------|
| 工具返回值 | 对话历史中的 tool_result | 不可变、累积占位、800+ tokens/次 | **高**（第一手数据） |
| 窗口感知 | system prompt 注入 | 可降级、预算控制、元数据为主 | **低**（"注入的上下文"） |

结果：
- 数据在两个地方各存一份（冗余）
- Agent（尤其非 GPT 模型）不信任窗口，反复调工具"验证"
- 对话历史中的 tool_result 线性累积，不可压缩

### 2.2 Benchmark 数据佐证

| 模型 | 路由预览利用 | WP 收益 | 根因 |
|------|------------|--------|------|
| GPT-5.3 | 直接采信 | -15%~-34% | 信任 system prompt 数据 |
| Kimi K2.5 | 不信任，需验证 | ≈0% | 必须自己调工具确认 |
| Sonnet 4.5 | 不信任 | 无效/负面 | 窗口额外上下文产生干扰 |
| DeepSeek | 不信任 | 负面 | 多余验证 + 子代理超时 |

非 GPT 模型不信任窗口的根因：**存在替代数据源**（tool_result 中有完整数据），窗口只是可选的辅助信息。

---

## 三、WURM 数据模型

### 3.1 Window 不是 GUI 概念

Window 是纯粹的数据模型——一个有 schema、有数据缓冲区、有生命周期的结构化容器。与 Excel 的实际界面、截图、GUI 窗口无关。

### 3.2 模型定义

```python
@dataclass
class Window:
    """工具返回值的统一数据容器。"""

    # ── 标识层 ──
    id: str                          # "W1", "W2", ...
    type: WindowType                 # FOLDER | SHEET

    # ── 数据源定位 ──
    source_file: str | None          # 文件路径
    source_sheet: str | None         # Sheet名
    sheet_tabs: list[str]            # 同文件所有Sheet标签

    # ── 结构层（schema）──
    columns: list[ColumnDef]         # 列名 + 推断类型
    total_rows: int                  # 数据源总行数
    total_cols: int                  # 数据源总列数

    # ── 数据层（可变缓冲区）──
    # 视口（viewport）：当前序列化输出的范围，受 window_full_max_rows 限制
    # 缓冲区（cache）：内存中缓存的全部范围，受 max_cached_rows 限制
    # 关系：viewport_range ⊆ cached_ranges ⊆ total data
    viewport_range: str              # 当前序列化输出范围，如"A1:E25"
    cached_ranges: list[CachedRange] # 所有已缓存的范围块（支持非连续）
    data_buffer: list[dict]          # 行级数据（内存中始终保持完整）
    max_cached_rows: int = 200       # 缓冲区行数上限
    stale_hint: str | None = None    # 写入后标记，提示依赖公式可能已变化

    # ── 筛选层 ──
    filter_state: dict | None        # 当前筛选条件
    unfiltered_buffer: list[dict] | None  # 筛选前的原始数据快照（用于清除筛选时恢复）

    # ── 元数据层 ──
    freeze_panes: str | None
    style_summary: str
    status_bar: dict                 # SUM/COUNT/AVG
    scroll_position: dict            # 纵向/横向位置百分比
    merged_ranges: list[str]
    conditional_formats: list

    # ── 操作历史层（纯记录）──
    operation_history: list[OpEntry] # 该 Window 经历过的所有操作（只记录操作+参数）
    max_history_entries: int = 20    # 上限，超出 FIFO 淘汰

    # ── 变更跟踪层（操作溯源）──
    change_log: list[ChangeRecord]   # 最近 N 次操作的变更记录（含行级影响分析）
    max_change_records: int = 5      # 只保留最近 5 条
    current_iteration: int = 0       # 当前 engine 迭代序号（用于判断"本轮"）

    # ── 生命周期层 ──
    state: LifecycleState            # ACTIVE | BACKGROUND | SUSPENDED | TERMINATED
    detail_level: DetailLevel        # FULL | SUMMARY | ICON | NONE
    idle_turns: int
    last_access_seq: int
    created_at_turn: int
    last_tool_call: str              # 最后更新此Window的工具描述

@dataclass
class OpEntry:
    """单次操作记录（纯记录，只存操作名和参数）。"""
    tool_name: str                   # 工具名，如 "read_excel", "write_cells", "filter_data"
    arguments: dict                  # 原始参数快照，如 {"file": "sales.xlsx", "range": "A1:E25"}
    iteration: int                   # 发生在哪个 engine 迭代
    success: bool = True             # 操作是否成功

@dataclass
class ChangeRecord:
    """单次操作的变更记录（轻量溯源，含行级影响分析）。"""
    operation: str                   # "read" | "write" | "filter" | "format"
    tool_summary: str                # 简述，如 "read_excel(A11:E25)" 或 "write_cells(B3, 999)"
    affected_range: str              # 受影响范围，如 "A11:E25" 或 "B3"
    change_type: str                 # "added" | "modified" | "filtered" | "enriched"
    iteration: int                   # 发生在哪个 engine 迭代
    affected_row_indices: list[int]  # 受影响的 buffer 行索引（用于行级标记）

@dataclass
class CachedRange:
    """单个缓存范围块。"""
    range_ref: str                   # 如 "A1:E25"
    rows: list[dict]                 # 该范围的行级数据
    is_current_viewport: bool        # 是否为当前活跃视口
    added_at_iteration: int = 0      # 该范围块加入时的迭代序号
```

### 3.3 类型体系

| WindowType | 数据源 | 创建时机 | 说明 |
|------------|--------|---------|------|
| **FOLDER** | 目录扫描结果 | Turn 1 自动 / list_files | Agent "睁眼" 看到的文件列表 |
| **SHEET** | Excel Sheet 数据 | read_excel / filter_data | 核心，每个 (file, sheet) 一个 |

### 3.4 DetailLevel 与序列化

降级不是 UI 效果，是**数据精度的有损压缩**：

| DetailLevel | 对应 State | 序列化内容 | Token 估算 |
|-------------|-----------|-----------|-----------|
| **FULL** | ACTIVE | schema + 数据行 + metadata | 200-500 |
| **SUMMARY** | BACKGROUND | schema + 维度统计（无数据行） | 40-80 |
| **ICON** | SUSPENDED | source + dimensions 一行 | 15-25 |
| **NONE** | TERMINATED | 不序列化，回收 | 0 |

**关键**：降级只影响序列化输出。`data_buffer` 在内存中始终保持完整（直到 TERMINATED 后回收）。恢复时无需重新调工具——提升 `detail_level` 即可零成本恢复。

---

## 四、工具调用 = Window 操作

### 4.1 工具与 Window 的映射

| 工具调用 | Window 操作 | 效果 |
|----------|-----------|------|
| `read_excel(file, range)` 首次 | **CREATE** | 新建 SheetWindow，写入 schema + 初始数据 |
| `read_excel(file, range)` 连续范围 | **EXTEND** | 增量追加 data_buffer |
| `read_excel(file, range)` 跳跃范围 | **REPLACE** | 替换视口数据（旧数据在内存缓存） |
| `read_excel(file, sheet=另一个)` | **CREATE** | 新建独立 Window |
| `filter_data(...)` | **MUTATE** | 更新 data_buffer 为筛选子集 + 记录 filter_state |
| `write_cells(...)` | **MUTATE** | 原地更新缓冲区中对应单元格 |
| `get_cell_format(...)` | **ENRICH** | 更新 Window 的 metadata 子树 |
| `list_files()` | **CREATE/UPDATE** | 更新 FolderWindow |

### 4.2 数据流变化

```
当前：
  工具执行 → result_str（完整JSON, ~800 tokens）
    → hard_cap 截断
    → enrich（追加感知块）
    → 写入对话历史 ← 数据永久占位

WURM：
  工具执行 → result_str（完整JSON）
    → WindowManager.ingest（数据写入 Window）
    → WindowManager.generate_confirmation（生成操作确认, ~50-60 tokens）
    → 确认文本写入对话历史 ← 极简占位
    → 下一次 LLM 调用前：重建 system prompt，Window 携带完整数据
```

**工具层零改动**。工具仍然返回完整 JSON。变化完全在 engine 层的拦截点。

### 4.3 Ingest 原子性保障

ingest 是 WURM 的核心路径，**必须保证原子性**：要么成功写入 Window 并返回确认，要么完整保留原始 tool_result。**绝不允许两边都没数据。**

```python
def ingest_and_confirm(self, tool_name, arguments, result_text, success):
    """WURM 核心入口：ingest + 确认生成，失败时原子回退。"""
    try:
        buffer_rows = self._extract_full_data(result_text)
        if not buffer_rows:
            raise IngestError("无法从工具返回中提取数据行")
        window = self._merge_into_window(tool_name, arguments, buffer_rows)
        return self._generate_confirmation(window)
    except Exception:
        # 原子回退：ingest 失败 → 退化为 enriched，保留完整 result_text
        logger.warning("ingest 失败，回退为 enriched 模式")
        return self._enriched_fallback(tool_name, arguments, result_text, success)
```

这是 WURM 的**安全底线**：任何解析异常都不会导致数据丢失。

### 4.4 操作确认格式

默认采用 `anchored` 格式（确认 + 首行预览 + 因果指引）：

```
✅ read_excel 执行成功 → 150行×5列
  数据已写入窗口[W3]，请在系统上下文「数据窗口」区域查看完整内容。
  首行预览: 2024-01-01 | A | 100 | 50 | 5000
```

**三个设计要点**：
- **因果指引**：明确告诉 Agent 去哪里找数据（“系统上下文数据窗口区域”）
- **首行预览**：作为因果键点，帮 Agent 建立“工具返回的数据 = Window 里的数据”关联
- **维度信息**：150行×5列，让 Agent 知道数据规模

约 50-60 tokens，vs 当前 800+ tokens，vs 统一模式的 ~30 tokens。额外 20 tokens 换来显著的模型兼容性提升。

---

## 五、增量写入机制

### 5.1 合并规则

| 场景 | 条件 | 行为 |
|------|------|------|
| **连续/重叠范围** | 同 file + 同 sheet + 范围相邻或重叠 | 合并去重，扩展当前 cached_range |
| **非连续范围** | 同 file + 同 sheet + 范围有 gap | 新增 cached_range，更新 viewport 指向新范围 |
| **筛选** | filter_data 调用 | 保存 unfiltered_buffer 快照，buffer 替换为子集，记录 filter_state |
| **清除筛选** | focus_window(action="clear_filter") | 从 unfiltered_buffer 恢复，清除 filter_state |
| **写入（范围内）** | write_cells 且在缓冲区范围内 | 原地更新单元格 + 设置 stale_hint |
| **写入（范围外）** | write_cells 但超出缓冲区范围 | 设置 stale_hint，不尝试模拟更新 |

### 5.2 合并算法伪代码

```python
def ingest_read_result(window, new_range, new_rows):
    # 查找是否与已有 cached_range 相邻/重叠
    merged = False
    for cr in window.cached_ranges:
        if is_adjacent_or_overlapping(cr.range_ref, new_range):
            cr.range_ref = union(cr.range_ref, new_range)
            cr.rows = deduplicated_merge(cr.rows, new_rows)
            cr.is_current_viewport = True
            merged = True
            break
    
    if not merged:
        # 非连续范围：新增 cached_range，保留旧范围
        for cr in window.cached_ranges:
            cr.is_current_viewport = False
        window.cached_ranges.append(CachedRange(
            range_ref=new_range,
            rows=new_rows,
            is_current_viewport=True,
        ))
    
    # 更新 viewport_range 指向当前活跃范围
    window.viewport_range = new_range
    
    # 缓冲区总行数上限控制（按范围块 LRU 淘汰，不按单行）
    total_rows = sum(len(cr.rows) for cr in window.cached_ranges)
    while total_rows > window.max_cached_rows and len(window.cached_ranges) > 1:
        oldest = min(window.cached_ranges, key=lambda x: not x.is_current_viewport)
        if oldest.is_current_viewport:
            break
        window.cached_ranges.remove(oldest)
        total_rows -= len(oldest.rows)
    
    # 重建 data_buffer（所有 cached_ranges 的行合并）
    window.data_buffer = []
    for cr in window.cached_ranges:
        window.data_buffer.extend(cr.rows)
    
    # 写入后的 stale 标记在新读取时自动清除
    window.stale_hint = None
    window.state = ACTIVE
    window.detail_level = FULL
    window.idle_turns = 0
```

### 5.3 写入后的 stale 标记

`write_cells` 后，依赖该单元格的公式值可能已变化，但 buffer 中其他单元格不会自动更新。策略：**标记而非模拟**。

```python
def ingest_write_result(window, target_range, written_values):
    # 在缓冲区范围内：原地更新单元格值
    if is_within(target_range, window.cached_ranges):
        update_cells_in_buffer(window.data_buffer, target_range, written_values)
    
    # 始终设置 stale 提示（不尝试模拟公式重算）
    window.stale_hint = f"{target_range} 已修改，依赖此区域的公式值可能已变化"
```

stale_hint 会在序列化时输出，让 Agent 自行决定是否重新读取。下次 read_excel 时自动清除。

### 5.4 筛选与恢复

```python
def ingest_filter_result(window, filter_condition, filtered_rows):
    # 保存筛选前快照（仅在首次筛选时）
    if window.unfiltered_buffer is None:
        window.unfiltered_buffer = list(window.data_buffer)
    
    window.data_buffer = filtered_rows
    window.filter_state = filter_condition

def clear_filter(window):
    # 从快照恢复，零工具调用
    if window.unfiltered_buffer is not None:
        window.data_buffer = window.unfiltered_buffer
        window.unfiltered_buffer = None
    window.filter_state = None
```

清除筛选通过 `focus_window(action="clear_filter")` 触发，不需要重新 read_excel。

---

## 六、序列化策略

### 6.1 FULL 模式（ACTIVE 窗口）

**单范围场景**（连续读取）：
```
[W3 · sales_data.xlsx / Sheet1]
Tabs: [▶Sheet1] [Sheet2] [汇总]
范围: 150行×5列 | 视口: A1:E25
列: [日期, 产品, 数量, 单价, 金额]
数据(A1:E25):
  2024-01-01 | A | 100 | 50 | 5000
  2024-01-01 | B | 200 | 30 | 6000
  2024-01-02 | A | 150 | 50 | 7500
  ... (共25行)
统计: SUM(金额)=284,000 | COUNT=150 | AVG=1,893
```

**多范围场景**（非连续读取时，所有已缓存范围均序列化）：
```
[W3 · sales_data.xlsx / Sheet1]
Tabs: [▶Sheet1] [Sheet2] [汇总]
范围: 150行×5列
列: [日期, 产品, 数量, 单价, 金额]
── 缓存范围 A1:E25 (25行) ──
  2024-01-01 | A | 100 | 50 | 5000
  2024-01-01 | B | 200 | 30 | 6000
  ... (共25行)
── 缓存范围 A50:E75 (26行) [当前视口] ──
  2024-03-15 | C | 80 | 45 | 3600
  ... (共26行)
统计: SUM(金额)=284,000 | COUNT=150 | AVG=1,893
```

**写入后带 stale 提示**：
```
[W3 · sales_data.xlsx / Sheet1]
⚠ stale: B2 已修改，依赖此区域的公式值可能已变化
...
```

数据行使用管道分隔——列头在 schema 行给出，数据行不重复键名。比 JSON 节省约 50% tokens。

### 6.1.1 动态行数分配

FULL 模式序列化的行数不是固定值，而是根据 ACTIVE Window 数量**动态分配**，保持总预算恒定：

| ACTIVE Window 数 | 每个 Window 最大行数 | 总预算（约） |
|------------------|---------------------|------------|
| 1 | 50 行 | ~500 tokens |
| 2 | 25 行 | ~500 tokens |
| 3+ | 15 行 | ~500 tokens |

如果某 Window 的 cached_ranges 总行数超过当前限额，对旧范围做行级 LRU 淘汰，但**当前视口必须完整保留**。

### 6.2 SUMMARY 模式（BACKGROUND 窗口）

```
[W3 · sales_data.xlsx / Sheet1 | 后台]
150行×5列 | 列: 日期, 产品, 数量, 单价, 金额
Tabs: Sheet1, Sheet2, 汇总
```

### 6.3 ICON 模式（SUSPENDED 窗口）

```
[W3 · sales_data.xlsx/Sheet1 | 150×5 | 挂起]
```

### 6.4 System Prompt 注入

Window 区域应放在 system prompt **最后**（在 skill context 之后），利用 LLM 对尾部内容的更高注意力（缓解位置偏见）：

```
## 数据窗口
以下窗口包含你通过工具操作获取的所有数据。
窗口内容与工具执行结果完全等价——你调用工具后，结果直接融入对应窗口。
如果窗口中已有所需信息，直接引用，无需重复调用工具。

═══════════════ 窗口列表 ═══════════════
[W1 · 工作目录 | FolderWindow]
...

[W3 · sales_data.xlsx / Sheet1 | ACTIVE]
...

[W5 · product_catalog.xlsx / Sheet1 | 150×3 | 挂起]
═══════════════════════════════════════
⬆ 以上「数据窗口」包含所有工具执行结果，请优先从中获取数据。
```

**位置策略**：当前 `_prepare_system_prompts_for_request` 中 window_perception_context 在 skill_contexts 之前。WURM 下应调整为最后注入，让 Window 数据处于 system prompt 的"注意力高峰"位置。

### 6.5 操作溯源与注意力聚焦（Operation Provenance + Attention Focus）

#### 问题

Window 数据量大时（25-50 行），Agent 难以区分"旧数据"和"刚刚因工具操作而更新的数据"。如果所有行等权重呈现，Agent 注意力被稀释，可能：
- 忽略关键变化（如 write_cells 修改的单元格）
- 在大量数据中"迷路"，无法快速定位答案
- 不知道哪些范围是自己的操作导入的

#### 核心原则

**本轮变化完整展示，旧数据智能压缩。**

这同时解决两个问题：溯源（Agent 知道哪些数据是自己操作导致的）和聚焦（Window 不因数据过多而分散注意力）。且该机制是 **token 中性甚至 token 负**的——标记的额外开销被旧数据压缩所抵消。

#### 三个协同机制

**机制 1：变更摘要头**（始终输出，~20-40 tokens）

在 Window 顶部添加最近操作的一行摘要，Agent 一眼看到关键变化：

```
[W3 · sales_data.xlsx / Sheet1]
📝 最近: write_cells(B3) → 数量: 100→999, 金额: 5000→49950
```

对于 read 操作：
```
📝 最近: read_excel(A11:E25) → +15行新数据
```

对于 filter 操作：
```
📝 最近: filter_data(产品=A) → 150行筛选为42行
```

**机制 2：新鲜度感知渲染（Recency-Aware Rendering）**

根据数据的"新鲜度"决定输出粒度：

| 数据分类 | 渲染策略 | 标记 |
|----------|---------|------|
| **本轮新增的行** | 完整输出 | 无特殊标记（默认完整） |
| **本轮修改的行** | 完整输出 + `*` 前缀 | `* 2024-01-02 \| A \| 999 \| ...  ← write(B3)` |
| **旧的未变化行** | 首尾行 + 省略 | `[首行] ... (8行省略, 未变化) ... [尾行]` |

完整示例（write_cells 后）：

```
[W3 · sales_data.xlsx / Sheet1]
📝 最近: write_cells(B3) → 数量: 100→999, 金额: 5000→49950
列: [日期, 产品, 数量, 单价, 金额]
── A1:E25 (25行) ──
  2024-01-01 | A | 100 | 50 | 5000
  ... (未变化, 省略1行)
* 2024-01-02 | A | 999 | 50 | 49950  ← write(B3)
  2024-01-02 | B | 150 | 30 | 4500
  ... (未变化, 省略20行)
  2024-02-15 | C | 80 | 45 | 3600
统计: SUM(金额)=284,000 | COUNT=150
```

完整示例（增量读取后，新旧范围并存）：

```
[W3 · sales_data.xlsx / Sheet1]
📝 最近: read_excel(A11:E25) → +15行新数据
列: [日期, 产品, 数量, 单价, 金额]
── A1:E10 (10行, 旧数据) ──
  [首行] 2024-01-01 | A | 100 | 50 | 5000
  ... (8行省略, 未变化)
  [尾行] 2024-01-10 | C | 90 | 45 | 4050
── A11:E25 (15行, 本轮新增) ──
  2024-01-11 | A | 120 | 50 | 6000
  2024-01-12 | B | 200 | 30 | 6000
  ... (完整输出15行)
统计: SUM(金额)=284,000 | COUNT=150
```

**机制 3：操作链**（可选，~15 tokens）

在 FULL 模式中附带 Window 的操作历史，让 Agent 理解数据是如何到达当前状态的：

```
操作链: read(A1:E10) → read(A11:E25) → write(B3,999)
```

#### Token 成本分析

| 组成 | 额外开销 | 节省 |
|------|---------|------|
| 变更摘要头 | +20~40 tokens | — |
| 行级标记（`*` + 来源） | +2 tokens/修改行 | — |
| 旧数据智能压缩（首尾行 + 省略） | — | **-50~100 tokens**（10行→2行） |
| 操作链 | +15 tokens | — |
| **净效果** | | **token 中性或略负**（数据量大时净节省） |

#### 实现要点

1. 每次 ingest 时记录 `ChangeRecord`，标记受影响的行索引
2. 序列化时，检查 `change_log` 中 `iteration == current_iteration` 的记录
3. 属于本轮变更的行：完整输出（+ `*` 标记 if modified）
4. 不属于本轮变更的行：首尾行 + 中间省略
5. `change_log` 滚动保留最近 5 条，超出自动淘汰

---

## 七、置信统一模型

### 7.1 核心机制

不是通过 prompt "说服" Agent 信任 Window，而是**消除替代数据源**：

```
tool_result 中不含数据
  → Agent 无法从对话历史获取数据
  → Window 是唯一数据来源
  → Agent 被迫使用 Window
```

### 7.2 当前 vs WURM 的 Agent 认知

```
当前（双信道）：
  Agent: "system prompt 说有150行5列... 但让我自己调工具验证一下"
  → 多浪费一轮，尤其 Kimi/Sonnet

WURM（单信道）：
  Agent: "工具说数据在 W3 里，system prompt 里 W3 确实有数据，直接用"
  → 无需验证，因为别无选择
```

### 7.3 安全阀：循环检测

基于 `(file, sheet, range)` **三元组**检测重复读取（而非 `(file, sheet)` 二元组，避免误判合理的范围切换）：

| 次数 | 处理 |
|------|------|
| 第 1 次 | 正常 ingest + 确认（含因果指引） |
| 第 2 次 | tool_result 附加强提示："⚠️ 此数据已在窗口 W3 中，与工具返回完全一致" |
| 第 3 次 | 自动退化为 enriched 模式（tool_result 携带完整数据） |

### 7.4 模型适配分级
| 模式 | tool_result 内容 | Token | 适用模型 |
|------|-----------------|-------|---------|
| `unified` | 纯操作确认 | ~30 | GPT-5.3（推荐默认） |
| `anchored` | 确认 + 首行预览 | ~60 | Kimi / Sonnet（过渡） |
| `enriched` | 完整数据 + 感知块 | ~800 | DS / 兜底（当前架构） |
配置项：`window_return_mode: "unified" | "anchored" | "enriched" | "adaptive"`

**重置条件**：如果两次相同读取之间有 write 操作（Agent 可能合理地需要验证写入效果），计数器重置为 0。


---

## 八、Token 成本分析

### 8.1 核心不对称性

对话历史中的 tool_result 是**累积计费**的（越早出现越贵，因为后续每次 LLM 调用都要重新发送）。

system prompt 中的 Window 通过降级是**动态压缩**的（不活跃 Window 逐渐缩小到 15 tokens）。

这个不对称性是 WURM token 效率的根本来源。

### 8.2 超复杂场景估算（6-7 轮，15 次 LLM 调用）

| 维度 | 当前 enriched | WURM unified |
|------|-------------|-------------|
| 历史中 tool_result | 5次 × 800 tok × 平均 10 次后续调用 = ~40,000 | 5次 × 40 tok × 10 = ~2,000 |
| System prompt Window 增量 | ~500 tok × 15 调用 = ~7,500 | ~800 tok × 15（含降级平均 ~500）= ~7,500 |
| **净 Window 相关 token** | **~47,500** | **~9,500** |
| **节省率** | — | **~80%**（Window 相关） |

### 8.3 预计总 Token 节省（vs OFF 基线）

| 场景 | 当前 HYBRID(flash) vs OFF | WURM unified vs OFF（预估） |
|------|--------------------------|---------------------------|
| 单轮简单 | ≈0% | ≈0%（无多轮累积） |
| 多轮 2-3 轮 | -23% | **-35%~-40%** |
| 超复杂 6-7 轮 | -34% | **-50%~-55%** |

### 8.4 大数据集场景的 token 膨胀风险

WURM 的 token 优势建立在 `window_full_max_rows` 限制上。如果用户需要查看大量数据行：

| 场景 | System prompt Window 开销/轮 | 风险 |
|------|----------------------------|------|
| 1 个 ACTIVE Window × 25 行 | ~300 tokens | ✅ 正常 |
| 1 个 ACTIVE Window × 200 行 | ~1,800 tokens | ⚠️ 较大 |
| 2 个 ACTIVE Window × 200 行 | ~3,600 tokens | ⚠️⚠️ 接近当前架构 |

**缓解措施**：动态行数分配（见 6.1.1）确保多 Window 场景下总预算恒定。即使单 Window 场景，200 行 × 15 次 LLM 调用 = 27,000 tokens，仍然远低于当前架构的 40,000+ tokens（因为数据只在 system prompt 中出现一份，而非对话历史中累积多份）。

---

## 九、冷启动与时序分析

### 9.1 冷启动不是问题

```
Turn 1:
  用户消息进入
  → 构建 messages（system prompt 无 Window / 仅 FolderWindow）
  → LLM 调用 #1 → 决定调用 read_excel
  → 执行 read_excel → 数据写入 W3
  → 重建 messages（system prompt 包含 W3 FULL 数据）  ← 每次 LLM 调用前重建
  → LLM 调用 #2 → 看到 W3 数据 + 操作确认 → 回答用户
```

engine 的 iterative 特性保证 Window 数据在同一轮就能被 Agent 看到。

### 9.2 并行工具调用

```
LLM 一次返回多个 tool_calls
  → engine 串行执行每个 tool call
  → 每个 tool call 的数据写入各自 Window
  → 所有 tool_results 一起返回
  → 重建 system prompt（包含所有更新后的 Window）
  → 发给 LLM
```

多个 tool call 更新不同 Window，system prompt 中都会反映。

---

## 十、风险与缓解

### 10.1 风险矩阵

| 风险 | 严重度 | 触发条件 | 缓解 |
|------|--------|---------|------|
| **🔴 ingest 失败导致数据黑洞** | **致命** | MCP 工具返回格式变化、JSON 解析失败 | ingest 原子性保障：失败时 fallback 到 enriched（见 4.3） |
| **🔴 Agent 不信任 Window，重复调工具** | 高 | 非 GPT 模型 | 循环检测（三元组）+ 自适应降级链 |
| **🟡 非连续范围读取信息丢失** | 中高 | Agent 先读 A1:E25 再读 A50:E75，需引用旧范围 | 多范围序列化：所有 cached_ranges 均输出（见 6.1） |
| **🟡 写入后公式级联不一致** | 中 | write_cells 后引用依赖公式的值 | stale_hint 标记（见 5.3），不模拟公式重算 |
| **🟡 序列化预算溢出** | 中 | 多 ACTIVE Window 撑爆 system prompt | 动态行数分配（见 6.1.1）+ 优先降级次要 Window 到 SUMMARY |
| **🟡 循环检测误判** | 中 | write 后 Agent 合理地重新读取验证 | 基于三元组检测 + write 后重置计数器 |
| Window 数据与文件实际状态不一致 | 中 | write 后未同步 | write 在缓冲区内则原地更新，范围外标记 stale |
| 降级后 Agent 需要已释放的数据 | 中 | 回顾挂起窗口 | 内存缓存 + focus_window 零成本恢复 |
| adaptive 模式判断错误 | 低 | 新模型出现，模型 ID 未匹配 | 降级链逐级降级，绝不跳级 |
| 增量合并数据冲突 | 低 | 两次读取间文件被外部修改 | 以最新一次为准覆盖 |
| TERMINATED 后 Agent 仍引用该 Window | 低 | 延迟回收 | TERMINATED 前确认无活跃引用；延迟清理 |

### 10.2 核心设计原则

**WURM 的所有新路径都必须有 enriched 降级兜底。** 任何异常状态都应 fallback 到当前已验证的架构。具体而言：
- ingest 失败 → enriched fallback（见 4.3）
- 循环检测第 3 次 → enriched（见 7.3）
- adaptive 降级链尽头 → enriched（见 7.4）
- 序列化预算溢出 → 降级为 SUMMARY/ICON，不做文本截断（截断会破坏数据完整性）

---

## 十一、与当前架构的集成路径

### 11.1 改动范围

| 组件 | 改动 |
|------|------|
| `models.py` | 新增 `CachedRange`；扩展 `WindowState`：新增 `columns`, `data_buffer`, `cached_ranges`, `viewport_range`, `detail_level`, `stale_hint`, `filter_state`, `unfiltered_buffer` |
| `manager.py` | 新增 `ingest_and_confirm`（含原子性保障）+ `generate_confirmation`；重构 `enrich_tool_result` 为模式分支入口 |
| `renderer.py` | 新增 FULL 数据渲染（管道分隔 + 多范围输出 + stale 提示）；调整 SUMMARY/ICON 格式 |
| `engine.py` | 工具结果处理分支：anchored/unified 走新流程，enriched 走旧流程；Window 注入位置调整为 system prompt 末尾 |
| `budget.py` | 新增动态行数分配逻辑（根据 ACTIVE Window 数量调整 max_rows） |
| **工具层** | **零改动** |
| **Advisor** | **零改动** |
| **生命周期管理** | **零改动**（复用现有 idle_turns → 降级逻辑） |

### 11.2 配置开关

```python
# 新增配置项
window_return_mode: str = "enriched"       # "unified" | "anchored" | "enriched" | "adaptive"
window_full_max_rows: int = 25             # FULL 模式单 Window 最大序列化行数（基准值，动态调整）
window_full_total_budget_tokens: int = 500 # 所有 ACTIVE Window 的 FULL 序列化总 token 预算
window_data_buffer_max_rows: int = 200     # 内存缓冲区行数上限
```

`enriched` 为默认值，完全向后兼容。上线后切换到 `anchored` 开始验证。

---

## 十二、实施计划（渐进式三阶段）

### Phase 1：anchored 模式（最小风险，~5 天）

| 步骤 | 内容 | 工期 |
|------|------|------|
| **P1-a** | WindowState 模型扩展：`CachedRange`, `columns`, `data_buffer`, `cached_ranges`, `viewport_range`, `detail_level`, `stale_hint`, `unfiltered_buffer` | 1 天 |
| **P1-b** | `ingest_and_confirm`（含原子性保障 + enriched fallback）+ `generate_confirmation`（anchored 格式） | 1.5 天 |
| **P1-c** | FULL 序列化渲染（管道分隔 + 多范围输出 + stale 提示 + 动态行数分配） | 1.5 天 |
| **P1-d** | engine 集成：`window_return_mode` 配置开关 + Window 注入位置调整为 system prompt 末尾 | 1 天 |

**Phase 1 交付物**：`anchored` 模式可用，`enriched` 仍为默认。用 benchmark 在 GPT 上初步验证。

### Phase 2：unified 模式 + 安全阀（~3 天）

| 步骤 | 内容 | 工期 |
|------|------|------|
| **P2-a** | `unified` 模式实现（tool_result 彻底移除数据） | 0.5 天 |
| **P2-b** | 循环检测安全阀（三元组 + write 重置） | 1 天 |
| **P2-c** | `focus_window` 工具（视口切换、清除筛选、扩展视口、恢复挂起窗口） | 1 天 |
| **P2-d** | benchmark 对比（anchored vs unified vs enriched vs OFF）在 GPT 上验证 | 0.5 天 |

**Phase 2 交付物**：`unified` 模式可用，GPT 上经 benchmark 验证。

### Phase 3：adaptive 模式 + 跨模型验证（~3 天）

| 步骤 | 内容 | 工期 |
|------|------|------|
| **P3-a** | `adaptive` 模式：基于模型 ID 的自动模式选择 + 逐级降级链 | 1 天 |
| **P3-b** | 跨模型 benchmark：Kimi/Sonnet/DS 上验证 anchored 是否足够 | 1.5 天 |
| **P3-c** | 参数调优（`window_full_max_rows`、动态行数阈值）+ 最终报告 | 0.5 天 |

**Phase 3 交付物**：`adaptive` 模式可用，全模型 benchmark 报告。

### 总计：~11 天（含 benchmark 验证时间）

---

## 十三、验证假设

通过 Phase 1-3 的 benchmark 逐步验证：

| 假设 | 验证方法 | 阶段 |
|------|---------|------|
| H1: anchored 在 GPT 上比 HYBRID(flash) 进一步节省 15-20% tokens | 同用例 token 对比 | P1 |
| H2: anchored/unified 在 Kimi/Sonnet 上减少验证性工具调用 | 观察 LLM 调用次数变化 | P3 |
| H3: 超复杂场景是收益最大的场景（vs OFF 节省 50%+） | 超复杂套件 benchmark | P2 |
| H4: 单轮简单场景无负面影响 | 基础读取套件 benchmark | P1 |
| **H5: 管道格式数据能被 Agent 正确解析** | **数据引用准确性测试**（Agent 需从 Window 提取特定单元格值回答） | P1 |
| **H6: 多范围序列化不导致 Agent 混淆** | 非连续读取后引用旧范围数据的准确性 | P2 |
| **H7: ingest 原子性保障不产生数据丢失** | 模拟 JSON 格式异常，验证 fallback 到 enriched | P1（单元测试） |

---

## 十四、开放问题

以下问题需要在实施前明确决策：

### Q1：data_buffer 的数据格式

行级 dict 还是列级 list？

| 方案 | 示例 | 优势 | 劣势 |
|------|------|------|------|
| **行级 dict** | `{"日期": "2024-01-01", "产品": "A", "数量": 100}` | 可读性好，序列化时直接映射列名 | 内存略大（重复键名） |
| **列级 list** | `["2024-01-01", "A", 100, 50, 5000]` | 紧凑，内存小 | 需要 schema 映射，序列化时需额外对齐 |

**倾向**：行级 dict。内存开销在 200 行量级下可忽略，且与当前 `preview_rows` 格式一致，改动更小。

### Q2：子代理的 Window 共享

| 方案 | 说明 | 优势 | 劣势 |
|------|------|------|------|
| **A: 只读共享** | 子代理通过 `parent_context` 看到 Window FULL 数据，但子代理的工具调用不更新主会话 Window | 简单，无一致性问题 | 子代理读取的新数据不会反映到主 Window |
| **B: 双向同步** | 子代理的工具调用也走 ingest 更新主会话 Window | 数据完整 | 并发一致性复杂 |

**倾向**：Phase 1 选 A（只读共享），Phase 3 评估是否需要 B。

### Q3：focus_window 的工具定义

`focus_window` 应该是一个**通用窗口操作工具**，还是多个专用工具？

```python
# 方案 A：通用工具（推荐）
focus_window(window_id="W3", action="scroll", range="A50:E75")
focus_window(window_id="W3", action="clear_filter")
focus_window(window_id="W3", action="expand", rows=50)
focus_window(window_id="W3", action="restore")  # 恢复挂起窗口

# 方案 B：专用工具
scroll_window(window_id="W3", range="A50:E75")
clear_filter(window_id="W3")
restore_window(window_id="W3")
```

**倾向**：方案 A（通用工具），减少工具列表膨胀，Agent 只需记住一个工具名。

### Q4：惰性序列化是否纳入 Phase 1？

"惰性序列化"（见发散性思考 Idea A）可以在 WURM 基础上再省 40-60% Window token，但增加了小模型预判的复杂度。

**倾向**：不纳入 Phase 1-3。作为 Phase 4 的独立优化项评估。

---

## 十五、发散性思考

以下是超出当前实施范围的前瞻性优化方向，记录以备后续评估：

### Idea A：惰性序列化（Lazy Serialization）

Window 不始终以 FULL 模式序列化。只在"Agent 即将引用数据"时才输出 FULL，其他时候用 SUMMARY：

```
工具执行后 → Window ACTIVE + buffer 已填充
           → 小模型预判"Agent 下一步要引用数据" → FULL
           → 小模型预判"Agent 下一步要调另一个工具" → SUMMARY
```

可在 WURM 基础上**再省 40-60%** Window token。风险：预判失败时 Agent 看不到数据（但 anchored 首行预览可兜底）。

### Idea B：数据指纹（Data Fingerprint）

在操作确认中用数据指纹替代首行预览，更紧凑但信息量更大：

```
✅ [W3] 更新成功 | 150×5 | fingerprint: 日期=2024-01-01..2024-06-30, 金额.SUM=284000
```

指纹覆盖全部数据的统计特征（值域范围、聚合值），Agent 可用指纹验证 Window 数据是否符合预期。约 50 tokens。

### Idea C：Window 引用追踪（Reference Tracking）

在 Agent 每次回复中检测 "W3" "窗口3" 等标识符：
- **被引用的 Window**：保持 ACTIVE，延长生命周期
- **未被引用的 Window**：加速降级（idle_turns += 2）

比固定 idle_turns 阈值更精准，避免"正在使用的 Window 被过早降级"。

### Idea D：Delta 序列化（Incremental Serialization）

首次完整输出 Window 数据，后续只输出变化部分：

```
# 首次（完整）
[W3 · FULL] 25行数据...

# 后续（增量）
[W3 · DELTA] +3行 (A26:E28) | B2: 100→999
```

风险：LLM 可能无法正确"应用" delta 到心理模型上。需要验证 LLM 的增量理解能力。
