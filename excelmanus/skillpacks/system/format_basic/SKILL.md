---
name: format_basic
description: 工作表格式化技能包
allowed_tools:
  - format_cells
  - adjust_column_width
  - read_excel
triggers:
  - 格式
  - 美化
  - 列宽
  - 字体
  - 边框
  - 颜色
  - 对齐
  - 样式
  - 加粗
file_patterns:
  - "*.xlsx"
priority: 6
version: "1.0.0"
---
格式化任务要求：
1. 先确认工作表和单元格范围。
2. 样式变更尽量最小化，避免影响全表。
3. 输出包含修改范围与影响单元格数量。
