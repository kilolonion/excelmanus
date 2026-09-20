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
样式、条件格式和数据验证都走 `format_spreadsheet`。合并区写锚点。工作区 xlsx 直接 `wb.save` 会失败。

用户明确要求美化时，可再读 `references/aesthetic_guide.md`。

```
format_spreadsheet(
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

`range` 写 A1:C5 或整列 `B:B`。多表时请带 `sheet`。列宽用 `kind=size` 的 `columns={"A":18}` 或 `auto_fit=true`。冻结首行：`kind=freeze` 且 `freeze_panes="A2"`。

条件格式用 `kind=conditional_format`，数据验证/下拉框用 `kind=data_validation`；两者都传 `rule`，字段先用 `introspect_capability(query_type="tool_detail", query="format_spreadsheet.operations.rule")` 查询。原生 Table、打印设置和图片插入当前没有已有工作簿的保存入口。

按产品名称标记整行时，range=`A2:H13` 配 `rule={"type":"formula","formula":"=$D2=\"未匹配\"","fill":{"patternType":"solid","fgColor":"FFC7CE"},"font":{"color":"FF9C0006"}}`。`formula1` 与 `formula` 同义；只传一个即可。相对行号以范围左上角为基准，`$D2` 锁列随行变化。数据验证 `type=custom` 也使用同一公式约定。
