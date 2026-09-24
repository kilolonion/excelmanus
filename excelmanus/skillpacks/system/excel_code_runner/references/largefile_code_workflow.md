# 大表的读取、计算和版本

标准汇总直接用 analyze_spreadsheet(mode="aggregate")，分文件直接用 split_spreadsheet。
以下仅用于 native 工具未覆盖的逐行 Python 计算。每页的 `regions[0].cells` 才是当前窗口，preview 只是样本。

```python
from em import observe_spreadsheet

try:
    offset = 1
    version = None
    total = 0.0
    processed = 0
    while True:
        page = observe_spreadsheet(
            file_path="outputs/book.xlsx", sheet="Sheet1", mode="range",
            range=f"A{offset}:Z{offset + 999}", facets=["data"], expected_version=version,
        )
        version = page["content_version"]
        cells = page["regions"][0]["cells"]
        for key, cell in cells.items():
            if key.split(",")[1] != "2":
                continue
            if cell.get("v") is None and cell.get("f"):
                raise ValueError("金额含未缓存公式，不能当作 0")
            if cell.get("v") is not None:
                total += float(cell["v"])
        processed += len({key.split(",")[0] for key in cells})
        if len(cells) < 1000:
            break
        offset += 1000
    print({"processed_rows": processed, "total": total, "content_version": version})
except Exception as exc:
    print(f"读取或计算失败，不能使用未完成的汇总: {exc}")
```

需要写回时：在同一次计算后把 version 作为 expected_version；修改目标列使用矩阵或 selection，
不把所有旧列重新写一遍。新建工作簿用 workbook_spec，已有工作簿用 operations。
分批写入的下一批使用上一批写回返回的版本；中断后检查已提交内容，不重放整个循环。
每个独立产物都检查 status、warnings 和实际路径后再 offer_download。
