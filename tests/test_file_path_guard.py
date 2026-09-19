"""HTTP file path confinement and upload name sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import symlink_or_skip

from excelmanus.api_app_state import sanitize_upload_filename, set_config
from excelmanus.api_app_state import resolve_excel_path, safe_uploads_path
from excelmanus.security.guard import (
    FileAccessGuard,
    SecurityViolationError,
    contained_in,
)
from excelmanus.security.url_fetch import UnsafeURLError, assert_public_http_url


def test_contained_in_rejects_prefix_sibling(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    sibling = tmp_path / "workspace-evil"
    root.mkdir()
    sibling.mkdir()
    secret = sibling / "x.json"
    secret.write_text("{}", encoding="utf-8")
    with pytest.raises(SecurityViolationError):
        contained_in(root, secret)


def test_path_in_workspace_rejects_prefix_sibling(tmp_path: Path) -> None:
    from excelmanus.session import _path_in_workspace

    ws = tmp_path / "workspace"
    evil = tmp_path / "workspace-evil"
    ws.mkdir()
    evil.mkdir()
    (evil / "x.txt").write_text("x", encoding="utf-8")
    (ws / "y.txt").write_text("ok", encoding="utf-8")
    assert _path_in_workspace(str(evil / "x.txt"), str(ws)) is False
    assert _path_in_workspace(str(ws / "y.txt"), str(ws)) is True


def test_safe_uploads_rejects_prefix_sibling(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    other = tmp_path / "uploads2"
    uploads.mkdir()
    other.mkdir()
    (other / "x.txt").write_text("no", encoding="utf-8")
    assert safe_uploads_path(uploads, "../uploads2/x.txt") is None


def test_safe_uploads_rejects_existing_symlink(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    real = tmp_path / "secret.txt"
    real.write_text("secret", encoding="utf-8")
    link = uploads / "link.txt"
    symlink_or_skip(link, real)
    assert safe_uploads_path(uploads, "link.txt") is None


def test_file_service_mkdir_does_not_follow_symlink_parent(tmp_path: Path) -> None:
    from excelmanus.workbook_commit import CommitError
    from excelmanus.workspace.file_service import WorkspaceFileService

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_or_skip(uploads / "trap", outside)
    svc = WorkspaceFileService(tmp_path)
    try:
        svc.mkdir("uploads/trap/child")
    except (CommitError, OSError):
        pass
    assert not (outside / "child").exists()


def test_sanitize_upload_filename_strips_paths() -> None:
    assert ".." not in sanitize_upload_filename("../../etc/passwd")
    assert "/" not in sanitize_upload_filename("a/b/c.xlsx")
    assert sanitize_upload_filename("ok_file.xlsx") == "ok_file.xlsx"
    assert sanitize_upload_filename("产品目录.csv") == "产品目录.csv"


def test_resolve_excel_path_uses_guard(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "book.xlsx").write_bytes(b"xl")
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"no")
    set_config(type("C", (), {"workspace_root": str(ws)})())
    assert resolve_excel_path("book.xlsx", workspace_root=str(ws))
    assert resolve_excel_path(str(outside), workspace_root=str(ws)) is None
    assert resolve_excel_path("../outside.xlsx", workspace_root=str(ws)) is None


def test_resolve_excel_path_does_not_guess_uploads(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    uploads = ws / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "hidden.xlsx").write_bytes(b"xl")
    (ws / "outputs").mkdir()
    (ws / "outputs" / "hidden.xlsx").write_bytes(b"xl")
    set_config(type("C", (), {"workspace_root": str(ws)})())
    assert resolve_excel_path("hidden.xlsx", workspace_root=str(ws)) is None
    found = resolve_excel_path("uploads/hidden.xlsx", workspace_root=str(ws))
    assert found is not None
    assert found.endswith("hidden.xlsx")
    assert "uploads" in found.replace("\\", "/")


def test_file_guard_blocks_product_secrets(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    guard = FileAccessGuard(str(ws))
    for name in (".secret_key", ".env", "config.env", "excelmanus.db"):
        with pytest.raises(SecurityViolationError):
            guard.resolve_and_validate(name)


def test_assert_public_http_url_blocks_loopback() -> None:
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("http://127.0.0.1/x.xlsx")
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("ftp://example.com/a.xlsx")
