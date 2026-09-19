---
name: word_basic
description: Word 文档读取、编辑与 Excel 双向搬运。
file_patterns:
  - "*.docx"
version: "1.2.0"
---
只支持 `.docx`。读取：`inspect_word` 看结构（标题树/表格/节），`read_word` 分页读段落与表格（表格在 `tables` 字段，含单元格文本），`search_word` 搜索文本。

写入走 `write_word` 的 `operations`，按顺序执行，任一操作失败则整次不落盘：

- 段落四则：`replace`（保留原段落样式与首个 run 的行内格式，需要改样式时显式传 `style`）/ `insert_after` / `append` / `delete`；
- `replace_table`：整表换成工作簿某个 range。
  `{"action": "replace_table", "table_index": 0, "source_file": "book.xlsx", "source_sheet": "Sheet1", "source_range": "A1:D10"}`
  用 `table_index`（0-based）或 `caption`（表前最近非空段落，子串匹配）定位；表格含合并单元格时会拒绝，改用 run_code；
- `fill_template`：填充正文与表格里的 `{{列名}}` 占位符与书签。
  `{"action": "fill_template", "values": {"客户名": "上海分公司"}}` 或 `source_file` + `source_row`（按 `header_row` 表头行取列名）；未给值的键会出现在结果的 `unfilled_keys` 里；
- `extract_table`：把 Word 表抽进 xlsx（新文件或已有 sheet）。
  `{"action": "extract_table", "table_index": 0, "target_file": "out.xlsx", "target_sheet": "Sheet1"}`
  纯抽取调用不改 docx；数字文本会解析为数字（前导零除外）。

按模板逐行生成多份文档：先 `read_word`/表工具拿到名册，再逐行调用
`write_word(file_path="template.docx", operations=[{"action": "fill_template", ...}], output_file="reports/客户A.docx")`；
`output_file` 只写新文件，不改源模板。

数字以 xlsx 为准。样式精调、页眉页脚、复杂版式、合并单元格表走 `run_code` + python-docx。
