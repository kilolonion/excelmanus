"""Tests for deploy improvements (Issues 15, 16, 17).

Issue 15: Version fingerprint-based restart detection
Issue 16: Rollback panel / canary UI enhancements (backend API)
Issue 17: Cross-machine deploy lock
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════
# Issue 17: Remote deploy lock
# ═══════════════════════════════════════════════════════════


class TestParseEnvDeploy:
    """Test _parse_env_deploy helper."""

    def test_parse_basic(self, tmp_path: Path):
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text(
            'BACKEND_HOST="10.0.0.1"\n'
            "SSH_USER=deploy\n"
            "# comment line\n"
            'SITE_URL="https://example.com"\n'
        )
        from excelmanus.updater import _parse_env_deploy

        result = _parse_env_deploy(tmp_path)
        assert result["BACKEND_HOST"] == "10.0.0.1"
        assert result["SSH_USER"] == "deploy"
        assert result["SITE_URL"] == "https://example.com"
        assert "#" not in "".join(result.keys())

    def test_parse_missing_file(self, tmp_path: Path):
        from excelmanus.updater import _parse_env_deploy

        result = _parse_env_deploy(tmp_path)
        assert result == {}

    def test_parse_empty_file(self, tmp_path: Path):
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text("")
        from excelmanus.updater import _parse_env_deploy

        result = _parse_env_deploy(tmp_path)
        assert result == {}


class TestCheckRemoteDeployLock:
    """Test check_remote_deploy_lock function."""

    def test_no_server_config(self, tmp_path: Path):
        """No .env.deploy → returns error."""
        from excelmanus.updater import check_remote_deploy_lock

        result = check_remote_deploy_lock(tmp_path)
        assert result["locked"] is False
        assert result["error"] is not None
        assert "未配置" in result["error"]

    def test_no_lock_file(self, tmp_path: Path):
        """SSH returns empty → not locked."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is False
        assert result["error"] is None

    def test_active_lock(self, tmp_path: Path):
        """SSH returns lock content → locked."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        ts = str(int(time.time()))
        lock_content = f"devbox:12345:{ts}:alice"

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=lock_content, returncode=0)
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is True
        assert result["holder_host"] == "devbox"
        assert result["holder_pid"] == "12345"
        assert result["holder_user"] == "alice"
        assert result["expired"] is False
        assert result["elapsed_s"] < 5

    def test_expired_lock(self, tmp_path: Path):
        """Lock older than TTL → locked but expired."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        old_ts = str(int(time.time()) - 3600)  # 1 hour ago
        lock_content = f"devbox:12345:{old_ts}:alice"

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=lock_content, returncode=0)
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is True
        assert result["expired"] is True

    def test_ssh_timeout(self, tmp_path: Path):
        """SSH timeout → error."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10)):
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is False
        assert "超时" in result["error"]

    def test_ssh_not_available(self, tmp_path: Path):
        """ssh binary not found → error."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run", side_effect=FileNotFoundError("ssh")):
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is False
        assert "不可用" in result["error"]

    def test_malformed_lock_content(self, tmp_path: Path):
        """Lock content with fewer than 3 fields → not locked."""
        env_file = tmp_path / "deploy" / ".env.deploy"
        env_file.parent.mkdir(parents=True)
        env_file.write_text('BACKEND_HOST="10.0.0.1"\nSSH_USER=deploy\n')

        from excelmanus.updater import check_remote_deploy_lock

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="just:garbage", returncode=0)
            result = check_remote_deploy_lock(tmp_path)

        assert result["locked"] is False


