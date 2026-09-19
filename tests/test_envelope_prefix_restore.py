"""重启 / 跨 worker 重建后信封前缀等值校验（只告警，不 fail-closed）。"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from excelmanus.engine_core.session_state import SessionState
from excelmanus.prompt.cache_restore import (
    PREFIX_STATE_KEY,
    attach_prefix_to_state_dict,
    capture_prefix_snapshot,
    configured_web_workers,
    extract_restored_prefix,
    multi_worker_cache_warning,
    prefix_drift_fields,
    remember_prefix_snapshot,
    warn_if_restored_prefix_drifted,
)
from excelmanus.prompt.envelope import assemble_envelope
from tests.test_request_envelope import _engine


def _snapshot(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "tools_digest": "tools-aaaaaaa",
        "system_digest": "system-bbbbbb",
        "catalog_digest": "catalog-cccc",
        "skill_names": ["xlsx_master"],
    }
    data.update(overrides)
    return data


def test_prefix_drift_fields_empty_when_equal() -> None:
    snap = _snapshot()
    assert prefix_drift_fields(snap, dict(snap)) == []


def test_prefix_drift_fields_reports_tools_system_catalog_skills() -> None:
    left = _snapshot()
    right = _snapshot(
        tools_digest="tools-changed",
        system_digest="system-changed",
        catalog_digest="catalog-changed",
        skill_names=[],
    )
    assert prefix_drift_fields(left, right) == [
        "tools_digest",
        "system_digest",
        "catalog_digest",
        "skill_names",
    ]


def test_prefix_drift_fields_corrupt_snapshot_is_fail_open() -> None:
    assert prefix_drift_fields(None, _snapshot()) == []
    assert prefix_drift_fields({"tools_digest": 1}, _snapshot()) == []


def test_session_state_store_roundtrip_keeps_envelope_prefix(tmp_path) -> None:
    from excelmanus.database import Database
    from excelmanus.stores.session_state_store import SessionStateStore

    db = Database(str(tmp_path / "prefix.db"))
    store = SessionStateStore(db)
    state_dict = attach_prefix_to_state_dict(SessionState().to_dict(), _snapshot())
    store.save_session_snapshot(
        session_id="s-restore",
        state_dict=state_dict,
        task_list_dict={"tasks": []},
        turn_number=3,
    )
    loaded = store.load_latest_checkpoint("s-restore")
    assert loaded is not None
    restored = extract_restored_prefix(loaded["state_dict"])
    assert restored is not None
    assert restored["tools_digest"] == "tools-aaaaaaa"
    assert restored["skill_names"] == ["xlsx_master"]
    db.close()


def test_attach_and_extract_roundtrip_via_session_state_dict() -> None:
    state_dict = SessionState().to_dict()
    attached = attach_prefix_to_state_dict(state_dict, _snapshot())
    assert PREFIX_STATE_KEY in attached
    restored = extract_restored_prefix(attached)
    assert restored == {
        "tools_digest": "tools-aaaaaaa",
        "system_digest": "system-bbbbbb",
        "catalog_digest": "catalog-cccc",
        "skill_names": ["xlsx_master"],
    }
    assert extract_restored_prefix(SessionState().to_dict()) is None
    SessionState.from_dict(attached)


def test_engine_snapshot_roundtrip_restores_prefix(tmp_path) -> None:
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig
    from excelmanus.database import Database
    from excelmanus.tools.registry import ToolRegistry

    db = Database(str(tmp_path / "engine.db"))
    cfg = ExcelManusConfig(
        api_key="t",
        base_url="https://x.example/v1",
        model="test-model",
        workspace_root=str(tmp_path),
    )
    engine = AgentEngine(config=cfg, registry=ToolRegistry(), database=db)
    engine._session_id = "sess-engine"
    engine._envelope_prefix_snapshot = _snapshot()
    engine.save_session_snapshot()

    restored_engine = AgentEngine(config=cfg, registry=ToolRegistry(), database=db)
    restored_engine._session_id = "sess-engine"
    assert restored_engine.restore_session_snapshot() is True
    assert restored_engine._restored_envelope_prefix is not None
    assert restored_engine._restored_envelope_prefix["tools_digest"] == "tools-aaaaaaa"
    db.close()


def test_warn_if_restored_prefix_drifted_logs_once_and_does_not_raise(
    caplog,
) -> None:
    engine = SimpleNamespace(
        _session_id="sess-restore",
        _restored_envelope_prefix=_snapshot(tools_digest="old-tools"),
        _active_skills=[SimpleNamespace(name="xlsx_master")],
    )
    envelope = SimpleNamespace(
        identity=SimpleNamespace(
            tools_digest="new-tools",
            system_digest="system-bbbbbb",
            catalog_digest="catalog-cccc",
        )
    )
    with caplog.at_level(logging.WARNING, logger="excelmanus.prompt.cache_restore"):
        drifted = warn_if_restored_prefix_drifted(engine, envelope)
    assert drifted is True
    assert "sess-restore" in caplog.text
    assert "tools_digest" in caplog.text
    assert engine._restored_envelope_prefix is None
    # 第二次不再告警（已消费）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="excelmanus.prompt.cache_restore"):
        assert warn_if_restored_prefix_drifted(engine, envelope) is False
    assert caplog.text == ""


def test_warn_skipped_when_prefix_matches(caplog) -> None:
    snap = _snapshot()
    engine = SimpleNamespace(
        _session_id="sess-ok",
        _restored_envelope_prefix=snap,
        _active_skills=[SimpleNamespace(name="xlsx_master")],
    )
    envelope = SimpleNamespace(
        identity=SimpleNamespace(
            tools_digest=snap["tools_digest"],
            system_digest=snap["system_digest"],
            catalog_digest=snap["catalog_digest"],
        )
    )
    with caplog.at_level(logging.WARNING, logger="excelmanus.prompt.cache_restore"):
        assert warn_if_restored_prefix_drifted(engine, envelope) is False
    assert caplog.text == ""
    assert engine._restored_envelope_prefix is None


def test_remember_prefix_snapshot_records_tools_and_system() -> None:
    engine = SimpleNamespace(_active_skills=[SimpleNamespace(name="skill-a")])
    envelope = SimpleNamespace(
        identity=SimpleNamespace(
            tools_digest="t1",
            system_digest="s1",
            catalog_digest="c1",
        )
    )
    remember_prefix_snapshot(engine, envelope)
    assert engine._envelope_prefix_snapshot == capture_prefix_snapshot(envelope, engine)
    assert engine._envelope_prefix_snapshot["skill_names"] == ["skill-a"]


def test_assemble_rebuild_warns_on_drift_but_still_returns_envelope(caplog) -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    engine._restored_envelope_prefix = _snapshot(
        tools_digest="stale-tools",
        system_digest="stale-system",
        catalog_digest="stale-catalog",
        skill_names=["gone"],
    )
    with caplog.at_level(logging.WARNING, logger="excelmanus.prompt.cache_restore"):
        envelope, err = assemble_envelope(engine)
    assert err is None and envelope is not None
    assert "重建后信封前缀" in caplog.text
    assert engine._restored_envelope_prefix is None
    assert engine._envelope_prefix_snapshot is not None
    assert engine._envelope_prefix_snapshot["tools_digest"] == envelope.identity.tools_digest


def test_assemble_rebuild_silent_when_digest_matches(caplog) -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    first, err1 = assemble_envelope(engine)
    assert err1 is None and first is not None
    remembered = dict(engine._envelope_prefix_snapshot)
    engine._last_envelope = None
    engine._envelope_system_head = None
    engine._restored_envelope_prefix = remembered
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="excelmanus.prompt.cache_restore"):
        second, err2 = assemble_envelope(engine)
    assert err2 is None and second is not None
    assert "重建后信封前缀" not in caplog.text


def test_assemble_persist_false_does_not_consume_restored_prefix() -> None:
    engine = _engine()
    engine.memory.add_user_message("hello")
    stale = _snapshot(tools_digest="stale-tools")
    engine._restored_envelope_prefix = stale
    envelope, err = assemble_envelope(engine, persist=False)
    assert err is None and envelope is not None
    assert engine._restored_envelope_prefix == stale
    snapshot = getattr(engine, "_envelope_prefix_snapshot", None)
    assert not isinstance(snapshot, dict)


def test_multi_worker_cache_warning_only_when_workers_gt_one() -> None:
    assert multi_worker_cache_warning(1) is None
    assert multi_worker_cache_warning(0) is None
    text = multi_worker_cache_warning(4)
    assert text is not None
    assert "4" in text
    assert "prompt cache" in text.lower() or "缓存" in text


def test_configured_web_workers_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("EXCELMANUS_WEB_WORKERS", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    assert configured_web_workers() == 1
    monkeypatch.setenv("EXCELMANUS_WEB_WORKERS", "4")
    assert configured_web_workers() == 4
    monkeypatch.delenv("EXCELMANUS_WEB_WORKERS", raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    assert configured_web_workers() == 2
