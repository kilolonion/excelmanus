"""片 F/G/M/P：inert 接线。默认不改目录/循环/profile/冷修剪。禁止打网。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.prompt.skill_catalog import attach_skill_catalog, render_available_skills
from excelmanus.system_one.host import (
    maybe_pin_skills,
    maybe_prune_observations,
    maybe_record_turn_exposure,
    maybe_suggest_loop_wrap,
    should_skip_skill_catalog_snapshot,
    turn_wire_profile,
)
from excelmanus.system_one.policy import T_BIG
from excelmanus.system_one.types import Decision


def _config(**overrides: object) -> ExcelManusConfig:
    return ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        max_iterations=8,
        max_consecutive_failures=3,
        workspace_root=str(Path(__file__).resolve().parent),
        ai_gateway_api_key="vck_test",
        **overrides,
    )


def _sign(monkeypatch: pytest.MonkeyPatch, *packs: str) -> None:
    monkeypatch.setattr(
        "excelmanus.system_one.calibration.SIGNED_ENFORCE_PACKS",
        frozenset(packs),
    )


class _Skill:
    def __init__(self, name: str, desc: str = "desc") -> None:
        self.name = name
        self.description = desc
        self.disable_model_invocation = False


def _stub(**overrides: object) -> SimpleNamespace:
    packs = {
        "alpha": _Skill("alpha", "first"),
        "beta": _Skill("beta", "second"),
        "gamma": _Skill("gamma", "third"),
    }
    base: dict[str, object] = {
        "config": _config(),
        "_subagent_config": None,
        "_is_host_session": True,
        "_current_chat_mode": "write",
        "_pending_plan_exit": None,
        "_turn_image_count": 0,
        "_exposure_last_tools": [],
        "_active_skills": [],
        "_turn_exposure": None,
        "_exposure_sticky": None,
        "_tools_cache": None,
        "_skill_pin": None,
        "_skill_pin_evaluated": False,
        "_loop_wrap": None,
        "_question_flow": SimpleNamespace(has_pending=lambda: False),
        "has_pending_approval": lambda: False,
        "_full_access_enabled": True,
        "_skill_resolver": None,
        "_skill_router": SimpleNamespace(
            _loader=SimpleNamespace(get_skillpacks=lambda: packs),
        ),
        "memory": SimpleNamespace(
            get_messages=lambda: [{"role": "user", "content": "使用 beta 处理 second 任务"}],
            messages=[{"role": "user", "content": "使用 beta 处理 second 任务"}],
            add_user_message=lambda *a, **k: None,
        ),
        "_memory": None,
        "_state": SimpleNamespace(last_iteration_count=1, last_failure_count=0),
        "_last_failure_count": 0,
    }
    base.update(overrides)
    engine = SimpleNamespace(**base)
    if engine._memory is None:
        engine._memory = engine.memory
    return engine


def _exposure_decision(*, domain: str = "chitchat", chat: float = 0.95, applied: bool = True) -> Decision:
    profile = "minimal" if domain == "chitchat" else "inspect"
    return Decision(
        kind="noop",
        reason=f"domain:{domain}",
        extras={
            "profile": profile,
            "domain": domain,
            "domain_confidence": 0.9,
            "mode_hint": "keep",
            "mode_hint_confidence": 0.9,
            "is_chitchat": chat,
            "wire_narrow": False,
        },
        applied=applied,
    )


@pytest.mark.asyncio
async def test_f_disabled_catalog_unchanged() -> None:
    # 二态契约默认全 enforce；总闸 off 时不评估、不改目录。
    engine = _stub(config=_config(jev_enabled="off"))
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_pin_skills(engine)
        mocked.assert_not_called()
    text = attach_skill_catalog(engine)
    assert "likely match" not in text
    assert engine._skill_pin is None


@pytest.mark.asyncio
async def test_f_enforce_pins_without_signoff() -> None:
    # 二态契约：enforce 下 applied 决策直接置顶，无需标定签字。
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
    )
    decision = Decision(
        kind="noop",
        reason="pin:beta",
        extras={"pin": "beta", "needs_skill": 0.95},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        await maybe_pin_skills(engine)
    assert engine._skill_pin == "beta"
    text = attach_skill_catalog(engine)
    assert "likely match" in text


@pytest.mark.asyncio
async def test_f_child_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "skill.pin")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        _subagent_config={"name": "child"},
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_pin_skills(engine)
        mocked.assert_not_called()
    assert engine._skill_pin is None


@pytest.mark.asyncio
async def test_f_signed_pins_likely_match(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "skill.pin")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
    )
    decision = Decision(
        kind="noop",
        reason="pin:beta",
        extras={"pin": "beta", "needs_skill": 0.95},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        await maybe_pin_skills(engine)
    assert engine._skill_pin == "beta"
    text = attach_skill_catalog(engine)
    assert text.index("`beta`") < text.index("`alpha`")
    assert "`beta`: second (likely match)" in text
    assert "skill()" not in text


def test_f_render_does_not_drop_skills() -> None:
    text = render_available_skills(
        {"zeta": _Skill("zeta"), "alpha": _Skill("alpha")},
        pin="zeta",
    )
    assert "`zeta`" in text and "`alpha`" in text
    assert text.index("`zeta`") < text.index("`alpha`")


@pytest.mark.asyncio
async def test_g_disabled_does_not_evaluate() -> None:
    # 二态契约默认全 enforce；总闸 off 时不评估。
    engine = _stub(config=_config(jev_enabled="off"))
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_suggest_loop_wrap(engine)
        mocked.assert_not_called()
    assert engine._loop_wrap is None


@pytest.mark.asyncio
async def test_g_enforce_advice_does_not_take_over_loop() -> None:
    # 二态契约：enforce 下建议直接 applied，但仍只是建议——不改写记忆、不接管循环。
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_calibrated=True),
        memory=SimpleNamespace(
            get_messages=lambda: [{"role": "user", "content": "你好"}],
            messages=[{"role": "user", "content": "你好"}],
        ),
    )
    engine._memory = engine.memory
    decision = Decision(
        kind="noop",
        reason="next:stop",
        extras={"next": "stop", "next_confidence": 0.95, "done_enough": 0.9},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        await maybe_suggest_loop_wrap(engine, tool_results=[SimpleNamespace(tool_name="observe_spreadsheet", success=True, result="ok")], iteration=1)
    assert engine._loop_wrap["applied"] is True
    assert engine.memory.messages == [{"role": "user", "content": "你好"}]


@pytest.mark.asyncio
async def test_g_signed_stop_does_not_take_over_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "loop.wrap")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_calibrated=True),
        memory=SimpleNamespace(
            get_messages=lambda: [{"role": "user", "content": "你好"}],
            messages=[{"role": "user", "content": "你好"}],
        ),
    )
    engine._memory = engine.memory
    decision = Decision(
        kind="noop",
        reason="next:stop",
        extras={"next": "stop", "next_confidence": 0.95, "done_enough": 0.9},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        await maybe_suggest_loop_wrap(engine, tool_results=[SimpleNamespace(tool_name="observe_spreadsheet", success=True, result="ok")], iteration=1)
    assert engine._loop_wrap["next"] == "stop"
    assert engine._loop_wrap["applied"] is True
    assert engine.memory.messages == [{"role": "user", "content": "你好"}]


@pytest.mark.asyncio
async def test_g_child_does_not_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    _sign(monkeypatch, "loop.wrap")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_calibrated=True),
        _subagent_config={"name": "child"},
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        await maybe_suggest_loop_wrap(engine)
        mocked.assert_not_called()


@pytest.mark.asyncio
async def test_m_unsigned_does_not_skip_catalog_or_narrow() -> None:
    engine = _stub(config=_config(jev_enabled="enforce", jev_exposure="enforce"))
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_exposure_decision(applied=False)),
    ):
        await maybe_record_turn_exposure(engine, "在忙什么")
    assert should_skip_skill_catalog_snapshot(engine) is False
    assert turn_wire_profile(engine) == "full"
    text = attach_skill_catalog(engine)
    assert "<available_skills>" in text


@pytest.mark.asyncio
async def test_m_signed_chitchat_minimal_and_skips_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign(monkeypatch, "exposure.turn")
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        memory=SimpleNamespace(
            get_messages=lambda: [{"role": "user", "content": "今天天气怎么样"}],
            messages=[],
            add_user_message=lambda *a, **k: None,
        ),
    )
    engine._memory = engine.memory
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_exposure_decision()),
    ):
        await maybe_record_turn_exposure(engine, "今天天气怎么样")
    assert turn_wire_profile(engine) == "minimal"
    assert should_skip_skill_catalog_snapshot(engine) is True
    assert attach_skill_catalog(engine) == ""


@pytest.mark.asyncio
async def test_m_mention_or_image_keeps_catalog() -> None:
    # 二态契约下无需签字；@mention 或图片都不得跳过目录快照。
    engine = _stub(
        config=_config(jev_enabled="enforce", jev_exposure="enforce", jev_calibrated=True),
        memory=SimpleNamespace(
            get_messages=lambda: [{"role": "user", "content": "看看 @file:a.xlsx"}],
            messages=[{"role": "user", "content": "看看 @file:a.xlsx"}],
        ),
    )
    engine._memory = engine.memory
    with patch(
        "excelmanus.system_one.evaluate",
        AsyncMock(return_value=_exposure_decision()),
    ):
        await maybe_record_turn_exposure(engine, "看看 @file:a.xlsx")
    assert should_skip_skill_catalog_snapshot(engine) is False
    engine._turn_image_count = 1
    engine.memory.get_messages = lambda: [{"role": "user", "content": "嗨"}]
    assert should_skip_skill_catalog_snapshot(engine) is False


@pytest.mark.asyncio
async def test_p_default_does_not_prune() -> None:
    engine = _stub()
    memory = SimpleNamespace(messages=[], _emit_replace=lambda *a, **k: None)
    with patch("excelmanus.system_one.evaluate", AsyncMock()) as mocked:
        assert await maybe_prune_observations(engine, memory) == 0
        mocked.assert_not_called()


def _fat_tool_memory(n: int = 6) -> SimpleNamespace:
    messages: list[dict] = []
    fat = "行" * (T_BIG + 20)
    for i in range(n):
        call_id = f"c{i}"
        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {
                            "name": "observe_spreadsheet",
                            "arguments": '{"file_path": "a.xlsx", "sheet": "S"}',
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": fat})
    replaced: list[str] = []
    memory = SimpleNamespace(messages=messages, _emit_replace=lambda msg, **k: replaced.append(msg["content"]))
    return memory


@pytest.mark.asyncio
async def test_p_signed_stubs_old_keeps_recent_k(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sign(monkeypatch, "observation.prune")
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_observation="enforce",
            jev_calibrated=True,
        ),
    )
    memory = _fat_tool_memory(6)
    object.__setattr__(engine.config, "workspace_root", str(tmp_path))
    decision = Decision(
        kind="noop",
        reason="prune",
        extras={"prune": True, "still_relevant": 0.1},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)):
        n = await maybe_prune_observations(engine, memory)
    assert n == 2  # batch cap 3 but last 4 of 6 are protected → 2 candidates
    stubs = [m["content"] for m in memory.messages if m.get("role") == "tool" and "已省略" in str(m.get("content"))]
    assert stubs
    assert all("read_text_file" in s and "spill:" in s for s in stubs)
    recent = [m["content"] for m in memory.messages if m.get("role") == "tool"][-4:]
    assert all("已收起" not in c for c in recent)


@pytest.mark.asyncio
async def test_p_gate_off_does_not_prune() -> None:
    # 二态契约：observation 子闸 off 时不评估也不修剪。
    engine = _stub(
        config=_config(
            jev_enabled="enforce",
            jev_observation="off",
            jev_calibrated=True,
        ),
    )
    memory = _fat_tool_memory(6)
    decision = Decision(
        kind="noop",
        reason="prune",
        extras={"prune": True, "still_relevant": 0.1},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)) as mocked:
        n = await maybe_prune_observations(engine, memory)
        mocked.assert_not_called()
    assert n == 0
    assert all("已收起" not in str(m.get("content")) for m in memory.messages if m.get("role") == "tool")


@pytest.mark.asyncio
async def test_p_skips_errors_pending_and_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sign(monkeypatch, "observation.prune")
    cfg = _config(jev_enabled="enforce", jev_observation="enforce", jev_calibrated=True)
    object.__setattr__(cfg, "workspace_root", str(tmp_path))
    child = _stub(config=cfg, _subagent_config={"name": "c"})
    pending = _stub(
        config=cfg,
        _question_flow=SimpleNamespace(has_pending=lambda: True),
    )
    fat = "行" * (T_BIG + 20)
    error_memory = SimpleNamespace(
        messages=[
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "e0",
                        "function": {
                            "name": "observe_spreadsheet",
                            "arguments": '{"file_path": "a.xlsx", "sheet": "S"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "e0", "content": fat + ' {"status": "error"}'},
            *(_fat_tool_memory(5).messages),
        ],
    )
    decision = Decision(
        kind="noop",
        reason="prune",
        extras={"prune": True, "still_relevant": 0.1},
        applied=True,
    )
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=decision)) as mocked:
        assert await maybe_prune_observations(child, SimpleNamespace(messages=[])) == 0
        assert await maybe_prune_observations(pending, SimpleNamespace(messages=[])) == 0
        assert mocked.call_count == 0
        await maybe_prune_observations(_stub(config=cfg), error_memory)
        assert mocked.call_count == 1
        first_tool = next(m for m in error_memory.messages if m.get("role") == "tool")
        assert '"status": "error"' in str(first_tool.get("content"))
        assert "已收起" not in str(first_tool.get("content"))
