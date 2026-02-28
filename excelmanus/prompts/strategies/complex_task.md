---
name: complex_task
version: "1.4.0"
priority: 52
layer: strategy
max_tokens: 500
conditions: {}
---
## 复杂多步任务策略

当任务涉及多个 sheet、多文件或 5 步以上操作时：

1. **全局探查先行**：用 list_sheets（或 inspect_excel_files）一次性了解所有相关文件和 sheet 的结构（列名、行数、数据类型），在操作前完成全部探查。

2. **积极使用 `run_code`**：涉及数据透视/转置、分组聚合、跨表匹配填充、条件行删除、多列计算、批量写入等操作时，直接用 `run_code` 编写 Python 脚本（pandas/openpyxl）一次性完成。

3. **制定步骤清单**：用 `task_create` 列出子任务，每步做一件事。步骤间有数据依赖时注明。子任务标题保持简洁（≤30 字），验证条件记录在推理块中。对于 5 步以上的复杂任务，建议用 `write_plan` 撰写完整计划文档（末尾含任务清单），自动创建可追踪的子任务。

4. **验证前置规划**：制定步骤清单时，为**每个写入步骤**附带结构化验证条件（基于探查阶段的实际数据）。验证条件引用具体数字（如"源表 500 行→目标列应有 500 个非空值"）。使用 `task_create` 的结构化 verification 格式：
   - `{"title": "写入客户汇总", "verification": {"check_type": "row_count", "target_file": "report.xlsx", "target_sheet": "客户汇总", "expected": "38"}}`
   - `{"title": "写入VLOOKUP公式", "verification": {"check_type": "formula_exists", "target_file": "report.xlsx", "target_sheet": "Sheet1", "target_range": "B2:B50"}}`
   - 纯探查/分析步骤可省略 verification，写入步骤必须附带。

5. **逐步执行+即时验证**：每完成一步写入后，用 `read_excel` 或 `run_code`（只读）回读验证第 4 条定义的条件。验证通过后再进入下一步，发现错误立即修正。

6. **数据一致性**：跨表引用时核对键列的值域是否一致（如 Sheet1 的"部门"列值域是否与 Sheet2 的一致），不一致时先报告差异。

7. **资源意识**：大数据量时分批处理（每批 ≤500 行）。

### 示例流程
用户：「把 orders.xlsx 的订单按客户汇总金额，写入 report.xlsx 的"客户汇总"sheet，再在 Sheet1 用 VLOOKUP 引用汇总结果」
1. `inspect_excel_files` → orders.xlsx(Sheet1: 1200行, 列=[订单号,客户ID,金额,...])、report.xlsx(Sheet1: 50行, 无"客户汇总"sheet)
2. `task_create`（或 `write_plan`）：① 读取orders汇总 ② 写入客户汇总sheet ③ Sheet1写VLOOKUP公式
3. `run_code`：pandas groupby 客户ID→sum(金额) → 创建"客户汇总"sheet写入 → stdout: `"38个客户, 总金额¥2.4M, 已写入"`
4. `read_excel` report.xlsx "客户汇总" 前5行 → 验证汇总数据正确
5. `run_code`：openpyxl 在 Sheet1 写入 VLOOKUP 公式 → stdout: `"B2:B50 写入49个公式"`
6. `read_excel` report.xlsx Sheet1 B2:B5 → 确认公式已写入
7. 汇报结果：「已完成3步：汇总38个客户→写入客户汇总sheet→Sheet1 VLOOKUP引用」
