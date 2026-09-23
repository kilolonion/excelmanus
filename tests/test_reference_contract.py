"""Structured workbook reference contract regressions."""

from __future__ import annotations

from excelmanus.tools.reference_contract import (
    augment_reference_schema,
    normalize_structured_references,
)


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "sheet_name": {"type": "string"},
            "range": {"type": "string"},
            "expected_version": {"type": "string"},
        },
    }


def test_nested_range_ref_preserves_sheet_file_and_version() -> None:
    args, error = normalize_structured_references(
        {
            "range_ref": {
                "sheet": {
                    "file": {"path": "reports/book.xlsx", "version": "sha256:abc"},
                    "name": "Summary",
                },
                "address": "A1:C4",
            },
        },
        _schema(),
    )

    assert error is None
    assert args == {
        "file_path": "reports/book.xlsx",
        "sheet_name": "Summary",
        "range": "A1:C4",
        "expected_version": "sha256:abc",
    }


def test_range_ref_schema_accepts_nested_and_legacy_sheet_forms() -> None:
    schema = augment_reference_schema(_schema())
    sheet = schema["$defs"]["RangeRef"]["properties"]["sheet"]
    assert {option.get("type") for option in sheet["oneOf"] if "type" in option} == {"string"}
    assert any(option.get("$ref") == "#/$defs/SheetRef" for option in sheet["oneOf"])


def test_source_ref_targets_source_file_contract() -> None:
    args, error = normalize_structured_references(
        {"source_ref": {"path": "source.xlsx", "version": "sha256:source"}},
        {
            "type": "object",
            "properties": {
                "source_file": {"type": "string"},
                "source_version": {"type": "string"},
            },
        },
    )

    assert error is None
    assert args == {"source_file": "source.xlsx", "source_version": "sha256:source"}


def test_reference_schema_keeps_aliases_usable() -> None:
    defs = augment_reference_schema(_schema())["$defs"]
    assert len(defs["FileRef"]["anyOf"]) == 3
    assert len(defs["SheetRef"]["anyOf"]) == 3
    assert len(defs["RangeRef"]["anyOf"]) == 3
