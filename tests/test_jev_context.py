"""Context advice contract: candidates, uncertainty, isolation and the LLM consumer."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from excelmanus.config import ExcelManusConfig
from excelmanus.system_one.adapter import bound_state
from excelmanus.system_one.intent_context import context_state, render_advice, suggest_context
from excelmanus.system_one.policy import decision_is_applied, settings_from, synthesize
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation
from excelmanus.workspace.refs import WorkspaceRef

PACK = "context.resolve"


def config(tmp_path, **kw):
    return ExcelManusConfig(api_key="test", model="test", base_url="https://test.invalid",
                           workspace_root=str(tmp_path), ai_gateway_api_key="vck_test",
                           jev_enabled=kw.pop("jev_enabled", "enforce"),
                           jev_exposure=kw.pop("jev_exposure", "enforce"), **kw)


def engine(tmp_path, **kw):
    return SimpleNamespace(config=config(tmp_path, **kw), _is_host_session=True, _subagent_config=None,
                           _workspace_ref=WorkspaceRef.from_root(tmp_path, workspace_id="sales", title="销售"),
                           _memory=SimpleNamespace(messages=[]), _mention_contexts=[], _file_registry=None)


def state():
    return {"current_workspace": {"id": "sales", "title": "销售"},
            "workspaces": [{"id": "sales", "title": "销售"}, {"id": "finance", "title": "财务"}],
            "targets": [{"path": "./sales.xlsx", "sheet": "明细", "range": "B2:B5", "source": "active_view"}]}


def evaluation(workspace="current", target="t0", edit_intent="specified", confidence=0.95, **extra):
    answers = {key: ChoiceAnswer(value, {value: confidence}, confidence)
               for key, value in {"workspace": workspace, "target": target,
                                  "edit_intent": edit_intent}.items()}
    for key, value in extra.items():
        if value is not None:
            answers[key] = ChoiceAnswer(value, {value: confidence}, confidence)
    return Evaluation(PACK, answers)


def state_with_columns():
    source = state()
    source["columns"] = [
        {"path": "./sales.xlsx", "sheet": "明细", "header": "到账金额", "column": "C",
         "header_row": 1, "cache_stamp": "1:2", "source": "cached_header"},
        {"path": "./sales.xlsx", "sheet": "明细", "header": "含税销售额", "column": "D",
         "header_row": 1, "cache_stamp": "1:2", "source": "cached_header"},
    ]
    return source


def test_current_selection_and_missing_change_are_separate():
    decision = synthesize(PACK, evaluation(edit_intent="unclear"), state())
    assert decision.extras["target_candidate"]["range"] == "B2:B5"
    assert decision.extras["next"] == "clarify"
    assert "选区只说明位置" in render_advice(decision)
    assert "读取候选" in render_advice(decision)


def test_new_blank_and_existing_workspace_recommendations():
    blank = synthesize(PACK, evaluation("new_blank", "none"), state())
    assert blank.extras["workspace"] == "new_blank"
    assert "尚未创建" in render_advice(blank)
    existing = synthesize(PACK, evaluation("w1"), state())
    assert existing.extras["workspace_candidate"]["id"] == "finance"
    assert existing.extras["workspace"] == "existing"
    assert existing.extras["target_candidate"] is None  # same filename in another workspace != same identity
    assert "尚未切换" in render_advice(existing)
    same = synthesize(PACK, evaluation("w0"), state())
    assert same.extras["workspace"] == "current"


@pytest.mark.parametrize("workspace,target", [("w9", "t9"), ("invented", "../secret.xlsx")])
def test_unknown_or_missing_candidates_become_questions(workspace, target):
    decision = synthesize(PACK, evaluation(workspace, target), state())
    assert decision.extras["workspace"] == "ask"
    assert decision.extras["target"] == "ask"
    assert decision.extras["target_candidate"] is None


@pytest.mark.parametrize("confidence", [0.5, float("nan"), 1.5])
def test_low_or_invalid_confidence_does_not_guess(confidence):
    decision = synthesize(PACK, evaluation(confidence=confidence), state())
    assert decision.extras["workspace"] == "ask"
    assert decision.extras["edit_intent"] == "unclear"


def test_close_runner_up_is_ambiguous():
    ev = evaluation()
    answers = dict(ev.answers, target=ChoiceAnswer("t0", {"t0": .85, "t1": .8}, .85))
    assert synthesize(PACK, Evaluation(PACK, answers), state()).extras["target"] == "ask"


@pytest.mark.parametrize("probabilities,confidence,expected", [
    ({"t0": .54, "t1": .46}, .95, "ask"),
    ({"t0": .90, "t1": .10}, .80, "resolved"),
    ({"t0": .45, "t1": .55}, .95, "ask"),
    ({"t1": .10}, .95, "ask"),
    ({"t0": float("nan"), "t1": .10}, .95, "ask"),
    ({"t0": .90, "t1": float("nan")}, .95, "ask"),
    ({"t0": 1.10, "t1": -.10}, .95, "ask"),
])
def test_candidate_margin_uses_probabilities_not_confidence(probabilities, confidence, expected):
    answers = dict(evaluation().answers, target=ChoiceAnswer("t0", probabilities, confidence))
    assert synthesize(PACK, Evaluation(PACK, answers), state()).extras["target"] == expected


def test_blank_is_not_recommended_for_existing_target():
    assert synthesize(PACK, evaluation("new_blank"), state()).extras["workspace"] == "ask"


def test_explicit_mention_outweighs_active_view():
    source = state()
    source["targets"].append({"path": "./other.xlsx", "source": "explicit_mention"})
    assert synthesize(PACK, evaluation(), source).extras["target"] == "ask"


def test_column_match_selects_cached_header_and_bounded_sample():
    decision = synthesize(PACK, evaluation(column="c0", read="column_sample"), state_with_columns())
    assert decision.extras["column"] == "matched"
    candidate = decision.extras["column_candidate"]
    assert candidate["header"] == "到账金额"
    assert candidate["column"] == "C"
    suggestion = decision.extras["read_suggestion"]
    assert suggestion["tool"] == "inspect_spreadsheet"
    assert suggestion["arguments"]["mode"] == "range"
    assert suggestion["arguments"]["range"] == "C1:C21"
    assert suggestion["purpose"] == "column_sample"
    assert decision.extras["next"] == "inspect_candidate"
    assert "核对名称、坐标" in render_advice(decision)


def test_column_ambiguity_asks_and_empty_candidates_never_ask():
    ambiguous = synthesize(PACK, evaluation(column="ask"), state_with_columns())
    assert ambiguous.extras["column"] == "ask"
    assert ambiguous.extras["column_candidate"] is None
    assert "列或口径尚不明确" in render_advice(ambiguous)
    # No cached columns: an invented c-index cannot ask the user about a column.
    missing = synthesize(PACK, evaluation(column="c3"), state())
    assert missing.extras["column"] == "none"
    assert missing.extras["column_candidate"] is None


def test_column_is_dropped_for_other_workspace_or_no_target():
    source = state_with_columns()
    moved = synthesize(PACK, evaluation("w1", "none", column="c0"), source)
    assert moved.extras["workspace"] == "existing"
    assert moved.extras["column_candidate"] is None
    no_target = synthesize(PACK, evaluation(target="none", column="c0"), source)
    assert no_target.extras["column_candidate"] is None


def test_read_suggestions_map_to_bounded_tool_arguments():
    formulas = synthesize(PACK, evaluation(read="formulas"), state())
    suggestion = formulas.extras["read_suggestion"]
    assert suggestion["tool"] == "inspect_spreadsheet"
    assert suggestion["arguments"] == {"mode": "range", "file_path": "./sales.xlsx",
                                       "sheet_name": "明细", "range": "B2:B5",
                                       "include": ["formulas"]}
    overview = synthesize(PACK, evaluation(read="overview"), state())
    assert overview.extras["read_suggestion"]["arguments"]["mode"] == "overview"
    none = synthesize(PACK, evaluation(read="none"), state())
    assert none.extras["read_suggestion"] is None
    assert none.extras["next"] == "inspect_target"
    invented = synthesize(PACK, evaluation(read="invented_tool"), state())
    assert invented.extras["read_suggestion"] is None


def test_build_context_is_scoped_bounded_and_ignores_hidden_advice(tmp_path):
    e = engine(tmp_path)
    e._memory.messages = [
        {"role": "user", "content": "请把利润列的负数标红"},
        {"role": "assistant", "content": "正在检查明细"},
        {"role": "user", "content": "OLD_ADVICE", "_ui_hidden": True, "_prompt_kind": "jev_context_advice"},
        {"role": "assistant", "tool_calls": [{"function": {"name": "inspect_spreadsheet", "arguments":
            json.dumps({"file_path": "sales.xlsx", "sheet": "明细", "range": "B2:B5", "api_key": "SECRET"})}}]},
    ]
    e._file_registry = SimpleNamespace(list_all=Mock(return_value=[]), scan_workspace=Mock())
    incoming = {"workspaces": [{"id": f"w{i}", "title": f"工作区{i}", "path": "PRIVATE_ROOT"} for i in range(15)],
                "sheet_context": {"workspace_id": "another", "path": "wrong.xlsx"}, "api_key": "SECRET"}
    result = context_state(e, "继续", incoming)
    assert len(result["workspaces"]) == 10
    assert result["workspaces_truncated"] is True
    assert result["targets"][0]["path"] == "./sales.xlsx"
    assert result["targets"][0]["range"] == "B2:B5"
    encoded = json.dumps(result)
    for forbidden in ("OLD_ADVICE", "PRIVATE_ROOT", "SECRET", "wrong.xlsx"):
        assert forbidden not in encoded
    e._file_registry.scan_workspace.assert_not_called()
    assert len(result["recent_context"]) == 2


@pytest.mark.parametrize("path", ["../private.xlsx", ".hidden.xlsx", "outputs/backups/a.xlsx"])
def test_invalid_target_paths_are_dropped(tmp_path, path):
    result = context_state(engine(tmp_path), "修改这里", {"sheet_context": {"workspace_id": "sales", "path": path}})
    assert result["targets"] == []


def test_explicit_candidates_and_cached_sheet_names(tmp_path):
    e = engine(tmp_path)
    e._mention_contexts = [SimpleNamespace(error=None, mention=SimpleNamespace(
        kind="file", value="named.xlsx", range_spec="明细!C2:C8"))]
    e._file_registry = SimpleNamespace(list_all=lambda: [SimpleNamespace(
        canonical_path="cached.xlsx", original_name="cached.xlsx", updated_at="", sheet_meta=[{"name": "汇总"}])])
    result = context_state(e, "把这些改成百分比", {"sheet_context": {
        "workspace_id": "sales", "path": "visible.xlsx", "sheet": "表1", "range": "A1"}})
    assert [r["source"] for r in result["targets"]] == ["explicit_mention", "active_view", "file_catalog", "sheet_catalog"]
    assert result["targets"][0]["range"] == "C2:C8"


def test_bound_state_does_not_send_arbitrary_context():
    bounded = bound_state(PACK, {**state(), "user_text": "a" * 3000, "history": ["SECRET"],
                                "targets": [{"path": "a.xlsx", "api_key": "SECRET"}] * 20})
    assert len(bounded["user_text"]) == 2000
    assert len(bounded["targets"]) == 10
    assert "SECRET" not in json.dumps(bounded)


def test_advice_enforce_does_not_enable_actuator_packs(tmp_path):
    # 二态契约：各题包只看自己的子闸；exposure 子闸关掉时 exposure.turn 不生效。
    settings = settings_from(config(tmp_path, jev_exposure="off"))
    assert not decision_is_applied("exposure.turn", settings)
    settings = settings_from(config(tmp_path))
    assert decision_is_applied(PACK, settings)
    assert decision_is_applied("approval.tool_call", settings)  # master 闸直接生效
    assert not decision_is_applied(PACK, settings_from(config(tmp_path, jev_enabled="off")))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,child", [("off", False), ("enforce", True)])
async def test_off_and_child_do_not_evaluate(tmp_path, mode, child):
    e = engine(tmp_path, jev_enabled=mode)
    e._is_host_session = not child
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock()) as evaluate:
        assert await suggest_context(e, "帮我改一下") == ""
        evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_off_skips_but_enforce_delivers_advice(tmp_path):
    # 二态契约：总闸 off 不评估不出建议；enforce 直接投递建议。
    from dataclasses import replace
    decision = replace(synthesize(PACK, evaluation(edit_intent="from_context"), state()), applied=True)
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock(return_value=decision)):
        assert await suggest_context(engine(tmp_path, jev_enabled="off"), "继续") == ""
        text = await suggest_context(engine(tmp_path), "继续")
        assert "承接最近对话" in text
        assert "sales.xlsx" in text


@pytest.mark.asyncio
async def test_timeout_and_provider_failure_do_not_block_chat(tmp_path):
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    with patch("excelmanus.system_one.intent_context.MAX_CONTEXT_SECONDS", .01), \
         patch("excelmanus.system_one.runtime.evaluate_for_host", side_effect=slow), \
         patch("excelmanus.system_one.trace.emit_jev_trace") as trace:
        assert await suggest_context(engine(tmp_path), "继续") == ""
        assert trace.call_args.args[1].reason == "unavailable:context_timeout"
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock(side_effect=RuntimeError)):
        assert await suggest_context(engine(tmp_path), "继续") == ""


@pytest.mark.asyncio
async def test_real_followup_appends_hidden_advice_once(tmp_path):
    from excelmanus.engine import AgentEngine
    from excelmanus.tools.registry import ToolRegistry
    e = AgentEngine(config=config(tmp_path), registry=ToolRegistry())
    e._client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))]))
    with patch("excelmanus.system_one.evaluate", AsyncMock(return_value=Decision.noop("test"))), \
         patch("excelmanus.system_one.intent_context.suggest_context", AsyncMock(return_value="建议先检查明细")) as advice:
        await e.followup("把这里改一下", context_input={"workspaces": [{"id": "w", "title": "销售"}]})
        assert advice.call_args.args[2]["workspaces"][0]["id"] == "w"
    injected = [m for m in e._memory.messages if m.get("_prompt_kind") == "jev_context_advice"]
    assert len(injected) == 1 and injected[0]["_ui_hidden"] is True
    request_messages = e._client.chat.completions.create.call_args.kwargs["messages"]
    assert "建议先检查明细" in json.dumps(request_messages, ensure_ascii=False)


def test_chat_request_context_uses_server_workspace_candidates(tmp_path):
    from pydantic import ValidationError
    from excelmanus.api_routes_chat import ChatRequest, _context_input
    request = ChatRequest(message="改这里", sheet_context={"workspace_id": "sales", "path": "sales.xlsx", "range": "B2"})
    manager = SimpleNamespace(list_workspaces=lambda: [{"id": "sales", "title": "销售", "path": "PRIVATE_ROOT"}])
    with patch("excelmanus.api_routes_chat.get_config", return_value=config(tmp_path)), \
         patch("excelmanus.api_routes_chat.get_session_manager", return_value=manager):
        incoming = _context_input(request)
    assert incoming["workspaces"] == [
        {"id": "sales", "title": "销售", "recent": [], "is_default": False}
    ]
    assert "PRIVATE_ROOT" not in json.dumps(incoming, ensure_ascii=False)
    assert incoming["sheet_context"]["range"] == "B2"
    with pytest.raises(ValidationError):
        ChatRequest(message="x", workspaces=[{"id": "invented"}])
    with pytest.raises(ValidationError):
        ChatRequest(message="x", sheet_context={"workspace_id": "sales", "path": "x" * 301})


def test_trace_shows_synthesized_advice_without_candidate_paths():
    from excelmanus.system_one.trace import build_jev_trace_payload
    decision = synthesize(PACK, evaluation(), state())
    trace = build_jev_trace_payload(PACK, decision, gate="shadow", transport="gateway")
    assert trace["answers"]["target"] == "resolved"
    assert trace["answers"]["target_source"] == "active_view"
    assert trace["action"] == "inspect_target"
    assert "sales.xlsx" not in json.dumps(trace)


def _routed_decision(choice="w1"):
    from dataclasses import replace
    return replace(synthesize(PACK, evaluation(choice, "none", "no_edit"), state()), applied=True)


def _route_manager(tmp_path, *, meta=None):
    return SimpleNamespace(
        _chat_history=SimpleNamespace(
            get_session_meta=lambda sid: meta,
            list_sessions=lambda limit=100: [],
            load_affected_files=lambda sid: [],
        ),
        _sessions={},
        _pending_creates=set(),
        default_workspace_binding=lambda: (str(tmp_path), "default"),
        resolve_workspace_binding=Mock(
            side_effect=lambda wid, wp=None: (str(tmp_path / "roots" / wid), wid)
        ),
        rebind_blank_session=Mock(return_value={"id": "s1"}),
        create_or_reuse_session=AsyncMock(return_value={"id": "created-sid"}),
    )


_ROUTE_WORKSPACES = {"workspaces": [{"id": "sales", "title": "销售"},
                                    {"id": "finance", "title": "财务"}]}


@pytest.mark.asyncio
async def test_blank_session_rebinds_to_recommended_workspace(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 1, "message_count": 0,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)
    with patch("excelmanus.system_one.runtime.evaluate_for_host",
               AsyncMock(return_value=_routed_decision("w1"))):
        sid, decision = await route_session_workspace(
            manager, config(tmp_path), "s1", "继续昨天的销售表", _ROUTE_WORKSPACES)
    assert sid == "s1"
    manager.rebind_blank_session.assert_called_once_with("s1", "finance")
    assert decision.extras["routed_workspace"] == "财务"


@pytest.mark.asyncio
async def test_in_progress_session_is_never_repointed(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 0, "message_count": 4,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock()) as evaluate:
        assert await route_session_workspace(
            manager, config(tmp_path), "s1", "继续昨天的销售表", _ROUTE_WORKSPACES) == (None, None)
        evaluate.assert_not_called()
    manager.rebind_blank_session.assert_not_called()


@pytest.mark.asyncio
async def test_live_or_mentioned_sessions_skip_routing(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 1, "message_count": 0,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock()) as evaluate:
        # An explicit @file:/@folder: mention anchors the bound workspace.
        assert await route_session_workspace(
            manager, config(tmp_path), "s1", "改一下 @file:a.xlsx", _ROUTE_WORKSPACES) == (None, None)
        # A session with a live engine is never re-pointed.
        manager._sessions["s1"] = object()
        assert await route_session_workspace(
            manager, config(tmp_path), "s1", "继续昨天的销售表", _ROUTE_WORKSPACES) == (None, None)
        evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_new_session_is_created_inside_recommended_workspace(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    manager = _route_manager(tmp_path)
    with patch("excelmanus.system_one.runtime.evaluate_for_host",
               AsyncMock(return_value=_routed_decision("w1"))):
        sid, decision = await route_session_workspace(
            manager, config(tmp_path), None, "继续昨天的销售表", _ROUTE_WORKSPACES)
    assert sid == "created-sid"
    manager.create_or_reuse_session.assert_awaited_once_with(workspace_id="finance")
    manager.rebind_blank_session.assert_not_called()


@pytest.mark.asyncio
async def test_routing_stays_advice_only_for_current_or_unknown(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 1, "message_count": 0,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)
    with patch("excelmanus.system_one.runtime.evaluate_for_host",
               AsyncMock(return_value=_routed_decision("current"))):
        sid, decision = await route_session_workspace(
            manager, config(tmp_path), "s1", "改这里", _ROUTE_WORKSPACES)
    assert sid is None and decision is not None
    manager.rebind_blank_session.assert_not_called()
    # A recommendation pointing at the already-bound workspace is not a route.
    with patch("excelmanus.system_one.runtime.evaluate_for_host",
               AsyncMock(return_value=_routed_decision("w0"))):
        sid, _ = await route_session_workspace(
            manager, config(tmp_path), "s1", "改这里", _ROUTE_WORKSPACES)
    assert sid is None
    # A workspace that no longer resolves cannot be routed to.
    from excelmanus.stores.workspace_store import WorkspacePathError
    manager.resolve_workspace_binding = Mock(side_effect=WorkspacePathError("工作区不存在"))
    with patch("excelmanus.system_one.runtime.evaluate_for_host",
               AsyncMock(return_value=_routed_decision("w1"))):
        sid, decision = await route_session_workspace(
            manager, config(tmp_path), "s1", "继续昨天的销售表", _ROUTE_WORKSPACES)
    assert sid is None
    manager.rebind_blank_session.assert_not_called()


@pytest.mark.asyncio
async def test_off_gate_never_routes(tmp_path):
    # 二态契约：总闸 off 时不评估也不改路由。
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 1, "message_count": 0,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)
    with patch("excelmanus.system_one.runtime.evaluate_for_host", AsyncMock()) as evaluate:
        assert await route_session_workspace(
            manager, config(tmp_path, jev_enabled="off"),
            "s1", "继续昨天的销售表", _ROUTE_WORKSPACES) == (None, None)
        evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_routing_failure_returns_timeout_decision(tmp_path):
    from excelmanus.system_one.intent_context import route_session_workspace
    meta = {"id": "s1", "blank": 1, "message_count": 0,
            "workspace_path": str(tmp_path), "workspace_id": "sales"}
    manager = _route_manager(tmp_path, meta=meta)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    with patch("excelmanus.system_one.intent_context.MAX_CONTEXT_SECONDS", .01), \
         patch("excelmanus.system_one.runtime.evaluate_for_host", side_effect=slow):
        sid, decision = await route_session_workspace(
            manager, config(tmp_path), "s1", "继续昨天的销售表", _ROUTE_WORKSPACES)
    assert sid is None
    assert decision.reason == "unavailable:context_timeout"


def test_workspace_activity_index_groups_titles_and_files(tmp_path):
    from excelmanus.api_routes_chat import _workspace_activity_index
    sales_dir = tmp_path / "sales"
    finance_dir = tmp_path / "finance"
    history = SimpleNamespace(
        list_sessions=lambda limit=100: [
            {"id": "s1", "workspace_id": "sales", "title": "月度对账"},
            {"id": "s2", "workspace_id": "", "workspace_path": str(finance_dir), "title": "财务处理"},
            {"id": "s3", "workspace_id": "ghost", "title": "不在登记列表"},
        ],
        load_affected_files=lambda sid: {
            "s1": ["./sales.xlsx", "./dir/明细.xlsx"],
            "s2": ["./f.xlsx"],
        }.get(sid, []),
    )
    manager = SimpleNamespace(_chat_history=history)
    rows = [{"id": "sales", "path": str(sales_dir)},
            {"id": "finance", "path": str(finance_dir)}]
    index = _workspace_activity_index(manager, rows)
    assert index["sales"] == ["会话:月度对账", "文件:sales.xlsx", "文件:明细.xlsx"]
    assert index["finance"] == ["会话:财务处理", "文件:f.xlsx"]
    assert index["ghost"] == ["会话:不在登记列表"]
    assert _workspace_activity_index(SimpleNamespace(), rows) == {}


def test_session_workspace_fields_report_routed_binding(tmp_path):
    from dataclasses import replace
    from excelmanus.api_routes_chat import _session_workspace_fields
    manager = SimpleNamespace(
        workspace_path_for_session=lambda sid: str(tmp_path / "销售"),
        workspace_id_for_session=lambda sid: "sales",
    )
    routed = replace(_routed_decision("w1"), extras={"routed_workspace": "销售"})
    with patch("excelmanus.api_routes_chat.get_session_manager", return_value=manager):
        fields = _session_workspace_fields("s1", routed)
    assert fields == {"workspace_id": "sales", "workspace_path": str(tmp_path / "销售"),
                      "workspace_title": "销售", "workspace_routed": True}
    with patch("excelmanus.api_routes_chat.get_session_manager", return_value=manager):
        assert _session_workspace_fields("s1")["workspace_routed"] is False

