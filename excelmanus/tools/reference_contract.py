"""Structured file/sheet/range reference contract for tool calls.

The public tool surface accepts compact legacy strings (``file_path``,
``sheet_name`` and A1 ranges), but every tool may also receive the same
structured reference objects.  This module is deliberately small: it only
normalizes references at the dispatch boundary and leaves workbook engines
unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, error_result

REFERENCE_DEFS: dict[str, dict[str, Any]] = {
    "FileRef": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "description": "工作区相对路径"},
            "relative": {"type": "string", "description": "path 的别名"},
            "workspace_id": {"type": "string"},
            "version": {"type": "string", "description": "读取该文件时的 content_version"},
            "content_version": {"type": "string", "description": "version 的别名"},
        },
        "anyOf": [
            {"required": ["path"]},
            {"required": ["relative"]},
            {"required": ["file_path"]},
        ],
    },
    "SheetRef": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file": {"$ref": "#/$defs/FileRef"},
            "file_ref": {"$ref": "#/$defs/FileRef"},
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "name": {"type": "string", "description": "工作表名"},
            "sheet": {"type": "string", "description": "name 的别名"},
            "sheet_name": {"type": "string", "description": "name 的别名"},
            "version": {"type": "string"},
            "content_version": {"type": "string"},
        },
        "anyOf": [
            {"required": ["name"]},
            {"required": ["sheet"]},
            {"required": ["sheet_name"]},
        ],
    },
    "RangeRef": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "sheet": {
                "oneOf": [
                    {"$ref": "#/$defs/SheetRef"},
                    {"type": "string", "description": "工作表名"},
                ],
            },
            "sheet_ref": {"$ref": "#/$defs/SheetRef"},
            "file": {"$ref": "#/$defs/FileRef"},
            "file_ref": {"$ref": "#/$defs/FileRef"},
            "path": {"type": "string"},
            "file_path": {"type": "string"},
            "sheet_name": {"type": "string"},
            "address": {"type": "string", "description": "A1 地址，如 A1:C20"},
            "range": {"type": "string", "description": "address 的别名"},
            "cell_range": {"type": "string", "description": "address 的别名"},
            "version": {"type": "string"},
            "content_version": {"type": "string"},
        },
        "anyOf": [
            {"required": ["address"]},
            {"required": ["range"]},
            {"required": ["cell_range"]},
        ],
    },
}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _ref_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict):
        return None
    return value


def _file_values(value: Any) -> tuple[str | None, str | None]:
    payload = _ref_payload(value)
    if payload is None:
        return None, None
    path = _first(payload, "path", "relative", "file_path")
    version = _first(payload, "version", "content_version")
    return (str(path) if path is not None else None,
            str(version) if version is not None else None)


def _sheet_values(value: Any) -> tuple[str | None, str | None, str | None]:
    payload = _ref_payload(value)
    if payload is None:
        return None, None, None
    nested_file = _first(payload, "file", "file_ref")
    path, version = _file_values(nested_file)
    path = path or (str(_first(payload, "path", "file_path")) if _first(payload, "path", "file_path") is not None else None)
    version = version or (str(_first(payload, "version", "content_version")) if _first(payload, "version", "content_version") is not None else None)
    name = _first(payload, "name", "sheet", "sheet_name")
    return (str(path) if path is not None else None,
            str(name) if name is not None else None,
            version)


def _range_values(value: Any) -> tuple[str | None, str | None, str | None, str | None]:
    payload = _ref_payload(value)
    if payload is None:
        return None, None, None, None
    # ``sheet`` is the canonical nested form emitted by ``RangeRef.to_dict``;
    # ``sheet_ref`` remains the explicit compatibility alias.  A plain string
    # in ``sheet`` is still accepted below as the legacy sheet-name form.
    nested_sheet = _first(payload, "sheet_ref", "sheet")
    path, sheet, version = _sheet_values(nested_sheet)
    nested_file = _first(payload, "file", "file_ref")
    file_path, file_version = _file_values(nested_file)
    path = path or file_path
    version = version or file_version
    path = path or (str(_first(payload, "path", "file_path")) if _first(payload, "path", "file_path") is not None else None)
    sheet = sheet or (str(_first(payload, "sheet_name", "sheet")) if _first(payload, "sheet_name", "sheet") is not None else None)
    version = version or (str(_first(payload, "version", "content_version")) if _first(payload, "version", "content_version") is not None else None)
    address = _first(payload, "address", "range", "cell_range")
    return (
        str(path) if path is not None else None,
        str(sheet) if sheet is not None else None,
        str(address) if address is not None else None,
        version,
    )


def _set_alias(args: dict[str, Any], key: str, value: Any) -> ToolResult | None:
    if value in (None, ""):
        return None
    old = args.get(key)
    if old not in (None, "") and old != value:
        return error_result(
            f"结构化引用与 {key} 值冲突",
            code="TOOL_ARGUMENT_VALIDATION_ERROR",
            fields={"field": key, "existing": old, "reference": value},
        )
    args[key] = value
    return None


def normalize_structured_references(
    arguments: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ToolResult | None]:
    """Fold structured refs into legacy arguments before schema validation."""
    args = dict(arguments or {})
    accepted = set((schema or {}).get("properties") or {}) if isinstance(schema, dict) else set()

    def target(preferred: tuple[str, ...], fallback: str) -> str:
        if accepted:
            for candidate in preferred:
                if candidate in accepted:
                    return candidate
        return fallback

    def optional_target(preferred: tuple[str, ...], fallback: str) -> str | None:
        if accepted:
            return next((candidate for candidate in preferred if candidate in accepted), None)
        return fallback
    # Generic refs are accepted by every file-aware schema.  They are removed
    # after normalization so strict ``additionalProperties: false`` schemas
    # remain compatible with older function signatures.
    refs: list[tuple[str, Any, str]] = [
        ("file_ref", args.pop("file_ref", None), "file"),
        ("sheet_ref", args.pop("sheet_ref", None), "sheet"),
        ("range_ref", args.pop("range_ref", None), "range"),
        ("source_range_ref", args.pop("source_range_ref", None), "source_range"),
        ("target_range_ref", args.pop("target_range_ref", None), "target_start"),
        ("source_ref", args.pop("source_ref", None), "source_file"),
        ("destination_ref", args.pop("destination_ref", None), "destination_file"),
    ]
    for _label, raw, kind in refs:
        if raw in (None, ""):
            continue
        if kind == "file":
            path, version = _file_values(raw)
            file_key = target(
                ("file_path", "path", "source", "destination", "source_file", "destination_file"),
                "file_path",
            )
            for key, value in ((file_key, path), (target(("expected_version", "content_version"), "expected_version"), version)):
                err = _set_alias(args, key, value)
                if err is not None:
                    return args, err
        elif kind == "sheet":
            path, sheet, version = _sheet_values(raw)
            file_key = target(("file_path", "path"), "file_path")
            sheet_key = target(("sheet_name", "sheet"), "sheet_name")
            for key, value in ((file_key, path), (sheet_key, sheet), (target(("expected_version", "content_version"), "expected_version"), version)):
                err = _set_alias(args, key, value)
                if err is not None:
                    return args, err
        elif kind in {"source_file", "destination_file"}:
            path, version = _file_values(raw)
            if kind == "source_file":
                file_key = target(("source_file", "source", "file_path", "path"), "source_file")
                version_key = optional_target(("source_version", "expected_version", "content_version"), "source_version")
            else:
                file_key = target(("destination_file", "destination", "file_path", "path"), "destination_file")
                version_key = optional_target(("destination_version", "expected_version", "content_version"), "destination_version")
            pairs = [(file_key, path)]
            if version_key is not None:
                pairs.append((version_key, version))
            for key, value in pairs:
                err = _set_alias(args, key, value)
                if err is not None:
                    return args, err
        else:
            path, sheet, address, version = _range_values(raw)
            file_key = target(("file_path", "path"), "file_path")
            sheet_key = target(("sheet_name", "sheet"), "sheet_name")
            range_key = target((kind, "range", "cell_range"), kind)
            for key, value in ((file_key, path), (sheet_key, sheet), (range_key, address), (target(("expected_version", "content_version"), "expected_version"), version)):
                err = _set_alias(args, key, value)
                if err is not None:
                    return args, err
    return args, None


def augment_reference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the v1 structured reference fields to a tool's wire schema."""
    if not isinstance(schema, dict) or schema.get("type") not in ("object", ["object", "null"]):
        return schema
    props = schema.get("properties")
    if not isinstance(props, dict):
        return schema
    relevant = set(props) & {
        "file_path", "path", "sheet", "sheet_name", "range", "cell_range",
        "source_range", "target_start", "source", "destination", "source_file", "destination_file",
    }
    if not relevant:
        return schema
    out = deepcopy(schema)
    out.setdefault("$defs", {})
    out["$defs"].update(deepcopy(REFERENCE_DEFS))
    for name, ref_name, description in (
        ("file_ref", "FileRef", "结构化文件引用；等价于 file_path + expected_version"),
        ("sheet_ref", "SheetRef", "结构化工作表引用；包含文件和 sheet 名"),
        ("range_ref", "RangeRef", "结构化区域引用；包含文件、sheet、A1 地址和版本"),
        ("source_range_ref", "RangeRef", "source_range 的结构化引用"),
        ("target_range_ref", "RangeRef", "target_start 的结构化引用"),
        ("source_ref", "FileRef", "source 的结构化文件引用"),
        ("destination_ref", "FileRef", "destination 的结构化文件引用"),
    ):
        if name not in out["properties"]:
            out["properties"][name] = {
                "$ref": f"#/$defs/{ref_name}",
                "description": description,
            }
    out["x-excelmanus-reference-contract"] = "v1"
    return out
