"""MCP 配置环境变量展开与策略测试。"""

from __future__ import annotations

import pytest

from excelmanus.mcp.config import MCPConfigLoader


def test_expand_env_refs_for_args_env_url_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTX7_TOKEN", "token-123")
    monkeypatch.setenv("API_VERSION", "v2")

    configs = MCPConfigLoader._parse_config(
        {
            "mcpServers": {
                "stdio-server": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["--token", "$CTX7_TOKEN", "prefix-${CTX7_TOKEN}"],
                    "env": {"TOKEN": "${CTX7_TOKEN}"},
                    "autoApprove": ["query_docs"],
                },
                "sse-server": {
                    "transport": "sse",
                    "url": "https://api.example.com/$API_VERSION/sse",
                    "headers": {"Authorization": "Bearer $CTX7_TOKEN"},
                },
            }
        },
        expand_env_refs=True,
    )

    assert len(configs) == 2
    stdio_cfg = next(item for item in configs if item.name == "stdio-server")
    assert stdio_cfg.args == ["--token", "token-123", "prefix-token-123"]
    assert stdio_cfg.env == {"TOKEN": "token-123"}
    assert stdio_cfg.auto_approve == ["query_docs"]

    sse_cfg = next(item for item in configs if item.name == "sse-server")
    assert sse_cfg.url == "https://api.example.com/v2/sse"
    assert sse_cfg.headers == {"Authorization": "Bearer token-123"}


def test_undefined_env_keep_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_EXISTS", raising=False)
    configs = MCPConfigLoader._parse_config(
        {
            "mcpServers": {
                "keep-server": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["--token", "$NOT_EXISTS"],
                }
            }
        },
        expand_env_refs=True,
        undefined_env="keep",
    )
    assert len(configs) == 1
    assert configs[0].args[-1] == "$NOT_EXISTS"


def test_undefined_env_error_policy_skips_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_EXISTS", raising=False)
    configs = MCPConfigLoader._parse_config(
        {
            "mcpServers": {
                "error-server": {
                    "transport": "stdio",
                    "command": "node",
                    "args": ["--token", "${NOT_EXISTS}"],
                }
            }
        },
        expand_env_refs=True,
        undefined_env="error",
    )
    assert configs == []


def test_strict_secret_check_blocks_plaintext() -> None:
    data = {
        "mcpServers": {
            "ctx": {
                "transport": "stdio",
                "command": "npx",
                "args": ["--api-key=plain-secret"],
            }
        }
    }
    assert MCPConfigLoader._parse_config(data, strict_secret_check=True) == []
    assert len(MCPConfigLoader._parse_config(data, strict_secret_check=False)) == 1


def test_streamable_http_feature_flag() -> None:
    data = {
        "mcpServers": {
            "http-server": {
                "transport": "streamable_http",
                "url": "https://example.com/mcp",
            }
        }
    }
    assert len(MCPConfigLoader._parse_config(data, streamable_http_enabled=True)) == 1
    assert MCPConfigLoader._parse_config(data, streamable_http_enabled=False) == []
