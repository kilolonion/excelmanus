"""Deterministic checks and disk-backed SQL for spreadsheet datasets."""
from __future__ import annotations
import ast
import csv
from io import BytesIO
import json
import operator
from pathlib import Path
import sqlite3
import tempfile
import time

from openpyxl import load_workbook, Workbook
from openpyxl.utils import get_column_letter

from excelmanus.tools.registry import ToolDef
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.engine_core.tool_result import from_payload, error_result
from excelmanus.tools.spreadsheet_engine_tools import _source, _publish, _cancelled, _failure
from excelmanus.workbook.formula_values import FormulaValueError


def init_guard(workspace_root): bind_workspace(workspace_root)


def evaluate_expression(expression, values):
    """Small arithmetic/predicate language; no attributes, imports or arbitrary calls."""
    tree=ast.parse(expression,mode="eval")
    if len(list(ast.walk(tree)))>100: raise ValueError("表达式过长")
    binary={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod}
    compare={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
    def visit(node):
        if isinstance(node,ast.Expression): return visit(node.body)
        if isinstance(node,ast.Constant) and isinstance(node.value,(str,int,float,bool,type(None))): return node.value
        if isinstance(node,ast.Name) and node.id in values: return values[node.id]
        if isinstance(node,ast.BinOp) and type(node.op) in binary:
            a,b=visit(node.left),visit(node.right)
            if not isinstance(a,(int,float)) or not isinstance(b,(int,float)): raise ValueError("算术表达式仅接受数值")
            return binary[type(node.op)](a,b)
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd,ast.Not)):
            v=visit(node.operand)
            return not v if isinstance(node.op,ast.Not) else -v if isinstance(node.op,ast.USub) else +v
        if isinstance(node,ast.BoolOp):
            return all(visit(v) for v in node.values) if isinstance(node.op,ast.And) else any(visit(v) for v in node.values)
        if isinstance(node,ast.Compare):
            left=visit(node.left)
            for op,right in zip(node.ops,node.comparators):
                value=visit(right)
                if type(op) not in compare or not compare[type(op)](left,value): return False
                left=value
            return True
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {"abs","round","min","max"} and not node.keywords:
            args=[visit(a) for a in node.args]
            if not all(isinstance(a,(int,float)) for a in args): raise ValueError("函数参数必须是数值")
            return {"abs":abs,"round":round,"min":min,"max":max}[node.func.id](*args)
        raise ValueError("不支持的表达式；仅允许列名/列字母、算术、比较和 abs/round/min/max")
    return visit(tree)


