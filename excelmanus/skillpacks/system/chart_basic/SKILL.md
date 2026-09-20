---
name: chart_basic
description: 已有数据上的原生 Excel 图表（Table 对象当前不可用）。
file_patterns:
  - "*.xlsx"
resources:
  - references/chart_and_table_templates.md
version: "2.1.0"
---
原生 Excel 图表走 `manage_spreadsheet_objects` 的 `operations`（`kind=chart`）。`data_range` 需包含表头行。

独立 PNG 可用 `run_code` + matplotlib。当前只支持创建原生图表；没有 Table 对象或已有图表更新/删除入口。

```
manage_spreadsheet_objects(
  file_path="book.xlsx",
  expected_version=...,
  operations=[{
    "kind": "chart",
    "sheet": "数据",
    "chart_type": "bar",
    "data_range": "A1:B12",
    "categories_range": "A2:A12",
    "target_cell": "E2"
  }]
)
```

`data_range` 写 A1:B12，也接受 `数据!A1:B12`。`chart_type` 仅 bar/line/pie/scatter/area（column 视为 bar）。
