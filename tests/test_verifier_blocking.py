"""选择性 blocking verifier 测试。

覆盖场景：
- advisory 模式下 fail 不阻断 finish
- blocking 模式下 fail+high 阻断 finish
- blocking 模式下第 2 次 finish 跳过 blocking（降为 advisory）
- verifier 执行失败 → fail-open
- pass → finish 正常通过
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.subagent.models import SubagentResult


# ── 辅助工厂 ──────────────────────────────────────────────

def _make_engine(
    *,
    subagent_enabled: bool = True,
    task_tags: tuple[str, ...] = (),
    verification_attempt_count: int = 0,
    has_write: bool = True,
    guard_mode: str = "off",
    write_hint: str = "may_write",
):
    """构建最小化 mock engine 供 FinishTaskHandler 使用。"""
    e = MagicMock()
    e._subagent_enabled = subagent_enabled
    e._has_write_tool_call = has_write
    e._current_write_hint = write_hint
    e._finish_task_warned = False
    e._verification_attempt_count = verification_attempt_count
    e._config = SimpleNamespace(guard_mode=guard_mode)

    # _last_route_result 携带 task_tags
    e._last_route_result = SimpleNamespace(task_tags=task_tags)

    # _subagent_registry.get("verifier") 返回非 None 表示 verifier 可用
    e._subagent_registry = MagicMock()
    e._subagent_registry.get.return_value = MagicMock()

    return e


def _make_verifier_result(
    *,
    success: bool = True,
    verdict: str = "pass",
    confidence: str = "high",
    issues: list[str] | None = None,
    checks: list[str] | None = None,
) -> SubagentResult:
    """构造 verifier 子代理的 mock 结果。"""
    import json
    summary_dict: dict = {"verdict": verdict, "confidence": confidence}
    if issues:
        summary_dict["issues"] = issues
    if checks:
        summary_dict["checks"] = checks
    return SubagentResult(
        success=success,
        summary=json.dumps(summary_dict) if success else "执行失败",
        subagent_name="verifier",
        permission_mode="readOnly",
        conversation_id="v1",
    )


# ── FinishTaskHandler 单元测试 ─────────────────────────────

class TestFinishTaskVerifierIntegration:
    """FinishTaskHandler 与 verifier 的集成行为。"""

    @pytest.fixture
    def _handler(self):
        from excelmanus.engine_core.tool_handlers import FinishTaskHandler
        dispatcher = MagicMock()
        dispatcher._emit_files_changed_from_report = MagicMock()
        handler = FinishTaskHandler(engine=MagicMock(), dispatcher=dispatcher)
        return handler

    @pytest.mark.asyncio
    async def test_advisory_fail_does_not_block(self, _handler):
        """无 blocking tags 时，verifier fail 仅追加文本，finish_accepted 仍为 True。"""
        engine = _make_engine(task_tags=("simple",))
        _handler._engine = engine

        vr = _make_verifier_result(verdict="fail", confidence="high", issues=["数据不完整"])
        engine._run_finish_verifier_advisory = AsyncMock(
            return_value="\n\n⚠️ **验证发现问题**（advisory）：数据不完整（任务仍标记完成，建议复查）"
        )

        from excelmanus.engine_core.tool_dispatcher import _ToolExecOutcome, _render_finish_task_report
        result = await _handler.handle(
            "finish_task", "tc1", {"summary": "done"},
            tool_scope=[], on_event=None, iteration=1, route_result=None,
        )

        assert result.finish_accepted is True
        assert "advisory" in result.result_str

    @pytest.mark.asyncio
    async def test_blocking_fail_high_blocks_finish(self, _handler):
        """cross_sheet tag + fail + high confidence → finish_accepted=False。"""
        engine = _make_engine(task_tags=("cross_sheet",), verification_attempt_count=0)
        _handler._engine = engine

        engine._run_finish_verifier_advisory = AsyncMock(
            return_value="BLOCK:⚠️ 验证未通过：输出文件不存在。请修正后再次调用 finish_task。"
        )

        result = await _handler.handle(
            "finish_task", "tc1", {"summary": "done"},
            tool_scope=[], on_event=None, iteration=1, route_result=None,
        )

        assert result.finish_accepted is False
        assert "验证未通过" in result.result_str
        # 确认 blocking=True 被传入
        engine._run_finish_verifier_advisory.assert_called_once()
        call_kwargs = engine._run_finish_verifier_advisory.call_args.kwargs
        assert call_kwargs["blocking"] is True

    @pytest.mark.asyncio
    async def test_second_finish_skips_blocking(self, _handler):
        """verification_attempt_count >= 1 时降为 advisory，不再 blocking。"""
        engine = _make_engine(
            task_tags=("cross_sheet",),
            verification_attempt_count=1,
        )
        _handler._engine = engine

        engine._run_finish_verifier_advisory = AsyncMock(
            return_value="\n\n⚠️ **验证发现问题**（advisory）：数据不完整（任务仍标记完成，建议复查）"
        )

        result = await _handler.handle(
            "finish_task", "tc1", {"summary": "done"},
            tool_scope=[], on_event=None, iteration=1, route_result=None,
        )

        assert result.finish_accepted is True
        # 确认 blocking=False
        call_kwargs = engine._run_finish_verifier_advisory.call_args.kwargs
        assert call_kwargs["blocking"] is False

    @pytest.mark.asyncio
    async def test_verifier_fail_open_on_error(self, _handler):
        """verifier 执行失败（返回 None）→ fail-open，finish_accepted 不受影响。"""
        engine = _make_engine(task_tags=("large_data",), verification_attempt_count=0)
        _handler._engine = engine

        engine._run_finish_verifier_advisory = AsyncMock(return_value=None)

        result = await _handler.handle(
            "finish_task", "tc1", {"summary": "done"},
            tool_scope=[], on_event=None, iteration=1, route_result=None,
        )

        assert result.finish_accepted is True

    @pytest.mark.asyncio
    async def test_verifier_pass_allows_finish(self, _handler):
        """verifier verdict=pass → finish 正常通过，追加通过文本。"""
        engine = _make_engine(task_tags=("cross_sheet",), verification_attempt_count=0)
        _handler._engine = engine

        engine._run_finish_verifier_advisory = AsyncMock(
            return_value="\n\n✅ **验证通过**（文件存在、数据行数正确）"
        )

        result = await _handler.handle(
            "finish_task", "tc1", {"summary": "done"},
            tool_scope=[], on_event=None, iteration=1, route_result=None,
        )

        assert result.finish_accepted is True
        assert "验证通过" in result.result_str


class TestVerifierAdvisoryBlockingMode:
    """_run_finish_verifier_advisory blocking 参数行为。"""

    @pytest.mark.asyncio
    async def test_blocking_fail_high_returns_block_prefix(self):
        """blocking=True + fail + high → 返回 BLOCK: 前缀。"""
        import json
        engine = MagicMock()
        engine._subagent_enabled = True
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = MagicMock()
        engine._build_parent_context_summary.return_value = ""
        engine._context_builder = MagicMock()
        engine._context_builder._build_task_list_status_notice.return_value = ""

        vr = _make_verifier_result(
            verdict="fail", confidence="high", issues=["输出文件不存在"],
        )
        engine.run_subagent = AsyncMock(return_value=vr)

        # 直接调用原始方法（需要绑定 self）
        from excelmanus.engine import AgentEngine
        result = await AgentEngine._run_finish_verifier_advisory(
            engine,
            report={"operations": "写入数据"},
            summary="",
            on_event=None,
            blocking=True,
        )

        assert result is not None
        assert result.startswith("BLOCK:")
        assert "输出文件不存在" in result

    @pytest.mark.asyncio
    async def test_blocking_fail_medium_stays_advisory(self):
        """blocking=True + fail + medium → 不阻塞，返回 advisory 文本。"""
        engine = MagicMock()
        engine._subagent_enabled = True
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = MagicMock()
        engine._build_parent_context_summary.return_value = ""
        engine._context_builder = MagicMock()
        engine._context_builder._build_task_list_status_notice.return_value = ""

        vr = _make_verifier_result(
            verdict="fail", confidence="medium", issues=["可能缺少数据"],
        )
        engine.run_subagent = AsyncMock(return_value=vr)

        from excelmanus.engine import AgentEngine
        result = await AgentEngine._run_finish_verifier_advisory(
            engine,
            report={"operations": "写入数据"},
            summary="",
            on_event=None,
            blocking=True,
        )

        assert result is not None
        assert not result.startswith("BLOCK:")
        assert "advisory" in result

    @pytest.mark.asyncio
    async def test_advisory_mode_never_blocks(self):
        """blocking=False + fail + high → 不阻塞。"""
        engine = MagicMock()
        engine._subagent_enabled = True
        engine._subagent_registry = MagicMock()
        engine._subagent_registry.get.return_value = MagicMock()
        engine._build_parent_context_summary.return_value = ""
        engine._context_builder = MagicMock()
        engine._context_builder._build_task_list_status_notice.return_value = ""

        vr = _make_verifier_result(
            verdict="fail", confidence="high", issues=["数据不完整"],
        )
        engine.run_subagent = AsyncMock(return_value=vr)

        from excelmanus.engine import AgentEngine
        result = await AgentEngine._run_finish_verifier_advisory(
            engine,
            report={"operations": "写入数据"},
            summary="",
            on_event=None,
            blocking=False,
        )

        assert result is not None
        assert not result.startswith("BLOCK:")
        assert "advisory" in result