def validate_spreadsheet(
    file_path,
    rules,
    sheet=None,
    expected_version=None,
    max_failures=200,
    allow_uncached=True,
):
    book=None
    try:
        if not isinstance(rules,list) or not rules or not 1<=max_failures<=2000: raise ValueError("rules/max_failures 无效")
        source,data,version=_source(file_path,expected_version)
        book=load_workbook(BytesIO(data),data_only=False)
        from excelmanus.workbook.formula_values import attach_formula_source,analysis_rows
        attach_formula_source(book,data)
        results=[]; failures=[]; failure_count=0; versions={file_path:version}
        uncalculated_cells=[]
        validation_status="complete"

        def note_uncalculated(cells):
            nonlocal validation_status
            validation_status = "partial"
            for cell in cells or []:
                cell = str(cell)
                if cell and cell not in uncalculated_cells:
                    uncalculated_cells.append(cell)

        for number,rule in enumerate(rules):
            ws=book[rule.get("sheet") or sheet] if rule.get("sheet") or sheet else book.active
            if not rule.get("sheet") and not sheet and len(book.worksheets)>1: raise ValueError("多工作表需要 sheet")
            kind=rule.get("kind"); failed=0; checked=0
            def fail(address,actual,expected=None):
                nonlocal failed,failure_count
                failed+=1; failure_count+=1
                if len(failures)<max_failures: failures.append({"rule":number,"kind":kind,"sheet":ws.title,"cell":address,"actual":actual,"expected":expected})
            if kind=="formula_errors":
                cached=load_workbook(BytesIO(data),data_only=True,read_only=True)
                try:
                    matrix=list(cached[ws.title].iter_rows())
                    for row in matrix:
                        for cell in row:
                            checked+=1
                            if cell.data_type=="e": fail(cell.coordinate,cell.value,"no formula error")
                    missing=[]
                    for cell in ws._cells.values():
                        if cell.data_type=="f" and (cell.row>len(matrix) or cell.column>len(matrix[cell.row-1]) or matrix[cell.row-1][cell.column-1].value is None):
                            missing.append(f"{ws.title}!{cell.coordinate}")
                    if missing:
                        if allow_uncached:
                            note_uncalculated(missing)
                        else:
                            for cell in missing:
                                fail(cell.split("!", 1)[-1],"missing_cache","calculated value")
                finally: cached.close()
            else:
                try:
                    rows=analysis_rows(ws)
                except FormulaValueError as exc:
                    if not allow_uncached:
                        raise
                    note_uncalculated(exc.cells)
                    results.append({
                        "rule":number,
                        "kind":kind,
                        "checked":0,
                        "failed":0,
                        "status":"uncalculated",
                        "formula_cache":"missing",
                        "cells":list(exc.cells or [])[:max_failures],
                    })
                    continue
                header_row=int(rule.get("header_row",1)); header=rows[header_row-1]
                names=[str(h) if h is not None else get_column_letter(i+1) for i,h in enumerate(header)]
                def cols(raw): return [names.index(v) if isinstance(v,str) and v in names else int(v)-1 for v in raw]
                indices=cols(rule.get("columns") or ([rule["column"]] if "column" in rule else []))
                body=rows[header_row:]
                if kind in {"unique","required","foreign_key"}:
                    if not indices: raise ValueError(f"{kind} 需要 columns")
                    seen=set(); reference=set()
                    if kind=="foreign_key":
                        ref=rule["reference"]; path,raw,ver=_source(ref["file_path"],ref.get("expected_version")); versions[ref["file_path"]]=ver
                        other=load_workbook(BytesIO(raw),data_only=False)
                        try:
                            attach_formula_source(other,raw)
                            other_ws=other[ref["sheet"]] if ref.get("sheet") else other.active
                            other_rows=analysis_rows(other_ws); h=int(ref.get("header_row",1)); headers=list(map(str,other_rows[h-1])); rc=[headers.index(c) for c in ref["columns"]]
                            reference={tuple(row[c] for c in rc) for row in other_rows[h:]}
                        finally: other.close()
                    for row_number,row in enumerate(body,header_row+1):
                        key=tuple(row[c] for c in indices); checked+=1
                        bad=any(v is None or v=="" for v in key) if kind=="required" else key in seen if kind=="unique" else key not in reference
                        if bad: fail(f"{get_column_letter(indices[0]+1)}{row_number}",list(key),kind)
                        seen.add(key)
                elif kind=="row_expression":
                    expression=rule["expression"]
                    for row_number,row in enumerate(body,header_row+1):
                        checked+=1
                        env={get_column_letter(i+1):v for i,v in enumerate(row)}; env.update({k:v for k,v in zip(names,row) if k.isidentifier()})
                        try: ok=bool(evaluate_expression(expression,env))
                        except (ValueError,TypeError,ZeroDivisionError) as exc: fail(f"A{row_number}",str(exc),expression); continue
                        if not ok: fail(f"A{row_number}",row,expression)
                elif kind=="total":
                    from excelmanus.workbook.numeric import parse_number
                    from math import fsum,isfinite
                    if len(indices)!=1: raise ValueError("total 需要一个 column")
                    values=[parse_number(row[indices[0]]) for row in body if row[indices[0]] is not None]
                    if not all(isfinite(v) for v in values): raise ValueError("合计列存在不能转换的数值")
                    value=fsum(values); expected=rule["expected"]; checked=len(values)
                    if abs(value-expected)>float(rule.get("tolerance",1e-9)): fail(f"{get_column_letter(indices[0]+1)}{header_row+1}",value,expected)
                else: raise ValueError(f"不支持的校验规则 {kind}")
            results.append({"rule":number,"kind":kind,"checked":checked,"failed":failed})
        return from_payload({
            "status":"partial" if validation_status != "complete" else "success",
            "file_path":file_path,
            "content_version":version,
            "valid":None if validation_status != "complete" else failure_count==0,
            "validation_status":validation_status,
            "uncalculated_cells":uncalculated_cells,
            "rules":results,
            "failure_count":failure_count,
            "failures":failures,
            "truncated":failure_count>len(failures),
            "source_versions":versions,
        })
    except FormulaValueError as exc:
        return error_result(
            str(exc),
            code=exc.code,
            fields={
                "cells":list(exc.cells or []),
                "validation_status":"blocked",
                "formula_cache":"missing",
                "executed":True,
                "committed":False,
            },
        )
    except (ValueError,KeyError,TypeError,OSError) as exc: return _failure(exc)
    finally:
        if book is not None: book.close()


