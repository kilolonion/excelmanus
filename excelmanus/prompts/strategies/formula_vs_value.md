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

- 简单公式（如 SUM、AVERAGE）且用户明确要求公式时，可写入公式。
- 复杂跨表查找（INDEX/MATCH/VLOOKUP 跨 sheet）时，优先计算出值后写入，因为 openpyxl 写入的数组公式无法被缓存计算。
- 写入公式后，write_cells 会返回 formula_warning，看到此警告时应考虑改为写入计算值。
