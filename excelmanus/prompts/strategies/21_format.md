---
name: tool:format
version: "10.1.0"
priority: 106
order: 106
layer: strategy
max_tokens: 125
conditions:
  catalog_mode: write
  tool: format_spreadsheet
---
随数据变化用条件格式：rule.type=formula，formula/formula1 同义。一次性指定某格外观用直接 format，下拉验证用 data_validation。多表带 sheet 或 表!A1，合并区写锚点。结果看 appearance/skipped_merged_non_anchors；不自动逐属性回读。
