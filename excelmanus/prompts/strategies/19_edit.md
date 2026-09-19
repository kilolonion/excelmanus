---
name: tool:edit
version: "11.0.0"
priority: 104
order: 104
layer: strategy
max_tokens: 100
conditions:
  catalog_mode: [write, code]
  tool: edit_spreadsheet
---
已有文件用 operations，新建用 workbook_spec。写入使用同一份观察数据的 content_version（参数 expected_version）。VERSION_CONFLICT 表示这次没有落盘：重读受影响内容并重新判断后再写，不要只换版本号重放旧操作。相关改动一次提交，写入串行。uploads/ 只读，改表写到 outputs/ 副本。
