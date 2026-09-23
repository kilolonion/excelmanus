"""A shared print-layout contract for workbook creation and later formatting."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PrintLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    print_area: str | None = Field(default=None, description="单个本表区域，如 A1:F17；空字符串清除打印区域")
    orientation: Literal["portrait", "landscape"] | None = None
    paper_size: Literal["A3", "A4", "A5", "Letter", "Legal"] | None = None
    fit_to_page: bool | None = None
    fit_to_width: int | None = Field(default=None, ge=0, le=32767, strict=True, description="页宽；0 不限；提供时自动启用 fit_to_page")
    fit_to_height: int | None = Field(default=None, ge=0, le=32767, strict=True, description="页高；0 不限")
    scale: int | None = Field(default=None, ge=10, le=400, strict=True, description="百分比；与启用的 fit 模式互斥")

    @field_validator("print_area")
    @classmethod
    def valid_area(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value.strip() if value is not None else None
        from openpyxl.worksheet.cell_range import CellRange

        try:
            area = CellRange(value.strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("print_area 需要本表的单个矩形区域，如 A1:F17") from exc
        if area.title or area.max_col > 16384 or area.max_row > 1048576:
            raise ValueError("print_area 不能引用其他表或超出 Excel 边界")
        return area.coord

    @model_validator(mode="after")
    def consistent_scaling(self) -> "PrintLayout":
        fitting = self.fit_to_page is True or (
            self.fit_to_page is not False and (self.fit_to_width is not None or self.fit_to_height is not None)
        )
        if fitting and self.scale is not None:
            raise ValueError("scale 与 fit_to_page/fit_to_width/fit_to_height 不能同时启用")
        return self


def apply_print_layout(ws: Any, layout: PrintLayout) -> None:
    from openpyxl.worksheet.properties import PageSetupProperties

    if layout.print_area is not None:
        ws.print_area = layout.print_area
    if layout.orientation is not None:
        ws.page_setup.orientation = layout.orientation
    if layout.paper_size is not None:
        ws.page_setup.paperSize = {"Letter": "1", "Legal": "5", "A3": "8", "A4": "9", "A5": "11"}[layout.paper_size]
    scaling_supplied = any(value is not None for value in (
        layout.fit_to_page, layout.fit_to_width, layout.fit_to_height, layout.scale,
    ))
    if not scaling_supplied:
        return
    if ws.sheet_properties.pageSetUpPr is None:
        ws.sheet_properties.pageSetUpPr = PageSetupProperties()
    fitting = layout.fit_to_page
    if fitting is None and (layout.fit_to_width is not None or layout.fit_to_height is not None):
        fitting = True
    if layout.scale is not None:
        fitting = False
        ws.page_setup.scale = layout.scale
    if fitting is not None:
        ws.sheet_properties.pageSetUpPr.fitToPage = fitting
    if fitting:
        ws.page_setup.scale = None
        if ws.page_setup.fitToWidth is None:
            ws.page_setup.fitToWidth = 1
        if ws.page_setup.fitToHeight is None:
            ws.page_setup.fitToHeight = 0
    if layout.fit_to_width is not None:
        ws.page_setup.fitToWidth = layout.fit_to_width
    if layout.fit_to_height is not None:
        ws.page_setup.fitToHeight = layout.fit_to_height
