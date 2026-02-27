"""测试：上传图片时视觉能力前置检查 —— 主模型不支持视觉且无 VLM 时直接拒绝。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.tools import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "workspace_root": str(Path(__file__).resolve().parent),
        "backup_enabled": False,
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


_FAKE_IMAGE = [{"data": "iVBORw0KGgo=", "media_type": "image/png", "detail": "auto"}]


class TestVisionRejectGuard:
    """视觉能力前置检查：无视觉时拒绝图片请求。"""

    @pytest.mark.asyncio
    async def test_reject_image_when_no_vision_and_no_vlm(self) -> None:
        """主模型无视觉 + 无 VLM → 直接拒绝。"""
        config = _make_config(main_model_vision="false", vlm_enhance=False)
        engine = AgentEngine(config, ToolRegistry())
        assert not engine._is_vision_capable
        assert not engine._vlm_enhance_available

        result = await engine.chat("请分析这张图片", images=_FAKE_IMAGE)
        assert isinstance(result, ChatResult)
        assert "不支持图片识别" in result.reply
        assert "VLM" in result.reply

    @pytest.mark.asyncio
    async def test_reject_image_when_auto_non_vision_model(self) -> None:
        """auto 推断为非视觉模型 + 无 VLM → 拒绝。"""
        config = _make_config(model="o3-mini", main_model_vision="auto", vlm_enhance=False)
        engine = AgentEngine(config, ToolRegistry())
        assert not engine._is_vision_capable

        result = await engine.chat("看看这个", images=_FAKE_IMAGE)
        assert "不支持图片识别" in result.reply

    @pytest.mark.asyncio
    async def test_allow_image_when_main_model_has_vision(self) -> None:
        """主模型有视觉能力 → 不拒绝（会进入后续路由）。"""
        config = _make_config(main_model_vision="true", vlm_enhance=False)
        engine = AgentEngine(config, ToolRegistry())
        assert engine._is_vision_capable

        # patch _tool_calling_loop 避免实际 LLM 调用，只验证不命中拒绝分支
        with patch.object(engine, "_tool_calling_loop", new_callable=AsyncMock, return_value=ChatResult(reply="ok")):
            result = await engine.chat("分析图片", images=_FAKE_IMAGE)
        assert "不支持图片识别" not in result.reply

    @pytest.mark.asyncio
    async def test_allow_image_when_vlm_available(self) -> None:
        """主模型无视觉但 VLM 可用 → 不拒绝。"""
        config = _make_config(
            main_model_vision="false",
            vlm_enhance=True,
            vlm_base_url="https://vlm.example.com/v1",
            vlm_model="qwen-vl-plus",
        )
        engine = AgentEngine(config, ToolRegistry())
        assert not engine._is_vision_capable
        assert engine._vlm_enhance_available

        with patch.object(engine, "_tool_calling_loop", new_callable=AsyncMock, return_value=ChatResult(reply="ok")):
            result = await engine.chat("分析图片", images=_FAKE_IMAGE)
        assert "不支持图片识别" not in result.reply

    @pytest.mark.asyncio
    async def test_no_reject_when_no_images(self) -> None:
        """无图片附件时不触发拒绝（即使模型无视觉）。"""
        config = _make_config(main_model_vision="false", vlm_enhance=False)
        engine = AgentEngine(config, ToolRegistry())

        with patch.object(engine, "_tool_calling_loop", new_callable=AsyncMock, return_value=ChatResult(reply="你好")):
            result = await engine.chat("你好")
        assert "不支持图片识别" not in result.reply
