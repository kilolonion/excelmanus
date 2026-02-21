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

1. **使用 `run_code`**：所有格式化操作（字体、填充、边框、对齐、列宽、行高、合并单元格）统一通过 `run_code` + openpyxl 完成。参考 run_code 代码模板中的格式化模式。

2. **感知优先**：修改样式前先用 `read_excel(include=["styles"])` 了解现有样式，避免覆盖用户未要求改变的属性。

3. **最小化修改**：只改动用户指定的属性（如只改颜色就不要动字体大小），保留其余样式不变。

4. **条件格式**：使用 `add_conditional_rule`（仍可用），或通过 run_code + openpyxl 的 `conditional_formatting` API 实现更复杂的规则。

5. **验证**：格式化后用 `read_excel(include=["styles"])` 确认样式已按预期变更。
