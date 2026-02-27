"""ReplicaSpec 数据协议单元测试。"""

from __future__ import annotations

import pytest
import pydantic


def test_minimal_spec_validates():
    """最小 ReplicaSpec 通过校验。"""
    from excelmanus.replica_spec import ReplicaSpec

    spec = ReplicaSpec.model_validate({
        "version": "1.0",
        "provenance": {
            "source_image_hash": "sha256:abc",
            "model": "gpt-4o",
            "timestamp": "2026-01-01T00:00:00Z",
        },
        "workbook": {"name": "test"},
        "sheets": [{
            "name": "Sheet1",
            "dimensions": {"rows": 2, "cols": 2},
            "cells": [
                {"address": "A1", "value": "hello", "value_type": "string", "confidence": 0.99},
            ],
            "styles": {},
        }],
        "uncertainties": [],
    })
    assert spec.version == "1.0"
    assert len(spec.sheets) == 1
    assert spec.sheets[0].cells[0].value == "hello"
    assert spec.sheets[0].cells[0].confidence == 0.99


def test_full_spec_with_styles_and_uncertainties():
    """完整 spec 含样式和不确定项。"""
    from excelmanus.replica_spec import ReplicaSpec

    spec = ReplicaSpec.model_validate({
        "version": "1.0",
        "provenance": {
            "source_image_hash": "sha256:abc123",
            "model": "gpt-4o-2024-08-06",
            "timestamp": "2026-02-21T11:30:00Z",
            "extraction_params": {"detail": "high"},
        },
        "workbook": {
            "name": "replica",
            "locale": "zh-CN",
            "default_font": {"name": "等线", "size": 11},
        },
        "sheets": [{
            "name": "Sheet1",
            "dimensions": {"rows": 15, "cols": 6},
            "freeze_panes": "A2",
            "cells": [
                {"address": "A1", "value": "产品名称", "value_type": "string", "style_id": "header", "confidence": 0.98},
                {"address": "B1", "value": "数量", "value_type": "string", "style_id": "header", "confidence": 0.98},
            ],
            "merged_ranges": [{"range": "A1:F1", "confidence": 0.95}],
            "styles": {
                "header": {
                    "font": {"bold": True, "size": 12, "color": "#FFFFFF"},
                    "fill": {"type": "solid", "color": "#4472C4"},
                    "border": {"style": "thin", "color": "#000000"},
                    "alignment": {"horizontal": "center", "vertical": "center"},
                },
            },
            "column_widths": [18, 12, 12, 12, 12, 15],
            "row_heights": {"1": 30},
            "semantic_hints": {
                "header_rows": [1],
                "formula_patterns": [
                    {"column": "F", "pattern": "=SUM(B{row}:E{row})", "confidence": 0.85},
                ],
            },
        }],
        "uncertainties": [
            {
                "location": "B3",
                "reason": "数字模糊，可能是 1200 或 1280",
                "candidate_values": ["1200", "1280"],
                "confidence": 0.65,
            },
        ],
    })
    assert spec.workbook.locale == "zh-CN"
    assert len(spec.sheets[0].styles) == 1
    assert spec.sheets[0].styles["header"].font.bold is True
    assert len(spec.uncertainties) == 1
    assert spec.uncertainties[0].location == "B3"
    assert spec.sheets[0].semantic_hints.formula_patterns[0].column == "F"


def test_invalid_spec_raises():
    """缺少必填字段时抛出 ValidationError。"""
    from excelmanus.replica_spec import ReplicaSpec

    with pytest.raises(pydantic.ValidationError):
        ReplicaSpec.model_validate({"version": "1.0"})


def test_spec_defaults():
    """默认值正确填充。"""
    from excelmanus.replica_spec import ReplicaSpec

    spec = ReplicaSpec.model_validate({
        "provenance": {
            "source_image_hash": "sha256:x",
            "model": "test",
            "timestamp": "2026-01-01T00:00:00Z",
        },
        "sheets": [{
            "name": "Sheet1",
            "dimensions": {"rows": 1, "cols": 1},
        }],
    })
    assert spec.version == "1.0"
    assert spec.workbook.name == "replica"
    assert spec.uncertainties == []
    assert spec.sheets[0].cells == []
    assert spec.sheets[0].merged_ranges == []


def test_cell_spec_value_types():
    """各 value_type 均可正确设置。"""
    from excelmanus.replica_spec import CellSpec

    for vt in ("string", "number", "date", "boolean", "formula", "empty"):
        cell = CellSpec(address="A1", value_type=vt)
        assert cell.value_type == vt
