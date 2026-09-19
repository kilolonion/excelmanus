"""配置 / 密钥持久化：主库为唯一设置仓。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from excelmanus.api_app_state import set_config_store
from excelmanus.config import load_config, load_runtime_env
from excelmanus.data_home import (
    get_excelmanus_home,
    get_secret_key_path,
    resolve_db_path,
)
from excelmanus.database import Database
from excelmanus.security.cipher import TokenCipher, derive_fernet_key
from excelmanus.settings_persist import (
    apply_settings_from_store,
    persist_settings,
)
from excelmanus.stores.config_store import GlobalConfigStore


def test_persist_settings_writes_database_not_env_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    db = Database(str(tmp_path / "settings.db"))
    store = GlobalConfigStore(db)
    set_config_store(store)
    try:
        persist_settings({"EXCELMANUS_EXA_API_KEY": "exa-secret"})
        assert store.get("EXCELMANUS_EXA_API_KEY") == "exa-secret"
        from excelmanus.settings_runtime import get_setting

        assert get_setting("EXCELMANUS_EXA_API_KEY") == "exa-secret"
        assert os.environ.get("EXCELMANUS_EXA_API_KEY") in (None, "")
        assert not (home / "config.env").is_file()
        assert not (tmp_path / ".env").is_file()
    finally:
        set_config_store(None)


def test_persist_settings_skips_locator_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path / "home"))
    db = Database(str(tmp_path / "settings.db"))
    store = GlobalConfigStore(db)
    set_config_store(store)
    try:
        persist_settings({
            "EXCELMANUS_HOME": str(tmp_path / "should-not-store"),
            "EXCELMANUS_JEV_ENABLED": "shadow",
        })
        assert store.get("EXCELMANUS_HOME") in (None, "")
        assert store.get("EXCELMANUS_JEV_ENABLED") == "shadow"
        from excelmanus.settings_runtime import get_setting

        assert get_setting("EXCELMANUS_JEV_ENABLED") == "shadow"
        assert get_setting("EXCELMANUS_HOME") is None
    finally:
        set_config_store(None)


def test_empty_process_env_does_not_hide_database_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(work)
    db = Database(str(tmp_path / "settings.db"))
    store = GlobalConfigStore(db)
    store.set("EXCELMANUS_API_KEY", "from-db")
    store.set("EXCELMANUS_BASE_URL", "https://example.com/v1")
    store.set("EXCELMANUS_MODEL", "from-db-model")
    monkeypatch.setenv("EXCELMANUS_API_KEY", "")
    monkeypatch.delenv("EXCELMANUS_BASE_URL", raising=False)
    monkeypatch.delenv("EXCELMANUS_MODEL", raising=False)
    apply_settings_from_store(store)
    cfg = load_config()
    assert cfg.api_key == "from-db"
    assert cfg.model == "from-db-model"


def test_cwd_dotenv_is_ignored_for_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(work)
    (work / ".env").write_text(
        "EXCELMANUS_API_KEY=from-cwd\n"
        "EXCELMANUS_BASE_URL=https://example.com/v1\n"
        "EXCELMANUS_MODEL=cwd-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXCELMANUS_API_KEY", raising=False)
    load_runtime_env()
    assert os.environ.get("EXCELMANUS_API_KEY") in (None, "")


def test_persist_settings_does_not_write_package_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excelmanus.data_home import get_package_root

    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXCELMANUS_MODEL", raising=False)
    package_env = get_package_root() / ".env"
    before = package_env.read_text(encoding="utf-8") if package_env.is_file() else None
    persist_settings({"EXCELMANUS_MODEL": "should-not-leak"})
    after = package_env.read_text(encoding="utf-8") if package_env.is_file() else None
    assert after == before
    assert "should-not-leak" not in (after or "")


def test_persist_settings_does_not_sync_cwd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("EXCELMANUS_MODEL=old-model\n", encoding="utf-8")
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(work)
    db = Database(str(tmp_path / "settings.db"))
    set_config_store(GlobalConfigStore(db))
    try:
        persist_settings({"EXCELMANUS_MODEL": "new-model"})
        leftover = (work / ".env").read_text(encoding="utf-8")
        assert "old-model" in leftover
        assert "new-model" not in leftover
    finally:
        set_config_store(None)


def test_leftover_config_env_is_not_hydrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    (home / "config.env").write_text(
        "EXCELMANUS_MAX_ITERATIONS=42\nEXCELMANUS_HOME=/should-not-copy\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("EXCELMANUS_MAX_ITERATIONS", raising=False)
    db = Database(str(tmp_path / "settings.db"))
    store = GlobalConfigStore(db)
    from excelmanus.settings_persist import hydrate_runtime_settings

    assert hydrate_runtime_settings(store) == 0
    assert store.get("EXCELMANUS_MAX_ITERATIONS") == ""
    leftover = (home / "config.env").read_text(encoding="utf-8")
    assert "EXCELMANUS_MAX_ITERATIONS=42" in leftover


def test_resolve_db_path_uses_legacy_only_when_primary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXCELMANUS_DB_PATH", raising=False)
    monkeypatch.setenv("EXCELMANUS_CHAT_HISTORY_DB_PATH", str(tmp_path / "legacy.db"))
    assert resolve_db_path() == str(tmp_path / "legacy.db")
    monkeypatch.setenv("EXCELMANUS_DB_PATH", str(tmp_path / "main.db"))
    assert resolve_db_path() == str(tmp_path / "main.db")


def test_fernet_key_lives_under_excelmanus_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "vol"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("EXCELMANUS_DATA_ROOT", raising=False)
    key = derive_fernet_key()
    assert key
    assert get_secret_key_path() == home / ".secret_key"
    assert get_secret_key_path().is_file()
    assert derive_fernet_key() == key


def test_legacy_data_root_secret_key_is_migrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryptography.fernet import Fernet

    home = tmp_path / "vol"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("EXCELMANUS_DATA_ROOT", raising=False)
    legacy = home / "data" / ".secret_key"
    legacy.parent.mkdir(parents=True)
    key = Fernet.generate_key()
    legacy.write_bytes(key)

    assert derive_fernet_key() == key
    assert get_secret_key_path() == home / ".secret_key"
    assert get_secret_key_path().read_bytes() == key


def test_encrypted_profile_key_survives_home_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "vol"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("EXCELMANUS_DATA_ROOT", raising=False)
    db = Database(str(tmp_path / "profiles.db"))
    store = GlobalConfigStore(db)
    assert TokenCipher().is_active
    store.add_profile("prod", "gpt-test", api_key="sk-live-key", base_url="https://example.com/v1")
    row = store.get_profile("prod")
    assert row is not None
    assert row["api_key"] == "sk-live-key"

    restarted = TokenCipher()
    raw = db.conn.execute(
        "SELECT api_key FROM model_profiles WHERE name = ?", ("prod",),
    ).fetchone()
    assert raw is not None
    assert restarted.decrypt_or_passthrough(raw["api_key"]) == "sk-live-key"


def test_get_excelmanus_home_respects_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = tmp_path / "custom-home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(custom))
    assert get_excelmanus_home() == custom.resolve()
