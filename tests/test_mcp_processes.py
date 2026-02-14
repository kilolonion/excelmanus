"""MCP 进程管理测试。"""

from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import patch

from excelmanus.mcp.processes import (
    ProcessInfo,
    _workspace_mcp_marker,
    list_workspace_mcp_processes,
    snapshot_workspace_mcp_pids,
    terminate_workspace_mcp_processes,
)


def test_workspace_marker_defaults_to_workspace_dir(tmp_path: Path) -> None:
    marker = _workspace_mcp_marker(str(tmp_path))
    assert marker.endswith("/.excelmanus/mcp/")


def test_workspace_marker_respects_env_state_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    custom_dir = tmp_path / "custom-mcp-state"
    monkeypatch.setenv("EXCELMANUS_MCP_STATE_DIR", str(custom_dir))
    marker = _workspace_mcp_marker(str(tmp_path))
    assert marker == f"{custom_dir.resolve(strict=False)}/"


def test_list_workspace_mcp_processes_filters_by_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    marker = (workspace / ".excelmanus" / "mcp").resolve(strict=False)
    output = (
        f"100 1 /usr/bin/node {marker}/npm/server-a/index.js\n"
        "101 1 /usr/bin/node /tmp/other/.excelmanus/mcp/npm/server-b/index.js\n"
    )
    with patch("subprocess.check_output", return_value=output):
        rows = list_workspace_mcp_processes(str(workspace))
    assert [item.pid for item in rows] == [100]


def test_snapshot_supports_custom_state_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    custom_state = (tmp_path / "runtime/mcp").resolve(strict=False)
    default_state = (workspace / ".excelmanus" / "mcp").resolve(strict=False)
    output = (
        f"201 1 /usr/bin/node {custom_state}/npm/server-a/index.js\n"
        f"202 1 /usr/bin/node {default_state}/npm/server-b/index.js\n"
    )
    with patch("subprocess.check_output", return_value=output):
        pids = snapshot_workspace_mcp_pids(
            str(workspace),
            state_dir=str(custom_state),
        )
    assert pids == {201}


def test_terminate_expands_descendants_in_same_scope() -> None:
    processes = [
        ProcessInfo(pid=301, ppid=1, command="/custom/mcp/a"),
        ProcessInfo(pid=302, ppid=301, command="/custom/mcp/b"),
        ProcessInfo(pid=303, ppid=1, command="/custom/mcp/c"),
    ]
    with (
        patch(
            "excelmanus.mcp.processes.list_workspace_mcp_processes",
            return_value=processes,
        ),
        patch("excelmanus.mcp.processes._send_signal") as mock_send_signal,
        patch(
            "excelmanus.mcp.processes._wait_for_exit",
            return_value=set(),
        ),
    ):
        remaining = terminate_workspace_mcp_processes(
            workspace_root="/repo",
            candidate_pids={301},
            state_dir="/custom/mcp",
        )
    assert remaining == set()
    mock_send_signal.assert_called_once_with({301, 302}, signal.SIGTERM)
