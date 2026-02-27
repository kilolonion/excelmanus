---
name: format_basic
description: 工作表格式化与样式感知技能包，覆盖颜色/字体/边框/填充/合并单元格/行列尺寸
file_patterns:
  - "*.xlsx"
resources:
  - references/color_palette.md
  - references/aesthetic_guide.md
version: "3.0.0"
---
格式化任务标准流程：

1. 感知优先
- 修改样式前**必须**先调用 `read_cell_styles` 了解目标范围的现有样式。
- 如果用户提到"把红色改为蓝色"等基于现有样式的需求，先用 `read_cell_styles` 定位哪些单元格有该样式。
- 对整表样式概览，可使用 `read_excel(include=["styles"])` 快速获取压缩样式类；也可按需附加 charts/images/freeze_panes 等维度。
- **列数感知**：格式化整行时，优先使用行引用（如 `1:1`）而非具体列范围（如 `A1:J1`），避免因视口限制遗漏列。如需精确列范围，从感知块的 `range: NNNr x NNc` 读取总列数。

2. 精准修改
- 样式变更尽量最小化，只改动用户指定的属性，避免覆盖其他样式。
- 颜色参数支持中文名（如"红色"、"浅蓝"）和十六进制码（如 "FF0000"），优先使用用户的表达方式。
- 边框支持统一模式（四边相同）和单边差异化模式（left/right/top/bottom 独立设置）。

3. 条件格式
- `add_conditional_rule` 支持三种模式：
  - **cell_is**（值比较）：需要 `operator` + `values`。数值比较用 `values` 数组，如 `values=[1000]`；范围比较用两个值，如 `values=[50, 150]`。
  - **formula**（公式条件）：需要 `formula` 字符串，如 `formula='=$C2="FATAL"'`。
  - **icon_set**（图标集）：需要 `icon_style`，如 `icon_style="3Arrows"`。
- 颜色参数支持中文名（"红色"）和十六进制（"FF0000"）。
- 示例：销售额低于 1000 高亮红色 → `add_conditional_rule(cell_range="I4:I2000", rule_type="cell_is", operator="lessThan", values=[1000], fill_color="红色")`

4. 布局操作
- 合并单元格前确认范围内只有左上角有数据，避免数据丢失。
- 行高/列宽调整支持手动指定和自动适配两种模式。
- **对齐约定**：数字/金额/百分比列右对齐，文本列左对齐，表头居中，合并单元格居中。
- **收尾动作**：数据和样式写入完成后，**always** 执行 `adjust_column_width(auto_fit=True)` + `adjust_row_height(auto_fit=True)` 做最终适配。

5. 输出规范
- 返回修改范围与影响单元格数量。
- 建议用户核实关键格式变更。

6. 审美设计
- 美化/专业排版任务时，参考 `references/aesthetic_guide.md` 获取完整设计原则、五套专业色板、场景模板和反模式清单。
- 核心原则：三色克制、字号层级递减、无框优先用交替行色、数据从 B2 开始留呼吸空间。
- 不确定配色时默认使用**商务蓝色板**（主色 002060 + 表头 4472C4 + 交替行 D6E4F0）。
