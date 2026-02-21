"""ToolDispatcher — 从 AgentEngine 解耦的工具调度组件。

负责管理：
- 工具参数解析（JSON string / dict / None）
- 普通工具的 registry 调用（含线程池执行）
- 单个工具调用的完整执行流程（execute）
- 工具结果截断
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.tools.registry import ToolNotAllowedError

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback

logger = get_logger("tool_dispatcher")


def _render_finish_task_report(
    report: dict[str, Any] | None,
    summary: str,
) -> str:
    """将 finish_task 的 report 对象或 summary 字符串渲染为用户可读文本。

    优先使用 report（结构化汇报），若 report 为空则回退到 summary。
    """
    if not report or not isinstance(report, dict):
        return summary.strip() if summary else ""

    parts: list[str] = []

    operations = (report.get("operations") or "").strip()
    if operations:
        parts.append(f"**执行操作**\n{operations}")

    key_findings = (report.get("key_findings") or "").strip()
    if key_findings:
        parts.append(f"**关键发现**\n{key_findings}")

    explanation = (report.get("explanation") or "").strip()
    if explanation:
        parts.append(f"**结果解读**\n{explanation}")

    suggestions = (report.get("suggestions") or "").strip()
    if suggestions:
        parts.append(f"**后续建议**\n{suggestions}")

    affected_files = report.get("affected_files")
    if affected_files and isinstance(affected_files, list):
        file_lines = [f"- {f}" for f in affected_files if isinstance(f, str) and f.strip()]
        if file_lines:
            parts.append("**涉及文件**\n" + "\n".join(file_lines))

    if not parts:
        return summary.strip() if summary else ""

    return "\n\n".join(parts)


class ToolDispatcher:
    """工具调度器：参数解析、分支路由、执行、审计。"""

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine

    # ── 向后兼容：供测试直接 ToolDispatcher(registry=xxx) ──

    @property
    def _registry(self) -> Any:
        return self._engine._registry

    @property
    def _persistent_memory(self) -> Any:
        return self._engine._persistent_memory

    # ── CoW 路径拦截与提取 ──────────────────────────────────

    def _extract_and_register_cow_mapping(self, result_str: str) -> dict[str, str] | None:
        """从工具结果 JSON 中提取 cow_mapping 并注册到会话级 registry。"""
        try:
            parsed = json.loads(result_str)
            if not isinstance(parsed, dict):
                return None
        except (json.JSONDecodeError, TypeError):
            return None
        cow_mapping = parsed.get("cow_mapping")
        if not cow_mapping or not isinstance(cow_mapping, dict):
            return None
        self._engine._state.register_cow_mappings(cow_mapping)
        return cow_mapping

    def _try_inject_image(self, result_str: str) -> str:
        """从工具返回值中提取 _image_injection，注入到 memory，返回清理后的结果。"""
        try:
            parsed = json.loads(result_str)
            if not isinstance(parsed, dict) or "_image_injection" not in parsed:
                return result_str
        except (json.JSONDecodeError, TypeError):
            return result_str
        injection = parsed.pop("_image_injection")
        self._engine._memory.add_image_message(
            base64_data=injection["base64"],
            mime_type=injection.get("mime_type", "image/png"),
            detail=injection.get("detail", "auto"),
        )
        logger.info("图片已注入 memory: mime=%s", injection.get("mime_type"))
        return json.dumps(parsed, ensure_ascii=False)

    async def _handle_extract_table(
        self,
        arguments: dict[str, Any],
        e: "AgentEngine",
    ) -> str:
        """处理 extract_table_from_image 元工具调用。

        读取图片 → 构造专用提取 prompt → 独立 VLM 调用（带超时+重试） → 解析为 ReplicaSpec → 写入文件。
        """
        import asyncio
        import base64
        import hashlib
        import re as _re
        from datetime import datetime, timezone
        from pathlib import Path

        from excelmanus.vision_extractor import (
            build_extraction_prompt,
            build_phase_a_prompt,
            build_phase_a_structured_prompt,
            build_phase_b_prompt,
            html_table_to_replica_spec,
            parse_extraction_result,
            parse_html_table,
            parse_phase_a_structured,
            phase_a_structured_to_replica_spec,
        )

        file_path = arguments.get("file_path", "")
        output_path = arguments.get("output_path", "outputs/replica_spec.json")
        focus = arguments.get("focus", "full")
        strategy = arguments.get("strategy", "two_phase")

        if not file_path:
            return "错误: file_path 参数不能为空。"

        workspace = Path(e._config.workspace_root).resolve()

        img_path = Path(file_path)
        if not img_path.is_absolute():
            img_path = workspace / img_path
        try:
            if not img_path.resolve().is_relative_to(workspace):
                return json.dumps({
                    "status": "error",
                    "message": f"路径安全限制: 输入图片路径必须在工作区内: {file_path}",
                }, ensure_ascii=False)
        except (ValueError, OSError):
            return json.dumps({
                "status": "error",
                "message": f"路径安全限制: 无法解析输入路径: {file_path}",
            }, ensure_ascii=False)

        out_path = Path(output_path)
        if not out_path.is_absolute():
            out_path = workspace / out_path
        try:
            if not out_path.resolve().is_relative_to(workspace):
                return json.dumps({
                    "status": "error",
                    "message": f"路径安全限制: 输出路径必须在工作区内: {output_path}",
                }, ensure_ascii=False)
        except (ValueError, OSError):
            return json.dumps({
                "status": "error",
                "message": f"路径安全限制: 无法解析输出路径: {output_path}",
            }, ensure_ascii=False)

        if not img_path.is_file():
            return f"错误: 图片文件不存在: {file_path}"

        # 读取图片
        try:
            raw = img_path.read_bytes()
        except OSError as exc:
            return f"错误: 读取图片失败: {exc}"

        source_hash = f"sha256:{hashlib.sha256(raw).hexdigest()[:16]}"

        # ── VLM 调用参数 ──
        vlm_client = e._vlm_client
        vlm_model = e._vlm_model
        vlm_timeout = e._config.vlm_timeout_seconds
        vlm_max_retries = e._config.vlm_max_retries
        vlm_base_delay = e._config.vlm_retry_base_delay_seconds
        max_long_edge = e._config.vlm_image_max_long_edge
        jpeg_quality = e._config.vlm_image_jpeg_quality
        logger.info("VLM 提取使用模型: %s, 策略: %s", vlm_model, strategy)

        # ── 策略分派 ──
        if strategy == "two_phase":
            # 双模式预处理：data（增强文字识别）+ style（保留颜色信息）
            compressed_data, mime_data = self._prepare_image_for_vlm(
                raw, max_long_edge=max_long_edge, jpeg_quality=jpeg_quality, mode="data",
            )
            compressed_style, mime_style = self._prepare_image_for_vlm(
                raw, max_long_edge=max_long_edge, jpeg_quality=jpeg_quality, mode="style",
            )
            b64_data = base64.b64encode(compressed_data).decode("ascii")
            b64_style = base64.b64encode(compressed_style).decode("ascii")
            logger.info(
                "VLM 双模式预处理: data=%d bytes, style=%d bytes (原始 %d bytes)",
                len(compressed_data), len(compressed_style), len(raw),
            )
            image_content_data = {"type": "image_url", "image_url": {"url": f"data:{mime_data};base64,{b64_data}", "detail": "high"}}
            image_content_style = {"type": "image_url", "image_url": {"url": f"data:{mime_style};base64,{b64_style}", "detail": "high"}}

            spec_or_error = await self._extract_two_phase(
                image_content_data=image_content_data,
                image_content_style=image_content_style,
                raw_style_bytes=compressed_style,
                vlm_client=vlm_client, vlm_model=vlm_model,
                vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
                vlm_base_delay=vlm_base_delay,
                file_path=file_path,
            )
        else:
            # single 策略：使用 data 模式预处理
            compressed, mime = self._prepare_image_for_vlm(
                raw, max_long_edge=max_long_edge, jpeg_quality=jpeg_quality, mode="data",
            )
            b64 = base64.b64encode(compressed).decode("ascii")
            image_content = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"}}
            spec_or_error = await self._extract_single(
                image_content=image_content, focus=focus,
                vlm_client=vlm_client, vlm_model=vlm_model,
                vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
                vlm_base_delay=vlm_base_delay,
                file_path=file_path,
                build_extraction_prompt=build_extraction_prompt,
                parse_extraction_result=parse_extraction_result,
            )

        if isinstance(spec_or_error, str):
            return spec_or_error  # 错误 JSON
        spec = spec_or_error

        # 补充 provenance
        spec.provenance.source_image_hash = source_hash
        spec.provenance.model = vlm_model
        spec.provenance.timestamp = datetime.now(timezone.utc).isoformat()

        # 写入文件
        out = Path(e._config.workspace_root) / output_path
        out.parent.mkdir(parents=True, exist_ok=True)
        spec_json = spec.model_dump_json(indent=2)
        out.write_text(spec_json, encoding="utf-8")

        # 构建摘要
        total_cells = sum(len(s.cells) for s in spec.sheets)
        total_styles = sum(len(s.styles) for s in spec.sheets)
        total_merges = sum(len(s.merged_ranges) for s in spec.sheets)

        # ── 自动流水线：extract 成功后自动 rebuild + verify ──
        auto_excel_path = output_path.replace("replica_spec.json", "draft.xlsx")
        if auto_excel_path == output_path:
            auto_excel_path = "outputs/draft.xlsx"
        auto_report_path = auto_excel_path.replace(".xlsx", "_diff_report.md")

        rebuild_result: dict[str, Any] = {"status": "skipped"}
        verify_result: dict[str, Any] = {"status": "skipped"}
        try:
            from excelmanus.tools.image_tools import (
                init_guard,
                rebuild_excel_from_spec,
                verify_excel_replica,
            )
            init_guard(e._config.workspace_root)
            rebuild_raw = rebuild_excel_from_spec(
                spec_path=str(out), output_path=str(Path(e._config.workspace_root) / auto_excel_path),
            )
            rebuild_result = json.loads(rebuild_raw)
            if rebuild_result.get("status") == "ok":
                verify_raw = verify_excel_replica(
                    spec_path=str(out),
                    excel_path=str(Path(e._config.workspace_root) / auto_excel_path),
                    report_path=str(Path(e._config.workspace_root) / auto_report_path),
                )
                verify_result = json.loads(verify_raw)
        except Exception as exc:
            logger.warning("自动流水线异常: %s", exc)

        summary = {
            "status": "ok",
            "spec_path": output_path,
            "summary": {
                "sheets": len(spec.sheets),
                "total_cells": total_cells,
                "style_classes": total_styles,
                "uncertainties": len(spec.uncertainties),
                "merged_ranges": total_merges,
            },
            "auto_pipeline": {
                "rebuild": {
                    "status": rebuild_result.get("status", "error"),
                    "output_path": auto_excel_path if rebuild_result.get("status") == "ok" else None,
                    "build_summary": rebuild_result.get("build_summary"),
                },
                "verify": {
                    "status": verify_result.get("status", "skipped"),
                    "match_rate": verify_result.get("match_rate"),
                    "report_path": auto_report_path if verify_result.get("status") == "ok" else None,
                },
            },
        }
        return json.dumps(summary, ensure_ascii=False)

    async def _call_vlm_with_retry(
        self,
        *,
        messages: list[dict],
        vlm_client: Any,
        vlm_model: str,
        vlm_timeout: int,
        vlm_max_retries: int,
        vlm_base_delay: float,
        phase_label: str = "",
        response_format: dict | None = None,
    ) -> tuple[str | None, Exception | None]:
        """共享的 VLM 调用逻辑（带超时+网络错误重试）。

        返回 (raw_text, last_error)。raw_text 为 None 表示全部失败。
        """
        import asyncio

        raw_text: str | None = None
        last_error: Exception | None = None
        label = f" [{phase_label}]" if phase_label else ""

        create_kwargs: dict[str, Any] = {
            "model": vlm_model,
            "messages": messages,
            "temperature": 0.0,
        }
        if response_format is not None:
            create_kwargs["response_format"] = response_format

        for attempt in range(vlm_max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    vlm_client.chat.completions.create(**create_kwargs),
                    timeout=vlm_timeout,
                )
                raw_text = response.choices[0].message.content or ""
                break
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"VLM 调用超时（{vlm_timeout}s）")
                logger.warning("VLM%s 超时（%ds），不重试", label, vlm_timeout)
                break
            except Exception as exc:
                last_error = exc
                sanitized = self._sanitize_vlm_error(exc)
                logger.warning(
                    "VLM%s 失败（attempt %d/%d）: %s",
                    label, attempt + 1, vlm_max_retries + 1, sanitized,
                )
                if attempt < vlm_max_retries:
                    delay = vlm_base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)

        return raw_text, last_error

    async def _extract_single(
        self,
        *,
        image_content: dict,
        focus: str,
        vlm_client: Any,
        vlm_model: str,
        vlm_timeout: int,
        vlm_max_retries: int,
        vlm_base_delay: float,
        file_path: str,
        build_extraction_prompt: Any,
        parse_extraction_result: Any,
    ) -> "ReplicaSpec | str":
        """单次调用策略：一次 VLM 调用提取数据+样式。"""
        prompt = build_extraction_prompt(focus=focus)
        messages = [
            {"role": "user", "content": [
                image_content,
                {"type": "text", "text": prompt},
            ]},
        ]

        raw_text, last_error = await self._call_vlm_with_retry(
            messages=messages,
            vlm_client=vlm_client, vlm_model=vlm_model,
            vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
            vlm_base_delay=vlm_base_delay, phase_label="single",
        )

        if raw_text is None:
            return self._build_vlm_failure_result(
                last_error, vlm_max_retries + 1, file_path,
            )

        try:
            return parse_extraction_result(raw_text)
        except ValueError as exc:
            return json.dumps({
                "status": "error",
                "error_code": "PARSE_FAILED",
                "message": f"提取结果解析失败: {exc}",
                "raw_length": len(raw_text),
                "fallback_hint": (
                    "VLM 返回了非法 JSON。建议：1) 重试 extract_table_from_image；"
                    "2) 或使用 read_image 查看图片后用 run_code + openpyxl 手动构建。"
                ),
            }, ensure_ascii=False)

    async def _extract_two_phase(
        self,
        *,
        image_content_data: dict,
        image_content_style: dict,
        raw_style_bytes: bytes,
        vlm_client: Any,
        vlm_model: str,
        vlm_timeout: int,
        vlm_max_retries: int,
        vlm_base_delay: float,
        file_path: str,
    ) -> "ReplicaSpec | str":
        """两阶段策略：Phase A 结构化 JSON + Phase B 语义颜色样式 + CV 像素分析。

        新流程：
        1. Phase A：用 data_image + 结构化 prompt → JSON cells
           - 失败时降级到旧 HTML prompt
        2. Phase B：用 style_image + 语义颜色 prompt → 样式 JSON
           - 失败时降级为无样式
        3. CV 分析：用 style_image → grid lines + colors + widths
           - 失败时跳过
        4. 合并：Phase A data + Phase B styles + CV hints → ReplicaSpec
        """
        from excelmanus.vision_extractor import (
            build_phase_a_prompt,
            build_phase_a_structured_prompt,
            build_phase_b_prompt,
            html_table_to_replica_spec,
            parse_html_table,
            parse_phase_a_structured,
            phase_a_structured_to_replica_spec,
        )

        # ── Phase A: 结构化 JSON 提取（优先）──
        prompt_a = build_phase_a_structured_prompt()
        messages_a = [
            {"role": "user", "content": [
                image_content_data,
                {"type": "text", "text": prompt_a},
            ]},
        ]

        raw_a, error_a = await self._call_vlm_with_retry(
            messages=messages_a,
            vlm_client=vlm_client, vlm_model=vlm_model,
            vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
            vlm_base_delay=vlm_base_delay, phase_label="Phase A (structured)",
            response_format={"type": "json_object"},
        )

        if raw_a is None:
            return self._build_vlm_failure_result(
                error_a, vlm_max_retries + 1, file_path,
            )

        # 尝试结构化解析，失败则降级到 HTML
        phase_a_data: dict[str, Any] | None = None
        html_table: str | None = None
        use_structured = False

        try:
            phase_a_data = parse_phase_a_structured(raw_a)
            use_structured = True
            logger.info(
                "Phase A (structured) 完成: %d cells",
                len(phase_a_data.get("cells", [])),
            )
        except ValueError as exc:
            logger.warning("Phase A 结构化解析失败，降级到 HTML: %s", exc)
            # 降级：用旧 HTML prompt 重试
            prompt_a_html = build_phase_a_prompt()
            messages_a_html = [
                {"role": "user", "content": [
                    image_content_data,
                    {"type": "text", "text": prompt_a_html},
                ]},
            ]
            raw_a_html, error_a_html = await self._call_vlm_with_retry(
                messages=messages_a_html,
                vlm_client=vlm_client, vlm_model=vlm_model,
                vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
                vlm_base_delay=vlm_base_delay, phase_label="Phase A (HTML fallback)",
            )
            if raw_a_html is not None:
                try:
                    html_table = parse_html_table(raw_a_html)
                    logger.info("Phase A (HTML fallback) 完成: %d 字符", len(html_table))
                except ValueError as exc2:
                    return json.dumps({
                        "status": "error",
                        "error_code": "PHASE_A_PARSE_FAILED",
                        "message": f"Phase A 解析失败（结构化+HTML 均失败）: {exc2}",
                        "raw_length": len(raw_a),
                        "fallback_hint": (
                            "VLM Phase A 未返回有效输出。"
                            "建议：1) 用 strategy='single' 重试；"
                            "2) 或用 read_image 查看图片后用 run_code 手动构建。"
                        ),
                    }, ensure_ascii=False)
            else:
                return self._build_vlm_failure_result(
                    error_a_html, vlm_max_retries + 1, file_path,
                )

        # ── Phase B: 语义颜色样式提取 ──
        # 构建 Phase B prompt（需要 Phase A 的表格结构作为参考）
        if use_structured and phase_a_data:
            # 从结构化数据构建简要表格描述供 Phase B 参考
            dims = phase_a_data.get("dimensions", {})
            phase_b_ref = f"表格结构: {dims.get('rows', '?')}行 x {dims.get('cols', '?')}列"
        else:
            phase_b_ref = html_table or ""

        prompt_b = build_phase_b_prompt(phase_b_ref)
        messages_b = [
            {"role": "user", "content": [
                image_content_style,
                {"type": "text", "text": prompt_b},
            ]},
        ]

        style_json: dict[str, Any] | None = None
        raw_b, error_b = await self._call_vlm_with_retry(
            messages=messages_b,
            vlm_client=vlm_client, vlm_model=vlm_model,
            vlm_timeout=vlm_timeout, vlm_max_retries=vlm_max_retries,
            vlm_base_delay=vlm_base_delay, phase_label="Phase B",
            response_format={"type": "json_object"},
        )

        if raw_b is not None:
            try:
                import re as _re
                text_b = raw_b.strip()
                code_match = _re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text_b, _re.DOTALL)
                if code_match:
                    text_b = code_match.group(1).strip()
                style_json = json.loads(text_b)
                logger.info("Phase B 完成: %d 个样式类", len(style_json.get("styles", {})))
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("Phase B 样式解析失败（降级为无样式）: %s", exc)
                style_json = None
        else:
            logger.warning("Phase B 调用失败（降级为无样式）")

        # ── CV 像素分析（使用 style 模式预处理的图片） ──
        cv_hints: dict[str, Any] | None = None
        try:
            from excelmanus.cv_analyzer import (
                compute_column_widths,
                compute_row_heights,
                detect_grid_lines,
                sample_cell_colors,
            )
            grid = detect_grid_lines(raw_style_bytes)
            if len(grid["h_lines"]) >= 2 and len(grid["v_lines"]) >= 2:
                cv_hints = {
                    "column_widths": compute_column_widths(grid),
                    "row_heights": compute_row_heights(grid),
                    "cell_colors": sample_cell_colors(raw_style_bytes, grid),
                }
                logger.info(
                    "CV 分析完成: %d行 x %d列",
                    len(grid["h_lines"]) - 1, len(grid["v_lines"]) - 1,
                )
            else:
                logger.info("CV 分析: 未检测到足够网格线，跳过")
        except Exception as exc:
            logger.warning("CV 分析失败（降级为纯 VLM）: %s", exc)

        # ── 合并 → ReplicaSpec ──
        try:
            if use_structured and phase_a_data:
                return phase_a_structured_to_replica_spec(
                    phase_a_data, style_json, cv_hints=cv_hints,
                )
            else:
                return html_table_to_replica_spec(html_table, style_json)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "error_code": "MERGE_FAILED",
                "message": f"ReplicaSpec 转换失败: {exc}",
                "fallback_hint": (
                    "数据提取成功但转换失败。"
                    "建议：用 strategy='single' 重试。"
                ),
            }, ensure_ascii=False)

    @staticmethod
    def _prepare_image_for_vlm(
        raw: bytes,
        *,
        max_long_edge: int = 2048,
        jpeg_quality: int = 92,
        mode: str = "data",  # "data" | "style"
    ) -> tuple[bytes, str]:
        """自适应预处理图片以提升 VLM 表格识别质量。

        根据图片特征自动选择处理策略：
        1. 长边超限时等比缩放（保留文字细节）
        2. 灰色背景检测 → 白底替换（消除表格灰底干扰）
        3. 自适应对比度增强（低对比度图片加强，高对比度跳过）
        4. 扫描件/复印件自动二值化（基于直方图双峰检测）
        5. 智能锐化（仅对模糊图片应用，避免过度锐化）
        6. 转为高质量 JPEG
        - 返回 (processed_bytes, mime_type)
        """
        import io

        try:
            from PIL import Image, ImageFilter, ImageOps, ImageStat
        except ImportError:
            return raw, "image/png"

        try:
            img = Image.open(io.BytesIO(raw))
        except Exception:
            return raw, "image/png"

        # ── 1. 缩放（仅在超限时） ──
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > max_long_edge:
            scale = max_long_edge / long_edge
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        # ── 2. 转 RGB ──
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # ── style 模式：仅缩放+RGB转换，保留原始颜色 ──
        if mode == "style":
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            compressed = buf.getvalue()
            if len(compressed) < len(raw):
                return compressed, "image/jpeg"
            return raw, "image/png"

        # ── 3. 分析图片特征 ──
        try:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean_brightness = stat.mean[0]  # 0-255
            stddev = stat.stddev[0]
            hist = gray.histogram()  # 256 bins
        except Exception:
            mean_brightness, stddev, hist = 128.0, 50.0, [0] * 256

        # ── 4. 灰色背景检测与去除 ──
        # 如果背景偏灰（中值亮度在 180-230 之间），将灰底白化
        try:
            if 180 <= mean_brightness <= 230 and stddev < 60:
                # 浅灰背景：将接近背景色的像素白化
                gray_thresh = img.point(lambda p: 255 if p > mean_brightness - 20 else p)
                img = gray_thresh
                logger.debug("图片预处理: 检测到灰色背景，已白化")
        except Exception:
            pass

        # ── 5. 自适应对比度增强 ──
        try:
            if stddev < 40:
                # 低对比度：强力增强
                img = ImageOps.autocontrast(img, cutoff=1)
                logger.debug("图片预处理: 低对比度(stddev=%.1f)，强力增强", stddev)
            elif stddev < 70:
                # 中等对比度：适度增强
                img = ImageOps.autocontrast(img, cutoff=0.5)
            # stddev >= 70：高对比度图片，跳过对比度增强
        except Exception:
            pass

        # ── 6. 扫描件二值化检测 ──
        # 通过直方图分析：如果亮度分布呈双峰（文字+背景），适用二值化
        try:
            if stddev > 30:
                # 计算直方图暗区(0-128)和亮区(128-255)的占比
                dark_ratio = sum(hist[:128]) / max(sum(hist), 1)
                light_ratio = sum(hist[128:]) / max(sum(hist), 1)
                # 双峰特征：暗区和亮区各占 10-90%
                is_bimodal = 0.05 < dark_ratio < 0.50 and 0.50 < light_ratio < 0.95
                # 对于扫描件（高对比度双峰），应用轻度阈值化增强
                if is_bimodal and stddev > 80:
                    gray_for_thresh = img.convert("L")
                    # Otsu-like 简化：用均值作为阈值
                    threshold = int(mean_brightness * 0.85)
                    binary = gray_for_thresh.point(lambda p: 255 if p > threshold else 0, "L")
                    img = binary.convert("RGB")
                    logger.debug("图片预处理: 扫描件特征，已二值化(阈值=%d)", threshold)
        except Exception:
            pass

        # ── 7. 智能锐化（仅对模糊图片） ──
        try:
            # 通过边缘检测评估清晰度
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_stat = ImageStat.Stat(edges)
            edge_mean = edge_stat.mean[0]
            if edge_mean < 15:
                # 模糊图片：应用锐化
                img = img.filter(ImageFilter.SHARPEN)
                logger.debug("图片预处理: 模糊图片(edge_mean=%.1f)，已锐化", edge_mean)
            elif edge_mean < 30:
                # 中等清晰度：轻度锐化
                img = img.filter(ImageFilter.DETAIL)
        except Exception:
            pass

        # ── 8. 输出 JPEG ──
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        compressed = buf.getvalue()
        if len(compressed) < len(raw):
            return compressed, "image/jpeg"
        return raw, "image/png"

    @staticmethod
    def _sanitize_vlm_error(exc: Exception) -> str:
        """净化 VLM 错误消息：移除 HTML 响应体，提取关键信息。"""
        import re as _re
        msg = str(exc)
        if "<html" in msg.lower() or "<!doctype" in msg.lower():
            code_match = _re.search(r"(\d{3})[:\s]", msg)
            code = code_match.group(1) if code_match else "unknown"
            title_match = _re.search(r"<title[^>]*>(.*?)</title>", msg, _re.IGNORECASE)
            title = title_match.group(1).strip() if title_match else "Gateway error"
            return f"HTTP {code}: {title}"
        return msg[:500]

    @staticmethod
    def _build_vlm_failure_result(
        exc: Exception | None, attempts: int, file_path: str,
    ) -> str:
        """构建 VLM 失败时的结构化降级引导结果。"""
        error_msg = ToolDispatcher._sanitize_vlm_error(exc) if exc else "未知错误"
        return json.dumps({
            "status": "error",
            "error_code": "VLM_CALL_FAILED",
            "message": f"VLM 提取在 {attempts} 次尝试后失败: {error_msg}",
            "fallback_hint": (
                "建议降级方案：1) 使用 read_image 查看图片，由主模型直接描述表格内容；"
                "2) 用 run_code + openpyxl 根据描述手动构建 Excel 文件。"
                "不要再次调用 extract_table_from_image，上游 API 可能暂时不可用。"
            ),
            "file_path": file_path,
        }, ensure_ascii=False)

    def _redirect_cow_paths(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """检查工具参数中的文件路径是否命中 CoW 注册表，自动重定向。

        返回 (可能修改过的 arguments, 重定向提醒消息列表)。
        """
        from excelmanus.tools.policy import (
            AUDIT_TARGET_ARG_RULES_ALL,
            AUDIT_TARGET_ARG_RULES_FIRST,
            READ_ONLY_SAFE_TOOLS,
        )

        registry = self._engine._state.cow_path_registry
        if not registry:
            return arguments, []

        path_fields: list[str] = []
        all_fields = AUDIT_TARGET_ARG_RULES_ALL.get(tool_name)
        if all_fields is not None:
            path_fields.extend(all_fields)
        else:
            first_fields = AUDIT_TARGET_ARG_RULES_FIRST.get(tool_name)
            if first_fields is not None:
                path_fields.extend(first_fields)

        if tool_name in READ_ONLY_SAFE_TOOLS:
            for key in ("file_path", "path", "directory"):
                if key in arguments and key not in path_fields:
                    path_fields.append(key)

        # run_code 的 code 参数中的路径由沙盒层 sandbox_hook 处理，此处不拦截
        if not path_fields:
            return arguments, []

        workspace_root = self._engine._config.workspace_root
        redirected = dict(arguments)
        reminders: list[str] = []
        for field_name in path_fields:
            raw = arguments.get(field_name)
            if raw is None:
                continue
            raw_str = str(raw).strip()
            if not raw_str:
                continue
            # 尝试匹配：直接匹配相对路径，或去掉 workspace_root 前缀后匹配
            rel_path = raw_str
            if workspace_root and raw_str.startswith(workspace_root):
                rel_path = raw_str[len(workspace_root):].lstrip("/")
            redirect = registry.get(rel_path)
            if redirect is not None:
                # 保持原始路径格式（绝对/相对）
                if raw_str.startswith(workspace_root):
                    new_path = f"{workspace_root}/{redirect}"
                else:
                    new_path = redirect
                redirected[field_name] = new_path
                reminders.append(
                    f"⚠️ 路径 `{raw_str}` 是受保护的原始文件，"
                    f"已自动重定向到副本 `{new_path}`。"
                    f"请在后续操作中直接使用副本路径。"
                )
                logger.info(
                    "CoW 路径拦截: tool=%s field=%s %s → %s",
                    tool_name, field_name, raw_str, new_path,
                )
        return redirected, reminders

    def parse_arguments(self, raw_args: Any) -> tuple[dict[str, Any], str | None]:
        """解析工具调用参数，返回 (arguments, error)。

        error 为 None 表示解析成功。
        """
        if raw_args is None or raw_args == "":
            return {}, None
        if isinstance(raw_args, dict):
            return raw_args, None
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if not isinstance(parsed, dict):
                    return {}, f"参数必须为 JSON 对象，当前类型: {type(parsed).__name__}"
                return parsed, None
            except (json.JSONDecodeError, TypeError) as exc:
                return {}, f"JSON 解析失败: {exc}"
        return {}, f"参数类型无效: {type(raw_args).__name__}"

    async def call_registry_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> str:
        """在线程池中调用工具，返回截断后的结果字符串。"""
        from excelmanus.tools import memory_tools

        registry = self._registry
        persistent_memory = self._persistent_memory

        def _call() -> Any:
            with memory_tools.bind_memory_context(persistent_memory):
                return registry.call_tool(
                    tool_name,
                    arguments,
                    tool_scope=tool_scope,
                )

        result_value = await asyncio.to_thread(_call)
        result_str = str(result_value)

        # 先处理图片注入，再做截断，避免截断破坏 JSON 导致注入失败。
        if tool_name == "read_image" and result_str:
            result_str = self._try_inject_image(result_str)

        # 工具结果截断
        tool_def = getattr(registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            result_str = tool_def.truncate_result(result_str)

        return result_str

    # ── 核心执行方法：从 AgentEngine._execute_tool_call 搬迁 ──

    async def execute(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: "EventCallback | None",
        iteration: int,
        route_result: Any | None = None,
    ) -> Any:
        """单个工具调用：参数解析 → 执行 → 事件发射 → 返回结果。

        从 AgentEngine._execute_tool_call 整体搬迁，通过 self._engine
        引用回调 AgentEngine 上的基础设施方法。
        """
        from excelmanus.engine import ToolCallResult, _AuditedExecutionError
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine  # 引擎快捷引用

        function = getattr(tc, "function", None)
        tool_name = getattr(function, "name", "")
        raw_args = getattr(function, "arguments", None)
        tool_call_id = getattr(tc, "id", "") or f"call_{int(time.time() * 1000)}"

        # 参数解析
        arguments, parse_error = self.parse_arguments(raw_args)

        # 发射 TOOL_CALL_START 事件
        e._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_START,
                tool_name=tool_name,
                arguments=arguments,
                iteration=iteration,
            ),
        )

        pending_approval = False
        approval_id: str | None = None
        audit_record = None
        pending_question = False
        question_id: str | None = None
        pending_plan = False
        plan_id: str | None = None
        defer_tool_result = False
        finish_accepted = False
        _cow_reminders: list[str] = []

        # 执行工具调用
        hook_skill = e._pick_route_skill(route_result)
        if parse_error is not None:
            result_str = f"工具参数解析错误: {parse_error}"
            success = False
            error = result_str
            log_tool_call(
                logger,
                tool_name,
                {"_raw_arguments": raw_args},
                error=error,
            )
        else:
            # ── 备份沙盒模式：重定向文件路径 ──
            arguments = e._redirect_backup_paths(tool_name, arguments)

            # ── CoW 路径拦截：将原始保护路径重定向到 outputs/ 副本 ──
            arguments, _cow_reminders = self._redirect_cow_paths(tool_name, arguments)

            pre_hook_raw = e._run_skill_hook(
                skill=hook_skill,
                event=HookEvent.PRE_TOOL_USE,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            pre_hook = await e._resolve_hook_result(
                event=HookEvent.PRE_TOOL_USE,
                hook_result=pre_hook_raw,
                on_event=on_event,
            )
            if pre_hook is not None and isinstance(pre_hook.updated_input, dict):
                arguments = dict(pre_hook.updated_input)
            skip_high_risk_approval_by_hook = (
                pre_hook is not None and pre_hook.decision == HookDecision.ALLOW
            )
            if skip_high_risk_approval_by_hook:
                logger.info(
                    "Hook ALLOW 已生效，跳过确认门禁：tool=%s iteration=%s",
                    tool_name,
                    iteration,
                )

            if pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                reason = pre_hook.reason or "Hook 拒绝执行该工具。"
                result_str = f"工具调用被 Hook 拒绝：{reason}"
                success = False
                error = result_str
                log_tool_call(logger, tool_name, arguments, error=error)
            elif pre_hook is not None and pre_hook.decision == HookDecision.ASK:
                try:
                    pending = e._approval.create_pending(
                        tool_name=tool_name,
                        arguments=arguments,
                        tool_scope=tool_scope,
                    )
                    pending_approval = True
                    approval_id = pending.approval_id
                    result_str = e._format_pending_prompt(pending)
                    success = True
                    error = None
                    e._emit_pending_approval_event(
                        pending=pending, on_event=on_event, iteration=iteration,
                    )
                    log_tool_call(logger, tool_name, arguments, result=result_str)
                except ValueError:
                    result_str = e._approval.pending_block_message()
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
            else:
                try:
                    skip_plan_once_for_task_create = False
                    if tool_name == "task_create" and e._suspend_task_create_plan_once:
                        skip_plan_once_for_task_create = True
                        e._suspend_task_create_plan_once = False

                    if tool_name == "activate_skill":
                        selected_name = arguments.get("skill_name")
                        if not isinstance(selected_name, str) or not selected_name.strip():
                            result_str = "工具参数错误: skill_name 必须为非空字符串。"
                            success = False
                            error = result_str
                        else:
                            result_str = await e._handle_activate_skill(
                                selected_name.strip(),
                            )
                            success = result_str.startswith("OK")
                            error = None if success else result_str
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "extract_table_from_image":
                        result_str = await self._handle_extract_table(
                            arguments=arguments, e=e,
                        )
                        success = not result_str.startswith("错误")
                        error = None if success else result_str
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "delegate_to_subagent":
                        task_value = arguments.get("task")
                        task_brief = arguments.get("task_brief")
                        # task_brief 优先：渲染为结构化 Markdown
                        if isinstance(task_brief, dict) and task_brief.get("title"):
                            task_value = e._render_task_brief(task_brief)
                        if not isinstance(task_value, str) or not task_value.strip():
                            result_str = "工具参数错误: task 或 task_brief 必须提供其一。"
                            success = False
                            error = result_str
                        else:
                            agent_name_value = arguments.get("agent_name")
                            if agent_name_value is not None and not isinstance(agent_name_value, str):
                                result_str = "工具参数错误: agent_name 必须为字符串。"
                                success = False
                                error = result_str
                            else:
                                raw_file_paths = arguments.get("file_paths")
                                if raw_file_paths is not None and not isinstance(raw_file_paths, list):
                                    result_str = "工具参数错误: file_paths 必须为字符串数组。"
                                    success = False
                                    error = result_str
                                else:
                                    delegate_outcome = await e._delegate_to_subagent(
                                        task=task_value.strip(),
                                        agent_name=agent_name_value.strip() if isinstance(agent_name_value, str) else None,
                                        file_paths=raw_file_paths,
                                        on_event=on_event,
                                    )
                                    result_str = delegate_outcome.reply
                                    success = delegate_outcome.success
                                    error = None if success else result_str

                                    # ── 写入传播：subagent 有文件变更时视为主 agent 写入 ──
                                    sub_result = delegate_outcome.subagent_result
                                    if (
                                        success
                                        and sub_result is not None
                                        and sub_result.structured_changes
                                    ):
                                        e._has_write_tool_call = True
                                        e._current_write_hint = "may_write"
                                        logger.info(
                                            "delegate_to_subagent 写入传播: structured_changes=%d, paths=%s",
                                            len(sub_result.structured_changes),
                                            sub_result.file_changes,
                                        )
                                    if (
                                        not success
                                        and sub_result is not None
                                        and sub_result.pending_approval_id is not None
                                    ):
                                        pending = e._approval.pending
                                        approval_id_value = sub_result.pending_approval_id
                                        high_risk_tool = (
                                            pending.tool_name
                                            if pending is not None and pending.approval_id == approval_id_value
                                            else "高风险工具"
                                        )
                                        question = e._enqueue_subagent_approval_question(
                                            approval_id=approval_id_value,
                                            tool_name=high_risk_tool,
                                            picked_agent=delegate_outcome.picked_agent or "subagent",
                                            task_text=delegate_outcome.task_text,
                                            normalized_paths=delegate_outcome.normalized_paths,
                                            tool_call_id=tool_call_id,
                                            on_event=on_event,
                                            iteration=iteration,
                                        )
                                        result_str = f"已创建待回答问题 `{question.question_id}`。"
                                        question_id = question.question_id
                                        pending_question = True
                                        defer_tool_result = True
                                        success = True
                                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "list_subagents":
                        result_str = e._handle_list_subagents()
                        success = True
                        error = None
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "finish_task":
                        report = arguments.get("report")
                        summary = arguments.get("summary", "")
                        rendered = _render_finish_task_report(report, summary)
                        _has_write = getattr(e, "_has_write_tool_call", False)
                        _hint = getattr(e, "_current_write_hint", "unknown")
                        if _has_write:
                            result_str = f"✅ 任务完成\n\n{rendered}" if rendered else "✓ 任务完成。"
                            success = True
                            error = None
                            finish_accepted = True
                        elif getattr(e, "_finish_task_warned", False):
                            result_str = f"✅ 任务完成（无写入）\n\n{rendered}" if rendered else "✓ 任务完成（无写入）。"
                            success = True
                            error = None
                            finish_accepted = True
                        else:
                            result_str = (
                                "⚠️ 未检测到写入类工具的成功调用。"
                                "如果确实不需要写入，请再次调用 finish_task 并在 report 或 summary 中说明原因。"
                                "否则请先执行写入操作。"
                            )
                            e._finish_task_warned = True
                            success = True
                            error = None
                            finish_accepted = False
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif tool_name == "ask_user":
                        result_str, question_id = e._handle_ask_user(
                            arguments=arguments,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                            iteration=iteration,
                        )
                        success = True
                        error = None
                        pending_question = True
                        defer_tool_result = True
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str,
                        )
                    elif (
                        tool_name == "task_create"
                        and e._plan_intercept_task_create
                        and not skip_plan_once_for_task_create
                    ):
                        result_str, plan_id, plan_error = await e._intercept_task_create_with_plan(
                            arguments=arguments,
                            route_result=route_result,
                            tool_call_id=tool_call_id,
                            on_event=on_event,
                        )
                        success = plan_error is None
                        error = plan_error
                        pending_plan = success
                        defer_tool_result = success
                        log_tool_call(
                            logger,
                            tool_name,
                            arguments,
                            result=result_str if success else None,
                            error=error if not success else None,
                        )
                    elif tool_name == "run_code" and e._config.code_policy_enabled:
                        # ── 动态代码策略引擎路由 ──
                        from excelmanus.security.code_policy import CodePolicyEngine, CodeRiskTier, extract_excel_targets
                        _code_arg = arguments.get("code") or ""
                        _cp_engine = CodePolicyEngine(
                            extra_safe_modules=e._config.code_policy_extra_safe_modules,
                            extra_blocked_modules=e._config.code_policy_extra_blocked_modules,
                        )
                        _analysis = _cp_engine.analyze(_code_arg)
                        _auto_green = (
                            _analysis.tier == CodeRiskTier.GREEN
                            and e._config.code_policy_green_auto_approve
                        )
                        _auto_yellow = (
                            _analysis.tier == CodeRiskTier.YELLOW
                            and e._config.code_policy_yellow_auto_approve
                        )
                        if _auto_green or _auto_yellow or e._full_access_enabled:
                            _sandbox_tier = _analysis.tier.value
                            _augmented_args = {**arguments, "sandbox_tier": _sandbox_tier}
                            result_value, audit_record = await e._execute_tool_with_audit(
                                tool_name=tool_name,
                                arguments=_augmented_args,
                                tool_scope=tool_scope,
                                approval_id=e._approval.new_approval_id(),
                                created_at_utc=e._approval.utc_now(),
                                undoable=False,
                            )
                            result_str = str(result_value)
                            tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                            if tool_def is not None:
                                result_str = tool_def.truncate_result(result_str)
                            success = True
                            error = None
                            # ── run_code 写入追踪 ──
                            # 三重检测：
                            #   1. audit_record.changes — 对 run_code 通常为空（不在
                            #      MUTATING_ALL_TOOLS，审计系统不做 workspace scan）
                            #   2. cow_mapping — 仅 bench 保护文件产生
                            #   3. AST 写入目标 — 检测 to_excel/wb.save 等写入调用
                            _rc_json: dict | None = None
                            try:
                                _rc_json = json.loads(result_str)
                                if not isinstance(_rc_json, dict):
                                    _rc_json = None
                            except (json.JSONDecodeError, TypeError):
                                pass
                            _has_cow = bool(_rc_json and _rc_json.get("cow_mapping"))
                            _has_ast_write = any(
                                t.operation == "write"
                                for t in extract_excel_targets(_code_arg)
                            )
                            if (
                                (audit_record is not None and audit_record.changes)
                                or _has_cow
                                or _has_ast_write
                            ):
                                e._state.record_write_action()
                            # ── run_code → window 感知桥接 ──
                            _stdout_tail = ""
                            if _rc_json is not None:
                                _stdout_tail = _rc_json.get("stdout_tail", "")
                            if audit_record is not None and e._window_perception is not None:
                                e._window_perception.observe_code_execution(
                                    code=_code_arg,
                                    audit_changes=audit_record.changes if audit_record else None,
                                    stdout_tail=_stdout_tail,
                                    iteration=iteration,
                                )
                            logger.info(
                                "run_code 策略引擎: tier=%s auto_approved=True caps=%s",
                                _analysis.tier.value,
                                sorted(_analysis.capabilities),
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        else:
                            # RED 或配置不允许自动执行 → /accept 流程
                            _caps_detail = ", ".join(sorted(_analysis.capabilities))
                            _details_text = "; ".join(_analysis.details[:3])
                            pending = e._approval.create_pending(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            pending_approval = True
                            approval_id = pending.approval_id
                            result_str = (
                                f"⚠️ 代码包含高风险操作，需要人工确认：\n"
                                f"- 风险等级: {_analysis.tier.value}\n"
                                f"- 检测到: {_caps_detail}\n"
                                f"- 详情: {_details_text}\n"
                                f"{e._format_pending_prompt(pending)}"
                            )
                            success = True
                            error = None
                            e._emit_pending_approval_event(
                                pending=pending, on_event=on_event, iteration=iteration,
                            )
                            logger.info(
                                "run_code 策略引擎: tier=%s → pending approval %s",
                                _analysis.tier.value,
                                pending.approval_id,
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                    elif e._approval.is_audit_only_tool(tool_name):
                        result_value, audit_record = await e._execute_tool_with_audit(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                            approval_id=e._approval.new_approval_id(),
                            created_at_utc=e._approval.utc_now(),
                            undoable=tool_name not in {"run_code", "run_shell"},
                        )
                        result_str = str(result_value)
                        tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                        if tool_def is not None:
                            result_str = tool_def.truncate_result(result_str)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                    elif e._approval.is_high_risk_tool(tool_name):
                        if not e._full_access_enabled and not skip_high_risk_approval_by_hook:
                            pending = e._approval.create_pending(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            pending_approval = True
                            approval_id = pending.approval_id
                            result_str = e._format_pending_prompt(pending)
                            success = True
                            error = None
                            e._emit_pending_approval_event(
                                pending=pending, on_event=on_event, iteration=iteration,
                            )
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        elif e._approval.is_mcp_tool(tool_name):
                            # 非白名单 MCP 工具在 fullaccess 下可直接执行（不做文件审计）。
                            result_value = await self.call_registry_tool(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                            )
                            result_str = str(result_value)
                            success = True
                            error = None
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                        else:
                            result_value, audit_record = await e._execute_tool_with_audit(
                                tool_name=tool_name,
                                arguments=arguments,
                                tool_scope=tool_scope,
                                approval_id=e._approval.new_approval_id(),
                                created_at_utc=e._approval.utc_now(),
                                undoable=tool_name not in {"run_code", "run_shell"},
                            )
                            result_str = str(result_value)
                            tool_def = getattr(e._registry, "get_tool", lambda _: None)(tool_name)
                            if tool_def is not None:
                                result_str = tool_def.truncate_result(result_str)
                            success = True
                            error = None
                            log_tool_call(logger, tool_name, arguments, result=result_str)
                    else:
                        result_value = await self.call_registry_tool(
                            tool_name=tool_name,
                            arguments=arguments,
                            tool_scope=tool_scope,
                        )
                        result_str = str(result_value)
                        success = True
                        error = None
                        log_tool_call(logger, tool_name, arguments, result=result_str)
                except ValueError as exc:
                    result_str = str(exc)
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
                except ToolNotAllowedError:
                    permission_error = {
                        "error_code": "TOOL_NOT_ALLOWED",
                        "tool": tool_name,
                        "message": f"工具 '{tool_name}' 不在当前授权范围内。",
                    }
                    result_str = json.dumps(permission_error, ensure_ascii=False)
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
                except Exception as exc:
                    root_exc: Exception = exc
                    if isinstance(exc, _AuditedExecutionError):
                        audit_record = exc.record
                        root_exc = exc.cause
                    result_str = f"工具执行错误: {root_exc}"
                    success = False
                    error = str(root_exc)
                    log_tool_call(logger, tool_name, arguments, error=error)

            # ── 检测 registry 层返回的结构化错误 JSON ──
            if success and e._registry.is_error_result(result_str):
                success = False
                try:
                    _err = json.loads(result_str)
                    error = _err.get("message") or result_str
                except Exception:
                    error = result_str

            post_hook_event = HookEvent.POST_TOOL_USE if success else HookEvent.POST_TOOL_USE_FAILURE
            post_hook_raw = e._run_skill_hook(
                skill=hook_skill,
                event=post_hook_event,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "success": success,
                    "result": result_str,
                    "error": error,
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            post_hook = await e._resolve_hook_result(
                event=post_hook_event,
                hook_result=post_hook_raw,
                on_event=on_event,
            )
            if post_hook is not None:
                if post_hook.additional_context:
                    result_str = f"{result_str}\n[Hook] {post_hook.additional_context}"
                if post_hook.decision == HookDecision.DENY:
                    reason = post_hook.reason or "post hook 拒绝"
                    success = False
                    error = reason
                    result_str = f"{result_str}\n[Hook 拒绝] {reason}"

        # ── 通用 CoW 映射提取：任何成功的工具调用都可能产生 cow_mapping ──
        if success and result_str:
            _cow_extracted = self._extract_and_register_cow_mapping(result_str)
            if _cow_extracted:
                logger.info(
                    "CoW 映射已注册: tool=%s mappings=%s", tool_name, _cow_extracted,
                )

        # ── CoW 路径拦截提醒：追加到工具结果中 ──
        if _cow_reminders:
            result_str = result_str + "\n" + "\n".join(_cow_reminders)

        # ── 图片注入：检测 _image_injection 并注入到 memory ──
        if success and result_str:
            result_str = self._try_inject_image(result_str)

        result_str = e._enrich_tool_result_with_window_perception(
            tool_name=tool_name,
            arguments=arguments,
            result_text=result_str,
            success=success,
        )
        result_str = e._apply_tool_result_hard_cap(result_str)
        if error:
            error = e._apply_tool_result_hard_cap(str(error))

        # 发射 TOOL_CALL_END 事件
        e._emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_END,
                tool_name=tool_name,
                arguments=arguments,
                result=result_str,
                success=success,
                error=error,
                iteration=iteration,
            ),
        )

        # 任务清单事件：成功执行 task_create/task_update 后发射对应事件
        if success and tool_name == "task_create" and not pending_plan:
            task_list = e._task_store.current
            if task_list is not None:
                e._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_LIST_CREATED,
                        task_list_data=task_list.to_dict(),
                    ),
                )
        elif success and tool_name == "task_update":
            task_list = e._task_store.current
            if task_list is not None:
                e._emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_ITEM_UPDATED,
                        task_index=arguments.get("task_index"),
                        task_status=arguments.get("status", ""),
                        task_result=arguments.get("result"),
                        task_list_data=task_list.to_dict(),
                    ),
                )

        return ToolCallResult(
            tool_name=tool_name,
            arguments=arguments,
            result=result_str,
            success=success,
            error=error,
            pending_approval=pending_approval,
            approval_id=approval_id,
            audit_record=audit_record,
            pending_question=pending_question,
            question_id=question_id,
            pending_plan=pending_plan,
            plan_id=plan_id,
            defer_tool_result=defer_tool_result,
            finish_accepted=finish_accepted,
        )
