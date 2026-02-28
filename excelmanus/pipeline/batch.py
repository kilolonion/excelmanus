"""批量渐进式 VLM 提取管线。

核心优化：将多个图片的同一阶段合并为单次 VLM 批量调用，
显著减少 VLM API 调用次数和图片预处理开销。
"""

from __future__ import annotations

import base64
import asyncio
import logging
import time
from typing import Any, Callable, Awaitable

from excelmanus.pipeline.models import (
    PipelineConfig,
    PipelinePhase,
)
from excelmanus.pipeline.phases import (
    build_phase1_prompt,
    build_phase2_prompt,
    build_phase2_chunked_prompt,
    build_phase3_prompt,
    build_phase4_prompt,
    build_phase4_chunked_prompt,
    build_skeleton_spec,
    build_structure_summary,
    build_full_summary,
    fill_data_into_spec,
    apply_styles_to_spec,
    build_partial_summary,
)
from excelmanus.replica_spec import ReplicaSpec
from excelmanus.pipeline.formula_detector import detect_formulas

logger = logging.getLogger(__name__)

# 类型别名
VLMCaller = Callable[
    [list[dict], str, dict | None],
    Awaitable[tuple[str | None, Exception | None]],
]
ImagePreparer = Callable[[bytes, str], tuple[bytes, str]]
EventCallback = Callable[..., Any] | None


class BatchPipelineConfig:
    """批量管线配置"""

    def __init__(
        self,
        chunk_threshold: int = 5000,  # 超过此单元格数时分区分块
        chunk_rows: int = 100,  # 每个数据块最多行数
        pause_threshold: float = 0.4,  # 暂停阈值
        max_retries: int = 2,  # 最大重试次数
    ):
        self.chunk_threshold = chunk_threshold
        self.chunk_rows = chunk_rows
        self.pause_threshold = pause_threshold
        self.max_retries = max_retries

    @classmethod
    def from_pipeline_config(cls, config: PipelineConfig) -> "BatchPipelineConfig":
        return cls(
            chunk_threshold=config.chunk_threshold,
            chunk_rows=config.chunk_rows,
            pause_threshold=config.pause_threshold,
            max_retries=config.max_retries,
        )


