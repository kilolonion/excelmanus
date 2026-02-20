---
name: formula_vs_value
version: "1.0.0"
priority: 45
layer: strategy
max_tokens: 150
conditions:
  write_hint: "may_write"
---
## 公式与值的选择

### 写公式的场景
- 用户**明确要求公式**（如"写一个 SUM 公式"）。
- 简单单 sheet 内公式（SUM、AVERAGE、COUNT、IF 等），且引用范围在同一 sheet 内。

### 写计算值的场景（优先）
- **跨 sheet 查找**（INDEX/MATCH/VLOOKUP 引用其他 sheet）→ 必须先 read_excel 读取源数据，计算匹配结果后用 write_cells 写入具体值。
- **数组公式**（CSE 公式）→ openpyxl 无法缓存计算值，写入后外部读取为空。
- **批量填充计算列**（如"利润 = 收入 × 30%"覆盖数百行）→ 读取源列数据，Python 层面计算后批量写入值，比逐行写公式更高效且可靠。
- 用户未明确要求公式，只要求"结果"或"数据"时。

### 警告处理
- write_cells 返回 `formula_warning` 时，说明写入了无缓存值的公式。应评估是否需要改为写入计算值。
