"""Read-only format evidence for the capability catalog, not an edit guarantee.

A renamed CSV or generic ZIP must not activate workbook tools. Only small
package metadata is read; worksheet contents are never loaded here.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree

_XML_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xltm"}
_CONTENT_TYPES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
    ".xltx": "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
    ".xltm": "application/vnd.ms-excel.template.macroEnabled.main+xml",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.main",
}
_WORKBOOK_TAGS = {
    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook",
    "{http://purl.oclc.org/ooxml/spreadsheetml/main}workbook",
}


def _xml(package: ZipFile, name: str, max_bytes: int):
    info = package.getinfo(name)
    if info.file_size > max_bytes or info.flag_bits & 1:
        raise ValueError("Workbook catalog metadata is oversized or encrypted")
    data = package.read(info)
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError("Workbook metadata must not contain a DTD")
    return root


@lru_cache(maxsize=512)
def _probe(path: str, signature: tuple[int, ...]) -> bool:
    # signature participates in the cache key: replacements/edits invalidate
    # positive AND negative observations, including same-size rewrites.
    del signature
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".xls":
            import xlrd
            book = xlrd.open_workbook(path, on_demand=True)
            try:
                return book.nsheets > 0
            finally:
                book.release_resources()
        if suffix not in _CONTENT_TYPES:
            return False
        with ZipFile(path) as package:
            types = _xml(package, "[Content_Types].xml", 1024 * 1024)
            ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
            if types.tag != ns + "Types":
                return False
            part = "xl/workbook.xml" if suffix in _XML_SUFFIXES else "xl/workbook.bin"
            matches = [node for node in types if node.tag == ns + "Override"
                       and node.get("PartName") == "/" + part]
            if len(matches) != 1 or matches[0].get("ContentType") != _CONTENT_TYPES[suffix]:
                return False
            if suffix == ".xlsb":
                # Binary workbook parsing remains the reading tool's job.
                return package.getinfo(part).file_size > 0
            root = _xml(package, part, 8 * 1024 * 1024)
            return root.tag in _WORKBOOK_TAGS
    except (OSError, ValueError, KeyError, BadZipFile, RuntimeError, EOFError, etree.XMLSyntaxError):
        return False
    except Exception:
        # xlrd uses a separate family of format-specific exceptions. A bad
        # input must not prevent discovery of other files in this workspace.
        return False


def is_workbook_file(path: str | Path) -> bool:
    """True for a recognized workbook container; does not promise edit support."""
    source = Path(path)
    try:
        stat = source.stat()
        if not source.is_file():
            return False
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return _probe(str(source.absolute()), key)
    except OSError:
        return False
