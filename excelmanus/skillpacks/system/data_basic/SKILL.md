---
name: data_basic
description: 数据读取、分析、筛选与转换
allowed_tools:
  - read_excel
  - write_excel
  - analyze_data
  - filter_data
  - transform_data
  - group_aggregate
  - analyze_sheet_mapping
  - list_sheets
  - inspect_excel_files
  - write_cells
  - insert_rows
  - insert_columns
  - focus_window
triggers:
  - 读取
  - 分析
  - 筛选
  - 转换
  - 排序
  - 打开
  - 查看
  - 统计
  - 分组
  - 汇总
  - 聚合
file_patterns:
  - "*.xlsx"
priority: 5
version: "1.0.0"
---
优先使用结构化方式处理数据：
1. 明确列名与过滤条件。
2. 先分析后修改，避免直接覆盖。
3. 需要改写时建议输出新文件路径。

## 多条件筛选

需要同时满足多个条件时，使用 `conditions` 数组一次调用完成，禁止分多次 filter_data 再手动取交集：

```json
{
  "file_path": "data.xlsx",
  "conditions": [
    {"column": "部门", "operator": "eq", "value": "销售部"},
    {"column": "金额", "operator": "gt", "value": 10000}
  ],
  "logic": "and"
}
```

- `logic` 支持 `"and"`（全部满足）和 `"or"`（任一满足），默认 `"and"`
- 筛选结果包含实际行数据，直接引用即可，禁止编造未出现在结果中的记录
