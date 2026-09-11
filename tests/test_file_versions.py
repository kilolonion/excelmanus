"""FileVersionManager is deleted. History is RevisionStore only."""

from __future__ import annotations

import pytest


def test_file_versions_module_removed() -> None:
    with pytest.raises(ImportError):
        __import__("excelmanus.file_versions")
