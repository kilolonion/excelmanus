"""Overlay backup HTTP endpoints are deleted (404, not 410 tombstones)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from excelmanus.api import app


@pytest.mark.asyncio
async def test_backup_list_is_gone() -> None:
    with patch("excelmanus.api_routes_workspace.get_session_manager", return_value=MagicMock()):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/backup/list", params={"session_id": "s1"})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_checkpoint_list_is_gone() -> None:
    with patch("excelmanus.api_routes_workspace.get_session_manager", return_value=MagicMock()):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/checkpoint/list", params={"session_id": "s1"})
    assert res.status_code == 404
