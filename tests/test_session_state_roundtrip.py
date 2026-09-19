"""SessionState / 会话快照往返：pin、fingerprint、generation、wire_epoch 恢复而非重算。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from excelmanus.engine_core.session_state import (
    SessionState,
    snapshot_wire_epoch,
)


def _epoch_payload(**overrides: object) -> dict[str, str]:
    data = {
        "key": "em_sess-rt-0123456789abcdef",
        "session_id": "sess-rt",
        "model": "gpt-test",
        "protocol": "openai",
        "call_config_digest": "cc-digest",
        "tools_digest": "tools-digest",
        "system_digest": "sys-digest",
        "catalog_digest": "cat-digest",
        "wire_digest": "wire-digest",
    }
    data.update({key: str(value) for key, value in overrides.items()})
    return data


class _SnapshotEpoch:
    def __init__(self, payload: dict[str, str]) -> None:
        self.session_id = payload["session_id"]
        self.model = payload["model"]
        self.protocol = payload["protocol"]
        self.call_config_digest = payload["call_config_digest"]
        self.tools_digest = payload["tools_digest"]
        self.system_digest = payload["system_digest"]
        self.catalog_digest = payload["catalog_digest"]
        self.wire_digest = payload["wire_digest"]
        self._key = payload["key"]

    def key(self) -> str:
        return self._key


def _require_a1_epoch():
    from excelmanus.prompt import envelope as env

    required = (
        "EpochIdentity",
        "compute_epoch_identity",
        "epoch_changed",
        "assert_wire_prefix_stable",
    )
    missing = [name for name in required if not hasattr(env, name)]
    if missing:
        pytest.fail(
            "阻塞于 A1 接口: excelmanus.prompt.envelope 缺少 " + ", ".join(missing)
        )
    return env


def _epoch_engine(**overrides: object) -> SimpleNamespace:
    engine = SimpleNamespace(
        _session_id="sess-rt",
        _wire_epoch=None,
        _wire_digest_payload=None,
        _wire_epoch_needs_restore=False,
        _last_envelope=SimpleNamespace(epoch=None),
        _last_wire_messages=None,
    )
    for key, value in overrides.items():
        setattr(engine, key, value)
    return engine


def _sealed_envelope(*, wire: list, system: str = "SYS", tools: list | None = None):
    return SimpleNamespace(
        wire_messages=list(wire),
        digest_payload=None,
        epoch=None,
        tools=list(tools or []),
        system=system,
        system_head=system,
        identity=SimpleNamespace(catalog_digest="cat-digest"),
        prompt_cache_key="em_sess-rt",
    )


def test_session_state_to_dict_from_dict_roundtrip() -> None:
    state = SessionState()
    state.session_turn = 4
    state.image_wire_pin_seq = ("pin-a", "pin-b")
    state.injected_context_fingerprint = "fp-12ab"
    state.compaction_generation = 9
    state.wire_epoch = _epoch_payload()
    state.request_series = {"series_id": "s1", "last_accepted": None, "events": [], "pending": []}

    restored = SessionState.from_dict(state.to_dict())
    assert restored.request_series == state.request_series
    assert restored.image_wire_pin_seq == ("pin-a", "pin-b")
    assert restored.injected_context_fingerprint == "fp-12ab"
    assert restored.compaction_generation == 9
    assert restored.wire_epoch is not None
    assert restored.wire_epoch["key"] == "em_sess-rt-0123456789abcdef"
    assert restored.wire_epoch["model"] == "gpt-test"
    assert restored.wire_epoch["tools_digest"] == "tools-digest"
    assert restored.session_turn == 4


def test_legacy_snapshot_missing_fields_uses_defaults() -> None:
    restored = SessionState.from_dict({
        "session_turn": 2,
        "has_write_tool_call": True,
        "current_write_hint": "may_write",
    })
    assert restored.session_turn == 2
    assert restored.has_write_tool_call is True
    assert restored.image_wire_pin_seq == ()
    assert restored.injected_context_fingerprint is None
    assert restored.compaction_generation == 0
    assert restored.wire_epoch is None
    assert restored.request_series is None
    assert restored.present_as == "native"


def test_snapshot_wire_epoch_reads_key_method() -> None:
    payload = _epoch_payload()
    snapped = snapshot_wire_epoch(_SnapshotEpoch(payload))
    assert snapped == payload


def test_engine_snapshot_restores_pin_fingerprint_generation_epoch(tmp_path) -> None:
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig
    from excelmanus.database import Database
    from excelmanus.prompt.envelope import compute_epoch_identity
    from excelmanus.tools.registry import ToolRegistry

    db = Database(str(tmp_path / "roundtrip.db"))
    cfg = ExcelManusConfig(
        api_key="t",
        base_url="https://x.example/v1",
        model="test-model",
        workspace_root=str(tmp_path),
    )
    epoch = compute_epoch_identity(
        session_id="sess-engine",
        model="test-model",
        protocol="openai",
        call_config={"temperature": 0},
        tools=[],
        system="SYS",
        catalog_digest="cat-digest",
        wire_payload=[{"role": "user", "content": "hi"}],
    )
    engine = AgentEngine(config=cfg, registry=ToolRegistry(), database=db)
    engine._session_id = "sess-engine"
    engine._image_wire_pin_seq = ("att-1", "att-2")
    engine._injected_context_fingerprint = "fp-restored"
    engine._compaction_generation = 6
    engine._wire_epoch = epoch
    from excelmanus.request.series import RequestSeries, series_of
    from excelmanus.request.types import RequestHeader

    series = series_of(engine)
    series.accept(RequestHeader(
        route_fingerprint="rf",
        tools_digest="td",
        catalog_digest="cat-digest",
        system_head_digest="sys",
        content_identity="cid",
        content_payload='{"role":"user","content":"hi"}\n',
        cache_policy_digest="cp",
        transport="inline",
        prompt_cache_key="em_sess-engine",
    ))
    engine.save_session_snapshot()

    restored_engine = AgentEngine(config=cfg, registry=ToolRegistry(), database=db)
    restored_engine._session_id = "sess-engine"
    assert restored_engine.restore_session_snapshot() is True
    assert restored_engine._image_wire_pin_seq == ("att-1", "att-2")
    assert restored_engine._injected_context_fingerprint == "fp-restored"
    assert restored_engine.state.injected_context_fingerprint == "fp-restored"
    assert restored_engine._compaction_generation == 6
    assert restored_engine._memory._compaction_generation == 6
    restored_epoch = restored_engine._wire_epoch
    assert restored_epoch is not None
    assert restored_epoch.key() == epoch.key()
    assert restored_engine._state.wire_epoch is not None
    assert restored_engine._state.wire_epoch["key"] == epoch.key()
    assert restored_engine._wire_epoch_needs_restore is False
    assert restored_engine._request_series.last_accepted is not None
    assert restored_engine._request_series.last_accepted.content_identity == "cid"
    db.close()


def test_session_state_store_roundtrip_keeps_new_fields(tmp_path) -> None:
    from excelmanus.database import Database
    from excelmanus.stores.session_state_store import SessionStateStore

    db = Database(str(tmp_path / "store.db"))
    store = SessionStateStore(db)
    state = SessionState()
    state.image_wire_pin_seq = ("p1",)
    state.injected_context_fingerprint = "fp-store"
    state.compaction_generation = 3
    state.wire_epoch = _epoch_payload(key="em_s-store-aaaaaaaaaaaaaaaa")
    store.save_session_snapshot(
        session_id="s-store",
        state_dict=state.to_dict(),
        task_list_dict={"tasks": []},
        turn_number=1,
    )
    loaded = store.load_latest_checkpoint("s-store")
    assert loaded is not None
    restored = SessionState.from_dict(loaded["state_dict"])
    assert restored.image_wire_pin_seq == ("p1",)
    assert restored.injected_context_fingerprint == "fp-store"
    assert restored.compaction_generation == 3
    assert restored.wire_epoch is not None
    assert restored.wire_epoch["key"] == "em_s-store-aaaaaaaaaaaaaaaa"
    db.close()


def test_epoch_change_opens_new_series_without_fail_closed(caplog) -> None:
    _require_a1_epoch()
    from excelmanus.agent.loop import apply_outbound_epoch
    from excelmanus.request.compiler import header_from_sealed
    from excelmanus.request.series import series_of

    first_wire = [{"role": "user", "content": "hello"}]
    engine = _epoch_engine(
        _last_wire_messages=first_wire,
        _session_id="sess-rt",
        _active_model="model-a",
        _active_base_url="https://x.example/v1",
        _active_api_key="k",
        _config=SimpleNamespace(model="model-a", base_url="https://x.example/v1", api_key="k"),
    )
    first_env = _sealed_envelope(wire=first_wire)
    first, first_epoch, first_err = apply_outbound_epoch(
        engine,
        first_env,
        model="model-a",
        protocol="openai",
        call_config={"vision_capable": False},
    )
    assert first_err is None
    assert first == first_wire
    assert first_epoch is not None
    series_of(engine).accept(header_from_sealed(engine, first_env))

    second_wire = first_wire + [{"role": "assistant", "content": "ok"}]
    engine._active_model = "model-b"
    engine._config.model = "model-b"
    second, second_epoch, second_err = apply_outbound_epoch(
        engine,
        _sealed_envelope(wire=second_wire),
        model="model-b",
        protocol="openai",
        call_config={"vision_capable": False},
    )
    assert second_err is None
    assert second == second_wire
    assert second_epoch is not None
    assert second_epoch.key() != first_epoch.key()
    assert series_of(engine).last_accepted is None


def test_same_epoch_prefix_break_fail_closed() -> None:
    _require_a1_epoch()
    from excelmanus.agent.loop import apply_outbound_epoch
    from excelmanus.request.compiler import header_from_sealed
    from excelmanus.request.series import series_of

    first_wire = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ok"},
    ]
    engine = _epoch_engine(
        _last_wire_messages=first_wire,
        _session_id="sess-rt",
        _active_model="model-a",
        _active_base_url="https://x.example/v1",
        _active_api_key="k",
        _config=SimpleNamespace(model="model-a", base_url="https://x.example/v1", api_key="k"),
    )
    first_env = _sealed_envelope(wire=first_wire)
    _, first_epoch, first_err = apply_outbound_epoch(
        engine,
        first_env,
        model="model-a",
        protocol="openai",
        call_config={"vision_capable": False},
    )
    assert first_err is None
    assert first_epoch is not None
    accepted = header_from_sealed(engine, first_env)
    series_of(engine).accept(accepted)

    broken = [{"role": "user", "content": "rewritten"}]
    _, _, broken_err = apply_outbound_epoch(
        engine,
        _sealed_envelope(wire=broken),
        model="model-a",
        protocol="openai",
        call_config={"vision_capable": False},
    )
    assert isinstance(broken_err, str) and broken_err
    assert series_of(engine).last_accepted == accepted
