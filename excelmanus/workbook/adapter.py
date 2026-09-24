"""OOXML support boundaries and bounded parser admission.

Unknown package data is never assumed to survive an openpyxl round trip.
"""

from __future__ import annotations

import re
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from excelmanus.engine_core.error_payload import SIGNED_WORKBOOK, UNSUPPORTED_PRESERVATION
from excelmanus.workbook_commit import CommitError

MAX_XML_BYTES = 64 * 1024 * 1024
# openpyxl does not preserve these Excel extensions, even when a containing
# worksheet/drawing still exists after serialization.
_UNSUPPORTED = {
    "extLst",
    "AlternateContent",
    "sp",
    "cxnSp",
    "grpSp",
    "oleObjects",
    "controls",
}
_KNOWN_PREFIXES = (
    "docProps/",
    "xl/worksheets/",
    "xl/charts/",
    "xl/drawings/",
    "xl/media/",
    "xl/comments/",
    "xl/tables/",
    "xl/pivotTables/",
    "xl/pivotCache/",
    "xl/externalLinks/",
    "xl/theme/",
)
_KNOWN_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/_rels/workbook.xml.rels",
    "xl/workbook.xml",
    "xl/styles.xml",
    "xl/sharedStrings.xml",
    "xl/calcChain.xml",
    "xl/vbaProject.bin",
}


_WORKBOOK_PART = "xl/workbook.xml"
_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_LO_CALC_URI = "{7626C862-2A13-11E5-B345-FEFF819CDC9F}"


def _preservable_workbook_extensions(root: ET.Element) -> ET.Element | None:
    """Allow only the known, reference-free LibreOffice ExcelA1 calc setting.

    Other extension payloads can own relationships or contain cell/sheet refs
    that an edit would invalidate. They still require explicit adapter support.
    """
    if root.tag != f"{{{_MAIN_NS}}}workbook":
        return None
    lists = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "extLst"]
    if len(lists) != 1 or lists[0] not in list(root):
        return None
    extensions = lists[0]
    if extensions.tag != f"{{{_MAIN_NS}}}extLst" or extensions.attrib or len(extensions) != 1:
        return None
    extension = extensions[0]
    if extension.tag != f"{{{_MAIN_NS}}}ext" or extension.attrib != {"uri": _LO_CALC_URI} or len(extension) != 1:
        return None
    setting = extension[0]
    if (setting.tag != "{http://schemas.libreoffice.org/}extCalcPr"
            or setting.attrib != {"stringRefSyntax": "ExcelA1"} or len(setting)):
        return None
    if any((node.text or "").strip() or (node.tail or "").strip() for node in extensions.iter()):
        return None
    return extensions


def _xml_identity(node: ET.Element) -> tuple:
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(),
            tuple(_xml_identity(child) for child in node))


def package_inventory(data: bytes) -> dict:
    with ZipFile(BytesIO(data)) as package:
        xml_bytes = sum(
            i.file_size
            for i in package.infolist()
            if i.filename.endswith((".xml", ".rels"))
        )
        if xml_bytes > MAX_XML_BYTES:
            raise CommitError(
                "WORKBOOK_TOO_LARGE",
                "Workbook XML exceeds the 64 MiB parser budget; use streaming analysis or split the workbook",
            )
        unknown, unsupported, preserved = [], [], []
        for name in package.namelist():
            if name not in _KNOWN_PARTS and not name.startswith(_KNOWN_PREFIXES):
                unknown.append(name)
            if name.startswith("_xmlsignatures/"):
                unsupported.append({"part": name, "feature": "package_signature"})
            if name.endswith(".xml") and name.startswith(
                (
                    "xl/worksheets/",
                    "xl/drawings/",
                    "xl/charts/",
                    "xl/workbook",
                    "xl/tables/",
                    "xl/styles",
                )
            ):
                root = ET.fromstring(package.read(name))
                features = {
                    node.tag.rsplit("}", 1)[-1] for node in root.iter()
                } & _UNSUPPORTED
                if name == _WORKBOOK_PART and _preservable_workbook_extensions(root) is not None:
                    features.discard("extLst")
                    preserved.append({"part": name, "feature": "extCalcPr", "uri": _LO_CALC_URI})
                unsupported.extend(
                    {"part": name, "feature": feature} for feature in sorted(features)
                )
        return {
            "xml_bytes": xml_bytes,
            "unknown_parts": sorted(unknown),
            "unsupported_features": unsupported,
            "preserved_features": preserved,
            "edit_support": "unsupported"
            if unsupported or unknown
            else "supported_subset",
            "limitations": [
                "openpyxl supported OOXML subset; rendering support is reported separately"
            ],
        }


def ensure_editable(data: bytes) -> dict:
    inventory = package_inventory(data)
    if inventory["unsupported_features"] or inventory["unknown_parts"]:
        signed = any(
            f["feature"] == "package_signature"
            for f in inventory["unsupported_features"]
        )
        raise CommitError(
            SIGNED_WORKBOOK if signed else UNSUPPORTED_PRESERVATION,
            "Workbook contains package data the adapter cannot safely round-trip",
            fields={"capabilities": inventory, "committed": False},
        )
    return inventory


