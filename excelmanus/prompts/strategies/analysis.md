---
name: analysis
version: "1.0.0"
priority: 48
layer: strategy
max_tokens: 200
conditions:
  write_hint: "read_only"
---
## 数据分析策略

1. **先结构后数据**：先用 list_sheets 了解 sheet 结构和行列数，再用 read_excel 获取样本数据，需要统计分析时用 `run_code` + pandas `describe()` 完成。
   - **Think-Act**：每次调用前用 1 句说明意图（如「先看结构再取样本」）；list_sheets/read_excel 返回后，用「观察」总结：有哪些 sheet、列名、行数、样本特征，再决定是继续取数还是跑 describe。

2. **结论必须附证据**：每个统计结论必须附带具体数字和来源范围（如"A列平均值=1234，来自 Sheet1!A2:A500"）。

3. **多条件筛选一次完成**：需要组合条件时使用 filter_data 的 conditions 数组 + logic 参数，禁止分多次调用再手动取交集。
   - **Think-Act**：调用 filter_data 前说明「观察到的列与取值 + 要筛选的条件组合 + 为什么用该 logic」。

4. **大数据量注意**：行数超过 read_excel 默认预览时，用 `run_code` + pandas 读取并计算统计摘要，而非尝试读取全量数据。
   - **Think-Act**：选择 run_code 而非 read_excel 时，推理中说明「行数超预览，改为 run_code 做摘要」。

5. **异常如实报告**：发现空值、重复、类型不一致等数据质量问题时如实报告，不忽略。
   - **Think-Act**：发现异常时，先输出「观察：某列空值比例/重复条数/类型」再给出结论或建议，不要直接跳到结论。

6. **行数口径声明**：汇报行数时必须注明统计口径。`len(df)` / `df.shape[0]` 是"数据行（不含表头）"，`ws.max_row` 是"物理行（含表头和可能的空尾行）"。向用户展示时使用"数据行数: N（不含表头）"格式，避免歧义。
