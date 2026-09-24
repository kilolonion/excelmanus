"""Local browser folder selection: native results and API boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock
import subprocess

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from excelmanus import api_routes_workspaces as routes
from excelmanus.workspace import folder_picker as picker


@pytest.fixture
def native(monkeypatch):
    monkeypatch.setattr(picker.sys, "platform", "darwin")
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="\n"))
    monkeypatch.setattr(picker.subprocess, "run", run)
    return run


def test_native_selection_preserves_unicode_and_spaces(tmp_path, native):
    folder = tmp_path / "报表 '季度' $(test) "
    folder.mkdir()
    native.return_value.stdout = str(folder) + "/\n"
    assert picker.select_local_folder() == str(folder)
    assert native.call_args.args[0][0] == "/usr/bin/osascript"
    assert str(folder) not in native.call_args.args[0][-1]
    assert native.call_args.kwargs["timeout"] > 30


def test_cancel_is_not_an_error(native):
    assert picker.select_local_folder() is None


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout=""),
    SimpleNamespace(returncode=0, stdout="/missing-workspace-picker-test/\n"),
])
def test_native_failure_releases_picker_for_retry(native, result):
    native.return_value = result
    with pytest.raises(picker.FolderPickerError):
        picker.select_local_folder()
    native.return_value = SimpleNamespace(returncode=0, stdout="\n")
    assert picker.select_local_folder() is None


def test_timeout_releases_picker_for_retry(native):
    native.side_effect = subprocess.TimeoutExpired("osascript", 300)
    with pytest.raises(picker.FolderPickerError, match="超时") as error:
        picker.select_local_folder()
    assert error.value.status_code == 408
    native.side_effect = None
    assert picker.select_local_folder() is None


def test_duplicate_request_does_not_open_another_dialog(native):
    with picker._picker_lock:
        with pytest.raises(picker.FolderPickerError) as error:
            picker.select_local_folder()
    assert error.value.status_code == 409
    native.assert_not_called()


@pytest.mark.parametrize(("peer", "origin", "forwarded", "server", "status"), [
    ("127.0.0.1", "http://localhost:3000", "", False, 200),
    ("::1", "http://[::1]:3000", "::ffff:127.0.0.1", False, 200),
    ("127.0.0.1", "http://127.0.0.1:3000", "::1, 127.0.0.1", False, 200),
    ("192.168.1.2", "http://localhost:3000", "", False, 403),
    ("127.0.0.1", "https://remote.example", "", False, 403),
    ("127.0.0.1", "http://localhost.evil.example", "", False, 403),
    ("127.0.0.1", "http://localhost:3000", "192.168.1.2", False, 403),
    ("127.0.0.1", "http://localhost:3000", "", True, 403),
    ("127.0.0.1", "null", "", False, 403),
    ("127.0.0.1", "", "", False, 403),
])
async def test_picker_api_local_boundary(monkeypatch, peer, origin, forwarded, server, status):
    native = Mock(return_value="/Users/example/报表")
    monkeypatch.setattr(routes, "select_local_folder", native)
    monkeypatch.setattr(routes, "get_config", lambda: SimpleNamespace(is_server=server))
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app, client=(peer, 123)), base_url="http://localhost") as client:
        response = await client.post("/api/v1/workspaces/select-folder", headers={
            "Origin": origin, "X-Forwarded-For": forwarded, "X-Requested-With": "ExcelManus",
        })
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"path": "/Users/example/报表"}
        assert response.headers["cache-control"] == "no-store"
    else:
        native.assert_not_called()


async def test_picker_api_cancel_error_and_csrf(monkeypatch):
    native = Mock(return_value=None)
    monkeypatch.setattr(routes, "select_local_folder", native)
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        headers = {"Origin": "http://localhost:3000", "X-Requested-With": "ExcelManus"}
        response = await client.post("/api/v1/workspaces/select-folder", headers=headers)
        assert response.json() == {"path": None}
        native.side_effect = picker.FolderPickerError("请重试", 408)
        response = await client.post("/api/v1/workspaces/select-folder", headers=headers)
        assert response.status_code == 408
        assert "请重试" in response.text
        native.reset_mock()
        response = await client.post("/api/v1/workspaces/select-folder", headers={"Origin": "http://localhost:3000"})
        assert response.status_code == 403
        native.assert_not_called()
