---
name: receipt_visual_replica
description: 将收据、发票或表单图片整理为可编辑 Excel，并核对明细、金额、合计和版式证据。
file_patterns:
  - "*.jpg"
  - "*.jpeg"
  - "*.png"
  - "*.webp"
version: "1.0.0"
---

# 图片收据还原

这是一个可选的任务路线。它把图片观察、结构化数据、公式核验和视觉预览连接起来，agent 可以根据实际图片和工具结果选择其中的步骤。

## 可用路线

1. 使用 `read_image` 把原图放入视觉上下文；`analyze_layout=true` 可以额外返回有界的暗像素投影、候选水平/垂直规则、墨迹边界、源尺寸、`grid_hint` 和可转成 `layout_reference` 的候选对象。它是布局提示，不是 OCR 结果。
2. 从原图整理收款单位、客户、日期、单号、项目、规格、数量、单价、金额、付款方式、备注和合计。同一附件同一 detail/crop 在本轮只观察一次；看不清的位置通过 `crop={x,y,width,height,zoom}` 一次裁剪放大，也可以在 `uncertainties` 中记录候选值和原因。
3. 新建工作簿时可以使用 `apply_spreadsheet_changes(workbook_spec=...)`，并将 `purpose` 设为 `visual_replica`。表格的 `layout_reference` 使用图片像素坐标；`column_widths` 是 Excel 字符宽度，`row_heights` 是 points，需要按工具单位换算。
4. 明细金额可以用“数量×单价”公式，合计可以用 `SUM`。日期可以使用 schema 接受的字符串或日期值。输出中的单价、金额和合计保留明确的数字格式。
5. 写入后可以按返回的 `content_version` 继续使用 `calculate_spreadsheet`、`validate_spreadsheet` 和 `preview_spreadsheet`。金额任务常用 `cell`、`total`、`formula_errors`；视觉任务可检查 `visual_coverage` 和裁切提示。
6. `run_code` 可以用于自定义图像测量、批量计算或跨工具组合；工具返回的候选、原图观察和预览结果共同决定下一步。多次探测后仍有不确定项时，也可以先交付带 `uncertainties` 的可编辑草稿，再继续修正。

## 字段提醒

- 创建样式通常使用 `fill: {type, color, end_color}`；变更操作也接受对应的兼容别名。
- `uncertainties` 使用 `location`、`reason`、可选的 `candidate_values` 和 `confidence`。
- 校验具体单元格使用 `kind: "cell"`、`sheet`、`cell`、`expected`；整列合计使用 `kind: "total"`、`column`、`expected`。
- `read_image` 的布局摘要是提示性证据，文字内容仍以视觉观察和后续工作簿回读为准。

## 交付证据

交付时可以说明实际输出路径、工作表、公式与合计结果、验证状态、预览覆盖范围和未解决的不确定项。工具成功提交、回读和预览分别代表不同证据层级。
