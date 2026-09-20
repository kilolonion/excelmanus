"""system_one A+B+H：闸、题包合成、profile 对账、无密钥 fail-open。禁止打网。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from excelmanus.system_one import evaluate
from excelmanus.system_one.adapter import bound_state
from excelmanus.system_one.client import noul_from_payload, normalize_answers
from excelmanus.system_one.packs import (
    ALWAYS_ON_CORE,
    PACKS,
    PROFILE_NAMES,
    get_pack,
    profile_selector_names,
    resolve_profile_tools,
)
from excelmanus.system_one.policy import (
    T_ALLOW,
    T_DESTRUCTIVE,
    T_NEEDS_WRITE,
    effective_flag,
    effective_gate,
    gate_for_pack,
    is_known_dangerous_call,
    jev_is_active,
    next_sticky_profile,
    settings_from,
    synthesize,
)
from excelmanus.system_one.types import ChoiceAnswer, Evaluation, NoulAnswer, ScoreAnswer
from excelmanus.tools.policy import TOOL_CATEGORIES, TOOL_SHORT_DESCRIPTIONS
from excelmanus.tools.registry import ToolRegistry


def _eval(pack_id: str, answers: dict) -> Evaluation:
    return Evaluation(pack_id=pack_id, answers=answers, model="jev-1.13.0")


def test_gate_matrix_master_off_kills_children() -> None:
    assert effective_gate("off", "enforce") == "off"
    assert effective_gate("off", "shadow") == "off"
    assert effective_flag("off", True) == "off"


def test_jev_is_active_requires_gate_and_key() -> None:
    off = settings_from(SimpleNamespace(
        jev_enabled="off",
        jev_exposure="enforce",
        jev_mode_hint=True,
        jev_observation="enforce",
        jev_ui_hint=True,
        jev_model="jev-1.13.0",
        typesafe_api_key="k",
        jev_timeout_seconds=1.5,
    ))
    no_key = settings_from(SimpleNamespace(
        jev_enabled="shadow",
        jev_exposure="shadow",
        jev_mode_hint=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        ai_gateway_api_key=None,
        jev_timeout_seconds=1.5,
    ))
    on = settings_from(SimpleNamespace(
        jev_enabled="shadow",
        jev_exposure="shadow",
        jev_mode_hint=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key="k",
        jev_timeout_seconds=1.5,
    ))
    assert jev_is_active(off) is False
    assert jev_is_active(no_key) is False
    assert jev_is_active(on) is True


def _jev_cfg(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "jev_enabled": "shadow",
        "jev_exposure": "off",
        "jev_mode_hint": False,
        "jev_observation": "off",
        "jev_ui_hint": False,
        "jev_model": "",
        "typesafe_api_key": None,
        "ai_gateway_api_key": None,
        "jev_active_provider": "",
        "jev_providers": (),
        "jev_timeout_seconds": 1.5,
        "jev_calibrated": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_settings_from_config_reads_custom_jev_provider() -> None:
    """config.jev_providers 里的自定义提供商也算「密钥已配」。"""
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_providers=(
            JevProviderRecord(
                id="custom-1",
                name="自建网关",
                protocol="gateway",
                base_url="https://gw.example/eval",
                model="typesafe-ai/jev",
                api_key="vck_custom",
            ),
        ),
    ))
    assert parsed.api_key == "vck_custom"
    assert parsed.protocol == "gateway"
    assert parsed.base_url == "https://gw.example/eval"
    assert parsed.model == "typesafe-ai/jev"
    assert jev_is_active(parsed) is True


def test_provider_model_wins_over_legacy_default_model() -> None:
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_model="jev-1.13.0",
        jev_providers=(
            JevProviderRecord(
                id="custom-ts",
                name="自建 TypeSafe",
                protocol="typesafe",
                base_url="https://typesafe.example/system-one",
                model="jev-custom",
                api_key="ts_custom",
            ),
        ),
    ))
    assert parsed.model == "jev-custom"
    assert parsed.base_url == "https://typesafe.example/system-one"


def test_settings_from_config_accepts_mapping_providers() -> None:
    parsed = settings_from(_jev_cfg(
        jev_providers=(
            {
                "id": "custom-1",
                "name": "c",
                "protocol": "gateway",
                "base_url": "https://gw.example/eval",
                "model": "m",
                "api_key": "vck_map",
            },
        ),
    ))
    assert parsed.api_key == "vck_map"
    assert parsed.protocol == "gateway"
    assert jev_is_active(parsed) is True


def test_settings_from_config_explicit_active_provider_wins() -> None:
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_active_provider="vercel",
        jev_providers=(
            JevProviderRecord(
                id="typesafe", name="TypeSafe", protocol="typesafe",
                base_url="", model="jev-1.13.0", api_key="ts_a",
            ),
            JevProviderRecord(
                id="vercel", name="Vercel", protocol="gateway",
                base_url="", model="typesafe-ai/jev", api_key="vck_b",
            ),
        ),
    ))
    assert parsed.api_key == "vck_b"
    assert parsed.protocol == "gateway"


def test_settings_from_config_keyless_active_falls_back_to_legacy() -> None:
    """激活提供商没配密钥时回落旧版密钥，与 resolve_jev_api_key 一致。"""
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_active_provider="custom-1",
        ai_gateway_api_key="vck_legacy",
        jev_providers=(
            JevProviderRecord(
                id="custom-1", name="c", protocol="gateway",
                base_url="", model="m", api_key="",
            ),
        ),
    ))
    assert parsed.api_key == "vck_legacy"
    assert jev_is_active(parsed) is True


def test_settings_from_config_keyless_provider_no_legacy_is_inactive() -> None:
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_active_provider="custom-1",
        jev_providers=(
            JevProviderRecord(
                id="custom-1", name="c", protocol="gateway",
                base_url="", model="m", api_key="",
            ),
        ),
    ))
    assert parsed.api_key is None
    assert jev_is_active(parsed) is False


def test_settings_from_config_does_not_cross_protocol_fallback() -> None:
    from excelmanus.system_one.providers import JevProviderRecord

    parsed = settings_from(_jev_cfg(
        jev_active_provider="vercel",
        typesafe_api_key="ts_only",
        jev_providers=(
            JevProviderRecord(
                id="vercel", name="Vercel", protocol="gateway",
                base_url="https://gw.example/eval", model="typesafe-ai/jev", api_key="",
            ),
        ),
    ))
    assert parsed.api_key is None
    assert parsed.protocol == "gateway"
    assert jev_is_active(parsed) is False


def test_gate_matrix_master_shadow_downgrades_enforce() -> None:
    assert effective_gate("shadow", "enforce") == "shadow"
    assert effective_gate("shadow", "shadow") == "shadow"
    assert effective_gate("shadow", "off") == "off"
    assert effective_flag("shadow", True) == "shadow"
    settings = SimpleNamespace(
        jev_enabled="shadow",
        jev_exposure="enforce",
        jev_mode_hint=True,
        jev_observation="enforce",
        jev_ui_hint=True,
        jev_model="jev-1.13.0",
        typesafe_api_key="k",
        jev_timeout_seconds=1.5,
    )
    parsed = settings_from(settings)
    assert gate_for_pack("exposure.turn", parsed) == "shadow"
    assert gate_for_pack("observation.shape", parsed) == "shadow"
    assert gate_for_pack("ui.surface", parsed) == "shadow"
    assert gate_for_pack("approval.tool_call", parsed) == "shadow"


def test_gate_matrix_master_enforce_keeps_child() -> None:
    assert effective_gate("enforce", "enforce") == "enforce"
    assert effective_gate("enforce", "shadow") == "shadow"
    assert effective_gate("enforce", "off") == "off"


def test_noul_unifies_gateway_probability() -> None:
    assert noul_from_payload({"probability": 0.19}) == pytest.approx(0.19)
    assert noul_from_payload({"noul": 0.88}) == pytest.approx(0.88)


def test_normalize_answers_three_primitives() -> None:
    spec = get_pack("approval.tool_call")
    raw = {
        "action": {"choice": "ask", "probabilities": {"allow": 0.47, "ask": 0.48, "deny": 0.05}, "confidence": 0.22},
        "destructive": {"probability": 0.19},
        "exfiltrating": {"noul": 0.01},
        "scope_ok": {"noul": 0.8},
        "blast_radius": {"score": 2.93, "confidence": 0.93},
    }
    answers = normalize_answers(spec, raw)
    assert isinstance(answers["destructive"], NoulAnswer)
    assert answers["destructive"].noul == pytest.approx(0.19)
    assert isinstance(answers["action"], ChoiceAnswer)
    assert answers["action"].choice == "ask"
    assert isinstance(answers["blast_radius"], ScoreAnswer)
    assert answers["blast_radius"].score == pytest.approx(2.93)


def test_exposure_domain_maps_to_named_profiles() -> None:
    inspect = synthesize(
        "exposure.turn",
        _eval("exposure.turn", {"domain": ChoiceAnswer("inspect_only", confidence=0.9)}),
        {"chat_mode": "write"},
    )
    assert inspect.extras["profile"] == "inspect"
    assert inspect.extras["wire_narrow"] is False
    file_code = synthesize(
        "exposure.turn",
        _eval("exposure.turn", {"domain": ChoiceAnswer("file_code", confidence=0.9)}),
        {"chat_mode": "write"},
    )
    assert file_code.extras["profile"] == "file_code"
    chitchat = synthesize(
        "exposure.turn",
        _eval("exposure.turn", {"domain": ChoiceAnswer("chitchat", confidence=0.9)}),
        {"chat_mode": "write"},
    )
    assert chitchat.extras["profile"] == "minimal"


def test_jev_config_defaults() -> None:
    from excelmanus.config import ExcelManusConfig, _parse_jev_gate

    cfg = ExcelManusConfig(
        api_key="k",
        base_url="https://example.com/v1",
        model="m",
    )
    assert cfg.jev_enabled == "off"
    assert cfg.jev_exposure == "off"
    assert cfg.jev_mode_hint is False
    assert cfg.jev_observation == "off"
    assert cfg.jev_ui_hint is False
    assert cfg.jev_model == "jev-1.13.0"
    assert cfg.typesafe_api_key is None
    assert cfg.jev_timeout_seconds == pytest.approx(1.5)
    assert _parse_jev_gate(None, "EXCELMANUS_JEV_ENABLED", "off") == "off"


def test_exposure_does_not_classify_call_syntax() -> None:
    assert "fits_code_mode" not in {q.qid for q in get_pack("exposure.turn").questions}


def test_jev_settings_come_from_store_not_process_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from excelmanus.settings_runtime import get_setting, is_locator_key, override_settings
    from excelmanus.system_one.client import resolve_jev_api_key
    from excelmanus.system_one.policy import gate_for_pack, settings_from

    monkeypatch.setenv("EXCELMANUS_JEV_ENABLED", "shadow")
    monkeypatch.setenv("EXCELMANUS_JEV_EXPOSURE", "shadow")
    monkeypatch.setenv("EXCELMANUS_JEV_OBSERVATION", "shadow")
    monkeypatch.setenv("EXCELMANUS_JEV_UI_HINT", "true")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_from_process_env")
    monkeypatch.setenv("EXCELMANUS_TYPESAFE_API_KEY", "vck_from_process_env")
    monkeypatch.setenv("EXCELMANUS_AI_GATEWAY_API_KEY", "vck_from_process_env")

    assert not is_locator_key("EXCELMANUS_JEV_ENABLED")
    assert not is_locator_key("EXCELMANUS_TYPESAFE_API_KEY")
    assert get_setting("EXCELMANUS_JEV_ENABLED") is None
    parsed = settings_from(None)
    assert parsed.enabled == "off"
    key, name = resolve_jev_api_key()
    assert key is None
    assert name == ""

    override_settings({
        "EXCELMANUS_JEV_ENABLED": "shadow",
        "EXCELMANUS_JEV_EXPOSURE": "shadow",
        "EXCELMANUS_JEV_OBSERVATION": "shadow",
        "EXCELMANUS_JEV_UI_HINT": "true",
        "EXCELMANUS_JEV_CALIBRATED": "0",
        "EXCELMANUS_AI_GATEWAY_API_KEY": "vck_test_not_real",
    })
    assert get_setting("EXCELMANUS_JEV_ENABLED") == "shadow"
    parsed = settings_from(None)
    assert parsed.enabled == "shadow"
    assert parsed.exposure == "shadow"
    assert parsed.observation == "shadow"
    assert parsed.ui_hint is True
    assert parsed.calibrated is False
    assert parsed.api_key == "vck_test_not_real"
    assert gate_for_pack("exposure.turn", parsed) == "shadow"
    key, name = resolve_jev_api_key()
    assert name == "EXCELMANUS_AI_GATEWAY_API_KEY"
    assert key == "vck_test_not_real"


def test_exposure_mixed_or_low_confidence_is_full() -> None:
    decision = synthesize(
        "exposure.turn",
        _eval("exposure.turn", {"domain": ChoiceAnswer("mixed", confidence=0.99)}),
        {"chat_mode": "write"},
    )
    assert decision.extras["profile"] == "full"
    low = synthesize(
        "exposure.turn",
        _eval("exposure.turn", {"domain": ChoiceAnswer("inspect_only", confidence=0.2)}),
        {"chat_mode": "write"},
    )
    assert low.extras["profile"] == "full"


def test_exposure_needs_write_promotes_inspect_to_edit() -> None:
    decision = synthesize(
        "exposure.turn",
        _eval(
            "exposure.turn",
            {
                "domain": ChoiceAnswer("inspect_only", confidence=0.9),
                "needs_write": NoulAnswer(T_NEEDS_WRITE),
            },
        ),
        {"chat_mode": "read"},
    )
    assert decision.extras["profile"] == "edit"


def test_exposure_mode_mismatch_write_does_not_starve() -> None:
    decision = synthesize(
        "exposure.turn",
        _eval(
            "exposure.turn",
            {
                "domain": ChoiceAnswer("inspect_only", confidence=0.9),
                "mode_mismatch": ChoiceAnswer("suggest_write", confidence=0.9),
            },
        ),
        {"chat_mode": "read"},
    )
    assert decision.extras["profile"] == "edit"
    assert decision.extras["wire_narrow"] is False


def test_observation_keep_on_error_or_verbatim() -> None:
    keep_err = synthesize(
        "observation.shape",
        _eval("observation.shape", {"shape": ChoiceAnswer("pointer", confidence=0.99)}),
        {"success": False},
    )
    assert keep_err.extras["shape"] == "keep"
    keep_verbatim = synthesize(
        "observation.shape",
        _eval(
            "observation.shape",
            {
                "shape": ChoiceAnswer("truncate", confidence=0.99),
                "user_wants_verbatim": NoulAnswer(0.9),
            },
        ),
        {"success": True},
    )
    assert keep_verbatim.extras["shape"] == "keep"


def test_observation_pointer_downgrades_when_not_refetchable() -> None:
    decision = synthesize(
        "observation.shape",
        _eval("observation.shape", {"shape": ChoiceAnswer("pointer", confidence=0.99)}),
        {"success": True, "re_fetchable": False, "spillable": True},
    )
    assert decision.extras["shape"] == "spill"


def test_ui_stay_high_confidence_suppresses_heuristic() -> None:
    decision = synthesize(
        "ui.surface",
        _eval("ui.surface", {"surface": ChoiceAnswer("stay", confidence=0.9)}),
        {},
    )
    assert decision.extras["suppress_heuristic"] is True
    low = synthesize(
        "ui.surface",
        _eval("ui.surface", {"surface": ChoiceAnswer("stay", confidence=0.4)}),
        {},
    )
    assert low.extras["suppress_heuristic"] is False


def test_skill_pin_low_confidence_keeps_catalog() -> None:
    decision = synthesize(
        "skill.pin",
        _eval(
            "skill.pin",
            {
                "needs_skill": NoulAnswer(0.4),
                "pick": ChoiceAnswer("first", confidence=0.9),
            },
        ),
        {"candidates": [{"name": "alpha"}]},
    )
    assert decision.extras["pin"] == ""


def test_skill_pin_high_confidence_selects_candidate() -> None:
    decision = synthesize(
        "skill.pin",
        _eval(
            "skill.pin",
            {
                "needs_skill": NoulAnswer(0.9),
                "pick": ChoiceAnswer("second", confidence=0.9),
            },
        ),
        {"candidates": [{"name": "alpha"}, {"name": "beta"}]},
    )
    assert decision.extras["pin"] == "beta"


def test_loop_wrap_low_confidence_is_continue() -> None:
    decision = synthesize(
        "loop.wrap",
        _eval("loop.wrap", {"next": ChoiceAnswer("stop", confidence=0.2)}),
    )
    assert decision.extras["next"] == "continue"


def test_observation_prune_keeps_errors_and_low_conf() -> None:
    keep_err = synthesize(
        "observation.prune",
        _eval("observation.prune", {"still_relevant": NoulAnswer(0.1)}),
        {"success": False, "re_fetchable": True},
    )
    assert keep_err.extras["prune"] is False
    prune = synthesize(
        "observation.prune",
        _eval("observation.prune", {"still_relevant": NoulAnswer(0.1)}),
        {"success": True, "re_fetchable": True},
    )
    assert prune.extras["prune"] is True
    keep_conf = synthesize(
        "observation.prune",
        _eval("observation.prune", {"still_relevant": NoulAnswer(0.7)}),
        {"success": True, "re_fetchable": True},
    )
    assert keep_conf.extras["prune"] is False


def test_approval_fail_closed_and_thresholds() -> None:
    deny = synthesize(
        "approval.tool_call",
        _eval("approval.tool_call", {"destructive": NoulAnswer(T_DESTRUCTIVE)}),
    )
    assert deny.kind == "deny"
    auto = synthesize(
        "approval.tool_call",
        _eval(
            "approval.tool_call",
            {
                "action": ChoiceAnswer("allow", confidence=T_ALLOW),
                "destructive": NoulAnswer(0.1),
                "exfiltrating": NoulAnswer(0.1),
                "blast_radius": ScoreAnswer(score=1.0),
            },
        ),
    )
    assert auto.kind == "auto"
    ask = synthesize("approval.tool_call", _eval("approval.tool_call", {}))
    assert ask.kind == "ask"


def test_mutation_verify_is_shadow_decision_only() -> None:
    decision = synthesize(
        "mutation.verify",
        _eval(
            "mutation.verify",
            {
                "satisfied": NoulAnswer(0.92),
                "scope_ok": NoulAnswer(0.9),
                "next": ChoiceAnswer("inspect_more", confidence=0.88),
            },
        ),
        {"verification_facts": {"success": True}},
    )
    assert decision.kind == "noop"
    assert decision.extras["next"] == "inspect_more"
    assert decision.applied is False


def test_recovery_pack_is_only_a_failure_suggestion() -> None:
    decision = synthesize(
        "recovery.next_step",
        _eval(
            "recovery.next_step",
            {
                "next": ChoiceAnswer("retry", confidence=0.9),
                "retryable": NoulAnswer(0.9),
                "needs_user": NoulAnswer(0.1),
            },
        ),
        {"safe_to_retry": True},
    )
    assert decision.kind == "noop"
    assert decision.extras["next"] == "retry"
    assert decision.applied is False


def test_recovery_state_uses_only_trailing_failure_streak() -> None:
    from excelmanus.engine_types import ToolCallResult
    from excelmanus.system_one.adapter import recovery_state_from_engine

    engine = SimpleNamespace(
        memory=SimpleNamespace(get_messages=lambda: [{"role": "user", "content": "继续"}]),
        _last_iteration_count=2,
    )
    results = [
        ToolCallResult("x", {}, "old", False, error="old"),
        ToolCallResult("y", {}, "ok", True),
        ToolCallResult("z", {}, "new1", False, error="new1"),
        ToolCallResult("w", {}, "new2", False, error="new2"),
    ]
    state = recovery_state_from_engine(engine, results)
    assert state["consecutive_failures"] == 2


def test_adapter_strips_secrets_history_and_bytes() -> None:
    bounded = bound_state(
        "approval.tool_call",
        {
            "user_text": "x" * 800,
            "messages": [{"role": "user", "content": "secret history"}],
            "api_key": "sk-live",
            "tool": {
                "name": "delete_file",
                "args": {"file_path": "a.xlsx", "token": "abc", "blob": b"bytes"},
            },
            "typesafe_api_key": "nope",
            "ai_gateway_api_key": "nope",
        },
    )
    assert "messages" not in bounded
    assert "api_key" not in bounded
    assert "typesafe_api_key" not in bounded
    assert "ai_gateway_api_key" not in bounded
    assert len(bounded["user_text"]) == 500
    assert bounded["tool"]["name"] == "delete_file"
    assert "token" not in bounded["tool"]["args"]
    assert "blob" not in bounded["tool"]["args"]


def test_adapter_clips_nested_tool_arguments_and_free_text() -> None:
    bounded = bound_state(
        "approval.tool_call",
        {
            "tool": {
                "name": "run_code",
                "args": {
                    "metadata": {"apiKey": "do-not-send", "note": "x" * 10_000},
                    "rows": ["y" * 10_000],
                },
            },
            "path": "p" * 10_000,
        },
    )
    args = bounded["tool"]["args"]
    assert "apiKey" not in args["metadata"]
    assert len(args["metadata"]["note"]) <= 300
    assert len(args["rows"][0]) <= 120
    assert len(bounded["path"]) <= 300


def test_profile_names_avoid_code_clash() -> None:
    assert "code" not in PROFILE_NAMES
    assert "file_code" in PROFILE_NAMES
    assert set(PACKS) == {
        "exposure.turn",
        "observation.shape",
        "observation.prune",
        "mutation.verify",
        "recovery.next_step",
        "ui.surface",
        "approval.tool_call",
        "skill.pin",
        "loop.wrap",
    }


def test_profile_selectors_have_no_ghost_names() -> None:
    known = (
        set(TOOL_SHORT_DESCRIPTIONS)
        | {name for members in TOOL_CATEGORIES.values() for name in members}
        | set(ALWAYS_ON_CORE)
        | {"write_plan", "exit_plan_mode", "parallel_search", "introspect_capability", "task_create", "task_update"}
    )
    for profile in PROFILE_NAMES:
        if profile == "full":
            continue
        unknown = profile_selector_names(profile) - known
        assert not unknown, unknown


def test_profile_intersects_authorized_catalog_and_preserves_core() -> None:
    from excelmanus.tools.catalog import derive_effective_catalog
    from excelmanus.tools.registry import ToolDef

    registered = {
        "inspect_spreadsheet",
        "edit_spreadsheet",
        "write_plan",
        "exit_plan_mode",
        "ask_user",
        "run_code",
        "mcp_demo_search",
        "parallel_search",
    }
    inspect_tools = resolve_profile_tools("inspect", registered)
    assert "write_plan" in inspect_tools
    assert "exit_plan_mode" in inspect_tools
    assert "inspect_spreadsheet" in inspect_tools
    assert "ask_user" in inspect_tools
    assert "edit_spreadsheet" in inspect_tools
    web_tools = resolve_profile_tools("web", registered)
    assert "mcp_demo_search" in web_tools
    full = resolve_profile_tools("full", registered)
    assert full == registered
    assert resolve_profile_tools("inspect", registered) <= registered
    tools = [ToolDef(
        name=name, description=name, input_schema={"type": "object", "properties": {}},
        func=lambda: None, write_effect="none",
    ) for name in registered]
    for mode in ("read", "write", "plan"):
        catalog = derive_effective_catalog(tools=tools, mode=mode)
        visible = resolve_profile_tools("inspect", catalog.names())
        assert ("write_plan" in visible) is (mode == "plan")
        assert ("exit_plan_mode" in visible) is (mode == "plan")


def test_profile_reconciles_with_builtin_registry(tmp_path) -> None:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    names = {tool.name for tool in registry.get_all_tools()}
    names |= {"task_create", "task_update", "write_plan", "exit_plan_mode", "introspect_capability"}
    for profile in PROFILE_NAMES:
        resolved = resolve_profile_tools(profile, names)
        assert resolved <= names
        if profile == "inspect":
            assert "write_plan" in resolved
            assert "edit_spreadsheet" in resolved


@pytest.mark.asyncio
async def test_evaluate_without_key_matches_current_behavior() -> None:
    off = await evaluate("exposure.turn", {"user_text": "你好"}, config=SimpleNamespace(
        jev_enabled="off",
        jev_exposure="enforce",
        jev_mode_hint=True,
        jev_observation="enforce",
        jev_ui_hint=True,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        jev_timeout_seconds=1.5,
    ))
    assert off.kind == "noop"
    assert off.extras.get("profile") == "full"
    shadow = await evaluate("exposure.turn", {"user_text": "改表"}, config=SimpleNamespace(
        jev_enabled="shadow",
        jev_exposure="enforce",
        jev_mode_hint=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        jev_timeout_seconds=1.5,
    ))
    assert shadow.kind == "noop"
    assert shadow.applied is False
    security = await evaluate("approval.tool_call", {"user_text": "删文件", "tool": {"name": "delete_file"}}, config=SimpleNamespace(
        jev_enabled="enforce",
        jev_exposure="off",
        jev_mode_hint=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key=None,
        jev_timeout_seconds=1.5,
    ))
    assert security.kind == "ask"


def test_sticky_requires_two_matching_turns_to_narrow() -> None:
    first_eff, first = next_sticky_profile(None, "inspect")
    assert first_eff == "full"
    assert first["streak"] == 1
    assert first["narrowed"] is False
    second_eff, second = next_sticky_profile(first, "inspect")
    assert second_eff == "inspect"
    assert second["narrowed"] is True
    bounce_eff, bounce = next_sticky_profile(second, "edit")
    assert bounce_eff == "full"
    assert bounce["narrowed"] is False
    full_eff, reset = next_sticky_profile(second, "full")
    assert full_eff == "full"
    assert reset["streak"] == 0


def test_known_dangerous_rm_rf_and_workspace_root() -> None:
    assert is_known_dangerous_call("run_shell", {"command": "rm -rf /tmp/ws"})
    assert is_known_dangerous_call("run_shell", {"command": "rm -fr ."})
    assert not is_known_dangerous_call("run_shell", {"command": "ls -la"})
    root = "/tmp/ws-root"
    assert is_known_dangerous_call("delete_file", {"file_path": "."}, root)
    assert is_known_dangerous_call("delete_file", {"file_path": root}, root)
    assert not is_known_dangerous_call("delete_file", {"file_path": "a.xlsx"}, root)
