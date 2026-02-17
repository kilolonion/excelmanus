---
name: chart_basic
description: 图表生成技能包
allowed_tools:
  - create_chart
  - create_excel_chart
  - read_excel
  - group_aggregate
  - list_sheets
triggers:
  - 图表
  - 可视化
  - 折线图
  - 柱状图
  - 饼图
  - 雷达图
  - 散点图
  - 画图
  - 绘制
  - 生成图
file_patterns:
  - "*.xlsx"
priority: 6
version: "1.0.0"
---
图表工具选择（必须先判断）：
- 需要在 Excel 文件中插入原生图表对象（可交互、随数据更新）→ 使用 `create_excel_chart`
  适用场景：用户要求"在Excel中画图"、"插入图表到工作表"、"生成带图表的Excel"、目标产物是 Excel 文件。
- 需要生成独立图片文件（PNG）→ 使用 `create_chart`
  适用场景：用户要求"导出图片"、"生成图表图片"、目标产物是图片文件。
- 当用户意图不明确但数据源是 Excel 文件时，默认优先 `create_excel_chart`。

图表任务默认流程：
1. 确认 x/y 列名是否存在。
2. 根据上述规则选择合适的图表工具。
3. 图表失败时先解释字段问题，再给可行参数。

聚合+画图推荐路径：
当需要对数据进行分组统计后再绘图时（如"各部门人数饼图"），推荐以下流程：
1. 调用 `group_aggregate` 对原始数据进行聚合，获取统计结果。
2. 根据目标产物选择工具：
   - 需要图片：将 `group_aggregate` 返回的 `data` 字段直接传给 `create_chart` 的 `data` 参数。
     示例：`create_chart(data=聚合结果的data字段, chart_type="pie", x_column="部门", y_column="count", output_path="chart.png")`
   - 需要 Excel 内嵌图表：先将聚合结果写回 Excel，再用 `create_excel_chart` 基于单元格范围创建原生图表。
