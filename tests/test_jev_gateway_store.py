"""Gateway 密钥：主库加密、GET 脱敏、overlay / 主库为唯一来源。禁止打印全文。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from excelmanus.api_app_state import set_config_store
from excelmanus.database import Database
from excelmanus.security.cipher import TokenCipher
from excelmanus.settings_persist import persist_settings
from excelmanus.settings_runtime import reset_runtime_settings
from excelmanus.stores.config_store import (
    ENCRYPTED_CONFIG_KV_KEYS,
    GlobalConfigStore,
    UserConfigStore,
)
from excelmanus.system_one.client import resolve_jev_api_key


FAKE_KEY = "vck_" + ("x" * 40)
STORE_KEY = "EXCELMANUS_AI_GATEWAY_API_KEY"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv(STORE_KEY, raising=False)
    monkeypatch.delenv("EXCELMANUS_TYPESAFE_API_KEY", raising=False)
    reset_runtime_settings()
    yield home
    reset_runtime_settings()
    set_config_store(None)


def test_gateway_key_encrypted_at_rest(isolated_home: Path) -> None:
    db = Database(str(isolated_home / "settings.db"))
    store = GlobalConfigStore(db)
    set_config_store(store)
    try:
        persist_settings({STORE_KEY: FAKE_KEY})
        assert store.get(STORE_KEY) == FAKE_KEY
        raw = db.conn.execute(
            "SELECT value FROM config_kv WHERE key = ?", (STORE_KEY,),
        ).fetchone()
        assert raw is not None
        ciphertext = str(raw["value"])
        assert ciphertext != FAKE_KEY
        assert ciphertext.startswith("gAAAA")
        assert FAKE_KEY not in ciphertext
        assert TokenCipher().decrypt_or_passthrough(ciphertext) == FAKE_KEY
        key, name = resolve_jev_api_key()
        assert name == STORE_KEY
        assert key is not None
        assert key.startswith("vck_")
        assert len(key) == len(FAKE_KEY)
    finally:
        set_config_store(None)


def test_user_store_roundtrip_decrypts(isolated_home: Path) -> None:
    db = Database(str(isolated_home / "user.db"))
    user = UserConfigStore(db)
    user.set(STORE_KEY, FAKE_KEY)
    assert user.get(STORE_KEY) == FAKE_KEY
    raw = db.conn.execute(
        "SELECT value FROM config_kv WHERE key = ?", (STORE_KEY,),
    ).fetchone()
    assert str(raw["value"]).startswith("gAAAA")


def test_store_wins_over_process_env(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(str(isolated_home / "settings.db"))
    store = GlobalConfigStore(db)
    set_config_store(store)
    try:
        persist_settings({STORE_KEY: FAKE_KEY})
        monkeypatch.setenv("AI_GATEWAY_API_KEY", "vck_" + ("e" * 40))
        key, name = resolve_jev_api_key()
        assert name == STORE_KEY
        assert key == FAKE_KEY
    finally:
        set_config_store(None)


def test_empty_env_does_not_hide_store_key(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(str(isolated_home / "settings.db"))
    store = GlobalConfigStore(db)
    set_config_store(store)
    try:
        persist_settings({STORE_KEY: FAKE_KEY})
        monkeypatch.setenv("AI_GATEWAY_API_KEY", "")
        monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
        key, name = resolve_jev_api_key()
        assert name == STORE_KEY
        assert key is not None
        assert key.startswith("vck_")
        assert len(key) == len(FAKE_KEY)
    finally:
        set_config_store(None)


def test_process_env_is_not_a_settings_source(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(str(isolated_home / "settings.db"))
    set_config_store(GlobalConfigStore(db))
    try:
        monkeypatch.setenv("AI_GATEWAY_API_KEY", FAKE_KEY)
        monkeypatch.setenv(STORE_KEY, FAKE_KEY)
        key, name = resolve_jev_api_key()
        assert key is None
        assert name == ""
    finally:
        set_config_store(None)


def test_restart_reads_encrypted_store_without_overlay(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database(str(isolated_home / "settings.db"))
    store = GlobalConfigStore(db)
    store.set(STORE_KEY, FAKE_KEY)
    reset_runtime_settings()
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    set_config_store(store)
    key, name = resolve_jev_api_key()
    assert name == STORE_KEY
    assert key is not None
    assert key.startswith("vck_")
    assert len(key) == len(FAKE_KEY)


def test_explicit_values_ignore_process_env(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_GATEWAY_API_KEY", FAKE_KEY)
    key, name = resolve_jev_api_key({})
    assert key is None
    assert name == ""


def test_runtime_get_returns_configured_and_last4_only() -> None:
    from excelmanus.api_routes_config import _secret_status

    status = _secret_status(FAKE_KEY)
    assert status["configured"] is True
    assert status["last4"] == FAKE_KEY[-4:]
    dumped = str(status)
    assert FAKE_KEY not in dumped
    assert "vck_" not in dumped
    empty = _secret_status(None)
    assert empty == {"configured": False, "last4": ""}


def test_runtime_put_skips_masked_gateway_key(isolated_home: Path) -> None:
    from excelmanus.api_routes_config import RuntimeConfigUpdate

    payload = RuntimeConfigUpdate(ai_gateway_api_key="****abcd").model_dump(exclude_none=True)
    assert "*" in payload["ai_gateway_api_key"]
    _API_KEY_FIELDS = {"ai_gateway_api_key"}
    for ak_field in _API_KEY_FIELDS:
        val = payload.get(ak_field)
        if isinstance(val, str) and ("*" in val or val == ""):
            payload.pop(ak_field, None)
    assert "ai_gateway_api_key" not in payload


def test_encrypted_kv_keys_are_not_chat_model_profiles() -> None:
    assert STORE_KEY in ENCRYPTED_CONFIG_KV_KEYS
    assert "EXCELMANUS_TYPESAFE_API_KEY" in ENCRYPTED_CONFIG_KV_KEYS
    assert "EXCELMANUS_JEV_PROVIDERS" in ENCRYPTED_CONFIG_KV_KEYS
    assert "api_key" not in ENCRYPTED_CONFIG_KV_KEYS


def test_settings_from_prefers_ai_gateway_field() -> None:
    from excelmanus.system_one.policy import settings_from

    cfg = SimpleNamespace(
        jev_enabled="off",
        jev_exposure="off",
        jev_mode_hint=False,
        jev_present_as_auto=False,
        jev_observation="off",
        jev_ui_hint=False,
        jev_model="jev-1.13.0",
        typesafe_api_key="ts_direct_not_used",
        ai_gateway_api_key=FAKE_KEY,
        jev_timeout_seconds=1.5,
        jev_calibrated=False,
    )
    parsed = settings_from(cfg)
    assert parsed.api_key == FAKE_KEY
