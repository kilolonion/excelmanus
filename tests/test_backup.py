"""Overlay staging / WorkspaceTransaction are gone."""

from __future__ import annotations

import pytest

from excelmanus.workspace import IsolatedWorkspace


def test_workspace_transaction_removed() -> None:
    import excelmanus.workspace as ws
    assert not hasattr(ws, "WorkspaceTransaction")


def test_isolated_workspace_has_no_overlay(tmp_path) -> None:
    iso = IsolatedWorkspace(tmp_path)
    assert not hasattr(iso, "transaction_enabled")
    env = iso.create_sandbox_env()
    assert env.get_tmp_dir().is_dir()
    assert not hasattr(env, "get_cow_log_path")