def preserve_workbook_extensions(before: bytes | None, after: bytes) -> bytes:
    """Restore admitted workbook metadata that openpyxl does not serialize."""
    if before is None:
        return after
    with ZipFile(BytesIO(before)) as source:
        extension = _preservable_workbook_extensions(ET.fromstring(source.read(_WORKBOOK_PART)))
    if extension is None:
        return after
    with ZipFile(BytesIO(after)) as serialized:
        xml = serialized.read(_WORKBOOK_PART)
        root = ET.fromstring(xml)
        existing = root.findall(f"{{{_MAIN_NS}}}extLst")
        if existing:
            if len(existing) == 1 and _xml_identity(existing[0]) == _xml_identity(extension):
                return after
            raise CommitError(UNSUPPORTED_PRESERVATION, "Serialized workbook extension metadata differs", fields={"committed": False})
        # Keep the serialized workbook markup intact. ElementTree supplies local
        # namespace declarations for prefixes inherited from the source root.
        closing = re.search(rb"</(?:[A-Za-z_][\w.-]*:)?workbook\s*>\s*$", xml)
        if closing is None:
            raise CommitError(UNSUPPORTED_PRESERVATION, "Cannot restore workbook extension metadata", fields={"committed": False})
        xml = xml[:closing.start()] + ET.tostring(extension, encoding="utf-8") + xml[closing.start():]
        out = BytesIO()
        with ZipFile(out, "w") as restored:
            restored.comment = serialized.comment
            for item in serialized.infolist():
                restored.writestr(item, xml if item.filename == _WORKBOOK_PART else serialized.read(item.filename))
    return out.getvalue()


def verify_workbook_extensions(before: ZipFile, after: ZipFile) -> None:
    source = ET.fromstring(before.read(_WORKBOOK_PART)).findall(f"{{{_MAIN_NS}}}extLst")
    if not source:
        return
    target = ET.fromstring(after.read(_WORKBOOK_PART)).findall(f"{{{_MAIN_NS}}}extLst")
    if [_xml_identity(node) for node in source] != [_xml_identity(node) for node in target]:
        raise CommitError(UNSUPPORTED_PRESERVATION, "Workbook extension metadata was not preserved", fields={"committed": False})


def preserve_empty_custom_properties(before: bytes | None, after: bytes) -> bytes:
    """Keep LibreOffice's empty custom-property part and its package links.

    openpyxl omits an empty property collection. Only the empty, standard part
    is admitted here; unsupported nonempty property data still fails the normal
    preservation checks.
    """
    if before is None:
        return after
    part = "docProps/custom.xml"
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
    with ZipFile(BytesIO(before)) as source, ZipFile(BytesIO(after)) as serialized:
        if part not in source.namelist() or part in serialized.namelist():
            return after
        raw = source.read(part)
        props = ET.fromstring(raw)
        if (props.tag != "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}Properties"
                or props.attrib or len(props) or (props.text or "").strip()):
            return after
        source_rels = [node for node in ET.fromstring(source.read("_rels/.rels"))
                       if node.get("Type") == rel_type and node.get("Target", "").lstrip("/") == part
                       and node.get("TargetMode", "Internal") == "Internal"]
        source_types = [node for node in ET.fromstring(source.read("[Content_Types].xml"))
                        if node.get("PartName") == "/" + part]
        if len(source_rels) != 1 or len(source_types) != 1:
            return after
        rels = ET.fromstring(serialized.read("_rels/.rels"))
        if any(node.get("Type") == rel_type for node in rels):
            return after
        relation = deepcopy(source_rels[0])
        ids = {node.get("Id") for node in rels}
        index = 1
        while f"rId{index}" in ids:
            index += 1
        relation.set("Id", f"rId{index}")
        rels.append(relation)
        types = ET.fromstring(serialized.read("[Content_Types].xml"))
        if not any(node.get("PartName") == "/" + part for node in types):
            types.append(deepcopy(source_types[0]))
        updates = {"_rels/.rels": ET.tostring(rels, encoding="utf-8", xml_declaration=True),
                   "[Content_Types].xml": ET.tostring(types, encoding="utf-8", xml_declaration=True)}
        out = BytesIO()
        with ZipFile(out, "w") as restored:
            restored.comment = serialized.comment
            for item in serialized.infolist():
                restored.writestr(item, updates.get(item.filename, serialized.read(item.filename)))
            restored.writestr(source.getinfo(part), raw)
    return out.getvalue()


def relationship_edges(package) -> set:
    """Relationship identity is the resolved target/type, not mutable rId numbers."""
    import posixpath

    edges = set()
    for name in package.namelist():
        if not name.endswith(".rels"):
            continue
        owner = (
            name.replace("/_rels/", "/").removesuffix(".rels")
            if name != "_rels/.rels"
            else ""
        )
        for relation in ET.fromstring(package.read(name)):
            kind = relation.get("Type", "")
            target = relation.get("Target", "")
            mode = relation.get("TargetMode", "Internal")
            if mode == "Internal":
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(owner), target)
                ).lstrip("/")
            edges.add((owner, kind.rsplit("/", 1)[-1], target, mode))
    return edges


def verify_relationships(before, after, operations):
    permitted = {"calcChain", "sharedStrings"}
    for op in operations:
        kind = op["kind"]
        if kind == "sheet" and op.get("action") == "delete":
            # Deleting a sheet also removes its owned objects, including rels.
            return
        if kind == "delete_chart":
            permitted.update(("chart", "drawing"))
        if op.get("action") == "delete":
            permitted.update(
                {
                    "image": {"image", "drawing"},
                    "table": {"table"},
                    "pivot_table": {
                        "pivotTable",
                        "pivotCacheDefinition",
                        "pivotCacheRecords",
                    },
                    "comment": {"comments", "vmlDrawing"},
                    "hyperlink": {"hyperlink"},
                }.get(kind, set())
            )
    lost = {
        edge
        for edge in relationship_edges(before) - relationship_edges(after)
        if edge[1] not in permitted
    }
    if lost:
        raise CommitError(
            "UNSUPPORTED_PRESERVATION",
            "Serialization would discard untouched OOXML relationships",
            fields={"lost_relationships": sorted(lost)[:20], "committed": False},
        )
