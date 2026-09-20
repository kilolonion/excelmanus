---
name: tool:edit
version: "11.0.0"
priority: 104
order: 104
layer: strategy
max_tokens: 150
conditions:
  catalog_mode: write
  tool: edit_spreadsheet
---
已有文件用 operations，新建用 workbook_spec。写入带观察到的 content_version；filter/range 的 selection 可用于 write/delete_rows，不能把投影列当 A 列。pivot 覆盖源表须 overwrite=true；含公式的 transform 会拒绝整表重写。VERSION_CONFLICT 表示这次没有落盘：重读重算，不要只换版本号重放。改动串行；edit→format 分别报告。检查 warnings/data_loss_warnings。uploads/ 只读，改表写到 outputs/。
