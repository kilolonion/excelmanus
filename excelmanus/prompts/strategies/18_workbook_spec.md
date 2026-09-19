---
name: spreadsheet:workbook_spec
version: "11.0.0"
priority: 110
order: 110
layer: strategy
max_tokens: 100
conditions:
  catalog_mode: [write, code]
  tool: edit_spreadsheet
---
WorkbookSpec 经 edit_spreadsheet 的 workbook_spec 一次编译出新工作簿；已有文件用 operations。已有工作区也可再新建另一本，不要覆盖已有文件。必填 sheets 与 uncertainties（无可疑项时为 []）。看不清的写入 uncertainties，不要编造。Excel 做不了圆角、阴影、胶囊条。xlsm 可保留宏字节，但不执行宏。
