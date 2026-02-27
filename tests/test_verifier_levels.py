"""分级验证强度 + fix-verify 循环测试。

覆盖：
- _resolve_verifier_level: skip / advisory / blocking 分级
- skip 级别: read_only + 无写入 → 跳过 verifier
- formula / multi_file tag → blocking
- simple tag → advisory
- 无 tag → default advisory
- fix-verify 循环: MAX_BLOCKING_ATTEMPTS=2, 第 3 次降为 advisory
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from excelmanus.engine_core.tool_handlers import FinishTaskHandler
from excelmanus.subagent.models import SubagentResult


def _make_handler() -> FinishTaskHandler:
    dispatcher = MagicMock()
    dispatcher._emit_files_changed_from_report = MagicMock()
    return FinishTaskHandler(engine=MagicMock(), dispatcher=dispatcher)


class TestResolveVerifierLevel:
    """_resolve_verifier_level 单元测试。"""

    def _call(self, task_tags, has_write=True, write_hint="may_write"):
        handler = _make_handler()
        return handler._resolve_verifier_level(task_tags, has_write, write_hint)

    def test_read_only_no_write_skips(self):
        assert self._call((), has_write=False, write_hint="read_only") == "skip"

    def test_read_only_with_write_does_not_skip(self):
        assert self._call((), has_write=True, write_hint="read_only") != "skip"

    def test_no_tags_default_advisory(self):
        assert self._call(()) == "advisory"

    def test_simple_tag_advisory(self):
        assert self._call(("simple",)) == "advisory"

    def test_cross_sheet_blocking(self):
        assert self._call(("cross_sheet",)) == "blocking"

    def test_large_data_blocking(self):
        assert self._call(("large_data",)) == "blocking"

    def test_formula_blocking(self):
        assert self._call(("formula",)) == "blocking"

    def test_multi_file_blocking(self):
        assert self._call(("multi_file",)) == "blocking"

    def test_mixed_tags_highest_wins(self):
        """simple + cross_sheet → blocking (最高级别)。"""
        assert self._call(("simple", "cross_sheet")) == "blocking"

    def test_unknown_tags_default(self):
        assert self._call(("some_unknown_tag",)) == "advisory"

    def test_unknown_write_hint_no_skip(self):
        assert self._call((), has_write=False, write_hint="unknown") != "skip"


class TestSkipVerifier:
    """skip 级别集成测试。"""

    @pytest.mark.asyncio
    async def test_skip_does_not_call_verifier(self):
        handler = _make_handler()
        engine = MagicMock()
        engine._has_write_tool_call = False
        engine._current_write_hint = "read_only"
        engine._last_route_result = SimpleNamespace(task_tags=())
        engine._verification_attempt_count = 0
        engine._run_finish_verifier_advisory = AsyncMock()

        result = await handler._run_verifier_if_needed(
            engine, report=None, summary="done", on_event=None,
        )

        assert result is None
        engine._run_finish_verifier_advisory.assert_not_called()


class TestFixVerifyLoop:
    """fix-verify 循环: blocking 最多 MAX_BLOCKING_ATTEMPTS 次后降为 advisory。"""

    @pytest.mark.asyncio
    async def test_attempt_0_is_blocking(self):
        handler = _make_handler()
        engine = MagicMock()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"
        engine._last_route_result = SimpleNamespace(task_tags=("cross_sheet",))
        engine._verification_attempt_count = 0
        engine._run_finish_verifier_advisory = AsyncMock(return_value="BLOCK:问题")

        await handler._run_verifier_if_needed(
            engine, report=None, summary="done", on_event=None,
        )

        call_kwargs = engine._run_finish_verifier_advisory.call_args.kwargs
        assert call_kwargs["blocking"] is True
        assert engine._verification_attempt_count == 1

    @pytest.mark.asyncio
    async def test_attempt_1_still_blocking(self):
        handler = _make_handler()
        engine = MagicMock()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"
        engine._last_route_result = SimpleNamespace(task_tags=("large_data",))
        engine._verification_attempt_count = 1
        engine._run_finish_verifier_advisory = AsyncMock(return_value="BLOCK:问题")

        await handler._run_verifier_if_needed(
            engine, report=None, summary="done", on_event=None,
        )

        call_kwargs = engine._run_finish_verifier_advisory.call_args.kwargs
        assert call_kwargs["blocking"] is True
        assert engine._verification_attempt_count == 2

    @pytest.mark.asyncio
    async def test_attempt_2_downgrades_to_advisory(self):
        handler = _make_handler()
        engine = MagicMock()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"
        engine._last_route_result = SimpleNamespace(task_tags=("cross_sheet",))
        engine._verification_attempt_count = 2  # == MAX_BLOCKING_ATTEMPTS
        engine._run_finish_verifier_advisory = AsyncMock(return_value="advisory text")

        await handler._run_verifier_if_needed(
            engine, report=None, summary="done", on_event=None,
        )

        call_kwargs = engine._run_finish_verifier_advisory.call_args.kwargs
        assert call_kwargs["blocking"] is False

    @pytest.mark.asyncio
    async def test_attempt_count_increments_only_in_blocking(self):
        """advisory 模式下不增加 attempt_count。"""
        handler = _make_handler()
        engine = MagicMock()
        engine._has_write_tool_call = True
        engine._current_write_hint = "may_write"
        engine._last_route_result = SimpleNamespace(task_tags=("simple",))
        engine._verification_attempt_count = 0
        engine._run_finish_verifier_advisory = AsyncMock(return_value="advisory")

        await handler._run_verifier_if_needed(
            engine, report=None, summary="done", on_event=None,
        )

        # advisory 模式不递增
        assert engine._verification_attempt_count == 0
