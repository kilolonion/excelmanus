"""Tests for version manifest fingerprint fields."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def _patch_project_root(tmp_path: Path):
    return patch(
        "excelmanus.api_routes_version._get_project_root",
        return_value=tmp_path,
    )


def _write_deploy_meta(tmp_path: Path, data: dict) -> Path:
    meta_file = tmp_path / ".deploy_meta.json"
    meta_file.write_text(json.dumps(data), encoding="utf-8")
    return meta_file


class TestManifestFingerprint:
    def test_manifest_has_fingerprint_fields(self, tmp_path: Path):
        from excelmanus.api_routes_version import get_manifest_data

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        for key in (
            "release_id",
            "backend_version",
            "api_schema_version",
            "frontend_build_id",
            "version_fingerprint",
            "git_commit",
        ):
            assert key in data, f"Missing expected key: {key}"
        assert "min_frontend_build_id" not in data
        assert "min_backend_version" not in data
        assert "requires_db_migration" not in data
        assert "last_upgrade" in data

    def test_frontend_build_id_from_file(self, tmp_path: Path):
        from excelmanus.api_routes_version import get_manifest_data

        build_dir = tmp_path / "web" / ".next"
        build_dir.mkdir(parents=True)
        (build_dir / "BUILD_ID").write_text("build-xyz\n", encoding="utf-8")

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        assert data["frontend_build_id"] == "build-xyz"
        assert "build-xyz" in (data["version_fingerprint"] or "")

    def test_fingerprint_includes_git_commit_with_build_id(self, tmp_path: Path):
        from excelmanus.api_routes_version import get_manifest_data

        build_dir = tmp_path / "web" / ".next"
        build_dir.mkdir(parents=True)
        (build_dir / "BUILD_ID").write_text("build-xyz\n", encoding="utf-8")

        with (
            _patch_project_root(tmp_path),
            patch("excelmanus.api_routes_version._get_git_commit", return_value="abc1234"),
        ):
            data = get_manifest_data()

        fp = data["version_fingerprint"] or ""
        assert "build-xyz" in fp
        assert "abc1234" in fp
        assert "last_upgrade" in data

    def test_frontend_build_id_from_deploy_meta(self, tmp_path: Path):
        from excelmanus.api_routes_version import get_manifest_data

        _write_deploy_meta(tmp_path, {"frontend_build_id": "meta-build"})

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        assert data["frontend_build_id"] == "meta-build"


class TestManifestDoesNotOpenDb:
    def test_get_manifest_data_does_not_construct_database(self, tmp_path: Path):
        from excelmanus.api_routes_version import get_manifest_data

        with (
            _patch_project_root(tmp_path),
            patch("excelmanus.database.Database") as db_cls,
            patch("excelmanus.updater.verify_database_migration") as verify,
        ):
            data = get_manifest_data()

        db_cls.assert_not_called()
        verify.assert_not_called()
        assert "requires_db_migration" not in data
