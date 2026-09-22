"""Web updates must be authenticated, exclusive, observable and data-preserving."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from excelmanus.upgrade.runtime import read_request, read_upgrade_status, write_request, write_runtime


def request(host="127.0.0.1"):
    return Request({"type": "http", "method": "POST", "path": "/api/v1/version/upgrade",
                    "headers": [(b"x-requested-with", b"ExcelManus")],
                    "client": (host, 1234), "query_string": b""})


@pytest.fixture
def managed(tmp_path, monkeypatch):
    from excelmanus import api_routes_version as routes
    from excelmanus.api_app_state import AppRuntime, bind_runtime, reset_runtime
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path / "profile"))
    monkeypatch.delenv("EXCELMANUS_DESKTOP", raising=False)
    monkeypatch.delenv("EXCELMANUS_WEB_UPGRADE_ENABLED", raising=False)
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    (root / "deploy").mkdir()
    (root / "deploy" / "start.ps1").touch()
    (root / "deploy" / "start.sh").touch()
    monkeypatch.setattr(routes, "_get_project_root", lambda: root)
    monkeypatch.setattr("excelmanus.auth.access.access_enabled", lambda: False)
    monkeypatch.setattr("excelmanus.auth.access.authenticated", lambda r: False)
    monkeypatch.setattr("excelmanus.upgrade.preflight.check_upgrade_environment", lambda root: None)
    state = AppRuntime(config=SimpleNamespace(is_server=False))
    token = bind_runtime(state)
    write_runtime({"project_root": str(root), "workers": 1})
    yield routes, root, state
    reset_runtime(token)


def test_server_update_requires_opt_in_and_auth(managed, monkeypatch):
    routes, _, state = managed
    state.config.is_server = True
    assert routes._web_upgrade_denial(request()).status_code == 403
    monkeypatch.setenv("EXCELMANUS_WEB_UPGRADE_ENABLED", "1")
    assert routes._web_upgrade_denial(request()).status_code == 403
    monkeypatch.setattr("excelmanus.auth.access.access_enabled", lambda: True)
    monkeypatch.setattr("excelmanus.auth.access.authenticated", lambda r: True)
    assert routes._web_upgrade_denial(request("192.0.2.1")) is None
    assert routes._require_control_plane(request()).status_code == 403


@pytest.mark.parametrize("options", [{"workers": 2}, {"workers": "bad"}, {"backend_only": True}, {"project_root": "missing"}])
def test_unsupported_deployment_has_explicit_reason(managed, options):
    routes, root, _ = managed
    write_runtime({"project_root": str(root), "workers": 1, **options})
    assert routes._web_upgrade_denial(request()).status_code == 409


@pytest.mark.asyncio
async def test_update_is_exclusive_and_always_backs_up(managed, monkeypatch):
    routes, root, state = managed
    spawn = MagicMock()
    monkeypatch.setattr("excelmanus.upgrade.helper.spawn_detached_helper", spawn)
    response = await routes.version_upgrade(routes.UpgradeRequest(skip_backup=True), request())
    assert response.status_code == 202
    request_id = json.loads(response.body)["request_id"]
    assert read_request()["request_id"] == request_id
    assert read_request()["skip_backup"] is False
    assert read_upgrade_status()["ok"] is None
    assert "finished_at" not in read_upgrade_status()
    assert state.draining
    assert routes._schedule_helper_and_exit(root, {"action": "upgrade"}).status_code == 409
    spawn.assert_called_once_with(root)


@pytest.mark.asyncio
async def test_running_task_prevents_shutdown(managed, monkeypatch):
    routes, _, state = managed
    state.active_chat_tasks["busy"] = SimpleNamespace(done=lambda: False)
    spawn = MagicMock()
    monkeypatch.setattr("excelmanus.upgrade.helper.spawn_detached_helper", spawn)
    assert (await routes.version_upgrade(routes.UpgradeRequest(), request())).status_code == 409
    assert not state.draining
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_spawn_failure_keeps_server_running(managed, monkeypatch):
    routes, _, state = managed
    monkeypatch.setattr("excelmanus.upgrade.helper.spawn_detached_helper", MagicMock(side_effect=OSError("cannot spawn")))
    assert (await routes.version_upgrade(routes.UpgradeRequest(), request())).status_code == 500
    assert not state.draining
    assert read_request() is None
    assert read_upgrade_status()["ok"] is False


@pytest.mark.asyncio
async def test_environment_failure_keeps_service_online(managed, monkeypatch):
    routes, _, state = managed
    monkeypatch.setattr("excelmanus.upgrade.preflight.check_upgrade_environment", lambda root: "Node.js is missing")
    spawn = MagicMock()
    monkeypatch.setattr("excelmanus.upgrade.helper.spawn_detached_helper", spawn)
    response = await routes.version_upgrade(routes.UpgradeRequest(), request())
    assert response.status_code == 409
    assert "Node.js" in json.loads(response.body)["error"]
    assert not state.draining and read_request() is None
    spawn.assert_not_called()


def test_helper_exception_publishes_matching_failure(managed, monkeypatch):
    from excelmanus.upgrade.helper import run_helper
    _, root, _ = managed
    write_request({"action": "upgrade", "request_id": "new", "skip_backup": True})
    def apply(*args, **kwargs):
        kwargs["progress_cb"]("building", 75)
        status = read_upgrade_status()
        assert status["request_id"] == "new" and status["ok"] is None
        assert "finished_at" not in status
        raise RuntimeError("build exploded")
    monkeypatch.setattr("excelmanus.upgrade.apply.apply_on_stopped_tree", apply)
    assert run_helper(root, skip_stop=True, skip_start=True) == 1
    assert read_upgrade_status()["request_id"] == "new"
    assert read_upgrade_status()["ok"] is False
    assert "build exploded" in read_upgrade_status()["error"]
    assert read_request() is None


@pytest.mark.parametrize("failure", ["backup", "restore", "stop"])
def test_helper_recovers_services_from_pre_apply_exceptions(managed, monkeypatch, failure):
    from excelmanus.upgrade.helper import run_helper
    _, root, _ = managed
    write_request({"action": "restore" if failure == "restore" else "upgrade", "request_id": "failure-test"})
    start = MagicMock()
    monkeypatch.setattr("excelmanus.upgrade.helper.exec_start", start)
    monkeypatch.setattr("excelmanus.upgrade.helper.time.sleep", lambda _: None)
    if failure == "backup":
        monkeypatch.setattr("excelmanus.updater.backup_user_data", MagicMock(side_effect=PermissionError("read-only profile")))
    elif failure == "restore":
        monkeypatch.setattr("excelmanus.updater.find_backup_dir", MagicMock(side_effect=OSError("disk error")))
    else:
        monkeypatch.setattr("excelmanus.upgrade.helper.stop_supervised", MagicMock(side_effect=OSError("stop failed")))
    assert run_helper(root, skip_stop=failure != "stop") == 1
    assert read_upgrade_status()["ok"] is False
    assert read_upgrade_status()["request_id"] == "failure-test"
    assert read_request() is None
    start.assert_called_once()


def test_helper_records_restart_failure_after_backup_failure(managed, monkeypatch):
    from excelmanus.upgrade.helper import run_helper
    _, root, _ = managed
    write_request({"action": "upgrade", "request_id": "restart-failure"})
    monkeypatch.setattr("excelmanus.updater.backup_user_data", MagicMock(side_effect=PermissionError("backup denied")))
    monkeypatch.setattr("excelmanus.upgrade.helper.exec_start", MagicMock(side_effect=OSError("missing interpreter")))
    assert run_helper(root, skip_stop=True) == 1
    assert "无法恢复服务" in read_upgrade_status()["error"]


def test_check_carries_successfully_fetched_target(tmp_path, monkeypatch):
    from excelmanus.updater import check_for_updates
    (tmp_path / ".git").mkdir()
    def command(cmd, **kwargs):
        if cmd[:3] == ["git", "fetch", "origin"]:
            return 1, "", "origin unavailable"
        if "--abbrev-ref" in cmd:
            return 0, "main", ""
        if "--verify" in cmd:
            return 0, "a" * 40, ""
        if "rev-list" in cmd:
            return 0, "1", ""
        return 0, "", ""
    monkeypatch.setattr("excelmanus.updater._run_cmd", command)
    info = check_for_updates(tmp_path, force=True)
    assert info.has_update and not info.check_failed
    assert info.target_ref == "github/main"
    assert info.target_commit == "a" * 40


def test_backup_preserves_key_and_user_workbook(managed, monkeypatch):
    from pathlib import Path
    from excelmanus.data_home import get_excelmanus_home
    from excelmanus.updater import backup_user_data
    _, root, _ = managed
    home = get_excelmanus_home()
    key = home / ".secret_key"
    key.write_bytes(b"test-only-key")
    book = root / "user.xlsx"
    book.write_bytes(b"user-owned-content")
    result = backup_user_data(root)
    assert result.success
    assert (Path(result.backup_dir) / "excelmanus_home" / ".secret_key").read_bytes() == key.read_bytes()
    assert book.read_bytes() == b"user-owned-content"
