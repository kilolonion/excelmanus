"""VLM 超时/重试/错误净化 单元测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.engine_core.tool_dispatcher import ToolDispatcher


class TestSanitizeVlmError:
    """_sanitize_vlm_error 错误净化测试。"""

    def test_html_504_extracts_code_and_title(self) -> None:
        exc = Exception(
            '<!DOCTYPE html><html><head><title>example.com | 504: Gateway time-out</title></head>'
            '<body>lots of html...</body></html>'
        )
        result = ToolDispatcher._sanitize_vlm_error(exc)
        assert "504" in result
        assert "Gateway time-out" in result
        assert "<html" not in result

    def test_html_502_extracts_code(self) -> None:
        exc = Exception(
            '<html><head><title>502 Bad Gateway</title></head><body></body></html>'
        )
        result = ToolDispatcher._sanitize_vlm_error(exc)
        assert "502" in result
        assert "<html" not in result

    def test_plain_error_preserved(self) -> None:
        exc = Exception("Connection refused")
        result = ToolDispatcher._sanitize_vlm_error(exc)
        assert result == "Connection refused"

    def test_long_error_truncated(self) -> None:
        exc = Exception("x" * 1000)
        result = ToolDispatcher._sanitize_vlm_error(exc)
        assert len(result) <= 500


class TestBuildVlmFailureResult:
    """_build_vlm_failure_result 降级引导测试。"""

    def test_contains_fallback_hint(self) -> None:
        exc = TimeoutError("VLM 调用超时（120s）")
        result = json.loads(ToolDispatcher._build_vlm_failure_result(exc, 3, "test.png"))
        assert result["status"] == "error"
        assert result["error_code"] == "VLM_CALL_FAILED"
        assert "fallback_hint" in result
        assert "read_image" in result["fallback_hint"]
        assert "run_code" in result["fallback_hint"]
        assert result["file_path"] == "test.png"

    def test_html_error_sanitized_in_message(self) -> None:
        exc = Exception('<html><head><title>504: Gateway time-out</title></head></html>')
        result = json.loads(ToolDispatcher._build_vlm_failure_result(exc, 2, "img.png"))
        assert "<html" not in result["message"]
        assert "504" in result["message"]

    def test_none_error_handled(self) -> None:
        result = json.loads(ToolDispatcher._build_vlm_failure_result(None, 1, "x.png"))
        assert "未知错误" in result["message"]


def _make_mock_engine(
    *,
    vlm_timeout: int = 5,
    vlm_max_retries: int = 1,
    vlm_base_delay: float = 0.01,
    model: str = "test-model",
) -> MagicMock:
    """构建用于测试的 mock engine。"""
    engine = MagicMock()
    engine._config = SimpleNamespace(
        model=model,
        workspace_root="/tmp",
        vlm_timeout_seconds=vlm_timeout,
        vlm_max_retries=vlm_max_retries,
        vlm_retry_base_delay_seconds=vlm_base_delay,
        vlm_image_max_long_edge=2048,
        vlm_image_jpeg_quality=92,
    )
    engine._client = MagicMock()
    engine._client.chat.completions.create = AsyncMock()
    # VLM 默认使用与主模型相同的 client/model
    engine._vlm_client = engine._client
    engine._vlm_model = model
    engine._memory = MagicMock()
    return engine


def _make_vlm_response(content: str) -> SimpleNamespace:
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


_VALID_SPEC_JSON = json.dumps({
    "version": "1.0",
    "provenance": {"source_image_hash": "sha256:x", "model": "test", "timestamp": "2026-01-01T00:00:00Z"},
    "workbook": {"name": "test"},
    "sheets": [{
        "name": "Sheet1",
        "dimensions": {"rows": 1, "cols": 1},
        "cells": [{"address": "A1", "value": "hello", "value_type": "string", "confidence": 0.9}],
        "styles": {},
    }],
    "uncertainties": [],
})


class TestHandleExtractTableRetry:
    """_handle_extract_table 超时/重试集成测试。"""

    @pytest.fixture
    def dispatcher(self) -> ToolDispatcher:
        engine = MagicMock()
        engine._memory = MagicMock()
        return ToolDispatcher(engine)

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """首次调用成功时直接返回。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.return_value = _make_vlm_response(_VALID_SPEC_JSON)

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "ok"
        assert engine._client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_no_retry(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """超时后不重试，直接返回降级引导（VLM 本身就慢）。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_timeout=1, vlm_max_retries=2, vlm_base_delay=0.01)
        engine._config.workspace_root = str(tmp_path)

        async def always_slow(**kwargs):
            await asyncio.sleep(10)

        engine._client.chat.completions.create.side_effect = always_slow

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        # 超时不重试，只调用一次
        assert result["status"] == "error"
        assert result["error_code"] == "VLM_CALL_FAILED"
        assert engine._client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_api_error_retries_exhausted_returns_fallback(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """网络错误重试耗尽后返回降级引导。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_timeout=5, vlm_max_retries=1, vlm_base_delay=0.01)
        engine._config.workspace_root = str(tmp_path)

        engine._client.chat.completions.create.side_effect = Exception("Connection reset")

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "error"
        assert result["error_code"] == "VLM_CALL_FAILED"
        assert "fallback_hint" in result
        assert "read_image" in result["fallback_hint"]
        # 网络错误重试 1 次 = 总共 2 次调用
        assert engine._client.chat.completions.create.call_count == 2

    @pytest.mark.asyncio
    async def test_api_error_retry_with_backoff(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """API 错误触发重试并成功。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_max_retries=2, vlm_base_delay=0.01)
        engine._config.workspace_root = str(tmp_path)

        call_count = 0

        async def fail_then_ok(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise Exception("Internal Server Error")
            return _make_vlm_response(_VALID_SPEC_JSON)

        engine._client.chat.completions.create.side_effect = fail_then_ok

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_html_error_sanitized_in_failure(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """HTML 错误在失败结果中被净化。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_max_retries=0, vlm_base_delay=0.01)
        engine._config.workspace_root = str(tmp_path)

        engine._client.chat.completions.create.side_effect = Exception(
            '<html><head><title>504: Gateway time-out</title></head></html>'
        )

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "error"
        assert "<html" not in result["message"]
        assert "504" in result["message"]

    @pytest.mark.asyncio
    async def test_parse_failure_returns_structured_error(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """VLM 返回非法 JSON 时返回结构化解析错误。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.return_value = _make_vlm_response("not valid json at all")

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "error"
        assert result["error_code"] == "PARSE_FAILED"
        assert "fallback_hint" in result

    @pytest.mark.asyncio
    async def test_config_values_respected(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """配置的 timeout/retries 被正确使用。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_timeout=2, vlm_max_retries=3, vlm_base_delay=0.01)
        engine._config.workspace_root = str(tmp_path)

        async def always_fail(**kwargs):
            raise Exception("always fails")

        engine._client.chat.completions.create.side_effect = always_fail

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "single"},
            engine,
        ))
        assert result["status"] == "error"
        # 3 retries + 1 initial = 4 calls total
        assert engine._client.chat.completions.create.call_count == 4

    @pytest.mark.asyncio
    async def test_rejects_outside_workspace_input_path(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """输入图片路径在 workspace 外时应被拒绝。"""
        outside_dir = Path(tempfile.mkdtemp())
        outside_img = outside_dir / "outside.png"
        outside_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.return_value = _make_vlm_response(_VALID_SPEC_JSON)

        result = json.loads(
            await dispatcher._handle_extract_table(
                {"file_path": str(outside_img), "output_path": "spec.json"},
                engine,
            )
        )
        assert result["status"] == "error"
        assert "路径" in result["message"]
        engine._client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_outside_workspace_output_path(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """输出 spec 路径在 workspace 外时应被拒绝。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        outside_dir = Path(tempfile.mkdtemp())
        outside_spec = outside_dir / "spec.json"

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.return_value = _make_vlm_response(_VALID_SPEC_JSON)

        result = json.loads(
            await dispatcher._handle_extract_table(
                {"file_path": str(img), "output_path": str(outside_spec)},
                engine,
            )
        )
        assert result["status"] == "error"
        assert "路径" in result["message"]


class TestTwoPhaseExtraction:
    """两阶段提取策略测试。"""

    @pytest.fixture
    def dispatcher(self) -> ToolDispatcher:
        engine = MagicMock()
        engine._memory = MagicMock()
        return ToolDispatcher(engine)

    @pytest.mark.asyncio
    async def test_two_phase_success(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """两阶段提取成功：Phase A HTML + Phase B 样式。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)

        html_response = "<table><tr><th>名称</th><th>数量</th></tr><tr><td>苹果</td><td>10</td></tr></table>"
        style_response = json.dumps({
            "styles": {"h1": {"font": {"bold": True}}},
            "cell_styles": {"A1": "h1", "B1": "h1"},
            "column_widths": [12, 8],
        })

        call_count = 0
        async def phase_responses(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_vlm_response(html_response)
            return _make_vlm_response(style_response)

        engine._client.chat.completions.create.side_effect = phase_responses

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "two_phase"},
            engine,
        ))
        assert result["status"] == "ok"
        assert call_count == 2  # Phase A + Phase B

    @pytest.mark.asyncio
    async def test_two_phase_degrades_without_styles(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """Phase B 失败时降级为无样式（Phase A 数据仍保留）。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)

        html_response = "<table><tr><td>test</td></tr></table>"

        call_count = 0
        async def phase_a_ok_b_fail(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_vlm_response(html_response)
            raise Exception("Phase B network error")

        engine._client.chat.completions.create.side_effect = phase_a_ok_b_fail

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "two_phase"},
            engine,
        ))
        # Phase A 成功就能出结果（无样式降级）
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_two_phase_a_failure_returns_error(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """Phase A 失败时返回错误。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine(vlm_max_retries=0)
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.side_effect = Exception("timeout")

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "two_phase"},
            engine,
        ))
        assert result["status"] == "error"
        assert result["error_code"] == "VLM_CALL_FAILED"

    @pytest.mark.asyncio
    async def test_two_phase_a_no_html_returns_parse_error(self, dispatcher: ToolDispatcher, tmp_path) -> None:
        """Phase A 返回非 HTML 内容时返回解析错误。"""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        engine = _make_mock_engine()
        engine._config.workspace_root = str(tmp_path)
        engine._client.chat.completions.create.return_value = _make_vlm_response(
            "这是一些纯文本，没有表格"
        )

        result = json.loads(await dispatcher._handle_extract_table(
            {"file_path": str(img), "output_path": "spec.json", "strategy": "two_phase"},
            engine,
        ))
        assert result["status"] == "error"
        assert result["error_code"] == "PHASE_A_PARSE_FAILED"
        assert "fallback_hint" in result


