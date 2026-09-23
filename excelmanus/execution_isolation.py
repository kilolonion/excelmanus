"""Optional Docker execution boundary for dynamic worker code.

The normal local subprocess remains the default.  Setting
``EXCELMANUS_EXECUTION_ISOLATION=docker`` moves each ``run_code`` invocation
into a short-lived container with a read-only root filesystem, no network by
default, bounded CPU/memory/PIDs and only the current workspace mounted.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def isolation_mode() -> str:
    return (os.environ.get("EXCELMANUS_EXECUTION_ISOLATION") or "local").strip().lower()


def docker_enabled() -> bool:
    return isolation_mode() in {"docker", "container", "containerized"}


def _container_path(value: str, root: Path) -> str:
    raw = str(value)
    if not Path(raw).is_absolute():
        return raw
    try:
        rel = Path(raw).resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return raw
    return "/workspace/" + rel.as_posix()


def _rewrite_env(env: dict[str, str], root: Path) -> dict[str, str]:
    host = str(root.resolve())
    out: dict[str, str] = {}
    for key, value in env.items():
        text = str(value)
        if text == host:
            text = "/workspace"
        elif text.startswith(host + os.sep):
            text = "/workspace/" + Path(text[len(host) + 1 :]).as_posix()
        out[str(key)] = text
    return out


def prepare_command(
    command: list[str],
    *,
    workspace_root: str | Path,
    workdir: str | Path,
    env: dict[str, str],
    allow_network: bool = False,
) -> tuple[list[str], Path, dict[str, str], bool]:
    """Return command/cwd/env for the configured isolation backend."""
    if not docker_enabled():
        return command, Path(workdir), env, False
    docker = shutil.which(os.environ.get("EXCELMANUS_DOCKER_BIN", "docker"))
    if not docker:
        raise RuntimeError(
            "EXCELMANUS_EXECUTION_ISOLATION=docker 但未找到 Docker CLI；"
            "请安装 Docker，或改回 EXCELMANUS_EXECUTION_ISOLATION=local。"
        )
    root = Path(workspace_root).resolve()
    try:
        work_rel = Path(workdir).resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise RuntimeError("Docker 执行工作目录必须位于当前工作区内") from exc
    image = (os.environ.get("EXCELMANUS_DOCKER_IMAGE") or "excelmanus/runtime:latest").strip()
    default_network = "bridge" if allow_network else "none"
    network = (os.environ.get("EXCELMANUS_DOCKER_NETWORK") or default_network).strip()
    cpus = (os.environ.get("EXCELMANUS_DOCKER_CPUS") or "2").strip()
    memory = (os.environ.get("EXCELMANUS_DOCKER_MEMORY") or "2g").strip()
    pids = (os.environ.get("EXCELMANUS_DOCKER_PIDS_LIMIT") or "256").strip()

    rewritten_command = [_container_path(item, root) for item in command]
    # The host interpreter path is not present in the runtime image.  The
    # wrapper only needs a Python executable; callers can override it for a
    # custom image containing a different interpreter name.
    if rewritten_command:
        rewritten_command[0] = os.environ.get("EXCELMANUS_DOCKER_PYTHON", "python")

    docker_command = [
        docker, "run", "--rm", "--init",
        "--network", network,
        "--cpus", cpus,
        "--memory", memory,
        "--pids-limit", pids,
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m",
        "-v", f"{root}:/workspace:rw",
        "-w", "/workspace/" + work_rel.as_posix() if str(work_rel) != "." else "/workspace",
    ]
    if os.name != "nt":
        try:
            docker_command.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        except AttributeError:
            pass
    for key, value in _rewrite_env(env, root).items():
        docker_command.extend(["-e", f"{key}={value}"])
    docker_command.extend([image, *rewritten_command])
    # Docker owns cwd and the mount.  Keep the host cwd at the workspace root
    # so relative paths used by the CLI itself remain valid before exec.
    cli_env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
        if os.environ.get(key)
    }
    return docker_command, root, cli_env, True
