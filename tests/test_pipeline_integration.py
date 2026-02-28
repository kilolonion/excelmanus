"""Pipeline 集成测试：mock VLM caller 覆盖 4 阶段流转、fallback、chunked、pause。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from excelmanus.pipeline.models import PipelineConfig, PipelinePhase
from excelmanus.pipeline.progressive import PipelinePauseError, ProgressivePipeline

# ── 测试用常量 ──

_PROVENANCE = {
    "source_image_hash": "sha256:test",
    "model": "test-vlm",
    "timestamp": "2026-01-01T00:00:00Z",
}

_DUMMY_IMAGE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
_DUMMY_MIME = "image/png"

# ── Mock VLM 响应工厂 ──

_PHASE1_RESPONSE = json.dumps({
    "tables": [{
        "name": "Sheet1",
        "dimensions": {"rows": 3, "cols": 2},
        "header_rows": [1],
        "total_rows": [],
        "merges": [],
        "col_widths": [15, 10],
        "row_types": {"1": "header", "2": "data", "3": "data"},
        "uncertainties": [],
    }]
})

_PHASE2_RESPONSE = json.dumps({
    "tables": [{
        "name": "Sheet1",
        "cells": [
            {"addr": "A1", "val": "Name", "type": "string"},
            {"addr": "B1", "val": "Score", "type": "string"},
            {"addr": "A2", "val": "Alice", "type": "string"},
            {"addr": "B2", "val": 90, "type": "number"},
            {"addr": "A3", "val": "Bob", "type": "string"},
            {"addr": "B3", "val": 85, "type": "number"},
        ],
        "uncertainties": [],
    }]
})

_PHASE3_RESPONSE = json.dumps({
    "styles": {
        "header": {"font": {"bold": True, "color": "white"}, "fill": {"color": "dark_blue"}},
    },
    "cell_styles": {"A1:B1": "header"},
})

_PHASE4_RESPONSE_NO_PATCHES = json.dumps({
    "patches": [],
    "overall_confidence": 0.95,
    "summary": "无错误",
})

_PHASE4_RESPONSE_WITH_PATCH = json.dumps({
    "patches": [{
        "target": "cell",
        "sheet_name": "Sheet1",
        "address": "B2",
        "field": "value",
        "old_value": 90,
        "new_value": 95,
        "reason": "原始图片中该数字为 95",
        "confidence": 0.9,
    }],
    "overall_confidence": 0.92,
    "summary": "1 处修正",
})


def _identity_image_preparer(image_bytes: bytes, mode: str) -> tuple[bytes, str]:
    return image_bytes, _DUMMY_MIME


def _make_pipeline(
    tmp_dir: str,
    vlm_caller,
    *,
    config: PipelineConfig | None = None,
    resume_from_phase: int | None = None,
    resume_spec_path: str | None = None,
) -> ProgressivePipeline:
    return ProgressivePipeline(
        image_bytes=_DUMMY_IMAGE,
        mime=_DUMMY_MIME,
        file_path="/fake/image.png",
        output_dir=tmp_dir,
        output_basename="test_spec",
        config=config or PipelineConfig(),
        vlm_caller=vlm_caller,
        image_preparer=_identity_image_preparer,
        provenance=_PROVENANCE,
        on_event=MagicMock(),
        resume_from_phase=resume_from_phase,
        resume_spec_path=resume_spec_path,
    )


class _SequentialVLMCaller:
    """按顺序返回预设响应的 mock VLM caller。

    每次调用返回 responses 列表中的下一个元素。
    元素为 str 时返回 (str, None)；为 None 时返回 (None, Exception("mock fail"))。
    """

    def __init__(self, responses: list[str | None]):
        self._responses = list(responses)
        self._call_idx = 0
        self.calls: list[dict] = []

    async def __call__(
        self,
        messages: list[dict],
        label: str,
        response_format: dict | None,
    ) -> tuple[str | None, Exception | None]:
        idx = self._call_idx
        self._call_idx += 1
        # 只检查最后一条 user 消息是否含图片（不含累积的历史消息）
        last_msg = messages[-1] if messages else {}
        last_content = last_msg.get("content") if isinstance(last_msg.get("content"), list) else []
        self.calls.append({
            "idx": idx,
            "label": label,
            "message_count": len(messages),
            "has_image": any(
                isinstance(c, dict) and c.get("type") == "image_url"
                for c in last_content
            ),
        })
        if idx < len(self._responses):
            resp = self._responses[idx]
        else:
            resp = None
        if resp is None:
            return None, Exception("mock VLM failure")
        return resp, None


# ════════════════════════════════════════════════════════════════
# 4 阶段正常流转
# ════════════════════════════════════════════════════════════════


class TestHappyPath:
    """4 阶段正常流转：所有 VLM 调用成功。"""

    def test_four_phases_complete(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, path = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        assert os.path.isfile(path)
        assert len(spec.sheets) == 1
        assert len(spec.sheets[0].cells) == 6
        # 4 阶段 = 4 次 VLM 调用
        assert len(caller.calls) == 4

    def test_four_phases_with_patch(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_WITH_PATCH,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        # Phase 4 应用了 B2: 90 → 95
        b2 = next(c for c in spec.sheets[0].cells if c.address == "B2")
        assert b2.value == 95

    def test_skip_style(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            # Phase 3 skipped
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        config = PipelineConfig(skip_style=True)
        pipeline = _make_pipeline(str(tmp_path), caller, config=config)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        # 3 次调用（跳过 Phase 3）
        assert len(caller.calls) == 3
        # 无样式
        assert spec.sheets[0].styles == {}

    def test_event_callback_called(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        asyncio.get_event_loop().run_until_complete(pipeline.run())

        # on_event 应被调用 8 次（每阶段 2 次：开始 + 结束）
        assert pipeline._on_event.call_count == 8


# ════════════════════════════════════════════════════════════════
# Phase 1 Fallback
# ════════════════════════════════════════════════════════════════


class TestPhase1Fallback:
    """Phase 1 首次失败 → 清空对话重试。"""

    def test_phase1_retry_succeeds(self, tmp_path):
        caller = _SequentialVLMCaller([
            None,               # Phase 1 首次失败
            _PHASE1_RESPONSE,   # Phase 1 重试成功
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        assert len(caller.calls) == 5  # 1 fail + 4 success

    def test_phase1_both_fail_raises(self, tmp_path):
        caller = _SequentialVLMCaller([
            None,  # Phase 1 首次失败
            None,  # Phase 1 重试也失败
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        with pytest.raises(RuntimeError, match="Phase 1"):
            asyncio.get_event_loop().run_until_complete(pipeline.run())


# ════════════════════════════════════════════════════════════════
# Phase 2 Fallback
# ════════════════════════════════════════════════════════════════


class TestPhase2Fallback:
    """Phase 2 multi-turn 失败 → 清空对话 + 带图独立调用。"""

    def test_phase2_fallback_to_independent(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            None,               # Phase 2 multi-turn 失败
            _PHASE2_RESPONSE,   # Phase 2 独立重试成功
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        assert len(caller.calls) == 5
        # 重试调用应带图（独立调用）
        retry_call = caller.calls[2]
        assert retry_call["has_image"] is True

    def test_phase2_both_fail_raises(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            None,  # Phase 2 multi-turn 失败
            None,  # Phase 2 独立重试也失败
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        with pytest.raises(RuntimeError, match="Phase 2"):
            asyncio.get_event_loop().run_until_complete(pipeline.run())


# ════════════════════════════════════════════════════════════════
# Phase 3 Fallback
# ════════════════════════════════════════════════════════════════


class TestPhase3Fallback:
    """Phase 3 首次失败 → 清空对话 + 重传图片重试。"""

    def test_phase3_retry_succeeds(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            None,               # Phase 3 首次失败
            _PHASE3_RESPONSE,   # Phase 3 重试成功
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        assert spec.sheets[0].cells[0].style_id == "header"

    def test_phase3_both_fail_degrades(self, tmp_path):
        """Phase 3 双重失败 → 降级跳过样式（不抛异常）。"""
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            None,  # Phase 3 首次失败
            None,  # Phase 3 重试也失败
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        # 降级成功，无样式
        assert spec is not None
        assert spec.sheets[0].styles == {}


# ════════════════════════════════════════════════════════════════
# Phase 4 Fallback
# ════════════════════════════════════════════════════════════════


class TestPhase4Fallback:
    """Phase 4 multi-turn 失败 → 带图独立调用。"""

    def test_phase4_fallback_to_independent(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            None,                          # Phase 4 multi-turn 失败
            _PHASE4_RESPONSE_WITH_PATCH,   # Phase 4 独立重试成功
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        b2 = next(c for c in spec.sheets[0].cells if c.address == "B2")
        assert b2.value == 95

    def test_phase4_both_fail_no_patches(self, tmp_path):
        """Phase 4 双重失败 → 不抛异常，最终 spec = styled_spec。"""
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            None,  # Phase 4 multi-turn 失败
            None,  # Phase 4 独立重试也失败
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        # 无修正，保留原样
        assert spec is not None
        b2 = next(c for c in spec.sheets[0].cells if c.address == "B2")
        assert b2.value == 90


# ════════════════════════════════════════════════════════════════
# Chunked Phase 2（大表格分区提取）
# ════════════════════════════════════════════════════════════════


_LARGE_PHASE1_RESPONSE = json.dumps({
    "tables": [{
        "name": "Sheet1",
        "dimensions": {"rows": 150, "cols": 5},
        "header_rows": [1],
        "total_rows": [],
        "merges": [],
        "col_widths": [15, 10, 10, 10, 10],
    }]
})


def _make_chunk_phase2_response(row_start: int, row_end: int) -> str:
    cells = []
    for r in range(row_start, row_end + 1):
        cells.append({"addr": f"A{r}", "val": f"Row{r}", "type": "string"})
    return json.dumps({"tables": [{"name": "Sheet1", "cells": cells}]})


class TestChunkedPhase2:
    """大表格触发分区 Phase 2。"""

    def test_chunked_phase2_triggers(self, tmp_path):
        """rows=150, cols=5 → 750 cells > 500 threshold → 分区。"""
        # Phase 1 → 大表格骨架
        # Phase 2 chunks: 1-100, 101-150 = 2 个分片
        # Phase 3 → 样式
        # Phase 4 → 校验
        caller = _SequentialVLMCaller([
            _LARGE_PHASE1_RESPONSE,
            _make_chunk_phase2_response(1, 100),
            _make_chunk_phase2_response(101, 150),
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        # 100 + 50 = 150 cells
        assert len(spec.sheets[0].cells) == 150
        assert len(caller.calls) == 5

    def test_chunked_phase2_partial_failure(self, tmp_path):
        """分区中某片失败 → 跳过该区间，其余正常。"""
        caller = _SequentialVLMCaller([
            _LARGE_PHASE1_RESPONSE,
            _make_chunk_phase2_response(1, 100),
            None,  # 第 2 片失败
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        # 只有第 1 片的 100 cells
        assert len(spec.sheets[0].cells) == 100


# ════════════════════════════════════════════════════════════════
# Chunked Phase 4（大表格分区校验）
# ════════════════════════════════════════════════════════════════


def _make_large_phase2_response(n_rows: int) -> str:
    cells = []
    for r in range(1, n_rows + 1):
        cells.append({"addr": f"A{r}", "val": f"R{r}", "type": "string"})
        cells.append({"addr": f"B{r}", "val": r * 10, "type": "number"})
        cells.append({"addr": f"C{r}", "val": r * 20, "type": "number"})
        cells.append({"addr": f"D{r}", "val": r * 30, "type": "number"})
        cells.append({"addr": f"E{r}", "val": r * 40, "type": "number"})
    return json.dumps({"tables": [{"name": "Sheet1", "cells": cells}]})


class TestChunkedPhase4:
    """大表格触发分区 Phase 4 校验。"""

    def test_chunked_phase4_triggers(self, tmp_path):
        """750 cells > 500 → Phase 4 分区校验。"""
        p4_chunk1 = json.dumps({
            "patches": [{
                "target": "cell", "sheet_name": "Sheet1",
                "address": "A50", "field": "value",
                "old_value": "Row50", "new_value": "FixedRow50",
                "reason": "修正", "confidence": 0.9,
            }],
            "overall_confidence": 0.95,
        })
        p4_chunk2 = json.dumps({
            "patches": [],
            "overall_confidence": 0.95,
        })

        caller = _SequentialVLMCaller([
            _LARGE_PHASE1_RESPONSE,
            # Phase 2 chunked: 2 chunks
            _make_chunk_phase2_response(1, 100),
            _make_chunk_phase2_response(101, 150),
            _PHASE3_RESPONSE,
            # Phase 4 chunked: 2 chunks (rows 1-100, 101-150)
            p4_chunk1,
            p4_chunk2,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        # A50 应被修正为 FixedRow50
        a50 = next(c for c in spec.sheets[0].cells if c.address == "A50")
        assert a50.value == "FixedRow50"

    def test_chunked_phase4_no_patches(self, tmp_path):
        """分区校验无 patches → 原样返回。"""
        caller = _SequentialVLMCaller([
            _LARGE_PHASE1_RESPONSE,
            _make_chunk_phase2_response(1, 100),
            _make_chunk_phase2_response(101, 150),
            _PHASE3_RESPONSE,
            # Phase 4 chunks: both empty
            json.dumps({"patches": []}),
            json.dumps({"patches": []}),
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        assert spec is not None
        # 无修正
        assert len(spec.sheets[0].cells) == 150

    def test_chunked_phase4_partial_failure(self, tmp_path):
        """Phase 4 某分区失败 → 跳过，其余 patches 仍应用。"""
        p4_good = json.dumps({
            "patches": [{
                "target": "cell", "sheet_name": "Sheet1",
                "address": "A1", "field": "value",
                "old_value": "R1", "new_value": "Row1",
                "reason": "修正", "confidence": 0.9,
            }],
        })
        caller = _SequentialVLMCaller([
            _LARGE_PHASE1_RESPONSE,
            _make_chunk_phase2_response(1, 100),
            _make_chunk_phase2_response(101, 150),
            _PHASE3_RESPONSE,
            p4_good,  # 第 1 片成功
            None,     # 第 2 片失败
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())

        a1 = next(c for c in spec.sheets[0].cells if c.address == "A1")
        assert a1.value == "Row1"


# ════════════════════════════════════════════════════════════════
# Pause 机制
# ════════════════════════════════════════════════════════════════


_PHASE1_WITH_UNCERTAINTIES = json.dumps({
    "tables": [{
        "name": "Sheet1",
        "dimensions": {"rows": 3, "cols": 2},
        "header_rows": [1],
        "merges": [],
        "uncertainties": [
            {"location": "row_count", "reason": "底部截断", "candidates": ["3", "4"]},
            {"location": "col_count", "reason": "右侧截断", "candidates": ["2", "3"]},
            {"location": "merge_A1", "reason": "不确定合并", "candidates": ["A1:B1"]},
            {"location": "header", "reason": "表头模糊", "candidates": ["1", "2"]},
            {"location": "extra1", "reason": "额外不确定", "candidates": []},
            {"location": "extra2", "reason": "再额外一项", "candidates": []},
        ],
    }]
})


class TestPauseMechanism:
    """Uncertainty 超阈值 → PipelinePauseError。"""

    def test_pause_on_high_uncertainty(self, tmp_path):
        caller = _SequentialVLMCaller([_PHASE1_WITH_UNCERTAINTIES])
        config = PipelineConfig(uncertainty_pause_threshold=5)
        pipeline = _make_pipeline(str(tmp_path), caller, config=config)

        with pytest.raises(PipelinePauseError) as exc_info:
            asyncio.get_event_loop().run_until_complete(pipeline.run())

        err = exc_info.value
        assert err.phase == PipelinePhase.STRUCTURE
        assert len(err.uncertainties) == 6
        assert err.checkpoint["completed_phase"] == 1
        assert os.path.isfile(err.spec_path)

    def test_no_pause_when_below_threshold(self, tmp_path):
        caller = _SequentialVLMCaller([
            _PHASE1_WITH_UNCERTAINTIES,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        # 阈值设高到不触发
        config = PipelineConfig(uncertainty_pause_threshold=100)
        pipeline = _make_pipeline(str(tmp_path), caller, config=config)

        spec, _ = asyncio.get_event_loop().run_until_complete(pipeline.run())
        assert spec is not None

    def test_low_confidence_triggers_pause(self, tmp_path):
        """单项置信度 < floor 也触发 pause。"""
        # 默认 floor = 0.3，所有 uncertainty confidence=0.5 → 不触发
        # 但如果 floor=0.6，则所有 uncertainty (conf=0.5) < 0.6 → 触发
        caller = _SequentialVLMCaller([_PHASE1_WITH_UNCERTAINTIES])
        config = PipelineConfig(
            uncertainty_pause_threshold=100,  # 数量不触发
            uncertainty_confidence_floor=0.6,  # 但置信度触发
        )
        pipeline = _make_pipeline(str(tmp_path), caller, config=config)

        with pytest.raises(PipelinePauseError):
            asyncio.get_event_loop().run_until_complete(pipeline.run())


# ════════════════════════════════════════════════════════════════
# 断点恢复
# ════════════════════════════════════════════════════════════════


class TestResumeFromPhase:
    """断点续跑：从指定阶段后继续。"""

    def test_resume_from_phase2(self, tmp_path):
        """先完成 Phase 1-2，保存 spec，然后从 Phase 2 恢复继续 Phase 3-4。"""
        # 第一轮：完成 Phase 1-2
        caller1 = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline1 = _make_pipeline(str(tmp_path), caller1)
        spec1, path1 = asyncio.get_event_loop().run_until_complete(pipeline1.run())

        # 用 Phase 2 的 spec 路径恢复
        p2_path = str(tmp_path / "test_spec_p2.json")
        caller2 = _SequentialVLMCaller([
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_WITH_PATCH,
        ])
        pipeline2 = _make_pipeline(
            str(tmp_path), caller2,
            resume_from_phase=2,
            resume_spec_path=p2_path,
        )
        spec2, _ = asyncio.get_event_loop().run_until_complete(pipeline2.run())

        assert spec2 is not None
        # Phase 4 应用了补丁
        b2 = next(c for c in spec2.sheets[0].cells if c.address == "B2")
        assert b2.value == 95
        # 只调用了 2 次（Phase 3 + Phase 4）
        assert len(caller2.calls) == 2


# ════════════════════════════════════════════════════════════════
# Multi-turn 对话行为
# ════════════════════════════════════════════════════════════════


class TestMultiTurnBehavior:
    """验证 multi-turn 对话管理正确性。"""

    def test_phase2_no_image_in_multiturn(self, tmp_path):
        """Phase 2 在 multi-turn 模式下不传图。"""
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            _PHASE2_RESPONSE,
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        asyncio.get_event_loop().run_until_complete(pipeline.run())

        # Phase 1: 有图, Phase 2: 无图, Phase 3: 有图, Phase 4: 无图
        assert caller.calls[0]["has_image"] is True   # Phase 1
        assert caller.calls[1]["has_image"] is False  # Phase 2
        assert caller.calls[2]["has_image"] is True   # Phase 3
        assert caller.calls[3]["has_image"] is False  # Phase 4

    def test_conversation_cleared_on_fallback(self, tmp_path):
        """Fallback 时对话被清空，重试消息数应为 1。"""
        caller = _SequentialVLMCaller([
            _PHASE1_RESPONSE,
            None,               # Phase 2 multi-turn 失败
            _PHASE2_RESPONSE,   # Phase 2 独立重试
            _PHASE3_RESPONSE,
            _PHASE4_RESPONSE_NO_PATCHES,
        ])
        pipeline = _make_pipeline(str(tmp_path), caller)
        asyncio.get_event_loop().run_until_complete(pipeline.run())

        # Phase 2 重试（idx=2）应在清空对话后发起，消息数 = 1
        assert caller.calls[2]["message_count"] == 1
        assert caller.calls[2]["has_image"] is True


# ════════════════════════════════════════════════════════════════
# build_partial_summary / build_phase4_chunked_prompt 单元测试
# ════════════════════════════════════════════════════════════════


class TestPartialSummary:
    def test_partial_summary_filters_rows(self):
        from excelmanus.pipeline.phases import build_partial_summary, build_skeleton_spec, fill_data_into_spec

        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 5, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        data = {"tables": [{"name": "S1", "cells": [
            {"addr": "A1", "val": "r1", "type": "string"},
            {"addr": "A2", "val": "r2", "type": "string"},
            {"addr": "A3", "val": "r3", "type": "string"},
            {"addr": "A4", "val": "r4", "type": "string"},
            {"addr": "A5", "val": "r5", "type": "string"},
        ]}]}
        spec = fill_data_into_spec(skeleton, data)

        summary = build_partial_summary(spec, 2, 4)
        assert "A2" in summary
        assert "A3" in summary
        assert "A4" in summary
        assert "A1" not in summary
        assert "A5" not in summary
        assert "3 个单元格" in summary

    def test_chunked_phase4_prompt_contains_range(self):
        from excelmanus.pipeline.phases import build_phase4_chunked_prompt

        prompt = build_phase4_chunked_prompt("- S1: summary", 51, 100)
        assert "51" in prompt
        assert "100" in prompt
        assert "校验" in prompt


class TestBuildFullSummarySampleCells:
    """验证 build_full_summary 对大表格的采样不会 NameError。"""

    def test_large_table_no_name_error(self):
        from excelmanus.pipeline.phases import build_full_summary, build_skeleton_spec, fill_data_into_spec

        skeleton = build_skeleton_spec({
            "tables": [{"name": "S1", "dimensions": {"rows": 100, "cols": 1}, "merges": []}]
        }, _PROVENANCE)
        cells = [{"addr": f"A{i}", "val": i, "type": "number"} for i in range(1, 101)]
        data = {"tables": [{"name": "S1", "cells": cells}]}
        spec = fill_data_into_spec(skeleton, data)

        # 此前会 NameError: name 'sample_cells' is not defined
        summary = build_full_summary(spec, head_cells=10, tail_cells=10)
        assert "中间区域采样" in summary
        assert "统计" in summary
