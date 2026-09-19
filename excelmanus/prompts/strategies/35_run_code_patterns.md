---
name: tool:run_code
version: "11.0.0"
priority: 150
order: 150
layer: strategy
max_tokens: 150
conditions:
  catalog_mode: [write, code]
  tool: run_code
---
工作区表格改写必须走 em.*；Workbook.save、to_excel、ExcelWriter 对工作区 xlsx 会失败。写入串行。stdout 不证明业务正确。可用 pandas 处理 SDK 返回的数据，也可读取获准的工作区文件做纯分析；改回工作簿仍以 SDK 读取作为版本依据。按覆盖与资源选择一次读或分页；跨页版本变化须重算。参数不明时在程序内 introspect_capability。
