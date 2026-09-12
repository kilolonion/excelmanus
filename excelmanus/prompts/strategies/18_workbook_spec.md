---
name: spreadsheet:workbook_spec
version: "9.0.0"
priority: 110
order: 110
layer: strategy
max_tokens: 900
conditions: {}
---
WorkbookSpec 经 edit_spreadsheet 的 workbook_spec 一次编译出新工作簿。已有文件用 operations 改，不要把规格当补丁。规格字段只来自本段和工具参数说明。

必填：
- sheets[]：每个表含 name、dimensions{rows,cols}
- uncertainties[]：必须出现；没有不确定项时为 []。项为 {location, reason, candidate_values}
每个 sheet 可用：
- value_blocks[{start, values:非空矩形二维数组}]
- formula_blocks[{start, formulas:非空矩形二维数组}]
- cells[{address, value；公式用 value="=A1" 且 value_type="formula"}]
- styles{style_id → {font, fill:{type,color}, border, alignment, number_format}}
- style_regions[{range, style_id}]
- merged_ranges[{range} 或 "A1:B1"]
- column_widths：从 A 起的数字数组 [18,12]，或 {"A":18,"B":12}（与 inspect / format size 相同）
- row_heights：{"1":22} 或 [22,15]
可选顶层：name、locale、default_font（{"name":"微软雅黑"} 或 "微软雅黑"）、theme_hint

规则：矩形块与样式区域不得超出 dimensions；style_id 必须在 styles 中存在；看不清的写入 uncertainties，不要编造。合并区把填充和边框写在锚点，style_regions 覆盖整个合并范围。活表用公式，冻结源用字面量。xlsm 可保留宏字节，但不执行宏，也不声称测过宏行为。

Excel 做不了圆角、阴影、胶囊条；编译成功不等于视觉临摹完成。
