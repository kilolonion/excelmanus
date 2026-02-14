"""MCPConfigLoader 单元测试。

测试配置文件搜索优先级、静默跳过、JSON 格式错误处理等场景。
Requirements: 1.2, 1.7, 1.8
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from excelmanus.mcp.config import MCPConfigLoader, MCPServerConfig


# ── 辅助工具 ──────────────────────────────────────────────────────

def _write_mcp_json(path: Path, data: dict) -> None:
    """将配置字典写入 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _valid_stdio_config() -> dict:
    """返回一个合法的 stdio 类型配置字典。"""
    return {
        "mcpServers": {
            "test-server": {
                "transport": "stdio",
                "command": "echo",
                "args": ["hello"],
            }
        }
    }


def _valid_sse_config() -> dict:
    """返回一个合法的 sse 类型配置字典。"""
    return {
        "mcpServers": {
            "web-api": {
                "transport": "sse",
                "url": "http://localhost:8080/sse",
                "timeout": 60,
            }
        }
    }


# ── 搜索优先级测试 ────────────────────────────────────────────────


class TestConfigSearchPriority:
    """测试配置文件搜索优先级（Requirements 1.2, 1.7）。

    优先级：环境变量 > config_path 参数 > workspace_root/mcp.json > ~/.excelmanus/mcp.json
    """

    def test_env_var_highest_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 EXCELMANUS_MCP_CONFIG 优先级最高。"""
        # 准备：环境变量指向的配置（stdio）
        env_config_path = tmp_path / "env" / "mcp.json"
        _write_mcp_json(env_config_path, {
            "mcpServers": {
                "from-env": {"transport": "stdio", "command": "env_cmd"}
            }
        })

        # 准备：workspace 配置（不同的 server 名称）
        ws_config_path = tmp_path / "workspace" / "mcp.json"
        _write_mcp_json(ws_config_path, {
            "mcpServers": {
                "from-workspace": {"transport": "stdio", "command": "ws_cmd"}
            }
        })

        monkeypatch.setenv("EXCELMANUS_MCP_CONFIG", str(env_config_path))

        result = MCPConfigLoader.load(
            workspace_root=str(tmp_path / "workspace"),
        )

        assert len(result) == 1
        assert result[0].name == "from-env"
        assert result[0].command == "env_cmd"

    def test_config_path_over_workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """config_path 参数优先于 workspace_root/mcp.json。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        # 准备：config_path 指向的配置
        explicit_path = tmp_path / "explicit" / "mcp.json"
        _write_mcp_json(explicit_path, {
            "mcpServers": {
                "from-explicit": {"transport": "stdio", "command": "explicit_cmd"}
            }
        })

        # 准备：workspace 配置
        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", {
            "mcpServers": {
                "from-workspace": {"transport": "stdio", "command": "ws_cmd"}
            }
        })

        result = MCPConfigLoader.load(
            config_path=str(explicit_path),
            workspace_root=str(ws_dir),
        )

        assert len(result) == 1
        assert result[0].name == "from-explicit"

    def test_workspace_root_over_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """workspace_root/mcp.json 优先于 ~/.excelmanus/mcp.json。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        # 准备：workspace 配置
        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", {
            "mcpServers": {
                "from-workspace": {"transport": "stdio", "command": "ws_cmd"}
            }
        })

        # 准备：模拟 home 目录配置
        fake_home = tmp_path / "fakehome"
        _write_mcp_json(fake_home / ".excelmanus" / "mcp.json", {
            "mcpServers": {
                "from-home": {"transport": "stdio", "command": "home_cmd"}
            }
        })
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert len(result) == 1
        assert result[0].name == "from-workspace"

    def test_fallback_to_home_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """所有高优先级路径不存在时，回退到 ~/.excelmanus/mcp.json。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        # workspace 目录存在但无 mcp.json
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)

        # 模拟 home 目录配置
        fake_home = tmp_path / "fakehome"
        _write_mcp_json(fake_home / ".excelmanus" / "mcp.json", {
            "mcpServers": {
                "from-home": {"transport": "sse", "url": "http://home:9090/sse"}
            }
        })
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert len(result) == 1
        assert result[0].name == "from-home"
        assert result[0].url == "http://home:9090/sse"

    def test_env_var_overrides_config_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量同时存在 config_path 时，环境变量优先。"""
        env_config_path = tmp_path / "env_mcp.json"
        _write_mcp_json(env_config_path, {
            "mcpServers": {
                "env-server": {"transport": "stdio", "command": "env_cmd"}
            }
        })

        explicit_path = tmp_path / "explicit_mcp.json"
        _write_mcp_json(explicit_path, {
            "mcpServers": {
                "explicit-server": {"transport": "stdio", "command": "explicit_cmd"}
            }
        })

        monkeypatch.setenv("EXCELMANUS_MCP_CONFIG", str(env_config_path))

        result = MCPConfigLoader.load(config_path=str(explicit_path))

        assert len(result) == 1
        assert result[0].name == "env-server"


# ── 配置文件不存在时静默跳过 ──────────────────────────────────────


class TestConfigFileMissing:
    """测试配置文件不存在时静默跳过（Requirement 1.8）。"""

    def test_no_config_returns_empty_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """所有候选路径均不存在时，返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)
        # 指向一个空目录作为 workspace
        ws_dir = tmp_path / "empty_workspace"
        ws_dir.mkdir()
        # 模拟 home 目录也没有配置
        fake_home = tmp_path / "fakehome_empty"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []

    def test_env_var_points_to_nonexistent_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量指向不存在的文件时，继续搜索后续候选路径。"""
        monkeypatch.setenv("EXCELMANUS_MCP_CONFIG", str(tmp_path / "nonexistent.json"))

        # workspace 有配置
        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", _valid_stdio_config())

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert len(result) == 1
        assert result[0].name == "test-server"

    def test_config_path_nonexistent_falls_through(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """config_path 指向不存在的文件时，继续搜索 workspace。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", _valid_sse_config())

        result = MCPConfigLoader.load(
            config_path=str(tmp_path / "no_such_file.json"),
            workspace_root=str(ws_dir),
        )

        assert len(result) == 1
        assert result[0].name == "web-api"


