---
name: format_basic
description: 工作表格式化与样式感知技能包，覆盖颜色/字体/边框/填充/合并单元格/行列尺寸
allowed_tools:
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
  - add_color_scale
  - add_data_bar
  - add_conditional_rule
  - set_print_layout
  - set_page_header_footer
  - read_excel
triggers:
  - 格式
  - 美化
  - 列宽
  - 行高
  - 字体
  - 边框
  - 颜色
  - 填充
  - 背景色
  - 对齐
  - 样式
  - 加粗
  - 下划线
  - 删除线
  - 合并
  - 取消合并
  - 居中
  - 红色
  - 蓝色
  - 黄色
  - 绿色
  - 高亮
  - 标记
  - 条件格式
  - 图标集
  - 仪表盘
  - 暗色
  - 打印
file_patterns:
  - "*.xlsx"
resources:
  - references/color_palette.md
priority: 6
version: "2.0.0"
---
格式化任务标准流程：

1. 感知优先
- 修改样式前**必须**先调用 `read_cell_styles` 了解目标范围的现有样式。
- 如果用户提到"把红色改为蓝色"等基于现有样式的需求，先用 `read_cell_styles` 定位哪些单元格有该样式。
- 对整表样式概览，可使用 `read_excel(include_style_summary=true)` 快速获取。

2. 精准修改
- 样式变更尽量最小化，只改动用户指定的属性，避免覆盖其他样式。
- 颜色参数支持中文名（如"红色"、"浅蓝"）和十六进制码（如 "FF0000"），优先使用用户的表达方式。
- 边框支持统一模式（四边相同）和单边差异化模式（left/right/top/bottom 独立设置）。

3. 布局操作
- 合并单元格前确认范围内只有左上角有数据，避免数据丢失。
- 行高/列宽调整支持手动指定和自动适配两种模式。

4. 输出规范
- 返回修改范围与影响单元格数量。
- 建议用户核实关键格式变更。