class TestGetDeployStatusLockInfo:
    """Test that get_deploy_status includes local_lock info."""

    def test_no_lock_file(self, tmp_path: Path):
        from excelmanus.updater import get_deploy_status

        result = get_deploy_status(tmp_path)
        assert result["local_lock"] is None

    def test_with_lock_file(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        lock_file = deploy_dir / ".deploy.lock"
        lock_file.write_text("12345\n2026-03-04 10:00:00\n")

        from excelmanus.updater import get_deploy_status

        result = get_deploy_status(tmp_path)
        assert result["local_lock"] is not None
        assert result["local_lock"]["pid"] == "12345"
        assert "2026-03-04" in result["local_lock"]["started_at"]


# ═══════════════════════════════════════════════════════════
# Issue 16: canary_start / get_deploy_log
# ═══════════════════════════════════════════════════════════


class TestCanaryStart:
    """Test canary_start function."""

    def test_no_deploy_script(self, tmp_path: Path):
        from excelmanus.updater import canary_start

        result = canary_start(tmp_path)
        assert result["success"] is False
        assert "deploy.sh" in result["error"]

    def test_already_active(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        (deploy_dir / "deploy.sh").write_text("#!/bin/bash\necho ok")
        canary_file = deploy_dir / ".deploy_canary.json"
        canary_file.write_text(json.dumps({"active": True}))

        from excelmanus.updater import canary_start

        result = canary_start(tmp_path)
        assert result["success"] is False
        assert "已有灰度" in result["error"]

    def test_start_success(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        (deploy_dir / "deploy.sh").write_text("#!/bin/bash\necho ok")

        from excelmanus.updater import canary_start

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = canary_start(tmp_path, target="backend", observe_seconds=30)

        assert result["success"] is True
        # Verify --canary and --backend-only flags
        cmd = mock_run.call_args[0][0]
        assert "--canary" in cmd
        assert "--backend-only" in cmd
        assert "--canary-observe" in cmd


class TestGetDeployLog:
    """Test get_deploy_log function."""

    def test_empty_release_id(self, tmp_path: Path):
        from excelmanus.updater import get_deploy_log

        assert get_deploy_log(tmp_path, "") == ""

    def test_path_traversal_blocked(self, tmp_path: Path):
        from excelmanus.updater import get_deploy_log

        assert get_deploy_log(tmp_path, "../../../etc/passwd") == ""
        assert get_deploy_log(tmp_path, "foo/bar") == ""
        assert get_deploy_log(tmp_path, "foo\\bar") == ""

    def test_log_file_exists(self, tmp_path: Path):
        log_dir = tmp_path / "deploy" / ".deploy_logs"
        log_dir.mkdir(parents=True)
        (log_dir / "20260304T100000.log").write_text("deploy log content here")

        from excelmanus.updater import get_deploy_log

        result = get_deploy_log(tmp_path, "20260304T100000")
        assert "deploy log content here" in result

    def test_fallback_to_history(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        history_file = deploy_dir / ".deploy_history"
        history_file.write_text(
            "2026-03-04 10:00:00 | SUCCESS | split/full | main | 120s\n"
            "2026-03-04 11:00:00 | FAILED | split/full | main | 30s\n"
        )

        from excelmanus.updater import get_deploy_log

        result = get_deploy_log(tmp_path, "SUCCESS")
        assert "SUCCESS" in result

    def test_no_log_found(self, tmp_path: Path):
        from excelmanus.updater import get_deploy_log

        result = get_deploy_log(tmp_path, "nonexistent_id")
        assert result == ""

    def test_large_log_truncated(self, tmp_path: Path):
        """Log files larger than 10000 chars should be truncated."""
        log_dir = tmp_path / "deploy" / ".deploy_logs"
        log_dir.mkdir(parents=True)
        big_content = "x" * 20000
        (log_dir / "big.log").write_text(big_content)

        from excelmanus.updater import get_deploy_log

        result = get_deploy_log(tmp_path, "big")
        assert len(result) == 10000


# ═══════════════════════════════════════════════════════════
# Issue 15: Version fingerprint parsing (health endpoint)
# ═══════════════════════════════════════════════════════════


class TestManifestData:
    """Test that health/manifest includes version fingerprint fields."""

    def test_get_manifest_data_includes_fields(self):
        from excelmanus.api_routes_version import get_manifest_data

        data = get_manifest_data()
        assert "version_fingerprint" in data
        assert "git_commit" in data
        assert "release_id" in data
        assert "backend_version" in data


class TestDeployHistoryStructured:
    """Test get_deploy_history_structured reads JSON correctly."""

    def test_empty_when_no_file(self, tmp_path: Path):
        from excelmanus.updater import get_deploy_history_structured

        result = get_deploy_history_structured(tmp_path)
        assert result == []

    def test_reads_json_array(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        entries = [
            {"release_id": "20260304T100000", "status": "SUCCESS", "git_commit": "abc123"},
            {"release_id": "20260304T110000", "status": "FAILED", "git_commit": "def456"},
        ]
        (deploy_dir / ".deploy_history.json").write_text(json.dumps(entries))

        from excelmanus.updater import get_deploy_history_structured

        result = get_deploy_history_structured(tmp_path)
        assert len(result) == 2
        assert result[0]["release_id"] == "20260304T100000"
        assert result[1]["status"] == "FAILED"

    def test_invalid_json_returns_empty(self, tmp_path: Path):
        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir(parents=True)
        (deploy_dir / ".deploy_history.json").write_text("not valid json{{{")

        from excelmanus.updater import get_deploy_history_structured

        result = get_deploy_history_structured(tmp_path)
        assert result == []
