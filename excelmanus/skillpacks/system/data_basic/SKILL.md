---
name: data_basic
description: 数据读取、分析、筛选与转换。
file_patterns:
  - "*.xlsx"
version: "1.1.0"
---
`analyze_spreadsheet` 的 `conditions` 一次可带多个条件，`logic` 为 `"and"` 或 `"or"`（默认 `"and"`）。

筛选结果里的行才是证据，不要编造未出现的记录。已有文件改写需带 `expected_version`。
