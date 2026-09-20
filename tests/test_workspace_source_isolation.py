"""Default data workspace and explicit source-workspace adoption."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.database import Database
from excelmanus.security.guard import FileAccessGuard, SecurityViolationError
from excelmanus.security.source_isolation import set_workspace_source_access, workspace_allows_source
from excelmanus.stores.workspace_store import WorkspaceStore
from excelmanus.workspace.paths import default_workspace_path
from tests.test_sandbox_hook import _run_in_sandbox
from tests.test_workspace_sessions import _workspace_api
from excelmanus.api import app


def test_default_workspace_does_not_follow_the_application_cwd(tmp_path, monkeypatch):
    from excelmanus.data_home import get_package_root
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path / "home"))
    expected = str(tmp_path / "home" / "data")
    assert default_workspace_path(SimpleNamespace(workspace_root=str(get_package_root()), data_root="")) == expected
    assert default_workspace_path(SimpleNamespace(workspace_root="", data_root="")) == expected
    custom = tmp_path / "reports"
    assert default_workspace_path(SimpleNamespace(workspace_root=str(custom), data_root="")) == str(custom)


def test_explicit_adoption_persists_without_changing_workspace_identity(tmp_path):
    root = tmp_path / "code"
    (root / "web").mkdir(parents=True)
    code = root / "web" / "app.ts"
    code.write_text("export const value = 1", encoding="utf-8")
    db_path = tmp_path / "registry.db"
    db = Database(str(db_path))
    store = WorkspaceStore(db)
    automatic = store.ensure_path(str(root))
    assert not automatic["source_access"]
    with pytest.raises(SecurityViolationError, match="PRODUCT_SOURCE_FORBIDDEN"):
        FileAccessGuard(str(root)).resolve_and_validate("web/app.ts")
    explicit, created = store.create(str(root))
    assert not created
    assert explicit["id"] == automatic["id"]
    assert FileAccessGuard(str(root)).resolve_and_validate("web/app.ts") == code
    db.close()

    set_workspace_source_access(root, False)
    db = Database(str(db_path))
    store = WorkspaceStore(db)
    assert store.get(explicit["id"])["source_access"]
    assert workspace_allows_source(root)
    # Source opt-in does not authorize credentials or files outside this root.
    with pytest.raises(SecurityViolationError):
        FileAccessGuard(str(root)).resolve_and_validate("../outside.py")
    assert store.delete(explicit["id"])
    assert not workspace_allows_source(root)
    db.close()


@pytest.mark.parametrize("tier", ["GREEN", "YELLOW", "RED"])
def test_subprocess_has_the_same_source_boundary(tmp_path, tier):
    (tmp_path / "web").mkdir()
    source = tmp_path / "web" / "sample.txt"
    source.write_text("workspace-source", encoding="utf-8")
    script = f"print(open({str(source)!r}).read())"
    denied = _run_in_sandbox(tmp_path, script, tier)
    assert denied.returncode != 0
    assert "PRODUCT_SOURCE_FORBIDDEN" in denied.stderr
    db = Database(str(tmp_path / ".registry.db"))
    store = WorkspaceStore(db)
    rec, _ = store.create(str(tmp_path))
    try:
        allowed = _run_in_sandbox(tmp_path, script, tier)
        assert allowed.returncode == 0, allowed.stderr
        assert "workspace-source" in allowed.stdout
    finally:
        store.delete(rec["id"])
        db.close()


@pytest.mark.asyncio
async def test_sidebar_mentions_and_catalog_ignore_code_until_adoption(tmp_path):
    from excelmanus.workspace.identity import catalog
    with _workspace_api(tmp_path) as state:
        root = state["default_ws"]
        (root / "desktop" / "dist").mkdir(parents=True)
        (root / "desktop" / "dist" / "bundle.js").write_text("source", encoding="utf-8")
        (root / "uploads").mkdir(exist_ok=True)
        (root / "uploads" / "report.csv").write_text("a,b\n1,2", encoding="utf-8")
        (root / "uploads" / "report.csv.em-lock").write_text("lock", encoding="utf-8")
        session = await state["manager"].create_or_reuse_session()
        params = {"session_id": session["id"]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = (await client.get("/api/v1/files/workspace/list", params=params)).json()
            assert {f["path"] for f in listed["files"]} == {"uploads", "uploads/report.csv"}
            mentions = (await client.get("/api/v1/mentions", params=params)).json()
            assert "desktop/" not in mentions["files"]
            denied = await client.get("/api/v1/mentions", params={**params, "path": "desktop/dist"})
            assert denied.status_code == 403
            assert all(not item.relative.startswith("desktop/") for item in catalog(root))
            adopted = await client.post("/api/v1/workspaces", json={"path": str(root)})
            assert adopted.status_code == 200
            listed = (await client.get("/api/v1/files/workspace/list", params=params)).json()
            assert "desktop/dist/bundle.js" in {f["path"] for f in listed["files"]}
            mentions = (await client.get("/api/v1/mentions", params=params)).json()
            assert "desktop/" in mentions["files"]
            assert any(item.relative == "desktop/dist/bundle.js" for item in catalog(root))
