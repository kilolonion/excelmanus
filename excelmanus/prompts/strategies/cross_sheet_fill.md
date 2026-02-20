---
name: cross_sheet_fill
version: "1.0.0"
priority: 50
layer: strategy
max_tokens: 300
conditions:
  sheet_count_gte: 2
  write_hint: "may_write"
---
## 跨 Sheet 数据填充策略

当需要从一个 Sheet 查找数据填入另一个 Sheet 时：

1. **确认完整数据范围**：用 list_sheets 确认源表和目标表的总行数，写入必须覆盖目标表的全部数据行，不能只处理前几十行。

2. **优先写值而非公式**：openpyxl 写入的公式没有缓存计算值，外部读取会显示为空。应先通过 read_excel 读取源数据，在工具层面完成匹配计算，再用 write_cells 写入具体值。

3. **匹配逻辑验证**：写入前先对少量行（如前 5 行）做匹配验证，确认键列、结果列的对应关系正确后再批量写入。

4. **写入后抽查**：批量写入完成后，用 read_excel 读取目标区域的前几行和末几行，确认值已正确写入。
