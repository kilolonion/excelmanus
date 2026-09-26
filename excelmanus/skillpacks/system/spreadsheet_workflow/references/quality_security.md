# 表格质量、安全与外部依据

## 经过调研后保留的原则

这些规则综合了 Microsoft Excel 的重算说明、Excel agent 文档和开源工作簿工具的常见限制；具体能力仍以 ExcelManus 当前工具合同为准。

1. **先观察再动作**：先拿到工作表、使用范围、表头、版本和必要的样式/对象事实；避免全表 dump 和只凭自然语言猜坐标。
2. **条件提交**：把观察到的 `content_version` 作为 `expected_version`。版本冲突时重新观察和规划，不能把旧请求原样重放。
3. **公式双阶段**：保存公式文本不会自动证明结果已计算。使用 `calculate_spreadsheet` 刷新缓存，再扫描 `#REF!`、`#DIV/0!`、`#VALUE!`、`#NAME?` 等错误。
4. **数值与显示分离**：日期、数字、百分比和货币保留为有类型的值，格式只负责显示。统一同列的数字格式，避免把单位塞进数值字符串。
5. **视觉闭环**：值和公式通过回读验证；宽度、高度、冻结、合并、条件格式、图表和打印效果还要通过 preview/render 验证。不能用“工具成功”代替“看起来正确”。
6. **大表分批**：优先 native 分析；自定义代码只取必要窗口，限制结果行数和输出字符，批次之间检查版本和提交回执。
7. **可重试**：操作声明目标状态而不是无条件追加；每个批次记录结果。重试前先判断是否已经提交，避免重复行、重复列和重复图表。
8. **最小权限**：`run_code` 只使用授权目录和 `import em`。不执行宏、不访问无关路径、不把外部网络或 shell 引入普通表格操作；对 `.xlsm` 保留原件并说明对象边界。

## 反模式

- 直接 `openpyxl.load_workbook(...).save()`、`pandas.to_excel()` 或 `wb.save()` 改写工作区文件；这会绕过版本、对象保真和事务回执。
- 用 `data_only` 读取后再保存，导致公式被值覆盖；用缺少缓存的公式结果参与汇总。
- 把筛选结果投影后的第 1 行当成 Excel 第 1 行，或跨页读取时混用不同版本。
- 为了“美化”覆盖既有主题、合并数据区域、改变用户给定尺寸，或创建没有表头/单位的图表。
- 看到失败就重放整批工具调用；失败可能发生在提交之后，必须先读取当前版本和回执。

## 可追溯参考

- [Microsoft Excel：Change formula recalculation, iteration, or precision](https://support.microsoft.com/en-us/excel/change-formula-recalculation-iteration-or-precision-in-excel)
- [Microsoft Learn：Excel recalculation](https://learn.microsoft.com/en-us/office/client-developer/excel/excel-recalculation)
- [Claude for Excel documentation](https://claude.com/docs/office-agents/excel)
- [openpyxl documentation](https://openpyxl.readthedocs.io/en/stable/)

外部文档只用于原则和限制的交叉验证，不是 ExcelManus 的接口契约；接口始终以 `introspect_capability`、工具 schema 和实际回执为准。
