"""ClawHub 集成测试：客户端、lockfile、manager 集成。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.skillpacks.clawhub import (
    ClawHubClient,
    ClawHubError,
    ClawHubNetworkError,
    ClawHubNotFoundError,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    ClawHubVersionInfo,
    _cli_available,
)
from excelmanus.skillpacks.clawhub_lockfile import ClawHubLockfile


# ── Lockfile 测试 ──────────────────────────────────────────


class TestClawHubLockfile:
    def test_read_empty(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        data = lf.read()
        assert data["version"] == 1
        assert data["skills"] == {}

    def test_add_and_read(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.add("my-skill", "1.0.0")
        installed = lf.get_installed()
        assert installed == {"my-skill": "1.0.0"}

    def test_remove(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.add("skill-a", "1.0.0")
        lf.add("skill-b", "2.0.0")
        assert lf.remove("skill-a")
        assert not lf.has("skill-a")
        assert lf.has("skill-b")

    def test_remove_nonexistent(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        assert not lf.remove("nonexistent")

    def test_update_version(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.add("skill-x", "1.0.0")
        lf.update_version("skill-x", "2.0.0")
        assert lf.get_installed()["skill-x"] == "2.0.0"

    def test_update_version_creates_if_missing(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.update_version("new-skill", "3.0.0")
        assert lf.get_installed()["new-skill"] == "3.0.0"

    def test_lockfile_path(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        assert lf.path == tmp_path / ".clawhub" / "lock.json"

    def test_corrupted_lockfile_resets(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.path.parent.mkdir(parents=True, exist_ok=True)
        lf.path.write_text("not json", encoding="utf-8")
        data = lf.read()
        assert data["version"] == 1
        assert data["skills"] == {}

    def test_wrong_version_resets(self, tmp_path: Path):
        lf = ClawHubLockfile(tmp_path)
        lf.path.parent.mkdir(parents=True, exist_ok=True)
        lf.path.write_text(json.dumps({"version": 99, "skills": {"x": {"version": "1"}}}))
        data = lf.read()
        assert data["skills"] == {}


# ── Client 测试（mock HTTP）──────────────────────────────


class TestClawHubClientSearch:
    @pytest.mark.asyncio
    async def test_search_via_api(self):
        client = ClawHubClient(prefer_cli=False)
        mock_resp = {
            "results": [
                {
                    "slug": "test-skill",
                    "displayName": "Test Skill",
                    "summary": "A test skill",
                    "version": "1.0.0",
                    "score": 0.95,
                    "updatedAt": 1700000000,
                }
            ]
        }
        with patch.object(client, "_http_get", new_callable=AsyncMock, return_value=mock_resp):
            results = await client.search("test")
        assert len(results) == 1
        assert results[0].slug == "test-skill"
        assert results[0].display_name == "Test Skill"
        assert results[0].score == 0.95

    @pytest.mark.asyncio
    async def test_search_empty_results(self):
        client = ClawHubClient(prefer_cli=False)
        with patch.object(client, "_http_get", new_callable=AsyncMock, return_value={"results": []}):
            results = await client.search("nonexistent")
        assert results == []


class TestClawHubClientGetSkill:
    @pytest.mark.asyncio
    async def test_get_skill(self):
        client = ClawHubClient(prefer_cli=False)
        mock_resp = {
            "skill": {
                "slug": "my-skill",
                "displayName": "My Skill",
                "summary": "Does things",
                "tags": ["productivity"],
                "createdAt": 1700000000,
                "updatedAt": 1700001000,
                "stats": {"installs": 42},
            },
            "latestVersion": {"version": "2.0.0", "changelog": "Fixed bugs"},
            "owner": {"handle": "alice", "displayName": "Alice"},
        }
        with patch.object(client, "_http_get", new_callable=AsyncMock, return_value=mock_resp):
            detail = await client.get_skill("my-skill")
        assert detail.slug == "my-skill"
        assert detail.latest_version == "2.0.0"
        assert detail.owner_handle == "alice"
        assert detail.tags == ["productivity"]

    @pytest.mark.asyncio
    async def test_get_skill_not_found(self):
        client = ClawHubClient(prefer_cli=False)
        with patch.object(client, "_http_get", new_callable=AsyncMock, return_value={"skill": None}):
            with pytest.raises(ClawHubNotFoundError):
                await client.get_skill("nonexistent")


class TestClawHubClientResolve:
    @pytest.mark.asyncio
    async def test_resolve_version(self):
        client = ClawHubClient(prefer_cli=False)
        mock_resp = {
            "match": {"version": "1.2.0"},
            "latestVersion": {"version": "2.0.0"},
        }
        with patch.object(client, "_http_post", new_callable=AsyncMock, return_value=mock_resp):
            info = await client.resolve_version("test-skill", "1.2.0")
        assert info.match_version == "1.2.0"
        assert info.latest_version == "2.0.0"

    @pytest.mark.asyncio
    async def test_resolve_no_match(self):
        client = ClawHubClient(prefer_cli=False)
        mock_resp = {"match": None, "latestVersion": {"version": "2.0.0"}}
        with patch.object(client, "_http_post", new_callable=AsyncMock, return_value=mock_resp):
            info = await client.resolve_version("test-skill")
        assert info.match_version is None
        assert info.latest_version == "2.0.0"


class TestClawHubClientCheckUpdates:
    @pytest.mark.asyncio
    async def test_check_updates(self):
        client = ClawHubClient(prefer_cli=False)
        async def mock_resolve(slug, ver=None):
            return ClawHubVersionInfo(match_version=ver, latest_version="2.0.0")

        with patch.object(client, "resolve_version", side_effect=mock_resolve):
            updates = await client.check_updates({"skill-a": "1.0.0", "skill-b": "2.0.0"})

        assert len(updates) == 2
        a = next(u for u in updates if u.slug == "skill-a")
        b = next(u for u in updates if u.slug == "skill-b")
        assert a.update_available is True
        assert b.update_available is False

    @pytest.mark.asyncio
    async def test_check_updates_error_resilient(self):
        client = ClawHubClient(prefer_cli=False)
        async def mock_resolve(slug, ver=None):
            raise ClawHubNetworkError("timeout")

        with patch.object(client, "resolve_version", side_effect=mock_resolve):
            updates = await client.check_updates({"skill-a": "1.0.0"})

        assert len(updates) == 1
        assert updates[0].update_available is False


class TestClawHubClientCLIDetection:
    def test_cli_available_when_missing(self):
        with patch("excelmanus.skillpacks.clawhub.shutil.which", return_value=None):
            assert not _cli_available()

    def test_cli_available_when_present(self):
        with patch("excelmanus.skillpacks.clawhub.shutil.which", return_value="/usr/local/bin/clawhub"):
            assert _cli_available()

    def test_client_prefers_cli_fallback(self):
        client = ClawHubClient(prefer_cli=True)
        # Force CLI detection to False
        client._cli_path = False
        assert not client._should_use_cli()

    def test_client_disables_cli(self):
        client = ClawHubClient(prefer_cli=False)
        assert not client._should_use_cli()
