"""Versioned request types shared by all workbook observation consumers."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "workbook/2"
Facet = Literal["data", "presentation", "geometry", "objects", "dependencies"]
FACETS = ("data", "presentation", "geometry", "objects", "dependencies")


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: str | None = None
    range: str | None = None
    facets: list[Facet] = Field(default_factory=lambda: ["data", "geometry"])
    mode: Literal["overview", "range", "search", "objects", "dependencies"] = "overview"
    query: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    locale: str = "zh-CN"

    def canonical(self) -> dict:
        value = self.model_dump()
        value["facets"] = sorted(set(self.facets))
        return value
