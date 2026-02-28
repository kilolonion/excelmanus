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
)
from excelmanus.pipeline.patch import apply_patches
from excelmanus.pipeline.formula_detector import detect_formulas
from excelmanus.pipeline.phases import (
    apply_styles_to_spec,
    build_data_summary,
    build_full_summary,
    build_partial_summary,
    build_phase1_prompt,
    build_phase2_prompt,
    build_phase2_chunked_prompt,
    build_phase3_prompt,
    build_phase4_prompt,
    build_phase4_chunked_prompt,
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

# JSON 代码块提取用正则
_JSON_FENCE_RE = _re.compile(r"```(?:json)?\s*(.*?)```", _re.DOTALL | _re.IGNORECASE)


def _parse_vlm_json(text: str, *, try_repair: bool = False) -> dict[str, Any] | None:
    """从 VLM 输出中提取 JSON dict。

    Args:
        text: VLM 原始输出文本。
        try_repair: 为 True 时尝试修复被截断的 JSON（补全未闭合的括号）。
    """
    from excelmanus.engine_core.tool_dispatcher import _parse_vlm_json as _shared_parse
    return _shared_parse(text, try_repair=try_repair)


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

        # 单元格差异
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

        # 合并区域差异
        prev_merges = {m.range for m in prev_sheet.merged_ranges}
        curr_merges = {m.range for m in sheet.merged_ranges}
        added_merges = curr_merges - prev_merges
        removed_merges = prev_merges - curr_merges

        # 样式差异
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
        # Multi-turn VLM 对话历史
        self._conversation: list[dict] = []
        # B 通道描述缓存，由 ExtractTableSpecHandler 注入
        self._b_channel_description: str | None = None
        # 图片预处理缓存：mode -> (compressed_bytes, mime)
        # 避免同一张图片在多个阶段重复进行耗时的图像处理
        self._image_cache: dict[str, tuple[bytes, str]] = {}

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

        # ── Phase 1: Structure（传图，B 通道描述作为先验） ──
        if self._should_run_phase(1):
            self._emit_phase_start(PipelinePhase.STRUCTURE, "正在识别表格结构...")
            p1_prompt = build_phase1_prompt()
            if self._b_channel_description:
                p1_prompt = (
                    f"以下是对该表格的初步描述（供参考）：\n"
                    f"{self._b_channel_description}\n\n---\n\n{p1_prompt}"
                )
            p1_json = await self._call_vlm_phase(
                PipelinePhase.STRUCTURE,
                p1_prompt,
                image_mode="data",
                include_image=True,
            )
            # Fallback：首次失败后清空对话重试一次
            if p1_json is None:
                logger.info("Phase 1 首次调用失败，清空对话重试")
                self._conversation.clear()
                p1_json = await self._call_vlm_phase(
                    PipelinePhase.STRUCTURE,
                    p1_prompt,
                    image_mode="data",
                    include_image=True,
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

        # ── Phase 2: Data（支持大表格分区提取）──
        if self._should_run_phase(2):
            self._emit_phase_start(PipelinePhase.DATA, "正在提取数据...")
            structure_summary = build_structure_summary(skeleton)
            estimated_cells = self._estimate_total_cells(skeleton)
            threshold = self.config.chunk_cell_threshold

            if estimated_cells > threshold:
                logger.info(
                    "大表格检测: 预估 %d cells > 阈值 %d，启用分区提取",
                    estimated_cells, threshold,
                )
                p2_json = await self._run_chunked_phase2(skeleton, structure_summary)
            else:
                # Multi-turn：Phase 2 不传图片，复用 Phase 1 的视觉上下文
                p2_json = await self._call_vlm_phase(
                    PipelinePhase.DATA,
                    build_phase2_prompt(structure_summary),
                    image_mode="data",
                    include_image=False,
                )
                # Fallback：如果 multi-turn 失败，回退到独立调用（带图）
                if p2_json is None:
                    logger.info("Phase 2 multi-turn 失败，回退到独立调用")
                    self._conversation.clear()
                    p2_json = await self._call_vlm_phase(
                        PipelinePhase.DATA,
                        build_phase2_prompt(structure_summary),
                        image_mode="data",
                        include_image=True,
                    )

            if p2_json is None:
                raise RuntimeError("Phase 2 (Data) VLM 调用失败")
            data_spec = fill_data_into_spec(skeleton, p2_json)
            # D4: 独立公式模式检测——在数据填充后自动推断 SUM / 列间算术
            detect_formulas(data_spec)
            self._save_spec(data_spec, phase=2)
            diff = compute_phase_diff(self._prev_spec, data_spec)
            cell_count = sum(len(s.cells) for s in data_spec.sheets)
            self._emit_progress(PipelinePhase.DATA, f"数据填充完成: {cell_count} 个单元格", data_spec, diff)
            self._prev_spec = data_spec
            self._check_pause(data_spec, PipelinePhase.DATA, completed_phase=2)
        else:
            data_spec = loaded_spec  # type: ignore[assignment]
            self._prev_spec = data_spec

        # ── Phase 3: Style (可选，传图——样式需要观察颜色) ──
        if not self.config.skip_style and self._should_run_phase(3):
            self._emit_phase_start(PipelinePhase.STYLE, "正在提取样式...")
            data_summary = build_data_summary(data_spec)
            p3_json = await self._call_vlm_phase(
                PipelinePhase.STYLE,
                build_phase3_prompt(data_summary),
                image_mode="style",
                include_image=True,
            )
            # Fallback：失败后清空对话 + 重传图片重试一次
            if p3_json is None:
                logger.info("Phase 3 首次调用失败，清空对话重试")
                self._conversation.clear()
                p3_json = await self._call_vlm_phase(
                    PipelinePhase.STYLE,
                    build_phase3_prompt(data_summary),
                    image_mode="style",
                    include_image=True,
                )
            if p3_json is not None:
                styled_spec = apply_styles_to_spec(data_spec, p3_json)
                logger.info("Phase 3 样式提取完成")
            else:
                styled_spec = data_spec
                logger.warning("Phase 3 样式提取失败（含重试），降级跳过")
            self._save_spec(styled_spec, phase=3)
            diff = compute_phase_diff(self._prev_spec, styled_spec)
            self._emit_progress(PipelinePhase.STYLE, "样式提取完成", styled_spec, diff)
            self._prev_spec = styled_spec
        elif self._should_run_phase(3):
            styled_spec = data_spec
        else:
            styled_spec = loaded_spec  # type: ignore[assignment]
            self._prev_spec = styled_spec

        # ── Phase 4: Verification（支持大表格分区校验）──
        if self._should_run_phase(4):
            self._emit_phase_start(PipelinePhase.VERIFICATION, "正在进行自校验...")
            cell_count = sum(len(s.cells) for s in styled_spec.sheets)
            threshold = self.config.chunk_cell_threshold

            if cell_count > threshold:
                logger.info(
                    "大表格检测: %d cells > 阈值 %d，启用分区校验",
                    cell_count, threshold,
                )
                p4_json = await self._run_chunked_phase4(styled_spec)
            else:
                full_summary = build_full_summary(styled_spec)
                p4_json = await self._call_vlm_phase(
                    PipelinePhase.VERIFICATION,
                    build_phase4_prompt(full_summary),
                    image_mode="data",
                    include_image=False,
                )
                # Fallback：multi-turn 失败时回退到独立调用（带图）
                if p4_json is None:
                    logger.info("Phase 4 multi-turn 失败，回退到独立调用")
                    self._conversation.clear()
                    p4_json = await self._call_vlm_phase(
                        PipelinePhase.VERIFICATION,
                        build_phase4_prompt(full_summary),
                        image_mode="data",
                        include_image=True,
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

    def _get_prepared_image(self, mode: str) -> tuple[bytes, str]:
        """获取预处理后的图片，使用缓存避免重复处理。

        Args:
            mode: 图像模式 ("data" 或 "style")

        Returns:
            (compressed_bytes, mime_type) 元组
        """
        if mode not in self._image_cache:
            compressed, mime = self._image_preparer(self._image_bytes, mode)
            self._image_cache[mode] = (compressed, mime)
            logger.debug("图片预处理缓存: mode=%s, size=%d bytes", mode, len(compressed))
        return self._image_cache[mode]

    async def _call_vlm_phase(
        self,
        phase: PipelinePhase,
        prompt: str,
        image_mode: str,
        *,
        include_image: bool = True,
    ) -> dict[str, Any] | None:
        """单阶段 VLM 调用，支持 multi-turn 累积对话。

        Args:
            include_image: 是否在本轮消息中包含图片。
                Phase 1/3 需要传图（首次看图 / 样式需观察颜色），
                Phase 2/4 可省略图片（复用 multi-turn 上下文）。
        """
        content: list[dict[str, Any]] = []
        if include_image:
            compressed, mime = self._get_prepared_image(image_mode)
            b64 = base64.b64encode(compressed).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
            })
        content.append({"type": "text", "text": prompt})

        # 追加到多轮对话历史
        self._conversation.append({"role": "user", "content": content})

        # 发射 VLM 调用开始事件，让用户知道正在调用
        self._emit_vlm_calling(phase, include_image)

        raw_text, error = await self._vlm_caller(
            self._conversation,
            f"渐进式提取-{phase.value}",
            {"type": "json_object"},
        )

        if raw_text is None:
            logger.warning("Phase %s VLM 调用失败: %s", phase.value, error)
            # 失败时移除刚追加的 user 消息，保持对话历史干净
            self._conversation.pop()
            return None

        # 将 VLM 回复加入对话历史，供后续阶段复用
        self._conversation.append({"role": "assistant", "content": raw_text})

        # 启发式截断检测
        from excelmanus.engine_core.tool_dispatcher import _is_likely_truncated
        likely_trunc = _is_likely_truncated(raw_text, None)
        parsed = _parse_vlm_json(raw_text, try_repair=likely_trunc)
        if parsed is None:
            logger.warning(
                "Phase %s VLM 返回无法解析为 JSON（%d 字符，可能截断=%s）",
                phase.value, len(raw_text), likely_trunc,
            )
        return parsed

    # ── 大表格分区提取 ──

    @staticmethod
    def _estimate_total_cells(skeleton: ReplicaSpec) -> int:
        """从骨架 spec 预估总 cell 数（rows × cols 之和）。"""
        total = 0
        for sheet in skeleton.sheets:
            dims = sheet.dimensions
            rows = dims.get("rows", 0)
            cols = dims.get("cols", 0)
            total += rows * cols
        return total

    async def _run_chunked_phase2(
        self,
        skeleton: ReplicaSpec,
        structure_summary: str,
    ) -> dict[str, Any] | None:
        """分区 Phase 2：按行范围分片调用 VLM，合并结果。

        每片最多覆盖 _CHUNK_ROWS 行，各片独立调用后将 cells 合并。
        """
        _CHUNK_ROWS = 100  # 每片最多行数

        merged_tables: list[dict[str, Any]] = []

        for sheet_idx, sheet in enumerate(skeleton.sheets):
            dims = sheet.dimensions
            total_rows = dims.get("rows", 0)
            total_cols = dims.get("cols", 0)
            if total_rows == 0 or total_cols == 0:
                merged_tables.append({
                    "name": sheet.name,
                    "cells": [],
                    "uncertainties": [],
                })
                continue

            all_cells: list[dict] = []
            all_uncertainties: list[dict] = []

            # 分片
            row_start = 1
            chunk_idx = 0
            while row_start <= total_rows:
                row_end = min(row_start + _CHUNK_ROWS - 1, total_rows)
                chunk_idx += 1
                logger.info(
                    "Phase 2 分区 %d: 表 %s 行 %d-%d (共 %d 行)",
                    chunk_idx, sheet.name, row_start, row_end, total_rows,
                )

                prompt = build_phase2_chunked_prompt(
                    structure_summary, row_start, row_end,
                )
                chunk_json = await self._call_vlm_phase(
                    PipelinePhase.DATA,
                    prompt,
                    image_mode="data",
                    include_image=(chunk_idx == 1),
                )

                if chunk_json is not None:
                    # 取第一个 table 的 cells（分片时每次只关注一个表）
                    tables = chunk_json.get("tables") or []
                    if tables:
                        # 匹配 sheet：优先按名称，否则按索引
                        target = tables[0]
                        for t in tables:
                            if t.get("name") == sheet.name:
                                target = t
                                break
                        all_cells.extend(target.get("cells") or [])
                        all_uncertainties.extend(target.get("uncertainties") or [])
                else:
                    logger.warning(
                        "Phase 2 分区 %d 失败（表 %s 行 %d-%d），跳过该区间",
                        chunk_idx, sheet.name, row_start, row_end,
                    )

                row_start = row_end + 1

            merged_tables.append({
                "name": sheet.name,
                "cells": all_cells,
                "uncertainties": all_uncertainties,
            })

        if not any(t.get("cells") for t in merged_tables):
            return None

        return {"tables": merged_tables}

    async def _run_chunked_phase4(
        self,
        spec: ReplicaSpec,
    ) -> dict[str, Any] | None:
        """分区 Phase 4：按行范围分片校验，每片独立带图调用，合并 patches。

        优化：只在首尾分区传图，中间分区使用 multi-turn 上下文（不传图），
        大幅减少图片预处理和 VLM 处理时间。
        """
        _CHUNK_ROWS = 100

        all_patches: list[dict] = []
        max_rows = max((s.dimensions.get("rows", 0) for s in spec.sheets), default=0)
        if max_rows == 0:
            return None

        # 计算总分区数，用于判断首尾分区
        total_chunks = (max_rows + _CHUNK_ROWS - 1) // _CHUNK_ROWS

        row_start = 1
        chunk_idx = 0
        while row_start <= max_rows:
            row_end = min(row_start + _CHUNK_ROWS - 1, max_rows)
            chunk_idx += 1
            # 判断是否为首分区或尾分区
            is_first_chunk = chunk_idx == 1
            is_last_chunk = chunk_idx == total_chunks

            logger.info(
                "Phase 4 分区 %d: 行 %d-%d (共 %d 行)%s",
                chunk_idx, row_start, row_end, max_rows,
                " [首区-传图]" if is_first_chunk else (" [尾区-传图]" if is_last_chunk else " [中间区-不传图]"),
            )

            partial_summary = build_partial_summary(spec, row_start, row_end)
            prompt = build_phase4_chunked_prompt(partial_summary, row_start, row_end)

            # 优化：只在首尾分区传图，中间分区使用 multi-turn 上下文（不传图）
            # 这样可以复用之前的视觉上下文，同时大幅减少图片预处理开销
            self._conversation.clear()
            include_image = is_first_chunk or is_last_chunk
            chunk_json = await self._call_vlm_phase(
                PipelinePhase.VERIFICATION,
                prompt,
                image_mode="data",
                include_image=include_image,
            )

            if chunk_json is not None:
                patches = chunk_json.get("patches") or []
                all_patches.extend(patches)
            else:
                logger.warning(
                    "Phase 4 分区 %d 失败（行 %d-%d），跳过",
                    chunk_idx, row_start, row_end,
                )

            row_start = row_end + 1

        if not all_patches:
            return None

        return {"patches": all_patches, "overall_confidence": 0.9, "summary": f"分区校验合并 {len(all_patches)} 条补丁"}

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
                from excelmanus.events import EventType, ToolCallEvent
                self._on_event(ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage=f"vlm_extract_{phase.value}",
                    pipeline_message=message,
                    pipeline_phase_index=phase_idx,
                    pipeline_total_phases=total_phases,
                    pipeline_spec_path=self._last_saved_path,
                    pipeline_diff=diff,
                    pipeline_checkpoint=checkpoint,
                ))
            except Exception:
                pass

    def _emit_phase_start(self, phase: PipelinePhase, message: str) -> None:
        """发射阶段开始事件，让前端更早获得反馈。"""
        phase_idx = _PHASE_INDEX.get(phase, -1)
        total_phases = 3 if self.config.skip_style else 4

        if self._on_event:
            try:
                from excelmanus.events import EventType, ToolCallEvent
                self._on_event(ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage=f"vlm_extract_{phase.value}",
                    pipeline_message=message,
                    pipeline_phase_index=phase_idx,
                    pipeline_total_phases=total_phases,
                    # 阶段开始时使用上一次保存的 spec 路径
                    pipeline_spec_path=self._last_saved_path,
                ))
            except Exception:
                pass

    def _emit_vlm_calling(self, phase: PipelinePhase, include_image: bool) -> None:
        """发射 VLM 开始调用事件，提供更细粒度的进度反馈。"""
        phase_idx = _PHASE_INDEX.get(phase, -1)
        total_phases = 3 if self.config.skip_style else 4
        
        action = "传图分析" if include_image else "文本推理"
        
        if self._on_event:
            try:
                from excelmanus.events import EventType, ToolCallEvent
                self._on_event(ToolCallEvent(
                    event_type=EventType.PIPELINE_PROGRESS,
                    pipeline_stage=f"vlm_calling_{phase.value}",
                    pipeline_message=f"正在调用 VLM ({action})...",
                    pipeline_phase_index=phase_idx,
                    pipeline_total_phases=total_phases,
                    # VLM 调用时使用上一次保存的 spec 路径
                    pipeline_spec_path=self._last_saved_path,
                ))
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
