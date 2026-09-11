---
name: data_basic
description: 数据读取、分析、筛选与转换
file_patterns:
  - "*.xlsx"
version: "1.0.0"
---
优先使用结构化方式处理数据：
1. 明确列名与过滤条件。
2. 先用 `inspect_spreadsheet` / `analyze_spreadsheet` 看清结构，再 `edit_spreadsheet` 改写。
3. 需要改写时建议输出新文件路径，已有文件必须带 `expected_version`。

## 多条件筛选

需要同时满足多个条件时，用 `analyze_spreadsheet` 一次带上 `conditions`，禁止分多次再手动取交集。

- `logic` 支持 `"and"`（全部满足）和 `"or"`（任一满足），默认 `"and"`
- 筛选结果包含实际行数据，直接引用即可，禁止编造未出现在结果中的记录
