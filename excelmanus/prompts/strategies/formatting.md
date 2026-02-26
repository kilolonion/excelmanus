---
name: formatting
version: "1.1.0"
priority: 48
layer: strategy
max_tokens: 250
conditions: {}
---
## 格式化任务策略

1. **使用 `run_code`**：所有格式化操作（字体、填充、边框、对齐、列宽、行高、合并单元格）统一通过 `run_code` + openpyxl 完成。参考 run_code 代码模板中的格式化模式。

2. **感知优先**：修改样式前先用 `read_excel(include=["styles"])` 了解现有样式，避免覆盖用户未要求改变的属性。

3. **最小化修改**：只改动用户指定的属性（如只改颜色则保留字体大小不变），保留其余样式不变。

4. **条件格式**：使用 `add_conditional_rule`（仍可用），或通过 run_code + openpyxl 的 `conditional_formatting` API 实现更复杂的规则。

5. **验证**：格式化后用 `read_excel(include=["styles"])` 确认样式已按预期变更。

6. **输出格式校准**：写入缺失值/空值前，先观察目标区域已有数据的表示方式（空单元格、空字符串、N/A 等），保持一致。当用户提示中出现"output may be empty string"或类似表述时，缺失值用空字符串或空单元格输出，保持与原数据一致。

7. **收尾动作**：格式化任务结束时，执行 `adjust_column_width(auto_fit=True)` + `adjust_row_height(auto_fit=True)` 自动调整列宽和行高，确保表格布局美观。参考 table_layout 策略的相关配置（数字右对齐、文本左对齐、标题居中等）。
