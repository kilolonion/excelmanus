"""阶段 4 补丁应用逻辑。"""

from __future__ import annotations

import logging
from typing import Any

from excelmanus.pipeline.models import CorrectionPatch, VerificationResult
from excelmanus.replica_spec import MergedRange, ReplicaSpec

logger = logging.getLogger(__name__)


def apply_patches(spec: ReplicaSpec, verification_json: dict[str, Any]) -> ReplicaSpec:
    """将 Phase 4 校验补丁应用到 ReplicaSpec。

    返回新的 ReplicaSpec（深拷贝），confidence < 0.7 的补丁跳过。
    """
    result = VerificationResult.model_validate(verification_json)
    patched = ReplicaSpec.model_validate(spec.model_dump())

    applied = 0
    skipped = 0
    for patch in result.patches:
        if patch.confidence < 0.7:
            skipped += 1
            continue
        try:
            if patch.target == "cell":
                _apply_cell_patch(patched, patch)
            elif patch.target == "merge":
                _apply_merge_patch(patched, patch)
            elif patch.target == "style":
                _apply_style_patch(patched, patch)
            elif patch.target == "dimension":
                _apply_dimension_patch(patched, patch)
            applied += 1
        except Exception:
            logger.warning("补丁应用失败: %s", patch, exc_info=True)
            skipped += 1

    logger.info("Phase 4 补丁: 应用 %d, 跳过 %d", applied, skipped)
    return patched


def _find_sheet(spec: ReplicaSpec, name: str | None):
    """按名称查找 sheet，找不到则返回第一个。"""
    if name:
        for s in spec.sheets:
            if s.name == name:
                return s
    return spec.sheets[0] if spec.sheets else None


def _apply_cell_patch(spec: ReplicaSpec, patch: CorrectionPatch) -> None:
    sheet = _find_sheet(spec, patch.sheet_name)
    if not sheet or not patch.address:
        return
    target_addr = patch.address.upper()
    for cell in sheet.cells:
        if cell.address.upper() == target_addr:
            if patch.field == "value":
                cell.value = patch.new_value
            elif patch.field == "value_type":
                cell.value_type = patch.new_value
            elif patch.field == "display_text":
                cell.display_text = patch.new_value
            elif patch.field == "number_format":
                cell.number_format = patch.new_value
            return


def _apply_merge_patch(spec: ReplicaSpec, patch: CorrectionPatch) -> None:
    sheet = _find_sheet(spec, patch.sheet_name)
    if not sheet:
        return
    if patch.old_value is None and patch.new_value:
        # 新增合并区域
        sheet.merged_ranges.append(
            MergedRange(range=patch.new_value, confidence=patch.confidence)
        )
    elif patch.new_value is None and patch.old_value:
        # 删除合并区域
        sheet.merged_ranges = [
            m for m in sheet.merged_ranges if m.range != patch.old_value
        ]
    elif patch.old_value and patch.new_value:
        # 替换合并区域
        for m in sheet.merged_ranges:
            if m.range == patch.old_value:
                m.range = patch.new_value
                break


def _apply_style_patch(spec: ReplicaSpec, patch: CorrectionPatch) -> None:
    sheet = _find_sheet(spec, patch.sheet_name)
    if not sheet or not patch.address:
        return
    target_addr = patch.address.upper()
    for cell in sheet.cells:
        if cell.address.upper() == target_addr and patch.field == "style_id":
            cell.style_id = patch.new_value
            return


def _apply_dimension_patch(spec: ReplicaSpec, patch: CorrectionPatch) -> None:
    sheet = _find_sheet(spec, patch.sheet_name)
    if not sheet:
        return
    if patch.field in ("rows", "cols") and patch.new_value is not None:
        sheet.dimensions[patch.field] = int(patch.new_value)
