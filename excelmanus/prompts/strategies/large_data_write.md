---
name: large_data_write
version: "1.0.0"
priority: 55
layer: strategy
max_tokens: 200
conditions:
  total_rows_gte: 100
  write_hint: "may_write"
---
## 大量数据写入策略

目标表行数超过 100 行时：

1. **先确认总行数**：从 list_sheets 返回的行数确定写入范围，不能仅根据 read_excel 的预览行数来决定。

2. **优先用 `run_code`**：超过 100 行的数据写入，优先用 `run_code` 编写 Python 脚本（pandas/openpyxl）一次性完成，比多次 write_cells 分批更可靠高效。`run_code` 已配备安全沙盒，可自动执行。

3. **分批写入（仅 write_cells 场景）**：如因特殊原因需用 write_cells，单次写入行数不超过 500 行，超过时分批处理。

4. **覆盖检查**：写入完成后确认写入范围的最后一行 = 数据总行数。