# ── JSON 格式错误处理 ────────────────────────────────────────────


class TestJsonErrorHandling:
    """测试 JSON 格式错误和结构异常的处理。"""

    def test_invalid_json_returns_empty_and_logs_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """JSON 格式错误时记录 ERROR 日志并返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        bad_json = ws_dir / "mcp.json"
        bad_json.write_text("{invalid json content!!!", encoding="utf-8")

        # 模拟 home 无配置
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        with caplog.at_level(logging.ERROR, logger="excelmanus.mcp.config"):
            result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []
        assert any("JSON 格式错误" in msg for msg in caplog.messages)

    def test_non_object_top_level_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """顶层不是 JSON 对象时记录 ERROR 并返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir()
        (ws_dir / "mcp.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        with caplog.at_level(logging.ERROR, logger="excelmanus.mcp.config"):
            result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []
        assert any("顶层必须为 JSON 对象" in msg for msg in caplog.messages)


# ── 缺少 mcpServers 字段 ─────────────────────────────────────────


class TestMissingMcpServersField:
    """测试缺少 mcpServers 字段时的处理。"""

    def test_missing_mcp_servers_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """配置文件缺少 mcpServers 字段时返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", {"someOtherKey": {}})

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []

    def test_mcp_servers_not_dict_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """mcpServers 不是字典时返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", {"mcpServers": "not_a_dict"})

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []

    def test_empty_mcp_servers_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """mcpServers 为空字典时返回空列表。"""
        monkeypatch.delenv("EXCELMANUS_MCP_CONFIG", raising=False)

        ws_dir = tmp_path / "workspace"
        _write_mcp_json(ws_dir / "mcp.json", {"mcpServers": {}})

        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        result = MCPConfigLoader.load(workspace_root=str(ws_dir))

        assert result == []
