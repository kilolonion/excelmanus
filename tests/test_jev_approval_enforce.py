"""片 E：inert 审批 enforce。未签字与接线前相同；禁止打网。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.engine_core.error_payload import PRE_EXECUTE_DENIED
from excelmanus.engine_core.tool_handlers import CodePolicyHandler, HighRiskApprovalHandler
from excelmanus.security.code_policy import CodeRiskTier
from excelmanus.system_one.host import maybe_shadow_approval
from excelmanus.system_one.types import Decision


def _ask_engine(*, jev_enabled: str = "off", jev_calibrated: bool = False):
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
            jev_calibrated=jev_calibrated,
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
    engine.execute_tool_with_audit = AsyncMock(return_value=("ok", None))
    return engine, pending


def _sign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "excelmanus.system_one.calibration.SIGNED_ENFORCE_PACKS",
        frozenset({"approval.tool_call"}),
    )


def _deny() -> Decision:
    return Decision(kind="deny", reason="destructive_or_exfil", applied=True)


def _auto() -> Decision:
    return Decision(kind="auto", reason="allow_high_confidence", applied=True)


@pytest.mark.asyncio
async def test_e_default_still_pending() -> None:
    engine, pending = _ask_engine()
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        outcome = await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_called_once()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id
    assert outcome.success is True


@pytest.mark.asyncio
async def test_e_unsigned_enforce_deny_still_pending() -> None:
    engine, pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_deny())):
        outcome = await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
    engine.approval.create_pending.assert_called_once()
    engine.execute_tool_with_audit.assert_not_called()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_e_signed_high_conf_deny_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_deny())):
        outcome = await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
    engine.approval.create_pending.assert_not_called()
    engine.execute_tool_with_audit.assert_not_called()
    assert outcome.pending_approval is False
    assert outcome.success is False
    assert outcome.error == PRE_EXECUTE_DENIED
    assert "destructive_or_exfil" in outcome.result_str


@pytest.mark.asyncio
async def test_e_signed_known_dangerous_denies_without_evaluate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        outcome = await handler.handle("run_shell", "c1", {"command": "rm -rf ."})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_not_called()
    assert outcome.success is False
    assert outcome.error == PRE_EXECUTE_DENIED
    assert "known_dangerous" in outcome.result_str


@pytest.mark.asyncio
async def test_e_signed_unavailable_is_not_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=MagicMock())

    async def _boom(*_a, **_k):
        raise RuntimeError("gateway down")

    with patch("excelmanus.system_one.evaluate", _boom):
        outcome = await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
    engine.approval.create_pending.assert_called_once()
    engine.execute_tool_with_audit.assert_not_called()
    assert outcome.pending_approval is True
    assert outcome.approval_id == pending.approval_id


@pytest.mark.asyncio
async def test_e_signed_auto_uses_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    dispatcher = MagicMock()
    dispatcher._coerce_tool_result.return_value = SimpleNamespace(
        model_text="ok",
        success=True,
        error=None,
        with_model_text=lambda text: SimpleNamespace(model_text=text, success=True, error=None),
    )
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=dispatcher)
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_auto())):
        outcome = await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
    engine.approval.create_pending.assert_not_called()
    engine.execute_tool_with_audit.assert_awaited_once()
    assert outcome.pending_approval is False
    assert outcome.success is True


@pytest.mark.asyncio
async def test_e_child_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    engine._subagent_config = object()
    dispatcher = MagicMock()
    dispatcher._coerce_tool_result.return_value = SimpleNamespace(
        model_text="ok",
        success=True,
        error=None,
        with_model_text=lambda text: SimpleNamespace(model_text=text, success=True, error=None),
    )
    handler = HighRiskApprovalHandler(engine=engine, dispatcher=dispatcher)
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await handler.handle("delete_file", "c1", {"file_path": "a.xlsx"})
        mocked.assert_not_called()
    engine.approval.create_pending.assert_not_called()


@pytest.mark.asyncio
async def test_e_tier_b_spreadsheet_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        assert await maybe_shadow_approval(
            engine, tool_name="edit_spreadsheet", arguments={"file_path": "a.xlsx"},
        ) is None
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_e_green_and_never_skip_jev(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_shadow_approval(
            engine, tool_name="run_code", arguments={"code": "print(1)"}, code_tier="GREEN",
        )
        engine._full_access_enabled = True
        await maybe_shadow_approval(engine, tool_name="delete_file", arguments={"file_path": "a.xlsx"})
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_e_code_red_signed_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch)
    engine, _pending = _ask_engine(jev_enabled="enforce", jev_calibrated=True)
    handler = CodePolicyHandler(engine=engine, dispatcher=MagicMock())
    mock_analysis = MagicMock()
    mock_analysis.tier = CodeRiskTier.RED
    mock_analysis.capabilities = {"subprocess"}
    mock_analysis.details = ["os.system"]
    with patch("excelmanus.security.code_policy.CodePolicyEngine.analyze", return_value=mock_analysis):
        with patch("excelmanus.security.code_policy.allows_auto_run", return_value=False):
            with patch("excelmanus.security.code_policy.strip_exit_calls", return_value=None):
                with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=_deny())):
                    outcome = await handler.handle("run_code", "c1", {"code": "os.system('x')"})
    engine.approval.create_pending.assert_not_called()
    assert outcome.success is False
    assert outcome.error == PRE_EXECUTE_DENIED
