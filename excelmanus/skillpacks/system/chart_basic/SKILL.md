---
name: chart_basic
description: 图表生成技能包
allowed_tools:
  - create_chart
  - read_excel
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
图表任务默认流程：
1. 确认 x/y 列名是否存在。
2. 优先生成单一图表并返回输出路径。
3. 图表失败时先解释字段问题，再给可行参数。
