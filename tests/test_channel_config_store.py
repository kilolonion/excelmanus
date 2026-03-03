"""Tests for ChannelConfigStore and ChannelLauncher hot start/stop."""

from __future__ import annotations

import pytest

from excelmanus.channels.config_store import (
    ChannelConfig,
    ChannelConfigStore,
    CHANNEL_CREDENTIAL_FIELDS,
)


# ── ChannelConfig dataclass tests ──────────────────────────


class TestChannelConfig:
    def test_has_required_credentials_telegram_complete(self):
        cfg = ChannelConfig(name="telegram", credentials={"token": "abc123"})
        assert cfg.has_required_credentials() is True

    def test_has_required_credentials_telegram_missing(self):
        cfg = ChannelConfig(name="telegram", credentials={})
        assert cfg.has_required_credentials() is False

    def test_get_missing_fields_telegram(self):
        cfg = ChannelConfig(name="telegram", credentials={})
        assert cfg.get_missing_fields() == ["token"]

    def test_get_missing_fields_telegram_complete(self):
        cfg = ChannelConfig(name="telegram", credentials={"token": "abc"})
        assert cfg.get_missing_fields() == []

    def test_has_required_credentials_qq_complete(self):
        cfg = ChannelConfig(name="qq", credentials={"app_id": "123", "secret": "xyz"})
        assert cfg.has_required_credentials() is True

    def test_has_required_credentials_qq_partial(self):
        cfg = ChannelConfig(name="qq", credentials={"app_id": "123"})
        assert cfg.has_required_credentials() is False

    def test_get_missing_fields_qq(self):
        cfg = ChannelConfig(name="qq", credentials={"app_id": "123"})
        assert "secret" in cfg.get_missing_fields()
        assert "app_id" not in cfg.get_missing_fields()

    def test_to_dict_and_from_dict(self):
        cfg = ChannelConfig(
            name="telegram",
            enabled=True,
            credentials={"token": "test-token", "allowed_users": "user1,user2"},
            updated_at="2025-01-01T00:00:00Z",
        )
        d = cfg.to_dict()
        assert d["name"] == "telegram"
        assert d["enabled"] is True
        assert d["credentials"]["token"] == "test-token"

        restored = ChannelConfig.from_dict(d)
        assert restored.name == cfg.name
        assert restored.enabled == cfg.enabled
        assert restored.credentials == cfg.credentials

    def test_unknown_channel_always_has_required(self):
        cfg = ChannelConfig(name="unknown_channel", credentials={})
        assert cfg.has_required_credentials() is True
        assert cfg.get_missing_fields() == []


# ── ChannelConfigStore tests ───────────────────────────────


class _FakeGlobalConfigStore:
    """Mock GlobalConfigStore using in-memory dict."""

    def __init__(self):
        self._kv: dict[str, str] = {}

    def get(self, key: str, default: str = "") -> str:
        return self._kv.get(key, default)

    def set(self, key: str, value: str) -> None:
        self._kv[key] = value


