"""片 C：create_pending 前 shadow。日志有，审批结果不变。"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.engine_core.tool_handlers import CodePolicyHandler, HighRiskApprovalHandler
from excelmanus.security.code_policy import CodeRiskTier
from excelmanus.system_one.host import maybe_shadow_approval
from excelmanus.system_one.types import Decision


def _ask_engine(*, jev_enabled: str = "shadow"):
    engine = SimpleNamespace(
        config=SimpleNamespace(
            jev_enabled=jev_enabled,
            jev_exposure="off",
            jev_mode_hint=False,
            jev_observation="off",
            jev_ui_hint=False,
            jev_model="jev-1.13.0",
            typesafe_api_key="vck_test",
            jev_timeout_seconds=1.5,
            code_policy_enabled=True,
            code_policy_green_auto_approve=True,
            code_policy_yellow_auto_approve=False,
            code_policy_extra_safe_modules=(),
            code_policy_extra_blocked_modules=(),
            workspace_root="/tmp/ws",
        ),
        _subagent_config=None,
        _is_host_session=True,
        _full_access_enabled=False,
        _current_chat_mode="write",
        _memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "删掉垃圾文件"}]),
        approval=MagicMock(),
        registry=SimpleNamespace(get_tool=lambda _name: None),
        record_write_action=MagicMock(),
        _state=None,
    )
    pending = SimpleNamespace(approval_id="ap-1")
    engine.approval.create_pending.return_value = pending
    engine.approval.is_mcp_tool.return_value = False
    engine.approval.new_approval_id.return_value = "ap-exec"
    engine.approval.utc_now.return_value = "2026-01-01T00:00:00Z"
    engine.approval.is_undoable_tool.return_value = True
    engine.emit_pending_approval_event = MagicMock()
    engine.format_pending_prompt = MagicMock(return_value="pending-prompt")
    return engine, pending


@pytest.mark.asyncio
async def test_high_risk_shadow_logs_but_still_creates_pending(caplog: pytest.LogCaptureFixture) -> None:
    engine, pending = _ask_engine(jev_enabled="shadow")
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    # 不能依赖真实网络失败：连得通但慢时会走 evaluate_for_host 的静默超时路径，
    # 没有决策日志。直接让 evaluate 抛错，断言 fail-closed 与日志记录。
    with patch("excelmanus.system_one.evaluate", AsyncMock(side_effect=RuntimeError("boom"))):
        with caplog.at_level(logging.INFO, logger="excelmanus.system_one"):
            outcome = await handler.handle(
                "delete_file",
                "call-1",
                {"file_path": "trash.xlsx"},
                tool_scope=["delete_file"],
            )
    # 二态契约：决策日志照常记录（gate=enforce），评估不可用时 fail-closed 走原审批
    assert "jev decision pack=approval.tool_call" in caplog.text
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_high_risk_off_skips_jev_and_still_asks() -> None:
    engine, pending = _ask_engine(jev_enabled="off")
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        outcome = await handler.handle("delete_file", "call-1", {"file_path": "a.xlsx"})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_fullaccess_never_skips_jev() -> None:
    engine, _pending = _ask_engine(jev_enabled="shadow")
    engine._full_access_enabled = True
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    engine.execute_tool_with_audit = AsyncMock(return_value=("ok", None))
    dispatcher = MagicMock()
    dispatcher._coerce_tool_result.return_value = SimpleNamespace(
        model_text="ok",
        success=True,
        error=None,
        with_model_text=lambda text: SimpleNamespace(model_text=text, success=True, error=None),
    )
    handler._dispatcher = dispatcher
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await handler.handle("delete_file", "call-1", {"file_path": "a.xlsx"})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_known_dangerous_skips_evaluate_and_denies() -> None:
    # 二态契约：enforce 下已知危险形不走 evaluate，直接确定性 DENY。
    engine, _pending = _ask_engine(jev_enabled="enforce")
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        with patch("excelmanus.system_one.host.record_jev_decision") as logged:
            outcome = await handler.handle(
                "run_shell",
                "call-1",
                {"command": "rm -rf ."},
            )
            mocked.assert_not_called()
            logged.assert_called_once()
            decision = logged.call_args.kwargs["decision"]
            assert decision.kind == "deny"
            assert decision.applied is True
    engine.approval.create_pending.assert_not_called()
    assert outcome.pending_approval is False
    assert outcome.success is False


@pytest.mark.asyncio
async def test_readonly_and_green_skip_jev() -> None:
    engine, _pending = _ask_engine(jev_enabled="shadow")
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_shadow_approval(engine, tool_name="observe_spreadsheet", arguments={})
        await maybe_shadow_approval(
            engine,
            tool_name="run_code",
            arguments={"code": "print(1)"},
            code_tier="GREEN",
        )
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_approval_unavailable_logs_ask_keeps_pending(caplog: pytest.LogCaptureFixture) -> None:
    engine, pending = _ask_engine(jev_enabled="shadow")
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("gateway down")

    with patch("excelmanus.system_one.evaluate", _boom):
        with caplog.at_level(logging.INFO, logger="excelmanus.system_one"):
            outcome = await handler.handle("delete_file", "call-1", {"file_path": "a.xlsx"})
    assert "jev decision pack=approval.tool_call" in caplog.text
    assert "unavailable:RuntimeError" in caplog.text
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_code_policy_green_does_not_call_jev() -> None:
    engine, _pending = _ask_engine(jev_enabled="shadow")
    dispatcher = MagicMock()
    handler = CodePolicyHandler(engine=engine, dispatcher=dispatcher)
    engine.execute_tool_with_audit = AsyncMock(return_value=('{"ok": true}', MagicMock(changes=[])))
    dispatcher._snapshot_uploads_dir.return_value = {}
    dispatcher._diff_uploads_snapshots.return_value = []
    dispatcher._record_files_from_run_code.return_value = None
    dispatcher._extract_run_code_write_summary.return_value = ""
    mock_analysis = MagicMock()
    mock_analysis.tier = CodeRiskTier.GREEN
    mock_analysis.capabilities = set()
    mock_analysis.details = []
    with patch("excelmanus.security.code_policy.CodePolicyEngine.analyze", return_value=mock_analysis):
        with patch("excelmanus.security.code_policy.allows_auto_run", return_value=True):
            with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
                await handler.handle("run_code", "c1", {"code": "print(1)"})
                mocked.assert_not_called()
    engine.approval.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_code_policy_red_shadows_then_pending() -> None:
    engine, pending = _ask_engine(jev_enabled="shadow")
    handler = CodePolicyHandler(engine=engine, dispatcher=MagicMock())
    mock_analysis = MagicMock()
    mock_analysis.tier = CodeRiskTier.RED
    mock_analysis.capabilities = {"subprocess"}
    mock_analysis.details = ["os.system"]
    shadows: list[str] = []

    async def _fake_eval(pack_id, state, *, config=None):
        shadows.append(pack_id)
        return Decision.ask("uncertain")

    with patch("excelmanus.security.code_policy.CodePolicyEngine.analyze", return_value=mock_analysis):
        with patch("excelmanus.security.code_policy.allows_auto_run", return_value=False):
            with patch("excelmanus.security.code_policy.strip_exit_calls", return_value=None):
                with patch("excelmanus.system_one.evaluate", _fake_eval):
                    outcome = await handler.handle("run_code", "c1", {"code": "os.system('x')"})
    assert shadows == ["approval.tool_call"]
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_child_session_skips_approval_shadow() -> None:
    engine, _pending = _ask_engine(jev_enabled="shadow")
    engine._subagent_config = object()
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    engine.execute_tool_with_audit = AsyncMock(return_value=("ok", None))
    dispatcher = MagicMock()
    dispatcher._coerce_tool_result.return_value = SimpleNamespace(
        model_text="ok",
        success=True,
        error=None,
        with_model_text=lambda text: SimpleNamespace(model_text=text, success=True, error=None),
    )
    handler._dispatcher = dispatcher
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await handler.handle("delete_file", "call-1", {"file_path": "a.xlsx"})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_deny_decision_does_not_change_pending() -> None:
    engine, pending = _ask_engine(jev_enabled="shadow")
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())

    async def _deny(*_args, **_kwargs):
        return Decision(kind="deny", reason="destructive_or_exfil", applied=False)

    with patch("excelmanus.system_one.evaluate", _deny):
        outcome = await handler.handle("delete_file", "call-1", {"file_path": "a.xlsx"})
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id
