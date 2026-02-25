"""渐进式 4 阶段 VLM 提取管线编排器。"""

from __future__ import annotations

import base64
import json
import logging
import re as _re
from pathlib import Path
from typing import Any, Callable, Awaitable

from excelmanus.pipeline.models import (
    PipelineConfig,
    PipelinePhase,
    PhaseResult,
)
from excelmanus.pipeline.patch import apply_patches
from excelmanus.pipeline.phases import (
    apply_styles_to_spec,
    build_data_summary,
    build_full_summary,
    build_phase1_prompt,
    build_phase2_prompt,
    build_phase3_prompt,
    build_phase4_prompt,
    build_skeleton_spec,
    build_structure_summary,
    fill_data_into_spec,
)
from excelmanus.replica_spec import ReplicaSpec

logger = logging.getLogger(__name__)

# 类型别名
VLMCaller = Callable[
    [list[dict], str, dict | None],
    Awaitable[tuple[str | None, Exception | None]],
]
ImagePreparer = Callable[[bytes, str], tuple[bytes, str]]
EventCallback = Callable[..., Any] | None

# 阶段序号映射
_PHASE_INDEX = {
    PipelinePhase.STRUCTURE: 0,
    PipelinePhase.DATA: 1,
    PipelinePhase.STYLE: 2,
    PipelinePhase.VERIFICATION: 3,
}

# JSON fence 提取正则
_JSON_FENCE_RE = _re.compile(r"```(?:json)?\s*(.*?)```", _re.DOTALL | _re.IGNORECASE)


def _parse_vlm_json(text: str) -> dict[str, Any] | None:
    """从 VLM 输出中提取 JSON dict。"""
    content = (text or "").strip()
    if not content:
        return None
    candidates = [content]
    candidates.extend(_JSON_FENCE_RE.findall(content))
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def compute_phase_diff(
    prev_spec: ReplicaSpec | None,
    curr_spec: ReplicaSpec,
) -> dict[str, Any]:
    """计算两个 ReplicaSpec 之间的 diff 摘要。

    返回结构化 diff 数据，供前端展示。
    """
    diff: dict[str, Any] = {"changes": [], "summary": ""}

    if prev_spec is None:
        # Phase 1: 从无到有
        for sheet in curr_spec.sheets:
            dims = sheet.dimensions
            diff["changes"].append({
                "type": "add_sheet",
                "sheet": sheet.name,
                "rows": dims.get("rows", 0),
                "cols": dims.get("cols", 0),
                "merges": len(sheet.merged_ranges),
            })
        diff["summary"] = f"新建 {len(curr_spec.sheets)} 个表格骨架"
        return diff

    # 逐 sheet 对比
    prev_sheets = {s.name: s for s in prev_spec.sheets}
    for sheet in curr_spec.sheets:
        prev_sheet = prev_sheets.get(sheet.name)
        if prev_sheet is None:
            diff["changes"].append({"type": "add_sheet", "sheet": sheet.name})
            continue

        # cells diff
        prev_cells = {c.address.upper(): c for c in prev_sheet.cells}
        curr_cells = {c.address.upper(): c for c in sheet.cells}
        added_cells = set(curr_cells) - set(prev_cells)
        modified_cells = []
        for addr in set(curr_cells) & set(prev_cells):
            pc, cc = prev_cells[addr], curr_cells[addr]
            if pc.value != cc.value or pc.style_id != cc.style_id:
                modified_cells.append({
                    "cell": addr,
                    "old_value": pc.value,
                    "new_value": cc.value,
                    "old_style": pc.style_id,
                    "new_style": cc.style_id,
                })

        # merges diff
        prev_merges = {m.range for m in prev_sheet.merged_ranges}
        curr_merges = {m.range for m in sheet.merged_ranges}
        added_merges = curr_merges - prev_merges
        removed_merges = prev_merges - curr_merges

        # styles diff
        prev_style_ids = set(prev_sheet.styles.keys())
        curr_style_ids = set(sheet.styles.keys())
        added_styles = curr_style_ids - prev_style_ids

        if added_cells or modified_cells or added_merges or removed_merges or added_styles:
            diff["changes"].append({
                "type": "update_sheet",
                "sheet": sheet.name,
                "cells_added": len(added_cells),
                "cells_modified": len(modified_cells),
                "modified_details": modified_cells[:20],  # 限制前 20 条
                "merges_added": len(added_merges),
                "merges_removed": len(removed_merges),
                "styles_added": len(added_styles),
            })

    # 生成摘要
    parts = []
    for ch in diff["changes"]:
        if ch["type"] == "add_sheet":
            parts.append(f"新建表 {ch['sheet']}")
        elif ch["type"] == "update_sheet":
            sub = []
            if ch.get("cells_added"):
                sub.append(f"+{ch['cells_added']}单元格")
            if ch.get("cells_modified"):
                sub.append(f"~{ch['cells_modified']}修改")
            if ch.get("styles_added"):
                sub.append(f"+{ch['styles_added']}样式")
            if ch.get("merges_added"):
                sub.append(f"+{ch['merges_added']}合并")
            parts.append(f"{ch['sheet']}: {', '.join(sub)}")
    diff["summary"] = "; ".join(parts) if parts else "无变化"
    return diff


