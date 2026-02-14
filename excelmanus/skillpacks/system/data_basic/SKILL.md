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
  - list_sheets
  - scan_excel_files
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