class TestVisionExtractor:
    """vision_extractor 模块单元测试。"""

    def test_parse_html_table_from_code_block(self) -> None:
        from excelmanus.vision_extractor import parse_html_table
        raw = "```html\n<table><tr><td>A</td></tr></table>\n```"
        assert "<table>" in parse_html_table(raw)

    def test_parse_html_table_raw(self) -> None:
        from excelmanus.vision_extractor import parse_html_table
        raw = "<table><tr><td>A</td></tr></table>"
        assert "<table>" in parse_html_table(raw)

    def test_parse_html_table_no_table_raises(self) -> None:
        from excelmanus.vision_extractor import parse_html_table
        with pytest.raises(ValueError, match="<table>"):
            parse_html_table("no table here")

    def test_html_table_to_replica_spec_basic(self) -> None:
        from excelmanus.vision_extractor import html_table_to_replica_spec
        html = "<table><tr><th>Name</th><th>Age</th></tr><tr><td>Alice</td><td>30</td></tr></table>"
        spec = html_table_to_replica_spec(html)
        assert len(spec.sheets) == 1
        assert len(spec.sheets[0].cells) == 4
        # 检查数值推断
        age_cell = [c for c in spec.sheets[0].cells if c.address == "B2"][0]
        assert age_cell.value == 30
        assert age_cell.value_type == "number"

    def test_html_table_to_replica_spec_with_styles(self) -> None:
        from excelmanus.vision_extractor import html_table_to_replica_spec
        html = "<table><tr><td>X</td></tr></table>"
        style = {
            "styles": {"s1": {"font": {"bold": True}}},
            "cell_styles": {"A1": "s1"},
            "column_widths": [15],
        }
        spec = html_table_to_replica_spec(html, style)
        assert spec.sheets[0].cells[0].style_id == "s1"
        assert "s1" in spec.sheets[0].styles
        assert spec.sheets[0].column_widths == [15]

    def test_html_table_colspan_merge(self) -> None:
        from excelmanus.vision_extractor import html_table_to_replica_spec
        html = '<table><tr><td colspan="3">Merged</td></tr><tr><td>A</td><td>B</td><td>C</td></tr></table>'
        spec = html_table_to_replica_spec(html)
        assert len(spec.sheets[0].merged_ranges) == 1
        assert spec.sheets[0].merged_ranges[0].range == "A1:C1"

    def test_infer_value_type_currency(self) -> None:
        from excelmanus.vision_extractor import _infer_value_type
        val, vtype = _infer_value_type("$1,200.50")
        assert vtype == "number"
        assert abs(val - 1200.50) < 0.01

    def test_infer_value_type_percent(self) -> None:
        from excelmanus.vision_extractor import _infer_value_type
        val, vtype = _infer_value_type("85%")
        assert vtype == "number"
        assert abs(val - 0.85) < 0.01

    def test_infer_value_type_empty(self) -> None:
        from excelmanus.vision_extractor import _infer_value_type
        val, vtype = _infer_value_type("")
        assert vtype == "empty"

    def test_col_num_to_letter(self) -> None:
        from excelmanus.vision_extractor import _col_num_to_letter
        assert _col_num_to_letter(1) == "A"
        assert _col_num_to_letter(26) == "Z"
        assert _col_num_to_letter(27) == "AA"

    def test_build_extraction_prompt_has_cot(self) -> None:
        from excelmanus.vision_extractor import build_extraction_prompt
        prompt = build_extraction_prompt(focus="full")
        assert "Step 1" in prompt
        assert "Step 2" in prompt
        assert "❌" in prompt  # 负面约束
        assert "number_format" in prompt  # few-shot 示例

    def test_build_phase_a_prompt(self) -> None:
        from excelmanus.vision_extractor import build_phase_a_prompt
        prompt = build_phase_a_prompt()
        assert "<table>" in prompt
        assert "HTML" in prompt
        assert "[?]" in prompt

    def test_build_phase_b_prompt_includes_html(self) -> None:
        from excelmanus.vision_extractor import build_phase_b_prompt
        prompt = build_phase_b_prompt("<table><tr><td>X</td></tr></table>")
        assert "<table>" in prompt
        assert "cell_styles" in prompt