def query_spreadsheet(sources, sql, output_path=None, max_rows=200, timeout_seconds=60, expected_output_version=None):
    """Stream source rows into a temporary SQLite database and query/export them."""
    try:
        if not isinstance(sources,list) or not 1<=len(sources)<=20 or not 1<=max_rows<=2000 or not 1<=timeout_seconds<=300: raise ValueError("sources/max_rows/timeout_seconds 无效")
        from excelmanus.workbook_commit import content_version_of_file
        from excelmanus.workspace.file_service import TargetSpec,ReadDependency,service_for_guard
        import re
        if not re.match(r"^\s*(SELECT|WITH)\b",sql,re.I): raise ValueError("仅支持 SELECT/WITH 查询")
        if output_path and Path(output_path).suffix.lower() not in {".xlsx",".csv"}: raise ValueError("输出只支持 xlsx 或 csv")
        started=time.monotonic(); dependencies=[]; summary=[]
        def check():
            _cancelled()
            if time.monotonic()-started>timeout_seconds: raise ValueError("数据查询超时")
        with tempfile.TemporaryDirectory(prefix="excelmanus-query-") as raw:
            root=Path(raw); connection=sqlite3.connect(root/"data.sqlite")
            try:
                for index,source in enumerate(sources):
                    alias=source.get("name",f"data{index+1}")
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",alias): raise ValueError("源 name 仅支持字母数字下划线")
                    path=require_guard().resolve_and_validate(source["file_path"])
                    version=content_version_of_file(path)
                    if source.get("expected_version") and source["expected_version"]!=version: raise ValueError("源文件版本冲突")
                    # Snapshot on disk avoids holding the workbook ZIP and rows in RAM.
                    snapshot=root/(alias+path.suffix); shutil_copy(path,snapshot)
                    if content_version_of_file(snapshot)!=version: raise ValueError("读取期间源文件改变")
                    dependencies.append(ReadDependency(path.relative_to(require_guard().workspace_root).as_posix(),version))
                    book=None; stream=None; formulas=None; formula_iter=None
                    try:
                        if path.suffix.lower() in {".csv",".tsv"}:
                            stream=snapshot.open(encoding=source.get("encoding","utf-8-sig"),newline="")
                            iterator=csv.reader(stream,delimiter="\t" if path.suffix.lower()==".tsv" else ",")
                        else:
                            book=load_workbook(snapshot,read_only=True,data_only=True)
                            if not source.get("sheet") and len(book.worksheets)>1: raise ValueError("数据源需指定 sheet")
                            ws=book[source["sheet"]] if source.get("sheet") else book.active
                            iterator=ws.iter_rows(values_only=True)
                            formulas=load_workbook(snapshot,read_only=True,data_only=False)
                            formula_iter=formulas[ws.title].iter_rows()
                        header_row=int(source.get("header_row",1))
                        if header_row<1: raise ValueError("header_row 必须为正数")
                        for _ in range(header_row):
                            header=next(iterator)
                            if formulas: next(formula_iter)
                        headers=[str(v) if v is not None else "" for v in header]
                        if not all(headers) or len(set(headers))!=len(headers): raise ValueError("数据源表头必须非空且唯一")
                        selected=source.get("columns") or headers
                        positions=[headers.index(c) for c in selected]
                        quote=lambda v:'"'+v.replace('"','""')+'"'
                        connection.execute(f'CREATE TABLE "{alias}" ({",".join(quote(c) for c in selected)})')
                        command=f'INSERT INTO "{alias}" VALUES ({",".join("?" for _ in selected)})'
                        batch=[]; count=0
                        types=source.get("types") or {}
                        for row in iterator:
                            if formulas:
                                formula_row=next(formula_iter)
                                if any(formula_row[i].data_type=="f" and row[i] is None for i in positions): raise ValueError("源数据存在缺失公式缓存，请先重算")
                            values=[]
                            for c,i in zip(selected,positions):
                                v=row[i] if i<len(row) else None
                                if v is not None and types.get(c)=="number":
                                    from excelmanus.workbook.numeric import parse_number
                                    import math
                                    v=parse_number(v)
                                    if not math.isfinite(v): raise ValueError(f"{alias}.{c} 包含无法转换的数值")
                                elif v is not None and types.get(c)=="text": v=str(v)
                                elif hasattr(v,"isoformat"): v=v.isoformat()
                                values.append(v)
                            batch.append(values); count+=1
                            if len(batch)>=1000: check(); connection.executemany(command,batch); batch.clear()
                        if batch: connection.executemany(command,batch)
                        connection.commit(); summary.append({"name":alias,"file_path":source["file_path"],"content_version":version,"rows":count,"columns":selected})
                    finally:
                        if formula_iter is not None:
                            formula_iter = None
                        iterator = None
                        if book: book.close()
                        if formulas: formulas.close()
                        if stream: stream.close()
                allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
                def authorizer(action,a,b,db,trigger):
                    if action==sqlite3.SQLITE_FUNCTION and str(b or a).lower() in {"load_extension","readfile","writefile"}: return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
                connection.set_authorizer(authorizer)
                def progress():
                    try: check(); return 0
                    except Exception: return 1
                connection.set_progress_handler(progress,10000)
                cursor=connection.execute(sql)
                columns=[d[0] for d in cursor.description]; preview=[]; total=0
                workbook=None; csv_stream=None; writer=None
                artifact=root/"result.xlsx" if output_path and output_path.endswith(".xlsx") else root/"result.csv"
                if output_path:
                    if artifact.suffix==".xlsx": workbook=Workbook(write_only=True); ws=workbook.create_sheet("Result"); ws.append(columns)
                    else: csv_stream=artifact.open("w",encoding="utf-8-sig",newline=""); writer=csv.writer(csv_stream); writer.writerow(columns)
                try:
                    for row in cursor:
                        total+=1
                        if len(preview)<max_rows: preview.append(list(row))
                        if workbook:
                            from openpyxl.cell import WriteOnlyCell
                            items=[]
                            for value in row:
                                c=WriteOnlyCell(ws,value=value)
                                if isinstance(value,str): c.data_type="s"
                                items.append(c)
                            if total>=1048576: raise ValueError("结果超出 Excel 最大行数，请输出 CSV")
                            ws.append(items)
                        if writer: writer.writerow(row)
                        if total%1000==0: check()
                    if workbook: workbook.save(artifact)
                finally:
                    if csv_stream: csv_stream.close()
                    if workbook: workbook.close()
                payload={"status":"success","columns":columns,"values":preview,"total_rows":total,"truncated":total>len(preview),"sources":summary,"engine":"sqlite","storage":"disk","elapsed_seconds":round(time.monotonic()-started,3)}
                if output_path:
                    dest=require_guard().resolve_and_validate(output_path)
                    if any(dest==require_guard().workspace_root/dep.path for dep in dependencies): raise ValueError("query 输出不能覆盖数据源")
                    if dest.exists() and expected_output_version is None: raise ValueError("输出已存在，需要 expected_output_version")
                    check()
                    receipt=service_for_guard(require_guard()).apply_batch([TargetSpec("update" if dest.exists() else "create",str(dest.relative_to(require_guard().workspace_root)),data=artifact.read_bytes(),expected_version=expected_output_version)],read_dependencies=dependencies)
                    payload.update(file_path=receipt.primary_path(),content_version=receipt.primary_version(),receipt=receipt.to_dict())
                return from_payload(payload)
            finally: connection.close()
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error,StopIteration) as exc: return _failure(exc)


