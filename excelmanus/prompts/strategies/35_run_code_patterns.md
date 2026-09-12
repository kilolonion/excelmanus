---
name: tool:run_code
version: "9.0.0"
priority: 150
order: 150
layer: strategy
max_tokens: 400
conditions: {}
---
工作区 xlsx 的改写必须走 SDK；直接保存工作簿必须失败。写入串行。stdout 或成功退出码不证明业务正确。不要用 run_code、shell、read_text_file 或 python -c 去读 excelmanus/、tests/、docs/。
