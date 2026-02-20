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

1. **先结构后数据**：先用 list_sheets 了解 sheet 结构和行列数，再用 read_excel 获取样本数据，最后用 analyze_data 做统计。

2. **结论必须附证据**：每个统计结论必须附带具体数字和来源范围（如"A列平均值=1234，来自 Sheet1!A2:A500"）。

3. **多条件筛选一次完成**：需要组合条件时使用 filter_data 的 conditions 数组 + logic 参数，禁止分多次调用再手动取交集。

4. **大数据量注意**：行数超过 read_excel 默认预览时，用 analyze_data 获取统计摘要而非尝试读取全量数据。

5. **异常如实报告**：发现空值、重复、类型不一致等数据质量问题时如实报告，不忽略。
