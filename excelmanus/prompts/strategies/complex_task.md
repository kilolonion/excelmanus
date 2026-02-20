---
name: complex_task
version: "1.0.0"
priority: 52
layer: strategy
max_tokens: 250
conditions:
  task_tags:
    - cross_sheet
    - large_data
---
## 复杂多步任务策略

当任务涉及多个 sheet、多文件或 5 步以上操作时：

1. **全局探查先行**：用 list_sheets（或 inspect_excel_files）一次性了解所有相关文件和 sheet 的结构（列名、行数、数据类型），不要边做边探查。

2. **制定步骤清单**：用 task_create 列出子任务，每步做一件事。步骤间有数据依赖时注明。

3. **逐步执行+即时验证**：每完成一步后读取结果确认正确，再进入下一步。发现错误立即修正，不累积到最后。

4. **数据一致性**：跨表引用时核对键列的值域是否一致（如 Sheet1 的"部门"列值域是否与 Sheet2 的一致），不一致时先报告差异。

5. **资源意识**：大数据量时分批处理（每批 ≤500 行），避免单次操作超出工具能力。
