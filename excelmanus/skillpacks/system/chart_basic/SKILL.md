---
name: chart_basic
description: 图表与表格技能包：Excel 原生图表插入、PNG 图表导出、Excel 表格对象创建
file_patterns:
  - "*.xlsx"
resources:
  - references/chart_and_table_templates.md
version: "2.0.0"
---
## 图表工具选择（必须先判断）

- **Excel 原生图表**（可交互、随数据更新）→ 直接调用 `create_excel_chart` 工具
  适用：用户要求"在 Excel 中画图"、"插入图表到工作表"、目标产物是 Excel 文件。
- **PNG 图片导出** → 通过 `run_code` + matplotlib（见参考模板）
  适用：用户要求"导出图片"、"生成图表图片"、目标产物是独立图片文件。
- 意图不明确且数据源是 Excel 时，**默认优先 `create_excel_chart`**。

## 图表任务流程

1. 先用 `read_excel` 或 `list_sheets(include=["columns"])` 确认数据范围和列名。
2. 根据上述规则选择工具路径。
3. `create_excel_chart` 的 `data_range` 必须包含表头行（第一行作为系列名）。
4. 图表失败时先解释字段/范围问题，再给可行参数。

## 聚合 + 画图

当需要分组统计后再绘图（如"各部门人数饼图"）：
1. 用 `run_code` 做 pandas 聚合并写回 Excel 新 sheet。
2. 用 `create_excel_chart` 基于该 sheet 的单元格范围创建图表。
3. 或用 `run_code` + matplotlib 直接生成 PNG（见参考模板）。

## Excel 表格对象（Table）

当用户要求"插入表格"、"创建筛选表"、"添加 Excel Table" 时，通过 `run_code` 使用 openpyxl 的 `ws.add_table()` 创建。参考模板见 `references/chart_and_table_templates.md`。

关键点：
- Table 名称在整个工作簿内必须唯一。
- `ref` 参数必须包含表头行（如 `"A1:D20"`）。
- 支持预定义样式（如 `TableStyleMedium9`）。
