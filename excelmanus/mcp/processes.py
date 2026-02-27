"""MCP 进程管理辅助工具。

仅识别并处理当前工作区 MCP 状态目录下的进程。
默认目录为 ``<workspace>/.excelmanus/mcp/``，可通过配置或环境变量覆盖。
用于 shutdown 异常时兜底回收，避免误伤 IDE/系统其它来源的 MCP 进程。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_ENV_MCP_STATE_DIR = "EXCELMANUS_MCP_STATE_DIR"
_MIN_STATE_DIR_DEPTH = 3


def _validate_state_dir_depth(resolved: Path) -> None:
    """校验 state_dir 路径深度，防止过浅 marker 误匹配全部进程。"""
    if len(resolved.parts) < _MIN_STATE_DIR_DEPTH:
        raise ValueError(
            f"MCP state_dir 路径过浅: {resolved}（至少需要 {_MIN_STATE_DIR_DEPTH} 层目录）"
        )


@dataclass(frozen=True)
class ProcessInfo:
    """最小进程信息。"""

    pid: int
    ppid: int
    command: str


def _resolve_state_dir(workspace_root: str, state_dir: str | None = None) -> Path:
    """解析 MCP 状态目录。

    优先级：
    1. 显式参数 ``state_dir``
    2. 环境变量 ``EXCELMANUS_MCP_STATE_DIR``
    3. 默认 ``<workspace>/.excelmanus/mcp``
    """
    root = Path(workspace_root).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve(strict=False)
    else:
        root = root.resolve(strict=False)

    raw = (state_dir or os.environ.get(_ENV_MCP_STATE_DIR) or "").strip()
    if raw:
        resolved = Path(raw).expanduser()
        if not resolved.is_absolute():
            resolved = (root / resolved).resolve(strict=False)
        else:
            resolved = resolved.resolve(strict=False)
        _validate_state_dir_depth(resolved)
        return resolved
    return (root / ".excelmanus" / "mcp").resolve(strict=False)


def _workspace_mcp_marker(workspace_root: str, state_dir: str | None = None) -> str:
    """返回工作区 MCP 状态目录标记串（以 `/` 结尾）。"""
    marker = str(_resolve_state_dir(workspace_root, state_dir=state_dir))
    if not marker.endswith("/"):
        marker += "/"
    return marker


def list_workspace_mcp_processes(
    workspace_root: str,
    state_dir: str | None = None,
) -> list[ProcessInfo]:
    """列出当前工作区 MCP 缓存目录下的所有进程。"""
    marker = _workspace_mcp_marker(workspace_root, state_dir=state_dir)
    try:
        output = subprocess.check_output(
            ["ps", "-A", "-o", "pid=,ppid=,command="],
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    result: list[ProcessInfo] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        command = parts[2]
        if marker in command:
            result.append(ProcessInfo(pid=pid, ppid=ppid, command=command))
    return result


def snapshot_workspace_mcp_pids(
    workspace_root: str,
    state_dir: str | None = None,
) -> set[int]:
    """获取当前工作区 MCP 进程 PID 快照。"""
    return {
        proc.pid
        for proc in list_workspace_mcp_processes(
            workspace_root,
            state_dir=state_dir,
        )
    }


def _expand_descendants(
    seed_pids: set[int],
    processes: list[ProcessInfo],
) -> set[int]:
    """将 seed PID 扩展为同一筛选集合内的后代 PID。"""
    children_by_parent: dict[int, set[int]] = {}
    for proc in processes:
        children_by_parent.setdefault(proc.ppid, set()).add(proc.pid)

    targets = set(seed_pids)
    queue = list(seed_pids)
    while queue:
        current = queue.pop()
        for child in children_by_parent.get(current, ()):
            if child in targets:
                continue
            targets.add(child)
            queue.append(child)
    return targets


def _is_alive(pid: int) -> bool:
    """检查进程是否仍存在。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _send_signal(pids: set[int], sig: int) -> None:
    """向 PID 集合发送信号，自动忽略已退出进程。"""
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue


def _wait_for_exit(pids: set[int], timeout_seconds: float) -> set[int]:
    """等待进程退出，返回超时后仍存活的 PID。"""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    alive = set(pids)
    while alive and time.monotonic() < deadline:
        alive = {pid for pid in alive if _is_alive(pid)}
        if alive:
            time.sleep(0.05)
    return alive


def terminate_workspace_mcp_processes(
    workspace_root: str,
    candidate_pids: Iterable[int] | None = None,
    state_dir: str | None = None,
    grace_seconds: float = 1.5,
) -> set[int]:
    """终止当前工作区 MCP 缓存进程。

    Args:
        workspace_root: 工作区根目录。
        candidate_pids: 待清理根 PID（只会在当前工作区 MCP 进程集中生效）。
            为空时不执行全量清理，直接返回空集合。
        state_dir: MCP 状态目录（可选）。
        grace_seconds: SIGTERM 后等待秒数。

    Returns:
        清理结束后仍存活的 PID 集合。
    """
    if candidate_pids is None:
        return set()

    processes = list_workspace_mcp_processes(
        workspace_root,
        state_dir=state_dir,
    )
    if not processes:
        return set()

    known_pids = {proc.pid for proc in processes}
    seed_pids = {int(pid) for pid in candidate_pids if int(pid) in known_pids}
    if not seed_pids:
        return set()

    targets = _expand_descendants(seed_pids, processes)

    _send_signal(targets, signal.SIGTERM)
    remaining = _wait_for_exit(targets, grace_seconds)
    if not remaining:
        return set()

    _send_signal(remaining, signal.SIGKILL)
    return _wait_for_exit(remaining, 0.5)