class TestPrepareImageForVlm:
    """图片预处理测试。"""

    def test_small_image_not_resized(self) -> None:
        """小图片不应被缩放。"""
        import io
        from PIL import Image
        img = Image.new("RGB", (800, 600), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()

        result, mime = ToolDispatcher._prepare_image_for_vlm(raw, max_long_edge=2048)
        # 不应放大（800 < 2048）
        assert len(result) > 0

    def test_large_image_resized(self) -> None:
        """大图片应被缩放到 max_long_edge。"""
        import io
        from PIL import Image
        img = Image.new("RGB", (4000, 3000), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()

        result, mime = ToolDispatcher._prepare_image_for_vlm(raw, max_long_edge=2048)
        # 验证输出确实被处理了
        result_img = Image.open(io.BytesIO(result))
        assert max(result_img.size) <= 2048

    def test_rgba_converted_to_rgb(self) -> None:
        """RGBA 图片应被转换为 RGB。"""
        import io
        from PIL import Image
        img = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()

        result, mime = ToolDispatcher._prepare_image_for_vlm(raw)
        assert len(result) > 0

    def test_gray_background_detection(self) -> None:
        """灰底图片应被检测并预处理。"""
        import io
        from PIL import Image
        # 创建灰底图片（mean ~200, low stddev）
        img = Image.new("RGB", (200, 200), (200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        result, mime = ToolDispatcher._prepare_image_for_vlm(raw)
        assert len(result) > 0

    def test_low_contrast_image_enhanced(self) -> None:
        """低对比度图片应被增强。"""
        import io
        from PIL import Image
        # 灰色图片（低 stddev）
        img = Image.new("RGB", (200, 200), (180, 180, 180))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
        result, mime = ToolDispatcher._prepare_image_for_vlm(raw)
        assert len(result) > 0


class TestInferNumberFormat:
    """_infer_number_format 数字格式推断测试。"""

    def test_percentage(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("85%") == "0%"

    def test_percentage_with_decimal(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("12.5%") == "0.0%"

    def test_percentage_two_decimals(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("12.50%") == "0.00%"

    def test_dollar_with_comma(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("$1,200") == "$#,##0"

    def test_dollar_with_decimals(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("$1,200.50") == "$#,##0.00"

    def test_yen_with_comma(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("¥1,200") == "¥#,##0"

    def test_comma_separated_no_decimal(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("1,200") == "#,##0"

    def test_comma_with_decimals(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("1,200.00") == "#,##0.00"

    def test_decimal_only(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("12.50") == "0.00"

    def test_plain_integer_returns_none(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("1200") is None

    def test_empty_returns_none(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("") is None

    def test_text_returns_none(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("hello") is None

    def test_negative_percentage(self) -> None:
        from excelmanus.tools.image_tools import _infer_number_format
        assert _infer_number_format("-5.5%") == "0.0%"


class TestRebuildBorderPerSide:
    """rebuild_excel_from_spec Border 四边独立测试。"""

    def test_per_side_border(self, tmp_path: Path) -> None:
        """四边独立 border 应正确应用。"""
        from excelmanus.tools.image_tools import rebuild_excel_from_spec, init_guard

        spec = {
            "version": "1.0",
            "provenance": {"source_image_hash": "", "model": "", "timestamp": ""},
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 1},
                "cells": [{"address": "A1", "value": "test", "value_type": "string", "style_id": "s1", "confidence": 0.9}],
                "styles": {
                    "s1": {
                        "border": {
                            "top": {"style": "thick", "color": "#000000"},
                            "bottom": {"style": "thin", "color": "#FF0000"},
                        }
                    }
                },
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        init_guard(str(tmp_path))

        result = json.loads(rebuild_excel_from_spec(
            spec_path=str(spec_path),
            output_path=str(tmp_path / "out.xlsx"),
        ))
        assert result["status"] == "ok"

        # 验证 border 已应用
        from openpyxl import load_workbook
        wb = load_workbook(str(tmp_path / "out.xlsx"))
        cell = wb.active["A1"]
        assert cell.border.top.style == "thick"
        assert cell.border.bottom.style == "thin"

    def test_uniform_border_backward_compat(self, tmp_path: Path) -> None:
        """统一 border（旧格式）应向后兼容。"""
        from excelmanus.tools.image_tools import rebuild_excel_from_spec, init_guard

        spec = {
            "version": "1.0",
            "provenance": {"source_image_hash": "", "model": "", "timestamp": ""},
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 1},
                "cells": [{"address": "A1", "value": "test", "value_type": "string", "style_id": "s1", "confidence": 0.9}],
                "styles": {"s1": {"border": {"style": "thin"}}},
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        init_guard(str(tmp_path))

        result = json.loads(rebuild_excel_from_spec(
            spec_path=str(spec_path),
            output_path=str(tmp_path / "out.xlsx"),
        ))
        assert result["status"] == "ok"

        from openpyxl import load_workbook
        wb = load_workbook(str(tmp_path / "out.xlsx"))
        cell = wb.active["A1"]
        assert cell.border.top.style == "thin"
        assert cell.border.left.style == "thin"

    def test_number_format_inferred_from_display_text(self, tmp_path: Path) -> None:
        """number_format 应从 display_text 自动推断。"""
        from excelmanus.tools.image_tools import rebuild_excel_from_spec, init_guard

        spec = {
            "version": "1.0",
            "provenance": {"source_image_hash": "", "model": "", "timestamp": ""},
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 2, "cols": 1},
                "cells": [
                    {"address": "A1", "value": 1200.5, "value_type": "number", "display_text": "$1,200.50", "confidence": 0.9},
                    {"address": "A2", "value": 0.85, "value_type": "number", "display_text": "85%", "confidence": 0.9},
                ],
                "styles": {},
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        init_guard(str(tmp_path))

        result = json.loads(rebuild_excel_from_spec(
            spec_path=str(spec_path),
            output_path=str(tmp_path / "out.xlsx"),
        ))
        assert result["status"] == "ok"

        from openpyxl import load_workbook
        wb = load_workbook(str(tmp_path / "out.xlsx"))
        ws = wb.active
        assert ws["A1"].number_format == "$#,##0.00"
        assert ws["A2"].number_format == "0%"

    def test_column_width_tolerance(self, tmp_path: Path) -> None:
        """列宽数组过长时不应崩溃。"""
        from excelmanus.tools.image_tools import rebuild_excel_from_spec, init_guard

        spec = {
            "version": "1.0",
            "provenance": {"source_image_hash": "", "model": "", "timestamp": ""},
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 1, "cols": 2},
                "cells": [
                    {"address": "A1", "value": "x", "value_type": "string", "confidence": 0.9},
                ],
                "column_widths": [15, 12, 10, 8, 20, 25, 30, 35, 40, 45, 50],
                "styles": {},
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        init_guard(str(tmp_path))

        result = json.loads(rebuild_excel_from_spec(
            spec_path=str(spec_path),
            output_path=str(tmp_path / "out.xlsx"),
        ))
        assert result["status"] == "ok"
