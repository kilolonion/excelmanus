"""excelmanus/mcp/npx_cache 模块的回归测试。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from excelmanus.mcp.config import MCPServerConfig
from excelmanus.mcp.npx_cache import (
    _bin_name_from_package,
    _parse_npx_args,
    _safe_dir_name,
    resolve_npx_config,
    try_resolve_from_cache,
)


# ---------------------------------------------------------------------------
# _parse_npx_args
# ---------------------------------------------------------------------------


class TestParseNpxArgs:
    def test_simple_package(self):
        pkg, rest = _parse_npx_args(["mongodb-mcp-server"])
        assert pkg == "mongodb-mcp-server"
        assert rest == []

    def test_yes_flag_short(self):
        pkg, rest = _parse_npx_args(["-y", "mongodb-mcp-server"])
        assert pkg == "mongodb-mcp-server"
        assert rest == []

    def test_yes_flag_long(self):
        pkg, rest = _parse_npx_args(["--yes", "@negokaz/excel-mcp-server"])
        assert pkg == "@negokaz/excel-mcp-server"
        assert rest == []

    def test_extra_args_preserved(self):
        pkg, rest = _parse_npx_args(["-y", "my-server", "--port", "3000"])
        assert pkg == "my-server"
        assert rest == ["--port", "3000"]

    def test_package_flag(self):
        pkg, rest = _parse_npx_args(["-p", "some-dep", "my-server"])
        assert pkg == "my-server"
        assert rest == []

    def test_empty_args(self):
        pkg, rest = _parse_npx_args([])
        assert pkg is None
        assert rest == []


# ---------------------------------------------------------------------------
# _bin_name_from_package / _safe_dir_name
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_bin_name_scoped(self):
        assert _bin_name_from_package("@negokaz/excel-mcp-server") == "excel-mcp-server"

    def test_bin_name_unscoped(self):
        assert _bin_name_from_package("mongodb-mcp-server") == "mongodb-mcp-server"

    def test_safe_dir_scoped(self):
        assert _safe_dir_name("@negokaz/excel-mcp-server") == "negokaz__excel-mcp-server"

    def test_safe_dir_unscoped(self):
        assert _safe_dir_name("mongodb-mcp-server") == "mongodb-mcp-server"


# ---------------------------------------------------------------------------
# resolve_npx_config
# ---------------------------------------------------------------------------


class TestResolveNpxConfig:
    def _make_config(self, *, command="npx", args=None, name="test-server"):
        return MCPServerConfig(
            name=name,
            transport="stdio",
            command=command,
            args=args or ["-y", "my-mcp-server"],
        )

    @pytest.mark.asyncio
    async def test_non_npx_passthrough(self):
        """非 npx 命令原样返回。"""
        cfg = self._make_config(command="uvx", args=["mcp-server-git"])
        result = await resolve_npx_config(cfg)
        assert result is cfg

    @pytest.mark.asyncio
    async def test_cached_binary_reused(self, tmp_path: Path):
        """缓存的二进制存在时直接复用，不触发 npm install。"""
        # 准备假的缓存二进制
        pkg_dir = tmp_path / "my-mcp-server" / "node_modules" / ".bin"
        pkg_dir.mkdir(parents=True)
        bin_file = pkg_dir / "my-mcp-server"
        bin_file.write_text("#!/bin/sh\necho hello")
        bin_file.chmod(0o755)

        cfg = self._make_config()
        result = await resolve_npx_config(cfg, cache_dir=tmp_path)

        assert result.command == str(bin_file)
        assert result.args == []
        assert result.name == "test-server"

    @pytest.mark.asyncio
    async def test_first_install_then_cache(self, tmp_path: Path):
        """首次安装成功后返回二进制路径。"""
        cfg = self._make_config()

        async def fake_npm_install(package, prefix, *, env=None):
            # 模拟 npm install：创建 bin 目录和可执行文件
            bin_dir = prefix / "node_modules" / ".bin"
            bin_dir.mkdir(parents=True)
            f = bin_dir / "my-mcp-server"
            f.write_text("#!/bin/sh\necho ok")
            f.chmod(0o755)

        with patch(
            "excelmanus.mcp.npx_cache._npm_install",
            side_effect=fake_npm_install,
        ):
            result = await resolve_npx_config(cfg, cache_dir=tmp_path)

        assert result.command.endswith("my-mcp-server")
        assert "npx" not in result.command
        assert result.args == []

    @pytest.mark.asyncio
    async def test_install_failure_falls_back_to_npx(self, tmp_path: Path):
        """npm install 失败时回退到原始 npx 命令。"""
        cfg = self._make_config()

        with patch(
            "excelmanus.mcp.npx_cache._npm_install",
            side_effect=RuntimeError("network error"),
        ):
            result = await resolve_npx_config(cfg, cache_dir=tmp_path)

        assert result.command == "npx"
        assert result.args == ["-y", "my-mcp-server"]

    @pytest.mark.asyncio
    async def test_install_ok_but_no_bin_falls_back(self, tmp_path: Path):
        """安装成功但找不到 bin 时回退到 npx。"""
        cfg = self._make_config()

        async def fake_npm_install(package, prefix, *, env=None):
            # 模拟安装但不创建 bin
            (prefix / "node_modules").mkdir(parents=True)

        with patch(
            "excelmanus.mcp.npx_cache._npm_install",
            side_effect=fake_npm_install,
        ):
            result = await resolve_npx_config(cfg, cache_dir=tmp_path)

        assert result.command == "npx"

    @pytest.mark.asyncio
    async def test_scoped_package_resolution(self, tmp_path: Path):
        """作用域包（@scope/name）正确解析 bin 名和缓存目录。"""
        cfg = self._make_config(
            args=["--yes", "@negokaz/excel-mcp-server"],
        )

        # 准备缓存
        pkg_dir = tmp_path / "negokaz__excel-mcp-server" / "node_modules" / ".bin"
        pkg_dir.mkdir(parents=True)
        bin_file = pkg_dir / "excel-mcp-server"
        bin_file.write_text("#!/bin/sh")
        bin_file.chmod(0o755)

        result = await resolve_npx_config(cfg, cache_dir=tmp_path)
        assert result.command == str(bin_file)

    @pytest.mark.asyncio
    async def test_extra_args_forwarded(self, tmp_path: Path):
        """包名之后的额外参数被保留到 result.args。"""
        cfg = self._make_config(
            args=["-y", "my-mcp-server", "--port", "8080"],
        )

        pkg_dir = tmp_path / "my-mcp-server" / "node_modules" / ".bin"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "my-mcp-server").write_text("#!/bin/sh")
        (pkg_dir / "my-mcp-server").chmod(0o755)

        result = await resolve_npx_config(cfg, cache_dir=tmp_path)
        assert result.args == ["--port", "8080"]

    @pytest.mark.asyncio
    async def test_env_preserved(self, tmp_path: Path):
        """原始 config 的 env 字段被保留到结果中。"""
        cfg = MCPServerConfig(
            name="test",
            transport="stdio",
            command="npx",
            args=["-y", "my-server"],
            env={"FOO": "bar"},
        )

        pkg_dir = tmp_path / "my-server" / "node_modules" / ".bin"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "my-server").write_text("#!/bin/sh")
        (pkg_dir / "my-server").chmod(0o755)

        result = await resolve_npx_config(cfg, cache_dir=tmp_path)
        assert result.env == {"FOO": "bar"}


# ---------------------------------------------------------------------------
# try_resolve_from_cache（同步、仅缓存检查）
# ---------------------------------------------------------------------------


class TestTryResolveFromCache:
    def _make_config(self, *, command="npx", args=None, name="test-server"):
        return MCPServerConfig(
            name=name,
            transport="stdio",
            command=command,
            args=args or ["-y", "my-mcp-server"],
        )

    def test_non_npx_passthrough(self):
        """非 npx 命令原样返回。"""
        cfg = self._make_config(command="uvx", args=["mcp-server-git"])
        result = try_resolve_from_cache(cfg)
        assert result is cfg

    def test_cache_hit(self, tmp_path: Path):
        """缓存命中时返回重写后的 config。"""
        pkg_dir = tmp_path / "my-mcp-server" / "node_modules" / ".bin"
        pkg_dir.mkdir(parents=True)
        bin_file = pkg_dir / "my-mcp-server"
        bin_file.write_text("#!/bin/sh")
        bin_file.chmod(0o755)

        cfg = self._make_config()
        result = try_resolve_from_cache(cfg, cache_dir=tmp_path)
        assert result is not None
        assert result.command == str(bin_file)

    def test_cache_miss_returns_none(self, tmp_path: Path):
        """缓存未命中时返回 None（而非触发安装）。"""
        cfg = self._make_config()
        result = try_resolve_from_cache(cfg, cache_dir=tmp_path)
        assert result is None

    def test_unparseable_args_returns_original(self):
        """无法解析包名时返回原始 config。"""
        cfg = MCPServerConfig(
            name="test-server",
            transport="stdio",
            command="npx",
            args=[],
        )
        result = try_resolve_from_cache(cfg)
        assert result is cfg
