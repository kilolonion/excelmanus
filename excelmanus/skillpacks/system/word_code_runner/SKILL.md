---
name: word_code_runner
description: 用 python-docx 处理复杂 Word 操作。
file_patterns:
  - "*.docx"
version: "1.1.0"
---
模板填充、邮件合并、复杂表格、图片或页眉页脚可用 `run_code` + python-docx。路径从 `run_code.args` 传入。需要 pandas 时设 `require_excel_deps: true`。

## 1. 批量替换模板变量

适用于 `{{变量名}}` 占位符较多、需要一次性填充整份模板的场景。

```yaml
run_code:
  args:
    - "template.docx"
    - "filled.docx"
  python_command: auto
  require_excel_deps: false
  code: |
    from docx import Document
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("用法: script.py <input_path> <output_path>")

    _, input_path, output_path = sys.argv

    variables = {
        "{{客户名}}": "上海分公司",
        "{{日期}}": "2026-03-06",
        "{{负责人}}": "王敏",
    }

    doc = Document(input_path)

    for para in doc.paragraphs:
        for key, value in variables.items():
            if key in para.text:
                para.text = para.text.replace(key, str(value))

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for key, value in variables.items():
                    if key in cell.text:
                        cell.text = cell.text.replace(key, str(value))

    doc.save(output_path)
```

## 2. 邮件合并

适用于“一个模板 + 一批记录”，按 DataFrame 每行生成一份完整文档。

```yaml
run_code:
  args:
    - "offer_template.docx"
    - "candidates.csv"
    - "outputs/offers"
  python_command: auto
  require_excel_deps: true
  code: |
    from docx import Document
    import sys
    from pathlib import Path
    import pandas as pd

    if len(sys.argv) != 4:
        raise SystemExit("用法: script.py <template_path> <records_path> <output_dir>")

    _, template_path, records_path, output_dir = sys.argv
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(records_path)
    doc = None
    output_path = None

    for _, row in df.iterrows():
        doc = Document(template_path)
        variables = {
            "{{姓名}}": str(row["姓名"]),
            "{{岗位}}": str(row["岗位"]),
            "{{部门}}": str(row["部门"]),
        }

        for para in doc.paragraphs:
            for key, value in variables.items():
                if key in para.text:
                    para.text = para.text.replace(key, value)

        safe_name = str(row["姓名"]).strip().replace("/", "_").replace("\\", "_")
        output_path = output_dir / f"{safe_name}_offer.docx"
        doc.save(output_path)

    if doc is None or output_path is None:
        raise ValueError("records_path 中没有可生成的记录")

    doc.save(output_path)
```

## 3. 表格批量操作

适用于批量写入清单、统计表、报价表等结构化内容。

```yaml
run_code:
  args:
    - "report_template.docx"
    - "report_with_table.docx"
  python_command: auto
  require_excel_deps: false
  code: |
    from docx import Document
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("用法: script.py <input_path> <output_path>")

    _, input_path, output_path = sys.argv

    rows = [
        ("华东", "128", "98%"),
        ("华北", "96", "95%"),
        ("华南", "143", "99%"),
    ]

    doc = Document(input_path)
    table = doc.add_table(rows=len(rows) + 1, cols=3)
    table.style = "Table Grid"

    headers = ["区域", "订单数", "达成率"]
    for col_idx, header in enumerate(headers):
        table.cell(0, col_idx).text = header

    for row_idx, row_data in enumerate(rows, start=1):
        for col_idx, value in enumerate(row_data):
            table.cell(row_idx, col_idx).text = str(value)

    doc.save(output_path)
```

## 4. 图片插入

适用于插入签名、图表、产品图，并用尺寸参数控制版式。

```yaml
run_code:
  args:
    - "proposal.docx"
    - "chart.png"
    - "proposal_with_chart.docx"
  python_command: auto
  require_excel_deps: false
  code: |
    from docx import Document
    import sys
    from docx.shared import Inches

    if len(sys.argv) != 4:
        raise SystemExit("用法: script.py <input_path> <image_path> <output_path>")

    _, input_path, image_path, output_path = sys.argv

    doc = Document(input_path)
    doc.add_picture(image_path, width=Inches(5.5))
    doc.save(output_path)
```

## 5. 页眉页脚

适用于批量补公司名、保密标记、页脚说明等跨 section 元素。

```yaml
run_code:
  args:
    - "contract.docx"
    - "contract_branded.docx"
  python_command: auto
  require_excel_deps: false
  code: |
    from docx import Document
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("用法: script.py <input_path> <output_path>")

    _, input_path, output_path = sys.argv

    doc = Document(input_path)

    for section in doc.sections:
        header = section.header
        footer = section.footer

        if header.paragraphs:
            header.paragraphs[0].text = "ExcelManus 内部文档"
        else:
            header.add_paragraph("ExcelManus 内部文档")

        if footer.paragraphs:
            footer.paragraphs[0].text = "仅供内部审批使用"
        else:
            footer.add_paragraph("仅供内部审批使用")

    doc.save(output_path)
```

## 6. 样式操作

适用于对现有段落和 run 做局部加粗、字号调整、样式切换。

```yaml
run_code:
  args:
    - "draft.docx"
    - "draft_styled.docx"
  python_command: auto
  require_excel_deps: false
  code: |
    from docx import Document
    import sys
    from docx.shared import Pt

    if len(sys.argv) != 3:
        raise SystemExit("用法: script.py <input_path> <output_path>")

    _, input_path, output_path = sys.argv

    doc = Document(input_path)

    for para in doc.paragraphs:
        if para.text.startswith("项目概览"):
            para.style = "Heading 1"

        for run in para.runs:
            if "重点" in run.text:
                run.bold = True
                run.font.size = Pt(14)

    doc.save(output_path)
```