class PipelinePauseError(Exception):
    """当 uncertainty 超过阈值时抛出，触发暂停。"""

    def __init__(
        self,
        phase: PipelinePhase,
        uncertainties: list,
        spec_path: str,
        checkpoint: dict[str, Any] | None = None,
    ):
        self.phase = phase
        self.uncertainties = uncertainties
        self.spec_path = spec_path
        self.checkpoint = checkpoint or {}
        super().__init__(f"Pipeline paused at {phase.value}")


class ProgressivePipeline:
    """4 阶段渐进式 VLM 提取管线。

    支持断点续跑：通过 resume_from_phase + resume_spec_path 从指定阶段恢复。
    """

    def __init__(
        self,
        *,
        image_bytes: bytes,
        mime: str,
        file_path: str,
        output_dir: str,
        output_basename: str,
        config: PipelineConfig,
        vlm_caller: VLMCaller,
        image_preparer: ImagePreparer,
        provenance: dict[str, Any],
        on_event: EventCallback = None,
        resume_from_phase: int | None = None,
        resume_spec_path: str | None = None,
    ):
        self._image_bytes = image_bytes
        self._mime = mime
        self._file_path = file_path
        self._output_dir = output_dir
        self._output_basename = output_basename
        self.config = config
        self._vlm_caller = vlm_caller
        self._image_preparer = image_preparer
        self._provenance = provenance
        self._on_event = on_event
        self._last_saved_path: str = ""
        self._prev_spec: ReplicaSpec | None = None
        self._resume_from_phase = resume_from_phase
        self._resume_spec_path = resume_spec_path

    async def run(self) -> tuple[ReplicaSpec, str]:
        """执行全部阶段，返回 (final_spec, final_spec_path)。

        如果 resume_from_phase 指定，则从该阶段之后继续。
        如果 uncertainty 超阈值，抛出 PipelinePauseError。
        """
        # ── 断点恢复：加载已有 spec ──
        if self._resume_from_phase is not None and self._resume_spec_path:
            loaded_spec = self._load_spec(self._resume_spec_path)
            if loaded_spec is None:
                raise RuntimeError(f"无法加载断点 spec: {self._resume_spec_path}")
            logger.info("从 Phase %d 恢复，加载 %s", self._resume_from_phase, self._resume_spec_path)
        else:
            loaded_spec = None

        # ── Phase 1: Structure ──
        if self._should_run_phase(1):
            p1_json = await self._call_vlm_phase(
                PipelinePhase.STRUCTURE,
                build_phase1_prompt(),
                image_mode="data",
            )
            if p1_json is None:
                raise RuntimeError("Phase 1 (Structure) VLM 调用失败")
            skeleton = build_skeleton_spec(p1_json, self._provenance)
            self._save_spec(skeleton, phase=1)
            diff = compute_phase_diff(None, skeleton)
            self._emit_progress(PipelinePhase.STRUCTURE, self._dims_summary(skeleton), skeleton, diff)
            self._prev_spec = skeleton
            self._check_pause(skeleton, PipelinePhase.STRUCTURE, completed_phase=1)
        else:
            skeleton = loaded_spec  # type: ignore[assignment]
            self._prev_spec = skeleton

        # ── Phase 2: Data ──
        if self._should_run_phase(2):
            structure_summary = build_structure_summary(skeleton)
            p2_json = await self._call_vlm_phase(
                PipelinePhase.DATA,
                build_phase2_prompt(structure_summary),
                image_mode="data",
            )
            if p2_json is None:
                raise RuntimeError("Phase 2 (Data) VLM 调用失败")
            data_spec = fill_data_into_spec(skeleton, p2_json)
            self._save_spec(data_spec, phase=2)
            diff = compute_phase_diff(self._prev_spec, data_spec)
            cell_count = sum(len(s.cells) for s in data_spec.sheets)
            self._emit_progress(PipelinePhase.DATA, f"数据填充完成: {cell_count} 个单元格", data_spec, diff)
            self._prev_spec = data_spec
            self._check_pause(data_spec, PipelinePhase.DATA, completed_phase=2)
        else:
            data_spec = loaded_spec  # type: ignore[assignment]
            self._prev_spec = data_spec

        # ── Phase 3: Style (可选) ──
        if not self.config.skip_style and self._should_run_phase(3):
            data_summary = build_data_summary(data_spec)
            p3_json = await self._call_vlm_phase(
                PipelinePhase.STYLE,
                build_phase3_prompt(data_summary),
                image_mode="style",
            )
            if p3_json is not None:
                styled_spec = apply_styles_to_spec(data_spec, p3_json)
                logger.info("Phase 3 样式提取完成")
            else:
                styled_spec = data_spec
                logger.warning("Phase 3 样式提取失败，降级跳过")
            self._save_spec(styled_spec, phase=3)
            diff = compute_phase_diff(self._prev_spec, styled_spec)
            self._emit_progress(PipelinePhase.STYLE, "样式提取完成", styled_spec, diff)
            self._prev_spec = styled_spec
        elif self._should_run_phase(3):
            styled_spec = data_spec
        else:
            styled_spec = loaded_spec  # type: ignore[assignment]
            self._prev_spec = styled_spec

        # ── Phase 4: Verification ──
        if self._should_run_phase(4):
            full_summary = build_full_summary(styled_spec)
            p4_json = await self._call_vlm_phase(
                PipelinePhase.VERIFICATION,
                build_phase4_prompt(full_summary),
                image_mode="data",
            )
            if p4_json is not None and p4_json.get("patches"):
                final_spec = apply_patches(styled_spec, p4_json)
                logger.info("Phase 4 自校验修正完成")
            else:
                final_spec = styled_spec
                logger.info("Phase 4 无修正补丁")
            final_path = self._save_spec(final_spec, phase=4, is_final=True)
            diff = compute_phase_diff(self._prev_spec, final_spec)
            self._emit_progress(PipelinePhase.VERIFICATION, "自校验修正完成", final_spec, diff)
        else:
            final_spec = styled_spec
            final_path = self._save_spec(final_spec, phase=4, is_final=True)

        return final_spec, final_path

    # ── 阶段控制 ──

    def _should_run_phase(self, phase_num: int) -> bool:
        """判断是否需要执行该阶段（断点续跑时跳过已完成的阶段）。"""
        if self._resume_from_phase is None:
            return True
        return phase_num > self._resume_from_phase

    # ── VLM 调用 ──

    async def _call_vlm_phase(
        self,
        phase: PipelinePhase,
        prompt: str,
        image_mode: str,
    ) -> dict[str, Any] | None:
        """单阶段 VLM 调用：准备图片 → 调用 → 解析 JSON。"""
        compressed, mime = self._image_preparer(self._image_bytes, image_mode)
        b64 = base64.b64encode(compressed).decode("ascii")

        messages = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{b64}", "detail": "high",
            }},
            {"type": "text", "text": prompt},
        ]}]

        raw_text, error = await self._vlm_caller(
            messages,
            f"渐进式提取-{phase.value}",
            {"type": "json_object"},
        )

        if raw_text is None:
            logger.warning("Phase %s VLM 调用失败: %s", phase.value, error)
            return None

        parsed = _parse_vlm_json(raw_text)
        if parsed is None:
            logger.warning("Phase %s VLM 返回无法解析为 JSON", phase.value)
        return parsed

    # ── Spec 保存/加载 ──

    def _save_spec(
        self, spec: ReplicaSpec, phase: int, is_final: bool = False,
    ) -> str:
        versioned = Path(self._output_dir) / f"{self._output_basename}_p{phase}.json"
        versioned.parent.mkdir(parents=True, exist_ok=True)
        spec_text = spec.model_dump_json(indent=2, exclude_none=True)
        versioned.write_text(spec_text, encoding="utf-8")
        self._last_saved_path = str(versioned)

        if is_final:
            final_path = Path(self._output_dir) / f"{self._output_basename}.json"
            final_path.write_text(spec_text, encoding="utf-8")
            self._last_saved_path = str(final_path)

        return self._last_saved_path

    @staticmethod
    def _load_spec(spec_path: str) -> ReplicaSpec | None:
        """从文件加载 ReplicaSpec（用于断点恢复）。"""
        p = Path(spec_path)
        if not p.is_file():
            return None
        try:
            return ReplicaSpec.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("加载 spec 失败: %s", spec_path, exc_info=True)
            return None

    # ── Uncertainty 检查 ──

    def _check_pause(
        self,
        spec: ReplicaSpec,
        phase: PipelinePhase,
        completed_phase: int,
    ) -> None:
        total = len(spec.uncertainties)
        low_conf = [
            u for u in spec.uncertainties
            if u.confidence < self.config.uncertainty_confidence_floor
        ]
        if total > self.config.uncertainty_pause_threshold or low_conf:
            checkpoint = {
                "completed_phase": completed_phase,
                "spec_path": self._last_saved_path,
                "file_path": self._file_path,
                "output_dir": self._output_dir,
                "output_basename": self._output_basename,
                "skip_style": self.config.skip_style,
            }
            raise PipelinePauseError(
                phase=phase,
                uncertainties=spec.uncertainties,
                spec_path=self._last_saved_path,
                checkpoint=checkpoint,
            )

    # ── 事件发射 ──

    def _emit_progress(
        self,
        phase: PipelinePhase,
        message: str,
        spec: ReplicaSpec,
        diff: dict[str, Any],
    ) -> None:
        phase_idx = _PHASE_INDEX.get(phase, -1)
        total_phases = 3 if self.config.skip_style else 4
        checkpoint = {
            "completed_phase": phase_idx + 1,
            "spec_path": self._last_saved_path,
            "file_path": self._file_path,
            "output_dir": self._output_dir,
            "output_basename": self._output_basename,
            "skip_style": self.config.skip_style,
        }

        logger.info("Pipeline [%s]: %s | diff: %s", phase.value, message, diff.get("summary", ""))

        if self._on_event:
            try:
                self._on_event({
                    "type": "pipeline_progress",
                    "stage": f"vlm_extract_{phase.value}",
                    "message": message,
                    "phase_index": phase_idx,
                    "total_phases": total_phases,
                    "spec_path": self._last_saved_path,
                    "diff": diff,
                    "checkpoint": checkpoint,
                })
            except Exception:
                pass

    @staticmethod
    def _dims_summary(spec: ReplicaSpec) -> str:
        parts = []
        for s in spec.sheets:
            dims = s.dimensions
            parts.append(
                f"{s.name}: {dims.get('rows', '?')}行×{dims.get('cols', '?')}列"
            )
        return f"骨架提取完成: {', '.join(parts)}"
