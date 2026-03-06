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


class TestVisionProbeKeywordCrossValidation:
    """probe 与关键词推断交叉验证：probe=False 不覆盖已知视觉模型的关键词推断。"""

    def _make_caps(self, *, vision: bool | None) -> object:
        """构造 fake ModelCapabilities。"""
        from types import SimpleNamespace
        return SimpleNamespace(supports_vision=vision)

    def test_probe_false_known_vision_model_trusts_keyword(self) -> None:
        """probe=False + 已知视觉模型（gpt-5-codex）→ 信任关键词，返回 True。"""
        config = _make_config(model="gpt-5.2-codex", main_model_vision="auto")
        fake_caps = self._make_caps(vision=False)
        with patch("excelmanus.model_probe.load_capabilities", return_value=fake_caps):
            from excelmanus.database import Database
            result = AgentEngine._infer_vision_capable(config, db=Database.__new__(Database))
        assert result is True

    def test_probe_false_unknown_model_trusts_probe(self) -> None:
        """probe=False + 非视觉模型 → 信任 probe，返回 False。"""
        config = _make_config(model="some-unknown-text-model", main_model_vision="auto")
        fake_caps = self._make_caps(vision=False)
        with patch("excelmanus.model_probe.load_capabilities", return_value=fake_caps):
            from excelmanus.database import Database
            result = AgentEngine._infer_vision_capable(config, db=Database.__new__(Database))
        assert result is False

    def test_probe_true_always_trusted(self) -> None:
        """probe=True → 始终信任，即使关键词不匹配。"""
        config = _make_config(model="some-unknown-model", main_model_vision="auto")
        fake_caps = self._make_caps(vision=True)
        with patch("excelmanus.model_probe.load_capabilities", return_value=fake_caps):
            from excelmanus.database import Database
            result = AgentEngine._infer_vision_capable(config, db=Database.__new__(Database))
        assert result is True

    def test_probe_none_falls_through_to_keyword(self) -> None:
        """probe=None（无缓存）→ 回退到关键词推断。"""
        config = _make_config(model="gpt-5.2-codex", main_model_vision="auto")
        with patch("excelmanus.model_probe.load_capabilities", return_value=None):
            from excelmanus.database import Database
            result = AgentEngine._infer_vision_capable(config, db=Database.__new__(Database))
        assert result is True  # gpt-5 匹配关键词

    def test_codex_model_vision_capable(self) -> None:
        """Codex 模型（gpt-5.1-codex-mini）无 probe 缓存时关键词推断为 True。"""
        config = _make_config(model="gpt-5.1-codex-mini", main_model_vision="auto")
        result = AgentEngine._infer_vision_capable(config, db=None)
        assert result is True

    def test_manual_override_false_wins_over_keyword(self) -> None:
        """手动 main_model_vision=false → 即使模型名匹配也返回 False。"""
        config = _make_config(model="gpt-5.2-codex", main_model_vision="false")
        result = AgentEngine._infer_vision_capable(config, db=None)
        assert result is False


class TestVisionProbeErrorClassification:
    """probe_vision 错误分类：精确区分视觉不支持 vs 无关 API 错误。"""

    def test_vision_unsupported_error_image_not_supported(self) -> None:
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("image input is not supported for this model") is True

    def test_vision_unsupported_error_vision_not_available(self) -> None:
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("vision is not available") is True

    def test_vision_unsupported_error_multimodal_unsupported(self) -> None:
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("multimodal content unsupported") is True

    def test_vision_unsupported_error_store_param(self) -> None:
        """store 参数不支持 → 不应被判为视觉不支持。"""
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("store is not a supported parameter") is False

    def test_vision_unsupported_error_generic_unsupported(self) -> None:
        """通用 unsupported 不含 image/vision → 不应被判为视觉不支持。"""
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("unsupported parameter: max_output_tokens") is False

    def test_vision_unsupported_error_image_url_in_context(self) -> None:
        """错误消息提及 image_url 但不表示不支持 → False。"""
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error("failed to decode image_url field") is False

    def test_param_unsupported_no_longer_has_image_url(self) -> None:
        """_is_param_unsupported_error 不再包含 image_url 关键词。"""
        from excelmanus.model_probe import _is_param_unsupported_error
        # 仅含 image_url 但不含其他关键词 → 不触发
        assert _is_param_unsupported_error("invalid image_url format") is False

    @pytest.mark.asyncio
    async def test_probe_vision_responses_api_401_returns_none(self) -> None:
        """ResponsesAPIError(401) → None（认证错误，不标记视觉不支持）。"""
        from excelmanus.model_probe import probe_vision
        from excelmanus.providers.openai_responses import ResponsesAPIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ResponsesAPIError(401, "Unauthorized")
        )
        result, _ = await probe_vision(mock_client, "gpt-5.2-codex")
        assert result is None

    @pytest.mark.asyncio
    async def test_probe_vision_responses_api_400_store_returns_none(self) -> None:
        """ResponsesAPIError(400) + store 参数错误 → None（非视觉拒绝）。"""
        from excelmanus.model_probe import probe_vision
        from excelmanus.providers.openai_responses import ResponsesAPIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ResponsesAPIError(400, "store is not a supported parameter")
        )
        result, _ = await probe_vision(mock_client, "gpt-5.2-codex")
        assert result is None

    @pytest.mark.asyncio
    async def test_probe_vision_responses_api_400_image_rejected(self) -> None:
        """ResponsesAPIError(400) + 明确拒绝 image → False。"""
        from excelmanus.model_probe import probe_vision
        from excelmanus.providers.openai_responses import ResponsesAPIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ResponsesAPIError(400, "image input is not supported")
        )
        result, _ = await probe_vision(mock_client, "test-model")
        assert result is False

    @pytest.mark.asyncio
    async def test_probe_vision_responses_api_429_returns_none(self) -> None:
        """ResponsesAPIError(429) → None（限流，不标记视觉不支持）。"""
        from excelmanus.model_probe import probe_vision
        from excelmanus.providers.openai_responses import ResponsesAPIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ResponsesAPIError(429, "Rate limit exceeded")
        )
        result, _ = await probe_vision(mock_client, "gpt-5.2-codex")
        assert result is None

    @pytest.mark.asyncio
    async def test_probe_vision_responses_api_500_returns_none(self) -> None:
        """ResponsesAPIError(500) → None（服务端错误）。"""
        from excelmanus.model_probe import probe_vision
        from excelmanus.providers.openai_responses import ResponsesAPIError

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=ResponsesAPIError(500, "Internal server error")
        )
        result, _ = await probe_vision(mock_client, "gpt-5.2-codex")
        assert result is None
