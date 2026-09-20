# 用版本绑定的读结果写回

工作区 xlsx 改写走 `em`。下列片段各自独立，路径示例均为可写副本。
uploads/ 先用 copy_file 复制到 outputs/，再读取副本；selection 绑定文件身份，不能把原件的 selection 直接用于副本。

## 只更新命中的列

```python
from em import analyze_spreadsheet, edit_spreadsheet

try:
    read = analyze_spreadsheet(
        file_path="outputs/book.xlsx", sheet_name="Sheet1", mode="filter",
        conditions=[{"column": "状态", "operator": "eq", "value": "待处理"}],
        columns=["状态"],
    )
    if read["returned_rows"]:
        selection = read["selection"]
        changed = edit_spreadsheet(
            file_path="outputs/book.xlsx",
            operations=[{"kind": "write", "selection": selection,
                         "values": [["已处理"] for _ in selection["rows"]]}],
        )
        print(changed)
    else:
        print("没有符合条件的行")
except Exception as exc:
    print(f"失败，未继续后续操作: {exc}")
```

selection 的 rows/cols 是原表 Excel 行列坐标；投影后仍可直接写回。
大 selection 的 `selection_spill` 可原样作为 `operations[].selection`。
若结果 coverage 为 truncated，只对返回选区修改；不要声称处理了所有命中行。

## 精确范围计算，只写目标列

```python
from em import inspect_spreadsheet, edit_spreadsheet

try:
    read = inspect_spreadsheet(file_path="outputs/book.xlsx", sheet_name="Sheet1",
                               mode="range", range="B2:B11", include=["formulas"])
    # 此例只处理明确的 10 行；没有公式缓存时停止，不把 null 当零。
    amounts = read["values"]
    if any(row[0] is None for row in amounts):
        raise ValueError("金额有空白或未计算公式，需要先确认")
    result = edit_spreadsheet(
        file_path="outputs/book.xlsx", expected_version=read["content_version"],
        operations=[{"kind": "write", "sheet": "Sheet1", "start_cell": "C2",
                     "values": [[float(row[0]) * 0.3] for row in amounts]}],
    )
    print(result)
except Exception as exc:
    print(f"失败，未继续后续操作: {exc}")
```

## 条件删除

`write` 只改传入矩形。更短的 values 不会清掉旧表尾部；覆盖写不等于删除。
真正删行使用 `delete_rows`，仅在工作簿结构和依赖允许时执行。

```python
from em import analyze_spreadsheet, edit_spreadsheet

try:
    read = analyze_spreadsheet(file_path="outputs/book.xlsx", sheet_name="Sheet1",
        mode="filter", conditions=[{"column": "状态", "operator": "eq", "value": "已取消"}])
    if read["returned_rows"]:
        print(edit_spreadsheet(file_path="outputs/book.xlsx",
            operations=[{"kind": "delete_rows", "selection": read["selection"]}]))
    else:
        print("没有要删除的行")
except Exception as exc:
    print(f"失败，未继续后续操作: {exc}")
```

## 跨表汇总与交付

先用 analyze mode=aggregate/pivot + join 得到只读结果。写入静态透视矩阵时用
`edit_spreadsheet(kind=pivot, sheet=源表, target_sheet=新表)`，参数与只读 pivot 一致。
覆盖任何非空目标表须 `overwrite=true`，该操作替换整张目标表的值；有对象时会拒绝。
结果不是可刷新的 Excel PivotTable。只要匹配填值时，按目标行 selection 写入所需列，不重写整张原表。

## 版本和恢复

每次后续变更使用上次读/写的 content_version。版本冲突后重读重算，不能只把版本换成最新值重放。
批量 operations 是一次工作簿提交；跨工具 edit → format 分两次提交。
批内参数错误会返回 operation_index（从 0 起）、operation_kind、committed=false 和 applied=[]，表示前面的操作也未保存。修正该操作后重试整批；如果只是后续 format 失败，保留已经成功的 edit 和它返回的版本，不重新插列或追加数据。
多文件拆分用 split_spreadsheet；遇 partial=true 保留 operation_id/tx_id 和 committed_files，不能重放整批。

## 插列、复制表头、写公式

一次 edit 的 operations 按顺序执行，后面的坐标基于前面操作后的工作表。同表复制用 `{"kind":"copy","sheet":"订单","source_range":"C1","target_start":"D1"}`；跨表复制再指定 source_sheet/target_sheet。sheet/sheet_name 是目标表别名，显式 target_sheet 必须与它一致；缺少一侧表名时使用另一侧。复制保留单元格样式并平移相对公式引用；随后 write 只改值，可以保留刚复制的表头样式。结构操作不能自动维护现有复杂引用时会拒绝，不能把重放已成功的插列当作恢复方法。
