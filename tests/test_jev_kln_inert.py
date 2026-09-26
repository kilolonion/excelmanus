"""片 K/L/N：inert 接线。默认不发卡、不改偏好、不改 model_text。禁止打网。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.engine_core.interaction_handler import InteractionHandler
from excelmanus.engine_core.meta_tools import MetaToolBuilder
from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta
from excelmanus.plan_mode import apply_chat_mode
from excelmanus.question_flow import QuestionFlowManager
from excelmanus.system_one.host import (
    maybe_enqueue_mode_switch,
    maybe_record_turn_exposure,
    maybe_shape_observation,
)
from excelmanus.system_one.policy import T_BIG, T_TIGHT_CHARS
from excelmanus.system_one.types import Decision
from excelmanus.tools.registry import ToolDef, ToolRegistry


def _config(**overrides: object) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        jev_experimental_enabled=True,
        **overrides,
    )


def _sign(monkeypatch: pytest.MonkeyPatch, *packs: str) -> None:
    monkeypatch.setattr(
        "excelmanus.system_one.calibration.SIGNED_ENFORCE_PACKS",
        frozenset(packs),
    )


def _decision(*, mode_hint: str = "keep", mode_conf: float = 0.9) -> Decision:
    return Decision(
        kind="noop",
        reason="domain:inspect_only",
        extras={
            "profile": "inspect",
            "domain": "inspect_only",
            "domain_confidence": 0.9,
            "mode_hint": mode_hint,
            "mode_hint_confidence": mode_conf,
            "wire_narrow": False,
        },
        applied=False,
    )


def _stub(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "config": _config(),
        "_subagent_config": None,
        "_is_host_session": True,
        "_current_chat_mode": "read",
        "_pending_plan_exit": None,
        "_turn_image_count": 0,
        "_exposure_last_tools": [],
        "_active_skills": [],
        "_turn_exposure": None,
        "_exposure_sticky": None,
        "_tools_cache": None,
        "_question_flow": QuestionFlowManager(),
        "_system_question_actions": {},
        "_interaction_handler": None,
        "_driver": None,
        "_registry": None,
        "_plan_active": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _schema_names(schemas: list[dict]) -> set[str]:
    names: set[str] = set()
    for schema in schemas:
        func = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(func, dict) and func.get("name"):
            names.add(str(func["name"]))
        elif isinstance(schema, dict) and schema.get("name"):
            names.add(str(schema["name"]))
    return names


def test_mode_switch_default_does_not_enqueue() -> None:
    engine = _stub(
        _turn_exposure={
            "mode_hint": "suggest_write",
            "mode_hint_confidence": 0.99,
        },
    )
    maybe_enqueue_mode_switch(engine)
    assert engine._question_flow.has_pending() is False
    assert engine._system_question_actions == {}


@pytest.mark.asyncio
async def test_mode_switch_signed_high_confidence_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "exposure.turn")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_exposure="enforce",
            jev_mode_hint=True,
            jev_calibrated=True,
        ),
        _current_chat_mode="read",
    )
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=replace(
            _decision(mode_hint="suggest_write", mode_conf=0.95), applied=True,
        )),
    ):
        await maybe_record_turn_exposure(engine, "把表里的错改掉")
    assert engine._question_flow.has_pending() is True
    pending = engine._question_flow.current()
    assert pending is not None
    assert "写入" in pending.text
    assert any(
        action.get("type") == "mode_switch_suggestion"
        for action in engine._system_question_actions.values()
    )
    assert engine._current_chat_mode == "read"


@pytest.mark.asyncio
async def test_mode_switch_write_suggest_read_does_not_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "exposure.turn")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_exposure="shadow",
            jev_mode_hint=True,
            jev_calibrated=True,
        ),
        _current_chat_mode="write",
    )
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_decision(mode_hint="suggest_read", mode_conf=0.99)),
    ):
        await maybe_record_turn_exposure(engine, "看看这张表")
    assert engine._question_flow.has_pending() is False


def test_mode_switch_confirm_changes_mode_reject_keeps() -> None:
    engine = _stub(_current_chat_mode="read", _tools_cache=["x"])
    handler = InteractionHandler(engine)
    confirm = SimpleNamespace(selected_options=[{"label": "切换到编辑"}])
    result = handler.handle_mode_switch_answer(parsed=confirm, target="write")
    assert engine._current_chat_mode == "write"
    assert "write" in result.reply or "编辑" in result.reply

    apply_chat_mode(engine, "read", source="request")
    reject = SimpleNamespace(selected_options=[{"label": "保持观察"}])
    kept = handler.handle_mode_switch_answer(parsed=reject, target="write")
    assert engine._current_chat_mode == "read"
    assert "保持" in kept.reply


@pytest.mark.asyncio
async def test_mode_switch_child_does_not_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "exposure.turn")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_exposure="shadow",
            jev_mode_hint=True,
            jev_calibrated=True,
        ),
        _subagent_config=object(),
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_record_turn_exposure(engine, "改一下")
        mocked.assert_not_called()
    assert engine._question_flow.has_pending() is False


@pytest.mark.asyncio
async def test_exposure_retains_direct_and_programmatic_core_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign(monkeypatch, "exposure.turn")
    registry = ToolRegistry()
    registry.register_tools(
        [
            ToolDef(
                name="observe_spreadsheet",
                description="d",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect="none",
            ),
            ToolDef(
                name="run_code",
                description="d",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect="dynamic",
            ),
            ToolDef(
                name="apply_spreadsheet_changes",
                description="d",
                input_schema={"type": "object", "properties": {}},
                func=lambda: None,
                write_effect="workspace_write",
            ),
        ]
    )
    engine = _stub(
        config=replace(
            _config(
                jev_enabled="enforce",
                jev_exposure="shadow",
                jev_calibrated=True,
            ),
            workspace_root=str(tmp_path),
        ),
        _current_chat_mode="write",
        _registry=registry,
        registry=registry,
        _skill_router=None,
        _skill_resolver=None,
        _fixed_capability=None,
    )
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_decision()),
    ):
        await maybe_record_turn_exposure(engine, "对每张表循环汇总")
    names = _schema_names(MetaToolBuilder(engine).build_v5_tools_impl())
    assert names == {"run_code", "observe_spreadsheet", "apply_spreadsheet_changes"}


def _big_result(*, success: bool = True, extra_ui: ToolUiMeta | None = None) -> ToolResult:
    text = "行" * (T_BIG + 50)
    return ToolResult(
        success=success,
        model_text=text,
        value={"rows": 3, "payload": "keep-me"},
        ui_meta=extra_ui or ToolUiMeta(files=["a.xlsx"]),
    )


@pytest.mark.asyncio
async def test_observation_gate_off_does_not_reshape() -> None:
    # 二态契约：observation 子闸 off 时不评估、不改 model_text。
    engine = _stub(config=_config(jev_observation="off"))
    original = _big_result()
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        out = await maybe_shape_observation(
            engine,
            original,
            tool_name="observe_spreadsheet",
            arguments={"file_path": "a.xlsx", "sheet": "Sheet1"},
        )
        mocked.assert_not_called()
    assert out.model_text == original.model_text
    assert out.value == original.value
    assert out.ui_meta is original.ui_meta


@pytest.mark.asyncio
async def test_observation_signed_truncate_keeps_value_ui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _sign(monkeypatch, "observation.shape")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_observation="enforce",
            jev_calibrated=True,
        ),
    )
    object.__setattr__(engine.config, "workspace_root", str(tmp_path))
    original = _big_result()
    decision = Decision(
        kind="noop",
        reason="shape:truncate",
        extras={"shape": "truncate"},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        out = await maybe_shape_observation(
            engine,
            original,
            tool_name="observe_spreadsheet",
            arguments={"file_path": "a.xlsx", "sheet": "Sheet1"},
        )
    assert out.model_text != original.model_text
    assert len(out.model_text) < len(original.model_text)
    assert T_TIGHT_CHARS >= 100
    assert out.value == original.value
    assert out.ui_meta is original.ui_meta
    assert out.truncated is True


@pytest.mark.asyncio
async def test_observation_keeps_errors_and_small_and_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "observation.shape")
    cfg = _config(jev_enabled="enforce", jev_observation="enforce", jev_calibrated=True)
    engine = _stub(config=cfg)
    err = _big_result(success=False)
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        out = await maybe_shape_observation(engine, err, tool_name="observe_spreadsheet")
        mocked.assert_not_called()
    assert out.model_text == err.model_text

    small = ToolResult(success=True, model_text="短", value={"ok": True})
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_shape_observation(engine, small, tool_name="observe_spreadsheet")
        mocked.assert_not_called()

    child = _stub(config=cfg, _subagent_config=object())
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        out = await maybe_shape_observation(child, _big_result(), tool_name="observe_spreadsheet")
        mocked.assert_not_called()
    assert out.model_text.startswith("行")


@pytest.mark.asyncio
async def test_observation_download_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "observation.shape")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_observation="enforce", jev_calibrated=True),
    )
    original = _big_result(extra_ui=ToolUiMeta(download={"name": "a.xlsx"}))
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        out = await maybe_shape_observation(engine, original, tool_name="observe_spreadsheet")
        mocked.assert_not_called()
    assert out.model_text == original.model_text
