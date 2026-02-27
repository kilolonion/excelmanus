"""Docker 容器沙盒：OS 级隔离的代码执行环境。

通过 Docker 容器提供进程、网络、文件系统、资源的全面隔离，
补充 Python 层 sandbox_hook 无法覆盖的 OS 级攻击面。

启用条件：
  1. EXCELMANUS_DOCKER_SANDBOX=true（或管理员通过 API 开启）
  2. Docker daemon 可用
  3. 沙盒镜像 excelmanus-sandbox:latest 已构建
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "excelmanus-sandbox:latest"
CONTAINER_WORKSPACE = "/workspace"

_SANDBOX_DOCKERFILE_CONTENT = """\
FROM python:3.12-slim

RUN pip install --no-cache-dir \\
    pandas openpyxl numpy matplotlib xlsxwriter et-xmlfile \\
    && rm -rf /root/.cache

WORKDIR /workspace
"""


def is_docker_available() -> bool:
    """检测 Docker daemon 是否可用。"""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def is_sandbox_image_ready() -> bool:
    """检测沙盒镜像是否已构建。"""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def build_sandbox_image(force: bool = False) -> tuple[bool, str]:
    """构建沙盒 Docker 镜像。返回 (成功, 消息)。

    优先使用仓库根目录的 ``Dockerfile.sandbox``，不存在时回退到内联内容。
    """
    if not force and is_sandbox_image_ready():
        return True, "镜像已存在"

    # 优先使用仓库根目录的 Dockerfile.sandbox
    repo_dockerfile = Path(__file__).resolve().parent.parent.parent / "Dockerfile.sandbox"
    if repo_dockerfile.exists():
        build_context = str(repo_dockerfile.parent)
        build_cmd = [
            "docker", "build",
            "-t", SANDBOX_IMAGE,
            "-f", str(repo_dockerfile),
            build_context,
        ]
    else:
        import tempfile
        _tmp_dir = tempfile.mkdtemp()
        tmp_dockerfile = Path(_tmp_dir) / "Dockerfile"
        tmp_dockerfile.write_text(_SANDBOX_DOCKERFILE_CONTENT)
        build_cmd = [
            "docker", "build",
            "-t", SANDBOX_IMAGE,
            "-f", str(tmp_dockerfile),
            _tmp_dir,
        ]

    try:
        result = subprocess.run(
            build_cmd,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "构建超时（>10 分钟）"
    except FileNotFoundError:
        return False, "docker 命令不可用"

    if result.returncode == 0:
        return True, "构建成功"
    return False, f"构建失败: {result.stderr[:500]}"


def host_to_container_path(host_path: Path, workspace_root: Path) -> str:
    """将宿主机路径转换为容器内路径。"""
    try:
        rel = host_path.resolve().relative_to(workspace_root.resolve())
        return f"{CONTAINER_WORKSPACE}/{rel}"
    except ValueError:
        raise ValueError(
            f"路径 {host_path} 不在工作区 {workspace_root} 内，无法映射到容器路径"
        )


def run_in_container(
    *,
    command_parts: list[str],
    workspace_root: Path,
    workdir: Path,
    env_vars: dict[str, str] | None = None,
    timeout_seconds: int = 120,
    memory_limit: str = "512m",
    cpu_limit: float = 1.0,
    pids_limit: int = 64,
) -> dict[str, Any]:
    """在 Docker 容器中执行命令。

    Returns:
        dict: return_code, timed_out, stdout, stderr, duration_seconds
    """
    container_name = f"em-run-{uuid.uuid4().hex[:12]}"
    container_workdir = host_to_container_path(workdir, workspace_root)
    resolved_workspace = str(workspace_root.resolve())

    docker_cmd = [
        "docker", "run",
        "--rm",
        "--name", container_name,
        "--network=none",
        f"--memory={memory_limit}",
        f"--cpus={cpu_limit}",
        f"--pids-limit={pids_limit}",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "--security-opt=no-new-privileges:true",
        "--cap-drop=ALL",
        "--cap-add=DAC_OVERRIDE",
        "-v", f"{resolved_workspace}:{CONTAINER_WORKSPACE}",
        "-w", container_workdir,
    ]

    # 匹配宿主机用户 UID/GID 以确保文件权限一致
    if hasattr(os, "getuid"):
        uid = os.getuid()
        gid = os.getgid()
        docker_cmd.extend(["--user", f"{uid}:{gid}"])

    if env_vars:
        for key, value in env_vars.items():
            docker_cmd.extend(["-e", f"{key}={value}"])

    docker_cmd.append(SANDBOX_IMAGE)
    docker_cmd.extend(command_parts)

    started = time.time()
    timed_out = False
    return_code = 1
    stdout = ""
    stderr = ""

    try:
        completed = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        return_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = 124
        stdout = (
            exc.stdout.decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else exc.stdout
        ) or ""
        stderr = (
            exc.stderr.decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else exc.stderr
        ) or ""
        try:
            subprocess.run(
                ["docker", "kill", container_name],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except Exception:
            pass

    return {
        "return_code": return_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "duration_seconds": round(time.time() - started, 3),
    }