def shutil_copy(src,dest):
    import shutil
    shutil.copyfile(src,dest)


def get_tools():
    return [ToolDef(name="validate_spreadsheet",description="确定性业务校验：unique/required/foreign_key/row_expression/total/formula_errors；返回失败单元格与真实覆盖数量，不修改文件。公式缓存缺失时默认返回 validation_status=partial、valid=null 和未计算单元格；allow_uncached=false 才阻断。",func=validate_spreadsheet,write_effect="none",input_schema={"type":"object","additionalProperties":False,"properties":{"file_path":{"type":"string"},"sheet":{"type":"string"},"expected_version":{"type":"string"},"max_failures":{"type":"integer","minimum":1,"maximum":2000},"allow_uncached":{"type":"boolean","description":"公式缓存缺失时是否允许 partial/valid=null；默认 true，不声称业务校验已通过。"},"rules":{"type":"array","minItems":1,"items":{"type":"object","properties":{"kind":{"type":"string","enum":["unique","required","foreign_key","row_expression","total","formula_errors"]},"sheet":{"type":"string"},"header_row":{"type":"integer","minimum":1},"columns":{"type":"array","items":{"type":["string","integer"]}},"column":{"type":["string","integer"]},"expression":{"type":"string","description":"列名或 A/B 列字母，例如 abs(金额-数量*单价)<0.01"},"expected":{"type":"number"},"tolerance":{"type":"number","minimum":0},"reference":{"type":"object","description":"{file_path,sheet,columns,header_row?,expected_version?}"}},"required":["kind"],"additionalProperties":False}}},"required":["file_path","rules"]},max_result_chars=8000),
            ToolDef(name="query_spreadsheet",description="将 Excel/CSV 按行流入磁盘 SQLite，执行只读 SQL；支持多表 join/union/计算列/窗口统计。返回有限预览，可将全部结果原子导出 xlsx/csv，源公式必须有缓存。",func=query_spreadsheet,write_effect="workspace_write",input_schema={"type":"object","additionalProperties":False,"properties":{"sources":{"type":"array","minItems":1,"maxItems":20,"items":{"type":"object","properties":{"file_path":{"type":"string"},"name":{"type":"string"},"sheet":{"type":"string"},"header_row":{"type":"integer","minimum":1},"columns":{"type":"array","items":{"type":"string"}},"types":{"type":"object","additionalProperties":{"enum":["number","text"]}},"encoding":{"type":"string"},"expected_version":{"type":"string"}},"required":["file_path"],"additionalProperties":False}},"sql":{"type":"string","description":"SELECT/WITH；源表名默认 data1/data2，可用 sources.name 指定。列名建议用双引号。"},"output_path":{"type":"string"},"expected_output_version":{"type":"string"},"max_rows":{"type":"integer","minimum":1,"maximum":2000},"timeout_seconds":{"type":"integer","minimum":1,"maximum":300}},"required":["sources","sql"]},max_result_chars=8000)]
