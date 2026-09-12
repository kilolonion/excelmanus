---
name: word_basic
description: Word 文档读取、编辑与内容生成。
file_patterns:
  - "*.docx"
  - "*.doc"
version: "1.1.0"
---
`inspect_word` 看结构，`read_word` 分页读，`write_word` 用 operations 改段落。替换默认继承原样式。

```json
{
  "file_path": "report.docx",
  "operations": [
    {"action": "replace", "index": 5, "text": "更新后的段落内容"}
  ]
}
```
