"""Compare appearance by cell coordinate, independent of data-key alignment."""
from openpyxl.xml.functions import tostring


def _xml(value):
    return tostring(value.to_tree()).decode("utf-8") if value is not None else None


def compare_appearance(left, right, sheet_a=None, sheet_b=None, max_diffs=500):
    a=left[sheet_a] if sheet_a else left.worksheets[0]
    b=right[sheet_b] if sheet_b else right.worksheets[0]
    differences=[]; total=0
    def diff(kind,key,x,y):
        nonlocal total
        if x==y: return
        total+=1
        if len(differences)<max_diffs: differences.append({"kind":kind,"cell_or_object":str(key),"before":x,"after":y})
    for pos in sorted(set(a._cells)|set(b._cells)):
        ca=a.cell(*pos); cb=b.cell(*pos)
        if ca.has_style or cb.has_style:
            sa={k:_xml(getattr(ca,k)) for k in ("font","fill","border","alignment","protection")}
            sb={k:_xml(getattr(cb,k)) for k in ("font","fill","border","alignment","protection")}
            sa["number_format"]=ca.number_format; sb["number_format"]=cb.number_format
            diff("cell_style",ca.coordinate,sa,sb)
        diff("comment",ca.coordinate, (ca.comment.text,ca.comment.author) if ca.comment else None,(cb.comment.text,cb.comment.author) if cb.comment else None)
        diff("hyperlink",ca.coordinate,_xml(ca.hyperlink),_xml(cb.hyperlink))
    diff("merged_cells","sheet",sorted(str(m) for m in a.merged_cells.ranges),sorted(str(m) for m in b.merged_cells.ranges))
    for attr in ("row_dimensions","column_dimensions"):
        x=getattr(a,attr); y=getattr(b,attr)
        for key in sorted(set(x)|set(y),key=str):
            diff(attr,key,dict(x[key]) if key in x else None,dict(y[key]) if key in y else None)
    for attr in ("page_margins","page_setup","print_options","sheet_properties","sheet_format","views","data_validations","auto_filter"):
        diff(attr,"sheet",_xml(getattr(a,attr)),_xml(getattr(b,attr)))
    diff("print_area","sheet",a.print_area,b.print_area)
    diff("print_titles","sheet",a.print_titles,b.print_titles)
    for attr in ("tables","defined_names"):
        x=getattr(a,attr); y=getattr(b,attr)
        for key in sorted(set(x)|set(y)):
            diff(attr,key,_xml(x[key]) if key in x else None,_xml(y[key]) if key in y else None)
    for attr in ("_charts","_pivots"):
        x=getattr(a,attr); y=getattr(b,attr)
        for i in range(max(len(x),len(y))): diff(attr,i,_xml(x[i]) if i<len(x) else None,_xml(y[i]) if i<len(y) else None)
    diff("images","count",len(a._images),len(b._images))
    cf=lambda ws:[(str(k.sqref),[_xml(rule) for rule in v]) for k,v in ws.conditional_formatting._cf_rules.items()]
    diff("conditional_formatting","sheet",cf(a),cf(b))
    for key in sorted(set(left.defined_names)|set(right.defined_names)):
        diff("workbook_name",key,_xml(left.defined_names.get(key)),_xml(right.defined_names.get(key)))
    return {"alignment":"position","sheet_a":a.title,"sheet_b":b.title,"difference_count":total,"differences":differences,"truncated":total>len(differences),"coverage":"cell styles/layout/rules/chart and pivot XML; images count only"}
