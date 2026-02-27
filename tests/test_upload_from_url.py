"""POST /api/v1/upload-from-url 端点回归测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app():
    """构造一个最小化的 FastAPI app 用于测试 upload-from-url 端点。"""
    import importlib
    import excelmanus.api as api_mod

    # 确保 _config 不为 None
    api_mod._config = MagicMock()
    api_mod._config.cors_allow_origins = ["*"]

    app = api_mod._app if hasattr(api_mod, "_app") else None
    if app is None:
        from excelmanus.api import create_app
        app = create_app(api_mod._config)

    # mock auth
    app.state.auth_enabled = False
    return app


@pytest.fixture()
def client(tmp_path):
    """提供一个 TestClient + 临时 workspace。"""
    import excelmanus.api as api_mod

    app = _make_app()

    ws_mock = MagicMock()
    ws_mock.root_dir = tmp_path
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    ws_mock.get_upload_dir.return_value = upload_dir
    ws_mock.check_upload_allowed.return_value = (True, "")

    with patch.object(api_mod, "_resolve_workspace", return_value=ws_mock), \
         patch.object(api_mod, "_get_file_registry", return_value=None):
        yield TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUploadFromUrl:
    """upload-from-url 端点测试。"""

    def test_missing_url(self, client):
        resp = client.post("/api/v1/upload-from-url", json={})
        assert resp.status_code == 400
        assert "url" in resp.json().get("error", "").lower()

    def test_invalid_scheme(self, client):
        resp = client.post("/api/v1/upload-from-url", json={"url": "ftp://example.com/a.xlsx"})
        assert resp.status_code == 400
        assert "http" in resp.json().get("error", "").lower()

    def test_no_extension(self, client):
        resp = client.post("/api/v1/upload-from-url", json={"url": "https://example.com/noext"})
        assert resp.status_code == 400
        assert "扩展名" in resp.json().get("error", "") or "推断" in resp.json().get("error", "")

    def test_unsupported_extension(self, client):
        resp = client.post("/api/v1/upload-from-url", json={"url": "https://example.com/doc.pdf"})
        assert resp.status_code == 400
        assert "不支持" in resp.json().get("error", "")

    @patch("httpx.AsyncClient")
    def test_success_xlsx(self, mock_httpx_cls, client, tmp_path):
        """模拟成功下载 xlsx 文件。"""
        import httpx

        fake_content = b"PK\x03\x04" + b"\x00" * 100  # 假 xlsx 内容

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = fake_content
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        resp = client.post(
            "/api/v1/upload-from-url",
            json={"url": "https://example.com/data/report.xlsx"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["filename"] == "report.xlsx"
        assert body["size"] == len(fake_content)
        assert "uploads" in body["path"]

    @patch("httpx.AsyncClient")
    def test_remote_error_502(self, mock_httpx_cls, client):
        """远程服务器返回 404 时应返回 502。"""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        exc = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=exc)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        resp = client.post(
            "/api/v1/upload-from-url",
            json={"url": "https://example.com/missing.xlsx"},
        )
        assert resp.status_code == 502

    @patch("httpx.AsyncClient")
    def test_empty_download(self, mock_httpx_cls, client):
        """下载到空内容应返回 400。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b""
        mock_response.raise_for_status = MagicMock()

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        mock_httpx_cls.return_value = mock_client_instance

        resp = client.post(
            "/api/v1/upload-from-url",
            json={"url": "https://example.com/empty.csv"},
        )
        assert resp.status_code == 400
        assert "空" in resp.json().get("error", "")
