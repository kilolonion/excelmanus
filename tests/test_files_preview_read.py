"""Workspace text preview API: special names and UTF-8 sniff."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from httpx import AsyncClient

from excelmanus.api_routes_files import is_text_preview_file
from tests.test_api import _make_transport, _setup_api_globals, _test_config


async def _session_scope(client: AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/sessions", json={})
    assert response.status_code == 200
    return {"session_id": response.json()["id"]}


@contextmanager
def _api_transport(tmp_path: Path):
    config = _test_config(workspace_root=str(tmp_path))
    with _setup_api_globals(config=config):
        yield _make_transport()


class TestIsTextPreviewFile:
    def test_env_local_is_text(self, tmp_path: Path) -> None:
        path = tmp_path / ".env.local"
        path.write_text("KEY=1\n", encoding="utf-8")
        assert is_text_preview_file(path) is True

    def test_xlsx_is_not_text(self, tmp_path: Path) -> None:
        path = tmp_path / "book.xlsx"
        path.write_bytes(b"PK\x03\x04binary")
        assert is_text_preview_file(path) is False

    def test_unknown_utf8_suffix_is_text(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.xyz"
        path.write_text("hello\n", encoding="utf-8")
        assert is_text_preview_file(path) is True

    def test_csv_is_not_text(self, tmp_path: Path) -> None:
        path = tmp_path / "sales.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        assert is_text_preview_file(path) is False

    def test_makefile_is_text_by_sniff(self, tmp_path: Path) -> None:
        path = tmp_path / "Makefile"
        path.write_text("all:\n\techo ok\n", encoding="utf-8")
        assert is_text_preview_file(path) is True

    def test_nul_bytes_are_binary(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.xyz"
        path.write_bytes(b"abc\x00def")
        assert is_text_preview_file(path) is False


@pytest.mark.asyncio
class TestFilesReadApi:
    async def test_reads_env_local(self, tmp_path: Path) -> None:
        (tmp_path / ".env.local").write_text("A=1\n", encoding="utf-8")
        with _api_transport(tmp_path) as transport:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/files/read",
                    params={"path": ".env.local", **await _session_scope(client)},
                )
        assert response.status_code == 200
        assert response.json()["content"] == "A=1\n"

    async def test_rejects_xlsx(self, tmp_path: Path) -> None:
        (tmp_path / "book.xlsx").write_bytes(b"PK\x03\x04")
        with _api_transport(tmp_path) as transport:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/files/read",
                    params={"path": "book.xlsx", **await _session_scope(client)},
                )
        assert response.status_code == 404

    async def test_read_is_not_cached(self, tmp_path: Path) -> None:
        (tmp_path / ".env.local").write_text("A=1\n", encoding="utf-8")
        with _api_transport(tmp_path) as transport:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/files/read",
                    params={"path": ".env.local", **await _session_scope(client)},
                )
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "private, no-store"

    async def test_image_is_not_cached(self, tmp_path: Path) -> None:
        (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        with _api_transport(tmp_path) as transport:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/files/image",
                    params={"path": "shot.png", **await _session_scope(client)},
                )
        assert response.status_code == 200
        assert response.headers.get("cache-control") == "private, no-store"
