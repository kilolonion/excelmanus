---
name: format_basic
description: 已有工作表上的样式、合并、冻结、条件格式和数据验证。
file_patterns:
  - "*.xlsx"
resources:
  - references/color_palette.md
  - references/aesthetic_guide.md
version: "4.2.0"
---
样式、条件格式和数据验证都走 `apply_spreadsheet_changes`。合并区写锚点。工作区 xlsx 直接 `wb.save` 会失败。

用户明确要求美化时，可再读 `references/aesthetic_guide.md`。

```
apply_spreadsheet_changes(
  file_path="book.xlsx",
  expected_version=...,
  operations=[{
    "kind": "format",
    "sheet": "区域汇总",
    "range": "A5:C5",
    "fill": {"color": "FFCC00"}
  }]
)
```

布局先用 observe_spreadsheet(mode="range", sheet=..., range=..., facets=["geometry","presentation"])。

## 字段合同速查（样式键名两套合同）

改已有表的 `operations` 样式键名：`fill` 用 `{"patternType":"solid","fgColor":"FFC7CE"}`（或 `{"type":"solid","color":"FFC7CE","end_color":...}` 等价），`font` 用 `{name,size,bold,italic,color,underline,strike}`。创建 `workbook_spec` 的样式键名不同：`fill` 只收 `{type, color, end_color}`（`type` 默认 `solid`），`font` 收 `{name,size,bold,italic,color,underline,strike}`。对照：`patternType`/`fill_type`/`pattern`→`type`，`fgColor`/`fg_color`/`fgcolor`/`start_color`→`color`，`strikethrough`→`strike`。创建用 workbook_spec 键名，改样式用 operations 键名；混用只有这些同义键会被归一，其余混入的键名会被拒（SPEC_VALIDATION_FAILED）。

横向是列宽，纵向是行高。`geometry.scale` 明确传 `x`、`y`；`size` 用 `column_widths={"A":18}` 与 `row_heights={"1":22}`。
按内容自动适配传 `auto_fit=true` 并带 `axis`，不要覆盖用户给定比例。列宽行高影响整轴。
样式、合并、尺寸、冻结和对象可放入同一批 operations；检查最终 observation 与 receipt，必要时 preview_spreadsheet 回看。
条件格式和验证规则分别查询 `apply_spreadsheet_changes.operations.conditional_format.rule` 与 `.operations.data_validation.rule`。
打印布局用 kind=print_layout；原生 Table、图片和定义名称用各自的 kind，字段以注册合同为准。

按产品名称标记整行时，range=`A2:H13` 配 `rule={"type":"formula","formula":"=$D2=\"未匹配\"","fill":{"patternType":"solid","fgColor":"FFC7CE"},"font":{"color":"FF9C0006"}}`。`formula1` 与 `formula` 同义；只传一个即可。相对行号以范围左上角为基准，`$D2` 锁列随行变化。数据验证 `type=custom` 也使用同一公式约定。
