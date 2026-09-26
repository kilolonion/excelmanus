---
name: chart_basic
description: 已有数据上的原生 Excel 图表与 Table 对象。
file_patterns:
  - "*.xlsx"
  - "*.csv"
  - "*.tsv"
resources:
  - references/chart_and_table_templates.md
version: "2.1.0"
---
原生 Excel 图表可以通过 `apply_spreadsheet_changes` 的 `operations`（`kind=chart`）创建。`data_range` 通常包含表头行。工作区只有 CSV、尚无 xlsx 时，新建不依赖已有 xlsx，也可以直接用 `workbook_spec` 创建第一个 xlsx，再按版本继续添加图表；`convert_spreadsheet` 适合实际格式转换，并不是新建工作簿的前置步骤。依赖已有工作簿的 `trace_spreadsheet_formulas` 在 CSV-only 下会在工作簿出现后再提供。

独立 PNG 可用 `run_code` + matplotlib。原生图表创建/更新/删除分别使用 chart/update_chart/delete_chart；Table 用 kind=table。

```
apply_spreadsheet_changes(
  file_path="book.xlsx",
  expected_version=...,
  operations=[{
    "kind": "chart",
    "sheet": "数据",
    "chart_type": "bar",
    "data_range": "B1:B12",
    "categories_range": "A2:A12",
    "target_cell": "E2"
  }]
)
```

`data_range` 写 A1:B12，也接受 `数据!A1:B12`。`chart_type` 仅 bar/line/pie/scatter/area（column 视为 bar）。

`sheet` 指数据源表，跨表放置用 `target_sheet`。图表视觉检查用 `preview_spreadsheet(surface="auto")` 并检查 `visual_coverage`；实际分页检查才指定 `surface="print"`；workbench 不渲染 drawing 对象。
