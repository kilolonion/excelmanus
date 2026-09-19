---
name: data_basic
description: 数据读取、分析、筛选与转换。
file_patterns:
  - "*.xlsx"
version: "1.2.0"
---
只打开、看看、质检脏表时不必加载本技能。脏表先 `inspect_spreadsheet` 读相关 sheet；`profile`/`quality` 按数据框，标题行不是表头。

`analyze_spreadsheet` 的 `conditions` 一次可带多个条件，`logic` 为 `"and"` 或 `"or"`（默认 `"and"`）。求和/分组汇总/TopN 用 `mode="aggregate"` + `group_by`/`aggregations`；列取值分布与重复键用 `mode="distinct"`。

筛选结果里的行才是证据，不要编造未出现的记录。已有文件改写需带 `expected_version`。
