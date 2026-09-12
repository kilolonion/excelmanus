"""Tests for stop-then-upgrade helper, HOME backups, and deploy_mode contraction."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from excelmanus.updater import (
    UpdateResult,
    UpgradeOutcome,
    VersionInfo,
    backup_user_data,
    find_backup_dir,
    list_backups,
)
from excelmanus.upgrade.apply import apply_on_stopped_tree
from excelmanus.upgrade.helper import run_helper
from excelmanus.upgrade.runtime import write_request


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t.example",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t.example",
    }
    return subprocess.check_output(["git", *args], cwd=cwd, text=True, env=env).strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    (path / "README").write_text("a\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "init")


class TestApplyFfOnly:
    def test_divergent_history_does_not_hard_reset(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        origin = tmp_path / "origin.git"
        other = tmp_path / "other"
        _init_repo(repo)
        subprocess.check_call(["git", "clone", "--bare", str(repo), str(origin)])
        _git(repo, "remote", "add", "origin", str(origin))
        _git(repo, "fetch", "origin")

        subprocess.check_call(["git", "clone", str(origin), str(other)])
        (other / "README").write_text("remote\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", "remote")
        subprocess.check_call(["git", "push", "origin", "HEAD:main"], cwd=other)

        (repo / "README").write_text("local\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "local")
        local_head = _git(repo, "rev-parse", "HEAD")
        _git(repo, "fetch", "origin")

        fake = VersionInfo(
            current="1.0.0", latest="1.0.1", has_update=True,
            commits_behind=1, check_method="git",
        )
        with patch("excelmanus.upgrade.apply.check_for_updates", return_value=fake):
            result = apply_on_stopped_tree(repo, skip_deps=True)

        assert result.success is False
        assert result.outcome is UpgradeOutcome.FF_CONFLICT
        assert "fast-forward" in (result.error or "").lower() or "冲突" in (result.error or "")
        assert _git(repo, "rev-parse", "HEAD") == local_head
        assert "reset --hard" not in json.dumps(result.steps_completed)


class TestHelperStopThenApply:
    def test_stop_runs_before_apply(self, tmp_path: Path) -> None:
        order: list[str] = []
        write_request({"action": "upgrade", "skip_backup": True, "skip_deps": True})

        def stop(_runtime, **_kwargs):
            order.append("stop")

        def apply(*_a, **_k):
            order.append("apply")
            return UpdateResult(outcome=UpgradeOutcome.ALREADY_LATEST)

        with (
            patch("excelmanus.upgrade.helper.stop_supervised", side_effect=stop),
            patch("excelmanus.upgrade.apply.apply_on_stopped_tree", side_effect=apply),
        ):
            code = run_helper(tmp_path, skip_start=True)

        assert code == 0
        assert order == ["stop", "apply"]
        from excelmanus.upgrade.runtime import read_upgrade_status
        status = read_upgrade_status()
        assert status is not None
        assert status.get("ok") is True
        assert status.get("outcome") == UpgradeOutcome.ALREADY_LATEST.value


class TestBackupHome:
    def test_backup_scans_excelmanus_home_and_restore_finds_it(self, tmp_path: Path) -> None:
        home = Path(os.environ["EXCELMANUS_HOME"])
        (home / "app.db").write_text("db", encoding="utf-8")
        (home / "config.env").write_text("KEY=1\n", encoding="utf-8")
        (home / "data").mkdir()
        (home / "data" / "upload.bin").write_text("u", encoding="utf-8")
        project = tmp_path / "proj"
        project.mkdir()
        (project / ".env").write_text("ROOT=1\n", encoding="utf-8")

        result = backup_user_data(project)
        assert result.success
        assert "app.db" in result.files_backed_up
        assert "config.env" in result.files_backed_up
        assert "data/" in result.files_backed_up
        assert ".env" in result.files_backed_up

        listed = list_backups(project)
        assert listed
        name = listed[0]["name"]
        found = find_backup_dir(name, project)
        assert found is not None
        assert found.is_dir()
        assert (found / "excelmanus_home" / "app.db").is_file()

    def test_sqlite_backup_api_roundtrip(self, tmp_path: Path) -> None:
        import sqlite3

        from excelmanus.updater import restore_from_backup

        home = Path(os.environ["EXCELMANUS_HOME"])
        db_path = home / "live.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'ok')")
        conn.commit()
        conn.close()
        project = tmp_path / "proj"
        project.mkdir()
        result = backup_user_data(project)
        assert result.success
        copied = Path(result.backup_dir) / "excelmanus_home" / "live.db"
        assert copied.is_file()
        verify = sqlite3.connect(str(copied))
        assert verify.execute("SELECT val FROM t WHERE id = 1").fetchone()[0] == "ok"
        verify.close()

        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE t SET val = 'changed'")
        conn.commit()
        conn.close()
        assert restore_from_backup(result.backup_dir, project)
        assert not db_path.with_name(db_path.name + "._restore_tmp").exists()
        check = sqlite3.connect(str(db_path))
        assert check.execute("SELECT val FROM t").fetchone()[0] == "ok"
        check.close()

    def test_restore_tree_keeps_old_on_rename_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.updater import restore_from_backup

        home = Path(os.environ["EXCELMANUS_HOME"])
        data = home / "data"
        data.mkdir()
        (data / "keep.bin").write_text("live", encoding="utf-8")
        project = tmp_path / "proj"
        project.mkdir()
        result = backup_user_data(project)
        assert result.success
        (data / "keep.bin").write_text("changed", encoding="utf-8")

        real_rename = os.rename
        calls = {"n": 0}

        def flaky_rename(src, dst):
            src_s = str(src)
            if src_s.endswith("._restore_tmp") and calls["n"] == 0:
                calls["n"] += 1
                raise OSError("simulated rename failure")
            return real_rename(src, dst)

        monkeypatch.setattr("excelmanus.updater.os.rename", flaky_rename)
        assert restore_from_backup(result.backup_dir, project) is False
        assert data.is_dir()
        assert (data / "keep.bin").is_file()
        assert (data / "keep.bin").read_text(encoding="utf-8") in {"live", "changed"}


class TestControlPlane:
    def test_server_mode_rejects_upgrade(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.api_routes_version import _require_control_plane

        monkeypatch.setattr(
            "excelmanus.api_app_state.get_config",
            lambda: SimpleNamespace(is_server=True, deploy_mode="server"),
        )
        req = MagicMock()
        req.client.host = "127.0.0.1"
        denied = _require_control_plane(req)
        assert denied is not None
        assert denied.status_code == 403

    def test_non_loopback_rejected_on_standalone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.api_routes_version import _require_control_plane

        monkeypatch.setattr(
            "excelmanus.api_app_state.get_config",
            lambda: SimpleNamespace(is_server=False, deploy_mode="standalone"),
        )
        req = MagicMock()
        req.client.host = "203.0.113.10"
        denied = _require_control_plane(req)
        assert denied is not None
        assert denied.status_code == 403

    def test_loopback_standalone_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.api_routes_version import _require_control_plane

        monkeypatch.setattr(
            "excelmanus.api_app_state.get_config",
            lambda: SimpleNamespace(is_server=False, deploy_mode="standalone"),
        )
        req = MagicMock()
        req.client.host = "127.0.0.1"
        assert _require_control_plane(req) is None


class TestDeployMode:
    def test_detect_is_standalone(self) -> None:
        from excelmanus.config import _detect_deploy_mode

        assert _detect_deploy_mode() == "standalone"

    def test_docker_env_value_becomes_standalone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.config import ExcelManusConfig, load_config

        monkeypatch.setenv("EXCELMANUS_API_KEY", "test-key")
        monkeypatch.setenv("EXCELMANUS_BASE_URL", "https://example.com/v1")
        monkeypatch.setenv("EXCELMANUS_MODEL", "test-model")
        monkeypatch.setenv("EXCELMANUS_DEPLOY_MODE", "docker")
        cfg = load_config()
        assert cfg.deploy_mode == "standalone"
        assert not hasattr(ExcelManusConfig, "is_docker")
        assert not hasattr(cfg, "is_docker")


class TestCheckForUpdates:
    def test_commits_behind_same_semver_is_update(self, tmp_path: Path) -> None:
        from excelmanus.updater import check_for_updates

        repo = tmp_path / "repo"
        origin = tmp_path / "origin.git"
        other = tmp_path / "other"
        _init_repo(repo)
        (repo / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "ver")
        subprocess.check_call(["git", "clone", "--bare", str(repo), str(origin)])
        _git(repo, "remote", "add", "origin", str(origin))
        subprocess.check_call(["git", "clone", str(origin), str(other)])
        (other / "README").write_text("remote\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", "remote same version")
        subprocess.check_call(["git", "push", "origin", "HEAD:main"], cwd=other)

        info = check_for_updates(repo, force=True)
        assert not info.check_failed
        assert info.has_update is True
        assert info.commits_behind == 1


class TestApplyBuildRollback:
    def test_frontend_build_failure_rolls_back_git(self, tmp_path: Path) -> None:
        import excelmanus.upgrade.apply as apply_mod

        repo = tmp_path / "repo"
        origin = tmp_path / "origin.git"
        other = tmp_path / "other"
        _init_repo(repo)
        (repo / "web").mkdir()
        (repo / "web" / "package.json").write_text('{"name":"x"}\n', encoding="utf-8")
        (repo / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "web")
        local_head = _git(repo, "rev-parse", "HEAD")

        subprocess.check_call(["git", "clone", "--bare", str(repo), str(origin)])
        _git(repo, "remote", "add", "origin", str(origin))
        subprocess.check_call(["git", "clone", str(origin), str(other)])
        (other / "README").write_text("remote\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", "remote")
        subprocess.check_call(["git", "push", "origin", "HEAD:main"], cwd=other)
        _git(repo, "fetch", "origin")

        original_run = apply_mod._run_cmd

        def wrapped(cmd, **kwargs):
            if cmd and cmd[0] == "npm":
                return 1, "", "build failed"
            return original_run(cmd, **kwargs)

        fake = VersionInfo(
            current="1.0.0", latest="1.0.0", has_update=True,
            commits_behind=1, check_method="git",
        )
        with (
            patch("excelmanus.upgrade.apply.check_for_updates", return_value=fake),
            patch("excelmanus.upgrade.apply._run_cmd", side_effect=wrapped),
        ):
            result = apply_on_stopped_tree(repo, skip_deps=True)

        assert result.success is False
        assert result.outcome is UpgradeOutcome.BUILD_FAILED
        assert "前端构建失败" in (result.error or "")
        assert "回滚" in (result.error or "")
        assert _git(repo, "rev-parse", "HEAD") == local_head


class TestHelperStatusAndStop:
    def test_helper_writes_upgrade_status_on_failure(self, tmp_path: Path) -> None:
        from excelmanus.upgrade.runtime import read_upgrade_status

        write_request({"action": "upgrade", "skip_backup": True, "skip_deps": True})

        with (
            patch("excelmanus.upgrade.helper.stop_supervised"),
            patch(
                "excelmanus.upgrade.apply.apply_on_stopped_tree",
                return_value=UpdateResult(
                    outcome=UpgradeOutcome.FF_CONFLICT,
                    error="fast-forward 合并失败",
                ),
            ),
        ):
            code = run_helper(tmp_path, skip_start=True)

        assert code == 1
        status = read_upgrade_status()
        assert status is not None
        assert status.get("ok") is False
        assert status.get("outcome") == UpgradeOutcome.FF_CONFLICT.value
        assert "fast-forward" in (status.get("error") or "")

    def test_stale_supervisor_skips_killpg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.upgrade import helper as h

        killed: list[tuple] = []
        monkeypatch.setattr(h.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))
        monkeypatch.setattr(h, "_pid_alive", lambda pid: False)
        monkeypatch.setattr(h, "_port_busy", lambda _p: False)
        monkeypatch.setattr(h.time, "sleep", lambda *_a, **_k: None)
        h.stop_supervised(
            {"supervisor_pid": 424242, "pgid": 424242, "backend_port": 18000},
            wait_s=0,
        )
        assert killed == []

    def test_empty_runtime_clears_default_ports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from excelmanus.upgrade import helper as h

        ports: list[int] = []
        monkeypatch.setattr(h, "_port_busy", lambda _p: True)
        monkeypatch.setattr(h, "_kill_port", lambda p: ports.append(p))
        monkeypatch.setattr(h.time, "sleep", lambda *_a, **_k: None)
        h.stop_supervised({}, wait_s=0)
        assert 8000 in ports
        assert 3000 in ports


class TestCheckFailedIsNotLatest:
    def test_apply_check_failed_is_failure(self, tmp_path: Path) -> None:
        fake = VersionInfo(
            current="1.0.0", latest="1.0.0", has_update=False,
            check_failed=True, error="无法从 origin 或 GitHub 获取远程提交",
        )
        with patch("excelmanus.upgrade.apply.check_for_updates", return_value=fake):
            result = apply_on_stopped_tree(tmp_path, skip_deps=True)

        assert result.success is False
        assert result.outcome is UpgradeOutcome.CHECK_FAILED
        assert "已是最新" not in (result.error or "")

    def test_apply_already_latest_uses_outcome(self, tmp_path: Path) -> None:
        fake = VersionInfo(
            current="1.0.0", latest="1.0.0", has_update=False, check_failed=False,
        )
        with patch("excelmanus.upgrade.apply.check_for_updates", return_value=fake):
            result = apply_on_stopped_tree(tmp_path, skip_deps=True)

        assert result.success is True
        assert result.outcome is UpgradeOutcome.ALREADY_LATEST
        assert result.error == ""

    def test_local_only_branch_is_check_failed(self, tmp_path: Path) -> None:
        from excelmanus.updater import _invalidate_version_cache, check_for_updates

        _invalidate_version_cache()
        repo = tmp_path / "repo"
        origin = tmp_path / "origin.git"
        _init_repo(repo)
        (repo / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "ver")
        subprocess.check_call(["git", "clone", "--bare", str(repo), str(origin)])
        _git(repo, "remote", "add", "origin", str(origin))
        _git(repo, "fetch", "origin")
        _git(repo, "checkout", "-b", "local-only")

        info = check_for_updates(repo, force=True)
        assert info.check_failed is True
        assert info.has_update is False
        assert info.commits_behind == 0

    def test_helper_writes_check_failed_status(self, tmp_path: Path) -> None:
        from excelmanus.upgrade.runtime import read_upgrade_status

        write_request({"action": "upgrade", "skip_backup": True, "skip_deps": True})
        with (
            patch("excelmanus.upgrade.helper.stop_supervised"),
            patch(
                "excelmanus.upgrade.apply.apply_on_stopped_tree",
                return_value=UpdateResult(
                    outcome=UpgradeOutcome.CHECK_FAILED,
                    error="无法从 origin 或 GitHub 获取远程提交",
                ),
            ),
        ):
            code = run_helper(tmp_path, skip_start=True)

        assert code == 1
        status = read_upgrade_status()
        assert status is not None
        assert status.get("ok") is False
        assert status.get("outcome") == UpgradeOutcome.CHECK_FAILED.value
        assert "already_latest" not in status or status.get("already_latest") is not True


class TestPrecheckFailure:
    def test_precheck_failure_rolls_back_and_fails(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        origin = tmp_path / "origin.git"
        other = tmp_path / "other"
        _init_repo(repo)
        (repo / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "ver")
        local_head = _git(repo, "rev-parse", "HEAD")

        subprocess.check_call(["git", "clone", "--bare", str(repo), str(origin)])
        _git(repo, "remote", "add", "origin", str(origin))
        subprocess.check_call(["git", "clone", str(origin), str(other)])
        (other / "README").write_text("remote\n", encoding="utf-8")
        _git(other, "add", ".")
        _git(other, "commit", "-m", "remote")
        subprocess.check_call(["git", "push", "origin", "HEAD:main"], cwd=other)
        _git(repo, "fetch", "origin")

        fake = VersionInfo(
            current="1.0.0", latest="1.0.0", has_update=True,
            commits_behind=1, check_method="git",
        )
        with (
            patch("excelmanus.upgrade.apply.check_for_updates", return_value=fake),
            patch(
                "excelmanus.upgrade.apply.verify_database_migration",
                return_value=(False, "无法只读读取 schema_version"),
            ),
        ):
            result = apply_on_stopped_tree(repo, skip_deps=True)

        assert result.success is False
        assert result.outcome is UpgradeOutcome.PRECHECK_FAILED
        assert "预检失败" in (result.error or "")
        assert _git(repo, "rev-parse", "HEAD") == local_head

    def test_verify_does_not_construct_database_or_migrate(self, tmp_path: Path) -> None:
        import sqlite3

        from excelmanus.database import _SQLITE_MIGRATIONS
        from excelmanus.updater import verify_database_migration

        home = Path(os.environ["EXCELMANUS_HOME"])
        db_path = home / "excelmanus.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)"
        )
        for sql in _SQLITE_MIGRATIONS[1]:
            conn.execute(sql)
        conn.commit()
        conn.close()

        with patch("excelmanus.database.Database") as db_cls:
            ok, msg = verify_database_migration()

        db_cls.assert_not_called()
        assert ok is True
        assert "待迁移" in msg
        check = sqlite3.connect(str(db_path))
        assert check.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] is None
        check.close()

    def test_verify_corrupt_file_fails(self) -> None:
        from excelmanus.updater import verify_database_migration

        home = Path(os.environ["EXCELMANUS_HOME"])
        db_path = home / "excelmanus.db"
        db_path.write_bytes(b"not a sqlite database")
        ok, msg = verify_database_migration()
        assert ok is False
        assert "schema_version" in msg

    def test_legacy_schema_version_is_stamp_not_latest(self) -> None:
        import sqlite3

        from excelmanus.database import Database
        from excelmanus.updater import _schema_status_message, verify_database_migration

        assert "stamp" in _schema_status_message(25, 1)
        assert "已是最新" not in _schema_status_message(25, 1)
        assert "已是最新" in _schema_status_message(1, 1)
        assert "待迁移" in _schema_status_message(0, 1)

        home = Path(os.environ["EXCELMANUS_HOME"])
        db_path = home / "excelmanus.db"
        db = Database(str(db_path))
        db.close()
        conn = sqlite3.connect(str(db_path))
        conn.execute("DELETE FROM schema_version")
        for version in range(1, 26):
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()
        conn.close()

        ok, msg = verify_database_migration()
        assert ok is True
        assert "stamp" in msg
        assert "已是最新" not in msg


class TestGitAndUv:
    def test_worktree_git_file_counts_as_repo(self, tmp_path: Path) -> None:
        from excelmanus.updater import _is_git_repo

        (tmp_path / ".git").write_text("gitdir: /tmp/main/.git/worktrees/feat\n", encoding="utf-8")
        assert _is_git_repo(tmp_path) is True
        assert _is_git_repo(tmp_path / "missing") is False

    def test_uv_pip_pins_project_venv(self, tmp_path: Path) -> None:
        from excelmanus.updater import _build_pip_cmd

        venv_bin = tmp_path / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        py = venv_bin / "python"
        py.write_text("", encoding="utf-8")
        cmd = _build_pip_cmd(tmp_path, False, True)
        assert cmd[:4] == ["uv", "pip", "install", "--python"]
        assert cmd[4] == str(py)
        assert "-e" in cmd

