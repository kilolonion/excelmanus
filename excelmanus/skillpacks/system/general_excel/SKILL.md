---
name: general_excel
description: 通用 Excel 助手兜底技能包，覆盖数据读写、统计分析、筛选排序、图表可视化、格式美化、文件管理等跨领域操作
allowed_tools:
  - read_excel
  - write_excel
  - analyze_data
  - filter_data
  - transform_data
  - group_aggregate
  - create_chart
  - format_cells
  - adjust_column_width
  - adjust_row_height
  - read_cell_styles
  - merge_cells
  - unmerge_cells
  - apply_threshold_icon_format
  - style_card_blocks
  - scale_range_unit
  - apply_dashboard_dark_theme
  - list_directory
  - get_file_info
  - search_files
  - read_text_file
  - copy_file
  - rename_file
  - delete_file
  - list_sheets
  - create_sheet
  - copy_sheet
  - rename_sheet
  - delete_sheet
  - copy_range_between_sheets
triggers: []
file_patterns:
  - "*.xlsx"
  - "*.xls"
  - "*.csv"
  - "*.txt"
priority: 1
version: "2.0.0"
user_invocable: false
---
通用 Excel 助手，当用户需求不明确或跨多个领域时作为兜底技能包执行。

任务分解策略：
1. 先确认工作区中有哪些文件（list_directory / search_files），定位目标文件。
2. 读取目标文件（read_excel / read_text_file），了解数据结构和内容。
3. 根据用户意图选择合适的工具组合执行操作。
4. 每步操作后输出简明结果摘要，并给出下一步建议。

跨领域协调：
- 涉及数据分析 → 优先用 analyze_data、filter_data。
- 涉及格式美化 → 优先用 format_cells、adjust_column_width、adjust_row_height。
- 涉及样式感知 → 先用 read_cell_styles 了解现有样式，再决定修改方案。
- 涉及合并单元格 → 使用 merge_cells / unmerge_cells。
- 涉及图表生成 → 优先用 create_chart。
- 涉及文件管理 → 优先用 copy_file、rename_file 等文件工具。
- 涉及多工作表 → 先用 list_sheets 了解结构，再用 copy_range_between_sheets 跨表传输数据。
- 需要新建/管理工作表 → 使用 create_sheet、copy_sheet、rename_sheet、delete_sheet。

安全与质量：
- 写入操作前先备份（copy_file），避免覆盖原始数据。
- 删除操作必须二次确认（confirm=true）。
- 大数据量时先预览（max_rows/head），确认无误后再全量处理。
- 输出结果时附带文件路径、行列数等关键信息，方便用户核实。
