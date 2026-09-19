"""Image-to-Excel 端到端集成测试。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest


# 最小有效 PNG（1x1 红色像素）
_MINIMAL_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _isolate_attachment_store(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
    from excelmanus.attachments.store import reset_attachment_store
    reset_attachment_store()
    yield
    reset_attachment_store()

_FULL_SPEC = {
    "version": "1.0",
    "provenance": {
        "source_image_hash": "sha256:abc123",
        "model": "test-model",
        "timestamp": "2026-02-21T11:30:00Z",
    },
    "workbook": {
        "name": "replica",
        "locale": "zh-CN",
        "default_font": {"name": "等线", "size": 11},
    },
    "sheets": [{
        "name": "Sheet1",
        "dimensions": {"rows": 3, "cols": 3},
        "freeze_panes": "A2",
        "cells": [
            {"address": "A1", "value": "产品", "value_type": "string", "style_id": "header", "confidence": 0.98},
            {"address": "B1", "value": "数量", "value_type": "string", "style_id": "header", "confidence": 0.98},
            {"address": "C1", "value": "金额", "value_type": "string", "style_id": "header", "confidence": 0.98},
            {"address": "A2", "value": "苹果", "value_type": "string", "confidence": 1.0},
            {"address": "B2", "value": 100, "value_type": "number", "confidence": 1.0},
            {"address": "C2", "value": 500.5, "value_type": "number", "confidence": 0.95},
            {"address": "A3", "value": "香蕉", "value_type": "string", "confidence": 1.0},
            {"address": "B3", "value": 200, "value_type": "number", "confidence": 1.0},
            {"address": "C3", "value": 300, "value_type": "number", "confidence": 0.9},
        ],
        "merged_ranges": [],
        "styles": {
            "header": {
                "font": {"bold": True, "size": 12, "color": "#FFFFFF", "name": "微软雅黑"},
                "fill": {"type": "solid", "color": "#4472C4"},
                "alignment": {"horizontal": "center"},
            },
        },
        "column_widths": [15, 10, 12],
        "row_heights": {"1": 28},
    }],
    "uncertainties": [
        {
            "location": "C2",
            "reason": "小数部分模糊",
            "candidate_values": ["500.5", "500.8"],
            "confidence": 0.65,
        },
    ],
}


class TestImageToExcelPipeline:
    """端到端：spec 文本编译为 xlsx，再用 openpyxl 核对格子。"""

    def test_spec_roundtrip(self, tmp_path: Path) -> None:
        from openpyxl import load_workbook

        from excelmanus.replica_spec import compile_spec_text_to_bytes

        excel_path = tmp_path / "draft.xlsx"
        data, summary = compile_spec_text_to_bytes(json.dumps(_FULL_SPEC))
        excel_path.write_bytes(data)
        assert excel_path.exists()
        assert summary["cells_written"] == 9

        wb = load_workbook(str(excel_path))
        ws = wb["Sheet1"]
        assert ws["A1"].value == "产品"
        assert ws["B2"].value == 100
        assert ws["C2"].value == 500.5
        assert ws["A1"].font.bold is True
        assert ws.freeze_panes == "A2"

    def test_read_image_returns_injection(self, tmp_path: Path) -> None:
        """read_image → ui_meta.image，不把 base64 写进 model_text。"""
        from excelmanus.tools.image_tools import read_image, init_guard

        png_data = base64.b64decode(_MINIMAL_PNG_B64)
        img_path = tmp_path / "test.png"
        img_path.write_bytes(png_data)
        init_guard(str(tmp_path))

        out = read_image(file_path=str(img_path))
        assert out.success
        injection = out.ui_meta.image
        assert injection["mime_type"] == "image/png"
        assert injection.get("attachment", {}).get("attachmentId", "").startswith("sha256:")
        assert "base64" not in injection
        assert "__tool_result_image__" not in out.model_text

    def test_rebuild_with_merged_cells_and_styles(self, tmp_path: Path) -> None:
        """复杂 spec（合并+样式+freeze）编译正确。"""
        from openpyxl import load_workbook

        from excelmanus.replica_spec import compile_spec_text_to_bytes

        merge_spec = {
            **_FULL_SPEC,
            "sheets": [{
                "name": "Sheet1",
                "dimensions": {"rows": 3, "cols": 3},
                "freeze_panes": "A2",
                "cells": [
                    {"address": "A1", "value": "标题", "value_type": "string", "style_id": "header", "confidence": 0.98},
                    {"address": "A2", "value": "苹果", "value_type": "string", "confidence": 1.0},
                    {"address": "B2", "value": 100, "value_type": "number", "confidence": 1.0},
                    {"address": "C2", "value": 500.5, "value_type": "number", "confidence": 0.95},
                ],
                "merged_ranges": [{"range": "A1:C1", "confidence": 0.95}],
                "styles": _FULL_SPEC["sheets"][0]["styles"],
                "column_widths": [15, 10, 12],
                "row_heights": {"1": 28},
            }],
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(merge_spec), encoding="utf-8")
        excel_path = tmp_path / "complex.xlsx"

        data, summary = compile_spec_text_to_bytes(json.dumps(merge_spec))
        excel_path.write_bytes(data)
        assert summary["merges_applied"] == 1

        # 验证 openpyxl 结构
        wb = load_workbook(str(excel_path))
        ws = wb["Sheet1"]
        assert ws.freeze_panes == "A2"
        assert len(ws.merged_cells.ranges) == 1
        assert ws["A1"].font.bold is True

    def test_replica_spec_pydantic_roundtrip(self) -> None:
        """ReplicaSpec JSON 序列化/反序列化一致性。"""
        from excelmanus.replica_spec import ReplicaSpec

        spec = ReplicaSpec.model_validate(_FULL_SPEC)
        json_str = spec.model_dump_json()
        spec2 = ReplicaSpec.model_validate_json(json_str)
        assert spec.version == spec2.version
        assert len(spec.sheets) == len(spec2.sheets)
        assert len(spec.sheets[0].cells) == len(spec2.sheets[0].cells)
        assert spec.uncertainties[0].location == spec2.uncertainties[0].location

    def test_multimodal_memory_integration(self, tmp_path, monkeypatch) -> None:
        """Memory 层多模态消息与 TokenCounter 集成。"""
        from excelmanus.config import ExcelManusConfig
        from excelmanus.memory import ConversationMemory, TokenCounter, IMAGE_TOKEN_ESTIMATE
        from excelmanus.attachments.store import reset_attachment_store

        monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
        reset_attachment_store()
        config = ExcelManusConfig(
            api_key="test", base_url="https://test.example.com/v1", model="test",
        )
        mem = ConversationMemory(config)

        # 添加图片消息
        png = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload

        ref = admit_image_bytes(decode_image_payload(png), media_type="image/png")
        mem.add_user_message([{"type": "image", "attachment": ref.to_dict()}])
        msgs = mem.get_messages()
        last = msgs[-1]
        assert last["role"] == "user"
        assert isinstance(last["content"], list)
        assert last["content"][0]["type"] == "image"

        count = TokenCounter.count_message(last)
        assert count >= IMAGE_TOKEN_ESTIMATE

    def test_provider_image_conversion(self) -> None:
        """Gemini 和 Claude provider 正确转换图片 content part。"""
        from excelmanus.providers.gemini import _openai_messages_to_gemini
        from excelmanus.providers.claude import _openai_messages_to_claude

        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "分析图片"},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64,abc123",
                }},
            ]},
        ]

        # Gemini
        _, contents = _openai_messages_to_gemini(messages)
        parts = contents[0]["parts"]
        assert any("inlineData" in p for p in parts)

        # Claude
        _, claude_msgs = _openai_messages_to_claude(messages)
        content = claude_msgs[0]["content"]
        assert any(b.get("type") == "image" for b in content)
