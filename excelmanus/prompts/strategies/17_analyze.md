---
name: tool:analyze
version: "11.0.0"
priority: 102
order: 102
layer: strategy
max_tokens: 100
conditions:
  tool: analyze_spreadsheet
---
常规汇总、去重、透视优先用 aggregate、distinct、pivot；维度在另一表时用 join 后聚合。复杂计算可用 Code Mode，对 SDK 返回的数据做 pandas 处理。profile/quality 按数据框表；版式表不是缺测。多表省略 sheet 仅在单可见表或列证据唯一时自动绑定。
