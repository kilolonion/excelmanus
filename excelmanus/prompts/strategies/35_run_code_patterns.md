---
name: tool:run_code
version: "12.0.0"
priority: 150
order: 150
layer: strategy
max_tokens: 210
conditions:
  catalog_mode: write
  tool: run_code
---
单次操作、聚合、批量参数直接调用；循环/组合/自定义计算用 run_code。import em 绑定授权目录；未知签名/字段先直接查 introspect_capability 的 tool_detail，再写程序，只输出必要结果。程序写工作区走 em.*；禁止 Workbook.save、to_excel、ExcelWriter。写入串行。stdout 不证明业务正确；失败或取消后别重放已提交写入。检查 status/error_code、版本、实际提交结果及警告，不假设 affected_range/committed 总存在。按覆盖分页，跨页版本变化须重算。
