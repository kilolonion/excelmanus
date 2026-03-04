"""Tests for manifest min_* compatibility fields (Issue 18)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_project_root(tmp_path: Path):
    """Return a patch that makes _get_project_root() return *tmp_path*."""
    return patch(
        "excelmanus.api_routes_version._get_project_root",
        return_value=tmp_path,
    )


def _write_deploy_meta(tmp_path: Path, data: dict) -> Path:
    meta_file = tmp_path / ".deploy_meta.json"
    meta_file.write_text(json.dumps(data), encoding="utf-8")
    return meta_file


# ---------------------------------------------------------------------------
# TestManifestMinFields
# ---------------------------------------------------------------------------


class TestManifestMinFields:
    """Verify min_frontend_build_id and min_backend_version appear in manifest."""

    def test_manifest_has_min_fields(self, tmp_path: Path):
        """get_manifest_data() must include both min_* keys."""
        from excelmanus.api_routes_version import get_manifest_data

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        assert "min_frontend_build_id" in data
        assert "min_backend_version" in data

    def test_manifest_min_defaults_none(self, tmp_path: Path):
        """With no deploy_meta and default constants, min_* should be None."""
        from excelmanus.api_routes_version import get_manifest_data

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        assert data["min_frontend_build_id"] is None
        assert data["min_backend_version"] is None

    def test_manifest_min_from_deploy_meta(self, tmp_path: Path):
        """min_* values from .deploy_meta.json should be surfaced."""
        from excelmanus.api_routes_version import get_manifest_data

        _write_deploy_meta(tmp_path, {
            "min_frontend_build_id": "abc123",
            "min_backend_version": "1.5.0",
        })

        with _patch_project_root(tmp_path):
            data = get_manifest_data()

        assert data["min_frontend_build_id"] == "abc123"
        assert data["min_backend_version"] == "1.5.0"

    def test_constant_overrides_deploy_meta(self, tmp_path: Path):
        """Module-level constant takes precedence over deploy_meta."""
        import excelmanus.api_routes_version as mod
        from excelmanus.api_routes_version import get_manifest_data

        _write_deploy_meta(tmp_path, {
            "min_frontend_build_id": "meta_val",
            "min_backend_version": "0.9.0",
        })

        original_fe = mod._MIN_FRONTEND_BUILD_ID
        original_be = mod._MIN_BACKEND_VERSION
        try:
            mod._MIN_FRONTEND_BUILD_ID = "const_val"
            mod._MIN_BACKEND_VERSION = "2.0.0"

            with _patch_project_root(tmp_path):
                data = get_manifest_data()

            assert data["min_frontend_build_id"] == "const_val"
            assert data["min_backend_version"] == "2.0.0"
        finally:
            mod._MIN_FRONTEND_BUILD_ID = original_fe
            mod._MIN_BACKEND_VERSION = original_be

    def test_manifest_preserves_existing_fields(self, tmp_path: Path):
        """Existing manifest fields must not be broken by the addition."""
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
