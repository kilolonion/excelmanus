---
name: word_basic
description: Word 文档读取、编辑与内容生成
file_patterns:
  - "*.docx"
  - "*.doc"
version: "1.0.0"
---
优先使用结构化工具处理 Word 文档：
1. 先用 `inspect_word` 了解文档结构（标题树、段落数、表格分布）再进行操作。
2. 用 `read_word` 分页读取内容，避免一次加载过多段落。
3. 修改时通过 `write_word` 的 operations 数组批量操作，减少调用次数。
4. 保留原有样式 —— 替换段落时默认继承原样式，除非用户明确要求修改格式。

## 常见操作模式

### 内容替换
```json
{
  "file_path": "report.docx",
  "operations": [
    {"action": "replace", "index": 5, "text": "更新后的段落内容"}
  ]
}
```

### 追加内容
```json
{
  "file_path": "report.docx",
  "operations": [
    {"action": "append", "text": "新增的结论段落", "style": "Normal"},
    {"action": "append", "text": "参考文献", "style": "Heading 1"}
  ]
}
```

### 搜索后替换
先用 `search_word` 定位段落索引，再用 `write_word` 的 replace 操作：
```json
{"query": "旧版本号", "file_path": "release.docx"}
```
获取到 paragraph_index 后替换。

## 复杂操作
批量替换、邮件合并、模板填充等复杂场景，建议使用 `run_code` 编写 python-docx 脚本：
```python
from docx import Document
doc = Document("template.docx")
for para in doc.paragraphs:
    if "{{name}}" in para.text:
        para.text = para.text.replace("{{name}}", "张三")
doc.save("output.docx")
```
