"""CanonicalPath / public_identity — P1 workspace identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.workspace.identity import (
    CanonicalPath,
    IdentityError,
    collect_public_identities,
    display_name_for,
    is_reserved_relative,
    public_identity,
    resolve_canonical,
)


def test_resolve_relative_and_slash_normalize(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "book.xlsx").write_bytes(b"x")
    ident = resolve_canonical(tmp_path, r"reports\book.xlsx")
    assert ident.relative == "reports/book.xlsx"
    assert ident.public == "./reports/book.xlsx"


def test_resolve_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(IdentityError):
        resolve_canonical(tmp_path, "../secret.xlsx")


def test_resolve_rejects_reserved(tmp_path: Path) -> None:
    for raw in (
        ".excelmanus/revisions/x.xlsx",
        "outputs/backups/foo.xlsx",
        "outputs/.versions/rev.xlsx",
        "outputs/audits/log.xlsx",
        ".versions/old.xlsx",
    ):
        with pytest.raises(IdentityError):
            resolve_canonical(tmp_path, raw)
        assert is_reserved_relative(raw)


def test_resolve_rejects_hidden_and_lock_names(tmp_path: Path) -> None:
    with pytest.raises(IdentityError):
        resolve_canonical(tmp_path, ".hidden.xlsx")
    with pytest.raises(IdentityError):
        resolve_canonical(tmp_path, "~$Book.xlsx")


def test_display_name_strips_upload_hex_prefix() -> None:
    assert display_name_for("uploads/abcd1234_sales.xlsx") == "sales.xlsx"
    assert display_name_for("outputs/report.xlsx") == "report.xlsx"


def test_public_identity_maps_timestamped_backup_when_original_exists(tmp_path: Path) -> None:
    (tmp_path / "sales.xlsx").write_bytes(b"orig")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "sales_20260911T091344_f525.xlsx").write_bytes(b"copy")

    assert public_identity(
        "outputs/backups/sales_20260911T091344_f525.xlsx",
        tmp_path,
    ) == "./sales.xlsx"
    assert public_identity(
        str(backup / "sales_20260911T091344_f525.xlsx"),
        tmp_path,
    ) == "./sales.xlsx"


def test_public_identity_maps_timestamped_backup_to_uploads(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "deadbeef_chart.xlsx").write_bytes(b"u")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "chart_20260911T091344_abcd.xlsx").write_bytes(b"c")

    assert public_identity(
        "outputs/backups/chart_20260911T091344_abcd.xlsx",
        tmp_path,
    ) == "./uploads/deadbeef_chart.xlsx"


def test_public_identity_skips_backup_when_original_missing(tmp_path: Path) -> None:
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "gone_20260911T091344_aaaa.xlsx").write_bytes(b"c")
    assert public_identity("outputs/backups/gone_20260911T091344_aaaa.xlsx", tmp_path) == ""


def test_public_identity_maps_sandbox_cow_basename(tmp_path: Path) -> None:
    (tmp_path / "report.xlsx").write_bytes(b"r")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "report.xlsx").write_bytes(b"c")
    assert public_identity("outputs/backups/report.xlsx", tmp_path) == "./report.xlsx"


def test_public_identity_skips_sandbox_cow_when_missing(tmp_path: Path) -> None:
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "onlycopy.xlsx").write_bytes(b"c")
    assert public_identity("outputs/backups/onlycopy.xlsx", tmp_path) == ""


def test_public_identity_sse_form(tmp_path: Path) -> None:
    (tmp_path / "a.xlsx").write_bytes(b"a")
    assert public_identity("a.xlsx", tmp_path) == "./a.xlsx"
    assert public_identity("./a.xlsx", tmp_path) == "./a.xlsx"
    assert public_identity(str(tmp_path / "a.xlsx"), tmp_path) == "./a.xlsx"


def test_public_identity_without_workspace_skips_overlay() -> None:
    assert public_identity("outputs/backups/foo_20260911T091344_abcd.xlsx", None) == ""
    assert public_identity("a.xlsx", None) == "./a.xlsx"


def test_collect_public_identities_dedupes_and_drops_backup(tmp_path: Path) -> None:
    (tmp_path / "sales.xlsx").write_bytes(b"o")
    backup = tmp_path / "outputs" / "backups"
    backup.mkdir(parents=True)
    (backup / "sales_20260911T091344_f525.xlsx").write_bytes(b"c")
    out = collect_public_identities(
        [
            "sales.xlsx",
            "./sales.xlsx",
            "outputs/backups/sales_20260911T091344_f525.xlsx",
            str(backup / "sales_20260911T091344_f525.xlsx"),
        ],
        tmp_path,
    )
    assert out == ["./sales.xlsx"]


def test_canonical_path_is_frozen() -> None:
    ident = CanonicalPath(relative="book.xlsx")
    assert ident.public == "./book.xlsx"
    assert ident.display_name == "book.xlsx"


def test_catalog_skips_reserved_and_hidden(tmp_path: Path) -> None:
    from excelmanus.workspace.identity import catalog

    (tmp_path / "sales.xlsx").write_bytes(b"live")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "report.xlsx").write_bytes(b"out")
    backups = tmp_path / "outputs" / "backups"
    backups.mkdir()
    (backups / "sales_20260911T091344_f525.xlsx").write_bytes(b"copy")
    hidden = tmp_path / ".excelmanus" / "revisions"
    hidden.mkdir(parents=True)
    (hidden / "x.xlsx").write_bytes(b"rev")
    (tmp_path / "~$lock.xlsx").write_bytes(b"lock")
    found = [item.relative for item in catalog(tmp_path)]
    assert "sales.xlsx" in found
    assert "outputs/report.xlsx" in found
    assert all(not rel.startswith(".excelmanus") for rel in found)
    assert all("outputs/backups" not in rel for rel in found)
    assert "~$lock.xlsx" not in found
