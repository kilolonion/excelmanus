"""新手引导进度跨浏览器持久化。"""

from __future__ import annotations

from pathlib import Path

from excelmanus.database import Database
from excelmanus.onboarding_state import (
    ONBOARDING_KEY,
    default_onboarding_state,
    load_onboarding_state,
    normalize_onboarding_state,
    save_onboarding_state,
)
from excelmanus.stores.config_store import UserConfigStore


def test_normalize_fills_defaults_and_clamps() -> None:
    state = normalize_onboarding_state(
        {
            "wizard_completed": True,
            "coach_phase": "nope",
            "coach_step_index": -3,
            "skipped_at": "  ",
        }
    )
    assert state["wizard_completed"] is True
    assert state["coach_marks_completed"] is False
    assert state["coach_phase"] == "basic"
    assert state["coach_step_index"] == 0
    assert state["skipped_at"] is None


def test_load_without_record_infers_wizard_when_configured() -> None:
    class EmptyStore:
        def get(self, key: str, default: str = "") -> str:
            return default

    inferred = load_onboarding_state(EmptyStore(), configured=True)
    assert inferred["wizard_completed"] is True
    assert inferred["coach_marks_completed"] is False

    fresh = load_onboarding_state(EmptyStore(), configured=False)
    assert fresh == default_onboarding_state()
    assert load_onboarding_state(None, configured=True)["wizard_completed"] is True


def test_save_roundtrip_in_config_kv(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "onboarding.db"))
    store = UserConfigStore(db.conn)
    saved = save_onboarding_state(
        store,
        {
            "wizard_completed": True,
            "coach_marks_completed": True,
            "coach_phase": "advanced",
            "coach_step_index": 2,
            "skipped_at": "2026-09-12T00:00:00Z",
        },
    )
    assert store.get(ONBOARDING_KEY)
    loaded = load_onboarding_state(store, configured=False)
    assert loaded == saved
    assert loaded["wizard_completed"] is True
    assert loaded["coach_phase"] == "advanced"
    assert loaded["coach_step_index"] == 2


def test_invalid_json_falls_back_to_configured_inference(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "onboarding.db"))
    store = UserConfigStore(db.conn)
    store.set(ONBOARDING_KEY, "{not-json")
    state = load_onboarding_state(store, configured=True)
    assert state["wizard_completed"] is True
    assert state["coach_marks_completed"] is False


def test_stored_incomplete_wizard_wins_over_configured_inference(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "onboarding.db"))
    store = UserConfigStore(db.conn)
    save_onboarding_state(store, {"wizard_completed": False, "coach_phase": "basic"})
    loaded = load_onboarding_state(store, configured=True)
    assert loaded["wizard_completed"] is False
