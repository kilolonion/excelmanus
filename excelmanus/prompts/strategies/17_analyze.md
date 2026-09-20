---
name: tool:analyze
version: "11.0.0"
priority: 102
order: 102
layer: strategy
max_tokens: 150
conditions:
  tool: analyze_spreadsheet
---
汇总、去重、透视优先用 aggregate、distinct、pivot；另一表维度用 join 后聚合。relationships 是列级证据，不等于业务主键。profile/quality 按数据框表，版式表不是缺测。受 limit、条件或转换影响时报告覆盖范围；多表只有证据唯一时省略 sheet。
