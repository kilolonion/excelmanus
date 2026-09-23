from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from starlette.requests import Request

from excelmanus.data_home import (
    InstallationDeletionError,
    discover_old_installations,
    get_installations_path,
    prune_missing_installations,
    register_installation,
    remove_installation,
)


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/version/installations",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
    })


def _use_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "profile"
    monkeypatch.setenv("EXCELMANUS_HOME", str(home))
    return home


def test_installation_list_marks_current_available_and_missing(monkeypatch, tmp_path: Path) -> None:
    home = _use_home(monkeypatch, tmp_path)
    current = tmp_path / "current"
    other = tmp_path / "other"
    current.mkdir()
    other.mkdir()
    register_installation(current, "1.8.0")
    register_installation(other, "1.7.2")
    missing = tmp_path / "removed"
    payload = json.loads(get_installations_path().read_text(encoding="utf-8"))
    payload["installations"].append(
        {"path": str(missing), "version": "1.6.0", "last_seen": "2026-01-01T00:00:00+00:00"}
    )
    get_installations_path().write_text(json.dumps(payload), encoding="utf-8")

    records = discover_old_installations(current)

    assert [record["status"] for record in records] == ["current", "available", "missing"]
    assert records[0]["is_current"] is True
    assert records[0]["exists"] is True
    assert records[1]["is_current"] is False
    assert records[2]["exists"] is False
    assert home.exists()


def test_remove_installation_normalizes_path_and_keeps_directory(monkeypatch, tmp_path: Path) -> None:
    _use_home(monkeypatch, tmp_path)
    install = tmp_path / "legacy"
    install.mkdir()
    register_installation(install, "1.7.2")

    removed = remove_installation(str(install / "."))

    assert removed is not None
    assert not discover_old_installations()
    assert install.is_dir()


def test_remove_installation_can_delete_an_old_directory(monkeypatch, tmp_path: Path) -> None:
    _use_home(monkeypatch, tmp_path)
    install = tmp_path / "legacy"
    (install / "excelmanus").mkdir(parents=True)
    (install / "excelmanus" / "__init__.py").write_text("__version__ = '1.7.2'", encoding="utf-8")
    register_installation(install, "1.7.2")

    removed = remove_installation(install, delete_directory=True)

    assert removed is not None
    assert removed["directory_deleted"] is True
    assert not install.exists()
    assert not discover_old_installations()


def test_remove_installation_rejects_reparse_children(monkeypatch, tmp_path: Path) -> None:
    _use_home(monkeypatch, tmp_path)
    install = tmp_path / "legacy"
    outside = tmp_path / "outside"
    install.mkdir()
    outside.mkdir()
    linked = install / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建目录符号链接")
    register_installation(install, "1.7.2")

    with pytest.raises(InstallationDeletionError):
        remove_installation(install, delete_directory=True)

    assert install.is_dir()
    assert outside.is_dir()


def test_prune_missing_installations_preserves_current(monkeypatch, tmp_path: Path) -> None:
    _use_home(monkeypatch, tmp_path)
    current = tmp_path / "current"
    current.mkdir()
    missing = tmp_path / "missing"
    register_installation(current, "1.8.0")
    register_installation(missing, "1.7.2")

    removed = prune_missing_installations(current)

    assert [item["path"] for item in removed] == [str(missing.resolve())]
    remaining = discover_old_installations(current)
    assert len(remaining) == 1
    assert remaining[0]["is_current"] is True


@pytest.mark.asyncio
async def test_installation_routes_protect_current_and_cleanup_records(monkeypatch, tmp_path: Path) -> None:
    _use_home(monkeypatch, tmp_path)
    current = tmp_path / "current"
    legacy = tmp_path / "legacy"
    current.mkdir()
    legacy.mkdir()
    register_installation(current, "1.8.0")
    register_installation(legacy, "1.7.2")

    from excelmanus import api_routes_version as routes

    monkeypatch.setattr(routes, "_get_project_root", lambda: current)
    monkeypatch.setattr(routes, "_require_control_plane", lambda request: None)

    listing = await routes.list_installations(_request())
    records = json.loads(listing.body)["installations"]
    assert [record["status"] for record in records] == ["current", "available"]

    protected = await routes.delete_installation(
        routes.DeleteInstallationRequest(path=str(current)), _request()
    )
    assert protected.status_code == 409

    removed = await routes.delete_installation(
        routes.DeleteInstallationRequest(path=str(legacy / "."), delete_directory=True), _request()
    )
    assert removed.status_code == 200
    assert json.loads(removed.body)["directory_deleted"] is True
    assert not legacy.exists()
    assert not discover_old_installations(current)[1:]
