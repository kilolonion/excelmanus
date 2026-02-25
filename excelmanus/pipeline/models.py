"""渐进式管线数据模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PipelinePhase(str, Enum):
    STRUCTURE = "structure"
    DATA = "data"
    STYLE = "style"
    VERIFICATION = "verification"


class PipelineConfig(BaseModel):
    """管线级配置，由 AppConfig 填充。"""

    skip_style: bool = False
    uncertainty_pause_threshold: int = 5
    uncertainty_confidence_floor: float = 0.3


class PhaseResult(BaseModel):
    """单阶段输出。"""

    phase: PipelinePhase
    success: bool
    raw_json: dict[str, Any] | None = None
    spec_snapshot_path: str | None = None
    uncertainties_count: int = 0
    error: str | None = None


class CorrectionPatch(BaseModel):
    """Phase 4 单条修正补丁。"""

    target: Literal["cell", "merge", "style", "dimension"]
    sheet_name: str | None = None
    address: str | None = None
    field: str
    old_value: Any = None
    new_value: Any = None
    reason: str = ""
    confidence: float = 0.8


class VerificationResult(BaseModel):
    """Phase 4 校验输出。"""

    patches: list[CorrectionPatch] = Field(default_factory=list)
    overall_confidence: float = 0.9
    summary: str = ""
