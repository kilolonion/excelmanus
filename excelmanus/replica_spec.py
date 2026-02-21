"""ReplicaSpec 数据协议：图片→Excel 复刻的结构化中间格式。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source_image_hash: str
    model: str
    timestamp: str
    extraction_params: dict[str, Any] = Field(default_factory=dict)


class FontSpec(BaseModel):
    name: str | None = None
    size: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = None  # hex like "#FF0000"


class FillSpec(BaseModel):
    type: Literal["solid", "pattern", "none"] = "solid"
    color: str | None = None


class BorderSideSpec(BaseModel):
    style: str | None = None  # thin, medium, thick, double, etc.
    color: str | None = None


class BorderSpec(BaseModel):
    style: str | None = None  # 统一边框样式（向后兼容）
    color: str | None = None  # 统一边框颜色（向后兼容）
    top: BorderSideSpec | None = None
    bottom: BorderSideSpec | None = None
    left: BorderSideSpec | None = None
    right: BorderSideSpec | None = None


class AlignmentSpec(BaseModel):
    horizontal: str | None = None
    vertical: str | None = None
    wrap_text: bool | None = None


class StyleClass(BaseModel):
    font: FontSpec | None = None
    fill: FillSpec | None = None
    border: BorderSpec | None = None
    alignment: AlignmentSpec | None = None


class CellSpec(BaseModel):
    address: str
    value: Any = None
    value_type: Literal["string", "number", "date", "boolean", "formula", "empty"] = "string"
    display_text: str | None = None
    number_format: str | None = None
    formula_candidate: str | None = None
    style_id: str | None = None
    confidence: float = 1.0


class MergedRange(BaseModel):
    range: str
    confidence: float = 1.0


class FormulaPattern(BaseModel):
    column: str
    pattern: str
    confidence: float = 0.5


class SemanticHints(BaseModel):
    header_rows: list[int] = Field(default_factory=list)
    total_rows: list[int] = Field(default_factory=list)
    grouping: Any | None = None
    formula_patterns: list[FormulaPattern] = Field(default_factory=list)


class ObjectsSpec(BaseModel):
    charts: list[Any] = Field(default_factory=list)
    images: list[Any] = Field(default_factory=list)
    shapes: list[Any] = Field(default_factory=list)


class SheetSpec(BaseModel):
    name: str
    dimensions: dict[str, int]  # {"rows": N, "cols": M}
    freeze_panes: str | None = None
    print_layout: Any | None = None
    cells: list[CellSpec] = Field(default_factory=list)
    merged_ranges: list[MergedRange] = Field(default_factory=list)
    styles: dict[str, StyleClass] = Field(default_factory=dict)
    column_widths: list[float] = Field(default_factory=list)
    row_heights: dict[str, float] = Field(default_factory=dict)  # row_num_str → height
    objects: ObjectsSpec = Field(default_factory=ObjectsSpec)
    semantic_hints: SemanticHints = Field(default_factory=SemanticHints)


class WorkbookSpec(BaseModel):
    name: str = "replica"
    locale: str | None = None
    default_font: FontSpec | None = None
    theme_hint: str | None = None


class Uncertainty(BaseModel):
    location: str
    reason: str
    candidate_values: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class ReplicaSpec(BaseModel):
    version: str = "1.0"
    provenance: Provenance
    workbook: WorkbookSpec = Field(default_factory=WorkbookSpec)
    sheets: list[SheetSpec]
    uncertainties: list[Uncertainty] = Field(default_factory=list)
