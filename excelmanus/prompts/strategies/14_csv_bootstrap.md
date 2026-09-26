---
name: spreadsheet:bootstrap
version: "1.1.0"
priority: 98
order: 98
layer: strategy
max_tokens: 240
conditions:
  catalog_mode: write
  profile: csv
  tool: convert_spreadsheet
---
本工作区只有 CSV、无 .xlsx：`apply_spreadsheet_changes` 可以直接用 workbook_spec 创建第一个 xlsx，再按版本用 operations 补公式与图表；源 CSV 保持只读。`convert_spreadsheet` 也可用于实际格式转换，产物工作表名通常是 `input`，引用前可用 `observe_spreadsheet(overview)` 确认。依赖已有工作簿的 `trace_spreadsheet_formulas` 会在第一个 xlsx 落在 `outputs/` 或工作区顶层后重新评估目录。
