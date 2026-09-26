---
name: data_basic
description: 数据读取、分析、筛选与转换。
file_patterns:
  - "*.xlsx"
  - "*.xlsm"
  - "*.csv"
  - "*.tsv"
version: "1.2.0"
---
只打开、看看、质检脏表时不必加载本技能。脏表先 `observe_spreadsheet` 读相关 sheet；`profile`/`quality` 按数据框，标题行不是表头。

`analyze_spreadsheet` 的 `conditions` 一次可带多个条件，`logic` 为 `"and"` 或 `"or"`（默认 `"and"`）。求和/分组汇总/TopN 用 `mode="aggregate"` + `group_by`/`aggregations`；列取值分布与重复键用 `mode="distinct"`。

## 字段合同速查（aggregate）

`analyze_spreadsheet(mode="aggregate")` 的 `aggregations` 有两种等价形状，也可传等价 JSON 字符串（自动解析）：

- 对象形：`{"销售额": ["sum", "mean"]}`（列名 → 函数或函数数组）。
- 数组形：`[{"column": "销售额", "func": "sum"}]`。

函数固定为 `sum/count/mean/min/max/median/std/nunique/first/last`；`"*"` 表示行计数。TopN 不是函数：用 `sort_by`（聚合输出列名如 `销售额_sum`，或源列名自动映射）+ `max_rows` + `ascending=false` 取最大 N 条（V2 入口不接受 `limit` 别名，传了会被判为未知字段）。

筛选结果里的行才是证据，不要编造未出现的记录。已有文件改写需带 `expected_version`。

把筛选结果写回时，使用返回的 selection（含文件、表、版本和原始行列），不能按投影列重编号。
跨页/跨表读取同一工作簿时带 expected_version；大结果按返回的 next_call 取回完整 JSON。result_spill 是字段名，实际句柄是它的完整值 `spill:…`，原样作为 read_text_file 的 file_path，不拼接字段名或目录。取回的是原始结果；不含 content/truncated 等文本文件包装。
只读 distinct 是值分布；真正删除重复行用 edit.kind=transform action=dedupe。
统计透视用 analyze.mode=pivot，落地新汇总表用 edit.kind=pivot + target_sheet；静态矩阵不等于原生 PivotTable。
