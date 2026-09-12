"""Turn-checkpoint file rollback is gone."""

from __future__ import annotations

import pytest


def test_turn_checkpoint_module_removed() -> None:
    with pytest.raises(ImportError):
        __import__("excelmanus.file_versions")
