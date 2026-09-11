"""配置 / 密钥持久化：正式仓、空值不遮盖、加密密钥跟随 HOME。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from excelmanus.config import load_config, load_runtime_env
from excelmanus.data_home import (
    get_config_env_path,
    get_excelmanus_home,
    get_secret_key_path,
    parse_env_file,
    persist_env_updates,
    reconcile_project_env_into_canonical,
)
from excelmanus.database import Database
from excelmanus.security.cipher import TokenCipher, derive_fernet_key
from excelmanus.stores.config_store import GlobalConfigStore


def test_persist_env_updates_writes_canonical_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    persist_env_updates({"EXCELMANUS_EXA_API_KEY": "exa-secret"})
    canonical = get_config_env_path()
    assert canonical == home / "config.env"
    assert parse_env_file(canonical)["EXCELMANUS_EXA_API_KEY"] == "exa-secret"
    assert os.environ["EXCELMANUS_EXA_API_KEY"] == "exa-secret"


def test_empty_process_env_does_not_hide_canonical_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(work)
    persist_env_updates({
        "EXCELMANUS_API_KEY": "from-canonical",
        "EXCELMANUS_BASE_URL": "https://example.com/v1",
        "EXCELMANUS_MODEL": "test-model",
    })
    monkeypatch.setenv("EXCELMANUS_API_KEY", "")
    load_runtime_env()
    cfg = load_config()
    assert cfg.api_key == "from-canonical"


def test_cwd_dotenv_empty_does_not_hide_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    persist_env_updates({
        "EXCELMANUS_API_KEY": "kept-key",
        "EXCELMANUS_BASE_URL": "https://example.com/v1",
        "EXCELMANUS_MODEL": "test-model",
    })
    (work / ".env").write_text(
        "EXCELMANUS_API_KEY=\n"
        "EXCELMANUS_BASE_URL=https://example.com/v1\n"
        "EXCELMANUS_MODEL=test-model\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(work)
    monkeypatch.delenv("EXCELMANUS_API_KEY", raising=False)
    load_runtime_env()
    assert os.environ.get("EXCELMANUS_API_KEY") == "kept-key"


def test_persist_env_updates_does_not_write_package_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excelmanus.data_home import get_package_root

    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    package_env = get_package_root() / ".env"
    before = package_env.read_text(encoding="utf-8") if package_env.is_file() else None
    persist_env_updates({"EXCELMANUS_MODEL": "should-not-leak"})
    after = package_env.read_text(encoding="utf-8") if package_env.is_file() else None
    assert after == before
    assert "should-not-leak" not in (after or "")


def test_persist_env_updates_syncs_cwd_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("EXCELMANUS_MODEL=old-model\n", encoding="utf-8")
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.chdir(work)
    persist_env_updates({"EXCELMANUS_MODEL": "new-model"})
    assert parse_env_file(work / ".env")["EXCELMANUS_MODEL"] == "new-model"


def test_persist_env_updates_takes_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from excelmanus import data_home as data_home_mod

    home = tmp_path / "home"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    seen: list[str] = []
    real_lock = data_home_mod._file_lock

    def _tracking_lock(lock_path, timeout=5.0):
        seen.append(str(lock_path))
        return real_lock(lock_path, timeout)

    monkeypatch.setattr(data_home_mod, "_file_lock", _tracking_lock)
    persist_env_updates({"EXCELMANUS_MODEL": "locked"})
    assert any(path.endswith("config.env.lock") for path in seen)
    assert parse_env_file(get_config_env_path())["EXCELMANUS_MODEL"] == "locked"


def test_reconcile_copies_project_env_gaps_into_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.env").write_text("EXCELMANUS_API_KEY=\nOTHER=keep-me\n", encoding="utf-8")
    (project / ".env").write_text("EXCELMANUS_API_KEY=from-project\nOTHER=ignore\n", encoding="utf-8")
    assert reconcile_project_env_into_canonical(project)
    stored = parse_env_file(home / "config.env")
    assert stored["EXCELMANUS_API_KEY"] == "from-project"
    assert stored["OTHER"] == "keep-me"


def test_fernet_key_lives_under_excelmanus_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "vol"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    monkeypatch.delenv("EXCELMANUS_SECRET_KEY", raising=False)
    monkeypatch.delenv("EXCELMANUS_DATA_ROOT", raising=False)
    key = derive_fernet_key()
    assert key
    assert get_secret_key_path() == home / "data" / ".secret_key"
    assert get_secret_key_path().is_file()
    assert derive_fernet_key() == key


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
