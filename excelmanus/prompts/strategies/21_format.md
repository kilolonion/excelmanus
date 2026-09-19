---
name: tool:format
version: "10.0.0"
priority: 106
order: 106
layer: strategy
max_tokens: 80
conditions:
  catalog_mode: [write, code]
  tool: format_spreadsheet
---
随数据变化的规则用条件格式；一次性指定某格外观用直接 format。批量固定样式可按范围操作，不必改成条件格式。单表可省略 sheet；多表必须带 sheet 或 表!A1。合并区以锚点为准。