class ProgressivePipelineBatch:
    """批量执行多个渐进式管线的编排器。

    核心优化：将多个图片的同一阶段合并为单次 VLM 批量调用。
    对于 N 个图片、M 个阶段的任务：
    - 优化前：N × M 次 VLM 调用
    - 优化后：M 次批量 VLM 调用（每次处理 N 张图片）

    示例（4个图片 × 4个阶段）：
    - 优化前：16 次 VLM 调用
    - 优化后：4 次 VLM 调用
    """

    def __init__(
        self,
        items: list[dict[str, Any]],  # 每个任务的相关数据
        config: BatchPipelineConfig,
        vlm_caller: VLMCaller,
        image_preparer: ImagePreparer,
        on_event: EventCallback = None,
    ):
        """初始化批量管线。

        Args:
            items: 任务列表，每个元素包含:
                - image_bytes: 图片二进制数据
                - mime: 图片 MIME 类型
                - file_path: 源文件路径
                - output_dir: 输出目录
                - output_basename: 输出基础名
                - provenance: 溯源信息
            config: 批量管线配置
            vlm_caller: VLM 调用器
            image_preparer: 图片预处理器
            on_event: 事件回调
        """
        self._items = items
        self._config = config
        self._vlm_caller = vlm_caller
        self._image_preparer = image_preparer
        self._on_event = on_event

        # 图片预处理缓存：image_bytes -> {mode -> (compressed, mime)}
        self._image_cache: dict[bytes, dict[str, tuple[bytes, str]]] = {}

        # 每个任务的中间状态
        self._specs: list[ReplicaSpec | None] = [None] * len(items)
        self._conversation_histories: list[list[dict]] = [[] for _ in items]

        # 计时
        self._start_time: float = 0
        self._task_start_times: list[float] = [0] * len(items)

    def _emit_batch_progress(
        self,
        batch_index: int,
        batch_status: str,
        message: str = "",
        elapsed: float = 0,
    ) -> None:
        """发射批量任务进度事件。"""
        if not self._on_event:
            return

        try:
            from excelmanus.events import EventType, ToolCallEvent
            item_name = self._items[batch_index].get("output_basename", f"任务{batch_index + 1}")
            self._on_event(ToolCallEvent(
                event_type=EventType.BATCH_PROGRESS,
                batch_index=batch_index,
                batch_total=len(self._items),
                batch_item_name=item_name,
                batch_status=batch_status,
                batch_elapsed_seconds=elapsed,
                pipeline_message=message,
            ))
        except Exception as e:
            logger.warning("发射批量进度事件失败: %s", e)

    def _emit_pipeline_progress(
        self,
        batch_index: int,
        phase: PipelinePhase,
        message: str,
        spec: ReplicaSpec | None = None,
        diff: dict[str, Any] | None = None,
    ) -> None:
        """发射单个任务的 Pipeline 进度事件。"""
        if not self._on_event:
            return

        try:
            from excelmanus.events import EventType, ToolCallEvent

            phase_idx = {
                PipelinePhase.STRUCTURE: 0,
                PipelinePhase.DATA: 1,
                PipelinePhase.STYLE: 2,
                PipelinePhase.VERIFICATION: 3,
            }.get(phase, 0)

            item_name = self._items[batch_index].get("output_basename", f"任务{batch_index + 1}")
            spec_path = ""
            if spec:
                # 保存中间结果
                spec_path = self._save_spec(spec, batch_index + 1, phase=phase_idx + 1)

            self._on_event(ToolCallEvent(
                event_type=EventType.PIPELINE_PROGRESS,
                pipeline_stage=f"vlm_extract_{phase.value}",
                pipeline_message=message,
                pipeline_phase_index=phase_idx,
                pipeline_total_phases=4,
                pipeline_spec_path=spec_path,
                pipeline_diff=diff,
                batch_index=batch_index,
                batch_total=len(self._items),
                batch_item_name=item_name,
            ))
        except Exception as e:
            logger.warning("发射 Pipeline 进度事件失败: %s", e)

    def _get_prepared_image(self, image_bytes: bytes, mode: str) -> tuple[bytes, str]:
        """获取预处理后的图片，使用缓存避免重复处理。"""
        if image_bytes not in self._image_cache:
            self._image_cache[image_bytes] = {}
        
        cache = self._image_cache[image_bytes]
        if mode not in cache:
            compressed, mime = self._image_preparer(image_bytes, mode)
            cache[mode] = (compressed, mime)
            logger.debug("图片预处理缓存: mode=%s, size=%d bytes", mode, len(compressed))
        return cache[mode]

    async def run(self) -> list[tuple[ReplicaSpec, str]]:
        """执行批量提取，返回结果列表。

        Returns:
            每个元素的 (spec, spec_path)
        """
        import time

        logger.info("开始批量提取 %d 个文件", len(self._items))
        self._start_time = time.time()

        # 发射批量开始事件
        for i in range(len(self._items)):
            self._emit_batch_progress(
                batch_index=i,
                batch_status="running",
                message="等待处理...",
                elapsed=0,
            )

        # ── Phase 1: 批量结构提取 ──
        phase1_results = await self._run_phase1_batch()
        if not all(phase1_results):
            logger.error("Phase 1 批量调用失败")
            # 降级：尝试逐个处理失败的项
            phase1_results = await self._fallback_phase1(phase1_results)

        # ── Phase 2: 批量数据提取 ──
        phase2_results = await self._run_phase2_batch()
        if not all(phase2_results):
            logger.warning("Phase 2 部分失败")

        # ── Phase 3: 批量样式提取 ──
        phase3_results = await self._run_phase3_batch()
        if not all(phase3_results):
            logger.warning("Phase 3 部分失败")

        # ── Phase 4: 批量校验 ──
        phase4_results = await self._run_phase4_batch()
        if not all(phase4_results):
            logger.warning("Phase 4 部分失败")

        # 生成最终结果
        results = []
        for i, item in enumerate(self._items):
            if self._specs[i] is not None:
                spec = self._specs[i]
                output_path = self._save_spec(spec, i + 1)
                results.append((spec, output_path))

                # 发射任务完成事件
                elapsed = time.time() - self._start_time
                self._emit_batch_progress(
                    batch_index=i,
                    batch_status="completed",
                    message="提取完成",
                    elapsed=elapsed,
                )
            else:
                results.append((None, ""))

                # 发射任务失败事件
                self._emit_batch_progress(
                    batch_index=i,
                    batch_status="failed",
                    message="提取失败",
                    elapsed=0,
                )

        return results

    # ── 批量 VLM 调用 ──

    async def _batch_vlm_call(
        self,
        prompts: list[str],
        image_mode: str,
        include_images: bool = True,
        batch_label: str = "batch",
    ) -> list[dict[str, Any] | None]:
        """批量 VLM 调用，单次请求处理多张图片。

        Args:
            prompts: 每个图片对应的 prompt 列表
            image_mode: 图片预处理模式
            include_images: 是否在请求中包含图片
            batch_label: 日志标签

        Returns:
            每个图片的解析结果列表
        """
        if len(prompts) != len(self._items):
            raise ValueError(f"prompts 数量 ({len(prompts)}) 与 items 数量 ({len(self._items)}) 不匹配")

        # 构建消息内容
        contents = []
        for i, item in enumerate(self._items):
            if include_images:
                compressed, mime = self._get_prepared_image(item["image_bytes"], image_mode)
                b64 = base64.b64encode(compressed).decode("ascii")
                contents.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                })
            contents.append({"type": "text", "text": prompts[i]})

        messages = [{"role": "user", "content": contents}]

        # 单次 VLM 调用
        raw_text, error = await self._vlm_caller(
            messages,
            batch_label,
            {"type": "json_object"},
        )

        if raw_text is None:
            logger.warning("批量 VLM 调用失败: %s", error)
            return [None] * len(self._items)

        # 解析批量响应
        return self._parse_batch_response(raw_text, len(self._items))

    def _parse_batch_response(self, raw_text: str, num_items: int) -> list[dict[str, Any] | None]:
        """解析批量 VLM 返回的 JSON。

        尝试多种解析策略：
        1. {"results": [...]} 格式
        2. JSON 数组格式
        3. 均匀拆分长文本
        """
        import json

        # 策略1：尝试解析为 {"results": [...]} 格式
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
                if len(results) == num_items:
                    return results
        except (json.JSONDecodeError, TypeError):
            pass

        # 策略2：尝试解析为 JSON 数组
        try:
            data = json.loads(raw_text)
            if isinstance(data, list) and len(data) == num_items:
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        # 策略3：检查是否包含多个 JSON 对象
        # VLM 有时返回格式如: {result1} {result2} {result3}
        json_objects = []
        import re
        # 匹配独立的 JSON 对象
        pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
        matches = re.findall(pattern, raw_text)
        
        if len(matches) >= num_items:
            for match in matches[:num_items]:
                try:
                    json_objects.append(json.loads(match))
                except json.JSONDecodeError:
                    json_objects.append(None)
            
            if len(json_objects) == num_items and any(json_objects):
                logger.info("使用正则匹配解析批量响应")
                return json_objects

        # 降级：返回 None 列表
        logger.warning("批量响应解析失败，降级到逐个处理")
        return [None] * num_items

    # ── Phase 1: 批量结构提取 ──

    async def _run_phase1_batch(self) -> list[bool]:
        """执行 Phase 1 批量提取。"""
        logger.info("Phase 1: 批量结构提取 (%d 个文件)", len(self._items))

        prompts = [build_phase1_prompt() for _ in self._items]
        results = await self._batch_vlm_call(
            prompts=prompts,
            image_mode="data",
            include_images=True,
            batch_label="批量提取-Phase1-Structure",
        )

        # 处理结果
        success = []
        for i, (item, result) in enumerate(zip(self._items, results)):
            if result is None:
                logger.warning("Phase 1 任务 %d 失败", i + 1)
                success.append(False)
                continue

            try:
                skeleton = build_skeleton_spec(result, item.get("provenance", {}))
                self._specs[i] = skeleton
                self._save_spec(skeleton, i + 1, phase=1)
                success.append(True)

                # 发射阶段进度事件
                dims_summary = f"{skeleton.dimensions.get('rows', 0)}行 × {skeleton.dimensions.get('columns', 0)}列"
                self._emit_pipeline_progress(
                    batch_index=i,
                    phase=PipelinePhase.STRUCTURE,
                    message=f"结构识别完成: {dims_summary}",
                    spec=skeleton,
                )
                logger.info("Phase 1 任务 %d 完成: %s", i + 1, skeleton.dimensions)
            except Exception as e:
                logger.error("Phase 1 任务 %d 解析失败: %s", i + 1, e)
                success.append(False)

        return success

    async def _fallback_phase1(self, previous_results: list[bool]) -> list[bool]:
        """Phase 1 失败时的降级处理：逐个重试失败的任务。"""
        logger.info("Phase 1 降级处理：逐个重试失败任务")

        prompts = [build_phase1_prompt() for _ in self._items]
        success = list(previous_results)

        for i in range(len(self._items)):
            if not success[i]:
                # 清除对话历史
                self._conversation_histories[i].clear()

                # 单独重试
                result = await self._batch_vlm_call(
                    prompts=[prompts[i]],
                    image_mode="data",
                    include_images=True,
                    batch_label=f"批量提取-Phase1-重试-{i+1}",
                )

                if result[0] is not None:
                    try:
                        skeleton = build_skeleton_spec(result[0], self._items[i].get("provenance", {}))
                        self._specs[i] = skeleton
                        self._save_spec(skeleton, i + 1, phase=1)
                        success[i] = True
                        logger.info("Phase 1 任务 %d 重试成功", i + 1)
                    except Exception as e:
                        logger.error("Phase 1 任务 %d 重试解析失败: %s", i + 1, e)

        return success

    # ── Phase 2: 批量数据提取 ──

    async def _run_phase2_batch(self) -> list[bool]:
        """执行 Phase 2 批量提取。"""
        logger.info("Phase 2: 批量数据提取 (%d 个文件)", len(self._items))

        prompts = []
        for i, spec in enumerate(self._specs):
            if spec is None:
                prompts.append("")
                continue

            structure_summary = build_structure_summary(spec)
            estimated_cells = spec.dimensions.get("rows", 0) * max(spec.dimensions.get("columns", 0), 1)

            # 大表格：分区提取
            if estimated_cells > self._config.chunk_threshold:
                prompt = build_phase2_chunked_prompt(structure_summary, chunk_idx=1, total_chunks=1)
            else:
                prompt = build_phase2_prompt(structure_summary)
            prompts.append(prompt)

        results = await self._batch_vlm_call(
            prompts=prompts,
            image_mode="data",
            include_images=True,
            batch_label="批量提取-Phase2-Data",
        )

        # 处理结果
        success = []
        for i, (item, result) in enumerate(zip(self._items, results)):
            if self._specs[i] is None or result is None:
                success.append(False)
                continue

            try:
                filled = fill_data_into_spec(self._specs[i], result)
                self._specs[i] = filled
                self._save_spec(filled, i + 1, phase=2)
                success.append(True)

                # 发射阶段进度事件
                cell_count = sum(len(s.cells) for s in filled.sheets)
                self._emit_pipeline_progress(
                    batch_index=i,
                    phase=PipelinePhase.DATA,
                    message=f"数据填充完成: {cell_count} 个单元格",
                    spec=filled,
                )
                logger.info("Phase 2 任务 %d 完成", i + 1)
            except Exception as e:
                logger.error("Phase 2 任务 %d 解析失败: %s", i + 1, e)
                success.append(False)

        return success

    # ── Phase 3: 批量样式提取 ──

    async def _run_phase3_batch(self) -> list[bool]:
        """执行 Phase 3 批量提取。"""
        logger.info("Phase 3: 批量样式提取 (%d 个文件)", len(self._items))

        prompts = []
        for i, spec in enumerate(self._specs):
            if spec is None:
                prompts.append("")
                continue

            full_summary = build_full_summary(spec)
            prompt = build_phase3_prompt(full_summary)
            prompts.append(prompt)

        results = await self._batch_vlm_call(
            prompts=prompts,
            image_mode="style",
            include_images=True,
            batch_label="批量提取-Phase3-Style",
        )

        # 处理结果
        success = []
        for i, (item, result) in enumerate(zip(self._items, results)):
            if self._specs[i] is None or result is None:
                success.append(False)
                continue

            try:
                styled_spec = apply_styles_to_spec(self._specs[i], result)
                final_spec = detect_formulas(styled_spec)
                self._specs[i] = final_spec
                self._save_spec(final_spec, i + 1, phase=3)
                success.append(True)

                # 发射阶段进度事件
                self._emit_pipeline_progress(
                    batch_index=i,
                    phase=PipelinePhase.STYLE,
                    message="样式提取完成",
                    spec=final_spec,
                )
                logger.info("Phase 3 任务 %d 完成", i + 1)
            except Exception as e:
                logger.error("Phase 3 任务 %d 解析失败: %s", i + 1, e)
                success.append(False)

        return success

    # ── Phase 4: 批量校验 ──

    async def _run_phase4_batch(self) -> list[bool]:
        """执行 Phase 4 批量校验。"""
        logger.info("Phase 4: 批量校验 (%d 个文件)", len(self._items))

        prompts = []
        for i, spec in enumerate(self._specs):
            if spec is None:
                prompts.append("")
                continue

            full_summary = build_full_summary(spec)
            prompt = build_phase4_prompt(full_summary)
            prompts.append(prompt)

        results = await self._batch_vlm_call(
            prompts=prompts,
            image_mode="data",
            include_images=True,
            batch_label="批量提取-Phase4-Verification",
        )

        # 处理结果
        success = []
        for i, (item, result) in enumerate(zip(self._items, results)):
            if self._specs[i] is None or result is None:
                success.append(False)
                continue

            try:
                from excelmanus.pipeline.patch import apply_patches
                patches = result.get("patches") or []
                if patches:
                    patched_spec = apply_patches(self._specs[i], patches)
                    self._specs[i] = patched_spec
                success.append(True)

                # 发射阶段进度事件
                patch_msg = f"自校验修正完成: {len(patches)} 条补丁" if patches else "自校验完成（无修正）"
                self._emit_pipeline_progress(
                    batch_index=i,
                    phase=PipelinePhase.VERIFICATION,
                    message=patch_msg,
                    spec=self._specs[i],
                )
                logger.info("Phase 4 任务 %d 完成: %d 条补丁", i + 1, len(patches))
            except Exception as e:
                logger.error("Phase 4 任务 %d 解析失败: %s", i + 1, e)
                success.append(False)

        # 保存最终版本
        for i, spec in enumerate(self._specs):
            if spec is not None:
                self._save_spec(spec, i + 1, phase=4, is_final=True)

        return success

    # ── Spec 保存/加载 ──

    def _save_spec(
        self, spec: ReplicaSpec, item_idx: int, phase: int, is_final: bool = False,
    ) -> str:
        """保存 spec 到文件。"""
        item = self._items[item_idx - 1]
        output_dir = item["output_dir"]
        output_basename = item["output_basename"]

        if is_final:
            versioned = Path(output_dir) / f"{output_basename}_final.json"
        else:
            versioned = Path(output_dir) / f"{output_basename}_p{phase}.json"

        versioned.parent.mkdir(parents=True, exist_ok=True)
        spec.dump(versioned)
        logger.debug("保存 spec: %s", versioned)
        return str(versioned)


# 兼容旧版本导入
from pathlib import Path
