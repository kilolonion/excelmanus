"""Narrow OOXML changes without round-tripping drawings through openpyxl."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import posixpath
from zipfile import ZipFile

from lxml import etree as ET

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"s": MAIN}
WORKBOOK = "xl/workbook.xml"


def xml(data: bytes):
    return ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))


def serialize(root) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def rewrite(package: ZipFile, updates: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as target:
        target.comment = package.comment
        for item in package.infolist():
            target.writestr(item, updates.get(item.filename, package.read(item.filename)))
    return output.getvalue()


def sheet_parts(package: ZipFile) -> dict[str, str]:
    relations = xml(package.read("xl/_rels/workbook.xml.rels"))
    targets = {
        node.get("Id"): posixpath.normpath(posixpath.join("xl", node.get("Target"))).lstrip("/")
        for node in relations if node.get("TargetMode", "Internal") == "Internal"
    }
    return {
        node.get("name"): targets[node.get(f"{{{REL}}}id")]
        for node in xml(package.read(WORKBOOK)).findall("s:sheets/s:sheet", NS)
    }


def merge_formula_caches(original: bytes, calculated: bytes) -> tuple[bytes, int]:
    """Copy only evaluated cell caches; retain source formulas and other parts.

    LibreOffice is a calculation engine here, not the workbook author. Its
    exported chart extensions, fonts, styles and print settings never replace
    the source package. Missing results fail before publication.
    """
    from openpyxl.utils.cell import range_boundaries, get_column_letter

    with ZipFile(BytesIO(original)) as source, ZipFile(BytesIO(calculated)) as result:
        if any(name.startswith("_xmlsignatures/") for name in source.namelist()):
            raise ValueError("Recalculation would invalidate the workbook package signature")
        source_parts, result_parts = sheet_parts(source), sheet_parts(result)
        if set(source_parts) != set(result_parts):
            raise ValueError("Calculation changed the worksheet set")
        def epoch(package):
            props = xml(package.read(WORKBOOK)).find("s:workbookPr", NS)
            return props is not None and props.get("date1904", "0") in {"1", "true"}
        if epoch(source) != epoch(result):
            raise ValueError("Calculation changed the date system")
        strings = []
        if "xl/sharedStrings.xml" in result.namelist():
            strings = ["".join(node.xpath("./s:t/text() | ./s:r/s:t/text()", namespaces=NS))
                       for node in xml(result.read("xl/sharedStrings.xml"))]
        updates = {}
        formula_count = 0
        for name, part in source_parts.items():
            root = xml(source.read(part))
            cells = {c.get("r"): c for c in root.findall("s:sheetData/s:row/s:c", NS)}
            formulas = {address: c.find("s:f", NS) for address, c in cells.items() if c.find("s:f", NS) is not None}
            if not formulas:
                continue
            evaluated = xml(result.read(result_parts[name]))
            values = {c.get("r"): c for c in evaluated.findall("s:sheetData/s:row/s:c", NS)}
            addresses = set(formulas)
            for address, formula in formulas.items():
                calculated_cell = values.get(address)
                calculated_formula = calculated_cell.find("s:f", NS) if calculated_cell is not None else None
                if calculated_formula is not None and calculated_formula.get("t") == "array":
                    before_extent = range_boundaries(formula.get("ref", address))
                    after_extent = range_boundaries(calculated_formula.get("ref", address))
                    if before_extent != after_extent:
                        raise ValueError(f"Calculation changed the array spill range: {name}!{address}")
            # Legacy array formulas have cached values outside the anchor.
            for formula in formulas.values():
                if formula.get("t") == "array" and formula.get("ref"):
                    c0, r0, c1, r1 = range_boundaries(formula.get("ref"))
                    if (r1 - r0 + 1) * (c1 - c0 + 1) > 1_000_000:
                        raise ValueError("Array formula cache exceeds the cell budget")
                    addresses.update(f"{get_column_letter(col)}{row}" for row in range(r0, r1 + 1) for col in range(c0, c1 + 1))
            for address in addresses:
                cached = values.get(address)
                if cached is None:
                    raise ValueError(f"Missing calculated cell: {name}!{address}")
                kind = cached.get("t", "n")
                value = cached.find("s:v", NS)
                if kind in {"s", "inlineStr"}:
                    text = strings[int(value.text)] if kind == "s" else "".join(cached.find("s:is", NS).itertext())
                    value = ET.Element(f"{{{MAIN}}}v")
                    value.text = text
                    kind = "str"
                if value is None or (value.text is None and kind != "str"):
                    raise ValueError(f"Missing formula cache: {name}!{address}")
                cell = cells.get(address)
                if cell is None:
                    # Keep arrays atomic until a source address can be mapped;
                    # never invent a spill layout from a different engine.
                    raise ValueError(f"Array cache has no source cell: {name}!{address}")
                for child in list(cell):
                    if child.tag in {f"{{{MAIN}}}v", f"{{{MAIN}}}is"}:
                        cell.remove(child)
                cell.set("t", kind)
                formula = cell.find("s:f", NS)
                cell.insert(list(cell).index(formula) + 1 if formula is not None else 0, deepcopy(value))
            formula_count += len(formulas)
            updates[part] = serialize(root)
        return rewrite(source, updates), formula_count


def render_selection(data: bytes, sheet: str | None, cell_range: str | None, *, fit_to_page: bool = False) -> bytes:
    """Scope print export while keeping source chart XML and formula caches.

    Hiding sheets alone is insufficient: Calc exports other sheets with saved
    print areas. Clear those print names in this temporary render input too.
    """
    from openpyxl.utils.cell import absolute_coordinate, quote_sheetname
    from excelmanus.workbook.refs import parse_rect

    with ZipFile(BytesIO(data)) as package:
        root = xml(package.read(WORKBOOK))
        sheets = root.findall("s:sheets/s:sheet", NS)
        names = [node.get("name") for node in sheets]
        if sheet is None:
            if len(names) != 1:
                raise ValueError("range 渲染需要明确 sheet")
            sheet = names[0]
        if sheet not in names:
            raise ValueError(f"工作表不存在：{sheet}；可用工作表：{names}")
        selected = names.index(sheet)
        for index, node in enumerate(sheets):
            node.set("state", "visible" if index == selected else "hidden")
        for view in root.findall("s:bookViews/s:workbookView", NS):
            view.set("activeTab", str(selected))
            view.set("firstSheet", str(selected))
        definitions = root.find("s:definedNames", NS)
        if definitions is not None:
            for node in list(definitions):
                if node.get("name") in {"_xlnm.Print_Area", "_xlnm.Print_Titles"}:
                    if node.get("localSheetId") != str(selected) or (cell_range and node.get("name") == "_xlnm.Print_Area"):
                        definitions.remove(node)
        if cell_range:
            rect = parse_rect(cell_range)
            if rect.whole_column or rect.whole_row or rect.sheet not in {None, sheet}:
                raise ValueError("渲染需要所选工作表的有限矩形区域")
            if definitions is None:
                definitions = ET.Element(f"{{{MAIN}}}definedNames")
                # definedNames follows sheets/functionGroups/externalReferences.
                following = next((child for child in root if ET.QName(child).localname in {"calcPr", "oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes", "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst"}), None)
                root.insert(list(root).index(following) if following is not None else len(root), definitions)
            area = ET.SubElement(definitions, f"{{{MAIN}}}definedName", name="_xlnm.Print_Area", localSheetId=str(selected))
            area.text = f"{quote_sheetname(sheet)}!{absolute_coordinate(rect.to_a1(include_sheet=False))}"
        updates = {WORKBOOK: serialize(root)}
        if fit_to_page:
            part = sheet_parts(package)[sheet]
            worksheet = xml(package.read(part))
            properties = worksheet.find("s:sheetPr", NS)
            if properties is None:
                properties = ET.Element(f"{{{MAIN}}}sheetPr")
                worksheet.insert(0, properties)
            setup_properties = properties.find("s:pageSetUpPr", NS)
            if setup_properties is None:
                setup_properties = ET.SubElement(properties, f"{{{MAIN}}}pageSetUpPr")
            setup_properties.set("fitToPage", "1")
            setup = worksheet.find("s:pageSetup", NS)
            if setup is None:
                setup = ET.Element(f"{{{MAIN}}}pageSetup")
                later = {"headerFooter", "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags", "drawing", "legacyDrawing", "legacyDrawingHF", "picture", "oleObjects", "controls", "webPublishItems", "tableParts", "extLst"}
                following = next((node for node in worksheet if ET.QName(node).localname in later), None)
                worksheet.insert(list(worksheet).index(following) if following is not None else len(worksheet), setup)
            setup.attrib.pop("scale", None)
            setup.set("fitToWidth", "1")
            setup.set("fitToHeight", "1")
            for tag in ("rowBreaks", "colBreaks"):
                for node in worksheet.findall(f"s:{tag}", NS):
                    worksheet.remove(node)
            updates[part] = serialize(worksheet)
        return rewrite(package, updates)
