"""测试：上传图片时视觉能力前置检查 —— 当前模型不支持视觉时直接拒绝。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.agent.loop import run_tool_loop
from excelmanus.engine import AgentEngine, ChatResult
from excelmanus.tools import ToolRegistry


def _make_config(**overrides) -> ExcelManusConfig:
    defaults = {
        "api_key": "test-key",
        "base_url": "https://test.example.com/v1",
        "model": "test-model",
        "max_iterations": 20,
        "workspace_root": str(Path(__file__).resolve().parent),
    }
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


_FAKE_IMAGE = [{"attachment_id": "sha256:deadbeef", "media_type": "image/png", "detail": "auto"}]


class TestVisionRejectGuard:
    """视觉能力前置检查：无视觉时拒绝图片请求。"""

    @pytest.mark.asyncio
    async def test_reject_image_when_no_vision(self) -> None:
        """当前模型无视觉 → 直接拒绝。"""
        config = _make_config(main_model_vision="false")
        engine = AgentEngine(config, ToolRegistry())
        assert not engine._is_vision_capable

        result = await engine.followup("请分析这张图片", images=_FAKE_IMAGE)
        assert isinstance(result, ChatResult)
        assert "不支持图片识别" in result.reply

    @pytest.mark.asyncio
    async def test_reject_image_when_auto_non_vision_model(self) -> None:
        """auto 推断为非视觉模型 + 无 VLM → 拒绝。"""
        config = _make_config(model="o3-mini", main_model_vision="auto")
        engine = AgentEngine(config, ToolRegistry())
        assert not engine._is_vision_capable

        result = await engine.followup("看看这个", images=_FAKE_IMAGE)
        assert "不支持图片识别" in result.reply

    @pytest.mark.asyncio
    async def test_allow_image_when_main_model_has_vision(self) -> None:
        """当前模型有视觉能力 → 不拒绝（会进入后续路由）。"""
        config = _make_config(main_model_vision="true")
        engine = AgentEngine(config, ToolRegistry())
        assert engine._is_vision_capable

        # 附件只按 attachment_id 准入；假 ref 避免进 LLM。
        ref = SimpleNamespace(
            attachment_id="sha256:deadbeef",
            to_dict=lambda: {"attachment_id": "sha256:deadbeef", "media_type": "image/png"},
        )
        store = SimpleNamespace(get_ref=lambda _id: ref)
        with (
            patch("excelmanus.attachments.store.get_attachment_store", return_value=store),
            patch("excelmanus.agent.loop.run_tool_loop", new_callable=AsyncMock, return_value=ChatResult(reply="ok")),
        ):
            result = await engine.followup("分析图片", images=_FAKE_IMAGE)
        assert "不支持图片识别" not in result.reply

    @pytest.mark.asyncio
    async def test_no_reject_when_no_images(self) -> None:
        """无图片附件时不触发拒绝（即使模型无视觉）。"""
        config = _make_config(main_model_vision="false")
        engine = AgentEngine(config, ToolRegistry())

        with patch("excelmanus.agent.loop.run_tool_loop", new_callable=AsyncMock, return_value=ChatResult(reply="你好")):
            result = await engine.followup("你好")
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

    def test_vision_unsupported_error_unknown_variant_image_url(self) -> None:
        from excelmanus.model_probe import _is_vision_unsupported_error
        assert _is_vision_unsupported_error(
            "unknown variant `image_url`, expected `text`"
        ) is True

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


class TestFlagshipVisionKeywordInference:
    """2026-09 旗舰 ID 的视觉关键词推断应与厂商实际多模态能力一致。"""

    def _infer(self, model: str) -> bool:
        config = _make_config(model=model, main_model_vision="auto")
        return AgentEngine._infer_vision_capable(config, db=None)

    def test_deepseek_flash_is_native_multimodal(self) -> None:
        assert self._infer("deepseek-flash") is True
        assert self._infer("deepseek-v4-flash") is True
        assert self._infer("deepseek-v4-flash-vision-exp") is True

    def test_deepseek_text_ids_are_not_vision(self) -> None:
        assert self._infer("deepseek-chat") is False
        assert self._infer("deepseek-reasoner") is False
        assert self._infer("deepseek-v3") is False
        assert self._infer("deepseek-v4-pro") is False

    def test_qwen_flagships_are_native_multimodal(self) -> None:
        assert self._infer("qwen3.7-plus") is True
        assert self._infer("qwen3.8-max") is True
        assert self._infer("qwen3.8-flash") is True
        assert self._infer("qwen3.7-flash") is True

    def test_other_current_flagships_keep_vision(self) -> None:
        assert self._infer("gpt-6-astra") is True
        assert self._infer("claude-sonnet-5") is True
        assert self._infer("gemini-3.8-flash") is True
        assert self._infer("glm-5.3") is True
        assert self._infer("glm-5.3-flash") is True
        assert self._infer("kimi-k3") is True
        assert self._infer("MiniMax-M3") is True
        assert self._infer("grok-4.6") is True
        assert self._infer("doubao-seed-2.1-pro") is True
