"""订阅提供商模块单元测试。"""

from __future__ import annotations

import base64
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── OpenAICodexProvider 测试 ──────────────────────────────────


def _make_jwt(claims: dict, exp: int | None = None) -> str:
    """构造一个简单的 JWT（不签名，仅用于测试解析）。"""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
    if exp is not None:
        claims["exp"] = exp
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.sig"


class TestOpenAICodexProvider:
    """OpenAICodexProvider 验证和模型匹配测试。"""

    def setup_method(self):
        from excelmanus.auth.providers.openai_codex import OpenAICodexProvider
        self.provider = OpenAICodexProvider()

    def test_matches_codex_models(self):
        assert self.provider.matches_model("gpt-5.2-codex")
        assert self.provider.matches_model("gpt-5.3-codex-spark")
        assert self.provider.matches_model("gpt-5.1-codex-mini")
        assert self.provider.matches_model("gpt-5.1-codex-max")
        assert self.provider.matches_model("codex-mini-latest")
        assert self.provider.matches_model("gpt-5.2")
        assert self.provider.matches_model("gpt-5.1")

    def test_does_not_match_non_codex_models(self):
        assert not self.provider.matches_model("claude-3-opus")
        assert not self.provider.matches_model("gemini-2.5-pro")
        assert not self.provider.matches_model("gpt-4o")
        assert not self.provider.matches_model("gpt-4.1-mini")

    def test_validate_codex_cli_format(self):
        """Codex CLI auth.json 格式。"""
        exp = int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp())
        token = _make_jwt({
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acc-123",
                "chatgpt_plan_type": "plus",
            },
        }, exp=exp)
        raw = {"token": token, "refresh_token": "rt_abc123"}
        cred = self.provider.validate_token_data(raw)
        assert cred.access_token == token
        assert cred.refresh_token == "rt_abc123"
        assert cred.account_id == "acc-123"
        assert cred.plan_type == "plus"
        assert cred.credential_type == "oauth"

    def test_validate_openclaw_format(self):
        """OpenClaw 风格格式。"""
        exp_ms = int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp() * 1000)
        token = _make_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-456"}})
        raw = {"type": "oauth", "access": token, "refresh": "rt_xyz", "expires": exp_ms}
        cred = self.provider.validate_token_data(raw)
        assert cred.access_token == token
        assert cred.refresh_token == "rt_xyz"
        assert cred.account_id == "acc-456"

    def test_validate_simplified_format(self):
        """简化格式。"""
        token = _make_jwt({})
        raw = {"access_token": token, "refresh_token": "rt_simple"}
        cred = self.provider.validate_token_data(raw)
        assert cred.access_token == token
        assert cred.refresh_token == "rt_simple"

    def test_validate_missing_token_raises(self):
        with pytest.raises(ValueError, match="缺少 access token"):
            self.provider.validate_token_data({})

    def test_get_api_credential(self):
        api_key, base_url = self.provider.get_api_credential("eyJtest")
        assert api_key == "eyJtest"
        assert base_url == "https://chatgpt.com/backend-api/codex"

    def test_supported_model_entries_have_friendly_alias_and_prefixed_public_id(self):
        entries = self.provider.list_supported_model_entries()
        assert len(entries) > 0

        spark = next((item for item in entries if item["model"] == "gpt-5.3-codex-spark"), None)
        assert spark is not None
        assert spark["display_name"] == "Codex Spark (Legacy)"
        assert spark["profile_name"] == "openai-codex/gpt-5.3-codex-spark"
        assert spark["public_model_id"] == "openai-codex/gpt-5.3-codex-spark"

    @pytest.mark.asyncio
    async def test_refresh_token_no_refresh(self):
        with pytest.raises(RuntimeError, match="无 refresh token"):
            await self.provider.refresh_token("")

    @pytest.mark.asyncio
    async def test_refresh_token_success(self):
        import httpx
        exp = int((datetime.now(tz=timezone.utc) + timedelta(hours=1)).timestamp())
        new_token = _make_jwt({"exp": exp})
        mock_resp = httpx.Response(
            200,
            json={"access_token": new_token, "refresh_token": "rt_new"},
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            result = await self.provider.refresh_token("rt_old")

        assert result.access_token == new_token
        assert result.refresh_token == "rt_new"

    @pytest.mark.asyncio
    async def test_refresh_token_failure(self):
        import httpx
        mock_resp = httpx.Response(
            401,
            text="unauthorized",
            request=httpx.Request("POST", "https://auth.openai.com/oauth/token"),
        )

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Token 刷新失败"):
                await self.provider.refresh_token("rt_expired")


# ── CredentialStore 测试 ─────────────────────────────────────


def _create_test_db():
    """创建包含 auth_profiles 表的内存 SQLite 数据库。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE auth_profiles (
        id              TEXT PRIMARY KEY,
        user_id         TEXT NOT NULL,
        provider        TEXT NOT NULL,
        profile_name    TEXT NOT NULL DEFAULT 'default',
        credential_type TEXT NOT NULL DEFAULT 'oauth',
        access_token    TEXT,
        refresh_token   TEXT,
        expires_at      TEXT,
        account_id      TEXT,
        plan_type       TEXT,
        extra_data      TEXT,
        is_active       INTEGER DEFAULT 1,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        UNIQUE(user_id, provider, profile_name)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ap_user ON auth_profiles(user_id)")
    conn.commit()
    return conn


class TestCredentialStore:
    """CredentialStore CRUD 测试。"""

    def setup_method(self):
        self.conn = _create_test_db()
        # 使用无加密的 CredentialStore（测试环境）
        from excelmanus.auth.providers.credential_store import CredentialStore
        self.store = CredentialStore(self.conn)

    def teardown_method(self):
        self.conn.close()

    def _make_credential(self, **overrides):
        from excelmanus.auth.providers.base import ValidatedCredential
        defaults = dict(
            access_token="eyJtest",
            refresh_token="rt_test",
            expires_at=datetime.now(tz=timezone.utc).isoformat(),
            account_id="acc-test",
            plan_type="plus",
            credential_type="oauth",
        )
        defaults.update(overrides)
        return ValidatedCredential(**defaults)

    def test_upsert_new_profile(self):
        cred = self._make_credential()
        summary = self.store.upsert_profile("user-1", "openai-codex", "default", cred)
        assert summary.provider == "openai-codex"
        assert summary.account_id == "acc-test"
        assert summary.is_active is True

    def test_upsert_update_existing(self):
        cred1 = self._make_credential(account_id="old")
        self.store.upsert_profile("user-1", "openai-codex", "default", cred1)

        cred2 = self._make_credential(account_id="new", plan_type="pro")
        self.store.upsert_profile("user-1", "openai-codex", "default", cred2)

        profile = self.store.get_active_profile("user-1", "openai-codex")
        assert profile is not None
        assert profile.account_id == "new"
        assert profile.plan_type == "pro"

    def test_get_active_profile(self):
        cred = self._make_credential()
        self.store.upsert_profile("user-1", "openai-codex", "default", cred)

        profile = self.store.get_active_profile("user-1", "openai-codex")
        assert profile is not None
        assert profile.access_token is not None
        assert profile.refresh_token is not None

    def test_get_active_profile_not_found(self):
        assert self.store.get_active_profile("user-1", "openai-codex") is None

    def test_list_profiles(self):
        cred = self._make_credential()
        self.store.upsert_profile("user-1", "openai-codex", "default", cred)
        profiles = self.store.list_profiles("user-1")
        assert len(profiles) == 1
        assert profiles[0].provider == "openai-codex"

    def test_delete_profile(self):
        cred = self._make_credential()
        self.store.upsert_profile("user-1", "openai-codex", "default", cred)
        assert self.store.delete_profile("user-1", "openai-codex") is True
        assert self.store.get_active_profile("user-1", "openai-codex") is None

    def test_delete_nonexistent(self):
        assert self.store.delete_profile("user-1", "openai-codex") is False

    def test_update_tokens(self):
        cred = self._make_credential()
        summary = self.store.upsert_profile("user-1", "openai-codex", "default", cred)

        new_expires = (datetime.now(tz=timezone.utc) + timedelta(hours=2)).isoformat()
        self.store.update_tokens(summary.id, "new_access", "new_refresh", new_expires)

        profile = self.store.get_active_profile("user-1", "openai-codex")
        assert profile is not None
        assert profile.expires_at == new_expires

    def test_deactivate_profile(self):
        cred = self._make_credential()
        summary = self.store.upsert_profile("user-1", "openai-codex", "default", cred)
        self.store.deactivate_profile(summary.id)

        profile = self.store.get_active_profile("user-1", "openai-codex")
        assert profile is None  # inactive profiles not returned

    def test_user_isolation(self):
        """不同用户的 profile 互相隔离。"""
        cred1 = self._make_credential(account_id="user1-acc")
        cred2 = self._make_credential(account_id="user2-acc")
        self.store.upsert_profile("user-1", "openai-codex", "default", cred1)
        self.store.upsert_profile("user-2", "openai-codex", "default", cred2)

        p1 = self.store.get_active_profile("user-1", "openai-codex")
        p2 = self.store.get_active_profile("user-2", "openai-codex")
        assert p1 is not None and p1.account_id == "user1-acc"
        assert p2 is not None and p2.account_id == "user2-acc"


# ── CredentialResolver 测试 ──────────────────────────────────


class TestCredentialResolver:
    """CredentialResolver 凭证解析优先级测试。"""

    def setup_method(self):
        self.conn = _create_test_db()
        from excelmanus.auth.providers.credential_store import CredentialStore
        from excelmanus.auth.providers.resolver import CredentialResolver
        self.cred_store = CredentialStore(self.conn)
        self.resolver = CredentialResolver(credential_store=self.cred_store)

    def teardown_method(self):
        self.conn.close()

    @pytest.mark.asyncio
    async def test_no_user_returns_none(self):
        result = await self.resolver.resolve(None, "gpt-5.2-codex")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_profile_returns_none(self):
        result = await self.resolver.resolve("user-1", "gpt-5.2-codex")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_matching_model_returns_none(self):
        result = await self.resolver.resolve("user-1", "claude-3-opus")
        assert result is None

    @pytest.mark.asyncio
    async def test_active_profile_returns_oauth_credential(self):
        from excelmanus.auth.providers.base import ValidatedCredential
        future = (datetime.now(tz=timezone.utc) + timedelta(hours=1)).isoformat()
        cred = ValidatedCredential(
            access_token="eyJvalid",
            refresh_token="rt_valid",
            expires_at=future,
            account_id="acc",
            plan_type="plus",
        )
        self.cred_store.upsert_profile("user-1", "openai-codex", "default", cred)

        result = await self.resolver.resolve("user-1", "gpt-5.2-codex")
        assert result is not None
        assert result.source == "oauth"
        assert result.provider == "openai-codex"
        assert result.base_url == "https://chatgpt.com/backend-api/codex"

    @pytest.mark.asyncio
    async def test_model_match_provider(self):
        from excelmanus.auth.providers.resolver import CredentialResolver
        assert CredentialResolver._match_provider("gpt-5.2-codex") == "openai-codex"
        assert CredentialResolver._match_provider("codex-mini-latest") == "openai-codex"
        assert CredentialResolver._match_provider("gpt-5.1") == "openai-codex"
        assert CredentialResolver._match_provider("claude-3-opus") is None
        assert CredentialResolver._match_provider("gpt-4o") is None


# ── OAuth State Token 加密测试 ────────────────────────────────


class TestOAuthStateToken:
    """加密 state token 的 seal/unseal 测试。"""

    def test_seal_unseal_roundtrip(self):
        from excelmanus.auth.router import _seal_oauth_state, _unseal_oauth_state
        payload = {
            "user_id": "user-abc",
            "device_auth_id": "daid-123",
            "user_code": "ABCD-1234",
            "ts": time.time(),
        }
        sealed = _seal_oauth_state(payload)
        assert isinstance(sealed, str)
        assert len(sealed) > 50  # Fernet token is long

        result = _unseal_oauth_state(sealed)
        assert result is not None
        assert result["user_id"] == "user-abc"
        assert result["device_auth_id"] == "daid-123"
        assert result["user_code"] == "ABCD-1234"

    def test_unseal_invalid_token(self):
        from excelmanus.auth.router import _unseal_oauth_state
        assert _unseal_oauth_state("garbage") is None
        assert _unseal_oauth_state("") is None

    def test_unseal_tampered_token(self):
        from excelmanus.auth.router import _seal_oauth_state, _unseal_oauth_state
        sealed = _seal_oauth_state({"user_id": "u1", "ts": time.time()})
        tampered = sealed[:-5] + "XXXXX"
        assert _unseal_oauth_state(tampered) is None

    def test_different_users_get_different_states(self):
        from excelmanus.auth.router import _seal_oauth_state, _unseal_oauth_state
        s1 = _seal_oauth_state({"user_id": "user-1", "ts": time.time()})
        s2 = _seal_oauth_state({"user_id": "user-2", "ts": time.time()})
        assert s1 != s2

        r1 = _unseal_oauth_state(s1)
        r2 = _unseal_oauth_state(s2)
        assert r1["user_id"] == "user-1"
        assert r2["user_id"] == "user-2"

    def test_state_preserves_device_auth_fields(self):
        from excelmanus.auth.router import _seal_oauth_state, _unseal_oauth_state
        sealed = _seal_oauth_state({
            "user_id": "u1",
            "device_auth_id": "daid-xyz",
            "user_code": "WXYZ-9876",
            "ts": time.time(),
        })
        result = _unseal_oauth_state(sealed)
        assert result["device_auth_id"] == "daid-xyz"
        assert result["user_code"] == "WXYZ-9876"


# ── DB 迁移测试 ───────────────────────────────────────────────


class TestAuthProfilesMigration:
    """验证 v18 迁移正确创建 auth_profiles 表。"""

    def test_migration_creates_table(self, tmp_path):
        from excelmanus.database import Database
        db = Database(str(tmp_path / "test.db"))
        # 验证表存在
        row = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_profiles'"
        ).fetchone()
        assert row is not None
        db.close()

    def test_migration_version_18(self, tmp_path):
        from excelmanus.database import Database, _LATEST_VERSION
        db = Database(str(tmp_path / "test.db"))
        assert db._current_version() >= 18
        assert _LATEST_VERSION >= 18
        db.close()

    def test_insert_into_auth_profiles(self, tmp_path):
        from excelmanus.database import Database
        db = Database(str(tmp_path / "test.db"))
        now = datetime.now(tz=timezone.utc).isoformat()
        db.conn.execute(
            """INSERT INTO auth_profiles
               (id, user_id, provider, profile_name, credential_type,
                access_token, refresh_token, expires_at,
                account_id, plan_type, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            ("id-1", "user-1", "openai-codex", "default", "oauth",
             "enc_token", "enc_refresh", now, "acc-1", "plus", now, now),
        )
        db.conn.commit()
        row = db.conn.execute(
            "SELECT * FROM auth_profiles WHERE id = ?", ("id-1",)
        ).fetchone()
        assert row is not None
        assert dict(row)["provider"] == "openai-codex"
        db.close()
