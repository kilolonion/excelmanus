---
name: formatting
version: "1.0.0"
priority: 48
layer: strategy
max_tokens: 250
conditions:
  task_tags:
    - formatting
---
## 格式化任务策略

1. **感知优先**：修改样式前必须先调用 `read_cell_styles` 了解目标范围现有样式，避免覆盖用户未要求改变的属性。

2. **最小化修改**：只改动用户指定的属性（如只改颜色就不要动字体大小），保留其余样式不变。

3. **整行/整列引用**：格式化整行时优先使用行引用（如 `1:1`）而非具体列范围（如 `A1:J1`），避免因列数不准而遗漏。需要精确列范围时从 list_sheets 的列数信息推算。

4. **颜色表达**：颜色参数支持中文名（"红色"、"浅蓝"）和十六进制码（"FF0000"），优先使用用户的原始表达方式。

5. **条件格式**：使用 `add_conditional_rule`，根据需求选择 cell_is（值比较）、formula（公式条件）或 icon_set（图标集）模式。

6. **验证**：格式化后用 `read_cell_styles` 确认目标单元格的样式已按预期变更。