class TestChannelConfigStore:
    def setup_method(self):
        self.fake_store = _FakeGlobalConfigStore()
        self.ccs = ChannelConfigStore(self.fake_store)

    def test_load_all_empty(self):
        configs = self.ccs.load_all()
        assert configs == {}

    def test_save_and_load(self):
        cfg = ChannelConfig(
            name="telegram",
            enabled=True,
            credentials={"token": "my-secret-token", "allowed_users": "u1,u2"},
        )
        self.ccs.save(cfg)

        loaded = self.ccs.load_all()
        assert "telegram" in loaded
        tg = loaded["telegram"]
        assert tg.enabled is True
        # Token should round-trip through encryption/decryption
        assert tg.credentials["token"]  # non-empty
        assert tg.credentials["allowed_users"] == "u1,u2"

    def test_save_multiple_channels(self):
        self.ccs.save(ChannelConfig(name="telegram", enabled=True, credentials={"token": "t1"}))
        self.ccs.save(ChannelConfig(name="qq", enabled=False, credentials={"app_id": "a", "secret": "s"}))

        loaded = self.ccs.load_all()
        assert len(loaded) == 2
        assert loaded["telegram"].enabled is True
        assert loaded["qq"].enabled is False

    def test_get_single(self):
        self.ccs.save(ChannelConfig(name="telegram", credentials={"token": "t1"}))
        cfg = self.ccs.get("telegram")
        assert cfg is not None
        assert cfg.name == "telegram"

    def test_get_nonexistent(self):
        cfg = self.ccs.get("nonexistent")
        assert cfg is None

    def test_delete(self):
        self.ccs.save(ChannelConfig(name="telegram", credentials={"token": "t1"}))
        assert self.ccs.delete("telegram") is True
        assert self.ccs.get("telegram") is None

    def test_delete_nonexistent(self):
        assert self.ccs.delete("nonexistent") is False

    def test_set_enabled(self):
        self.ccs.save(ChannelConfig(name="telegram", enabled=False, credentials={"token": "t1"}))
        assert self.ccs.set_enabled("telegram", True) is True
        cfg = self.ccs.get("telegram")
        assert cfg is not None
        assert cfg.enabled is True

    def test_set_enabled_nonexistent(self):
        assert self.ccs.set_enabled("nonexistent", True) is False

    def test_updated_at_set_on_save(self):
        cfg = ChannelConfig(name="telegram", credentials={"token": "t1"})
        assert cfg.updated_at == ""
        self.ccs.save(cfg)
        loaded = self.ccs.get("telegram")
        assert loaded is not None
        assert loaded.updated_at != ""

    def test_corrupted_json_returns_empty(self):
        self.fake_store.set("channel_config", "not valid json{{{")
        configs = self.ccs.load_all()
        assert configs == {}


# ── ChannelLauncher hot start/stop tests ───────────────────


class TestChannelLauncherStatus:
    def test_channel_status_stopped_when_no_task(self):
        from excelmanus.channels.launcher import ChannelLauncher
        launcher = ChannelLauncher([])
        assert launcher.channel_status("telegram") == "stopped"

    def test_active_channels_empty(self):
        from excelmanus.channels.launcher import ChannelLauncher
        launcher = ChannelLauncher([])
        assert launcher.active_channels == []

    def test_all_channel_status_includes_known(self):
        from excelmanus.channels.launcher import ChannelLauncher
        launcher = ChannelLauncher([])
        statuses = launcher.all_channel_status()
        assert "telegram" in statuses
        assert "qq" in statuses
        assert all(s == "stopped" for s in statuses.values())


# ── CHANNEL_CREDENTIAL_FIELDS consistency tests ────────────


class TestCredentialFields:
    def test_all_channels_have_fields(self):
        for channel in ("telegram", "qq", "feishu"):
            assert channel in CHANNEL_CREDENTIAL_FIELDS
            fields = CHANNEL_CREDENTIAL_FIELDS[channel]
            assert len(fields) > 0

    def test_all_fields_have_required_keys(self):
        for channel, fields in CHANNEL_CREDENTIAL_FIELDS.items():
            for field in fields:
                assert "key" in field, f"{channel}: field missing 'key'"
                assert "label" in field, f"{channel}: field missing 'label'"
                assert "required" in field, f"{channel}: field missing 'required'"
                assert "secret" in field, f"{channel}: field missing 'secret'"

    def test_telegram_requires_token(self):
        tg_fields = CHANNEL_CREDENTIAL_FIELDS["telegram"]
        required_keys = [f["key"] for f in tg_fields if f["required"]]
        assert "token" in required_keys

    def test_qq_requires_app_id_and_secret(self):
        qq_fields = CHANNEL_CREDENTIAL_FIELDS["qq"]
        required_keys = [f["key"] for f in qq_fields if f["required"]]
        assert "app_id" in required_keys
        assert "secret" in required_keys
