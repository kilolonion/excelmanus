"""脱离 API 进程的停机升级 / 恢复 helper。"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from excelmanus.upgrade.runtime import (
    clear_request,
    read_request,
    read_runtime,
    write_upgrade_status,
)

logger = logging.getLogger(__name__)


def _port_busy(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def api_is_running(port: int | None = None) -> bool:
    if port is None:
        rt = read_runtime() or {}
        raw = rt.get("backend_port")
        explicit = (os.environ.get("EXCELMANUS_API_PORT", "") or "").strip()
        port = (
            int(raw)
            if raw is not None
            else int(explicit or os.environ.get("EXCELMANUS_BACKEND_PORT", "") or "8000")
        )
    return _port_busy(int(port))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_pid(pid: int, *, force: bool = False) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []),
                capture_output=True, timeout=10, check=False,
            )
        else:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass


def _pid_command_line(pid: int) -> str:
    """Best-effort process command line for PID identity checks."""
    if pid <= 0:
        return ""
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8, check=False,
            )
            line = (r.stdout or "").strip()
            if line:
                return line
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            r = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={int(pid)}",
                 "get", "CommandLine", "/value"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=8, check=False,
            )
            for raw in (r.stdout or "").splitlines():
                line = raw.strip()
                if line.lower().startswith("commandline="):
                    return line.split("=", 1)[1].strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return ""

    proc = Path(f"/proc/{int(pid)}/cmdline")
    try:
        if proc.is_file():
            return (
                proc.read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
                .strip()
            )
    except OSError:
        pass
    try:
        r = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, check=False,
        )
        return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _pid_is_managed(
    pid: int,
    *,
    project_root: str | Path | None = None,
    allowed_pids: set[int] | None = None,
) -> bool:
    """Return True only for a process we can identify as part of this install."""
    if pid <= 0:
        return False
    if allowed_pids and pid in allowed_pids:
        return True
    command = _pid_command_line(pid).lower()
    if not command:
        return False
    hints = ["excelmanus", "uvicorn"]
    if project_root:
        try:
            hints.append(str(Path(project_root).resolve()).lower())
        except OSError:
            hints.append(str(project_root).lower())
    return any(hint and hint in command for hint in hints)


def _listener_pids(port: int) -> list[int]:
    """Return PIDs currently LISTENing on *port* (best effort)."""
    if port <= 0:
        return []
    pids: list[int] = []
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10, check=False,
            )
            pattern = re.compile(rf":{int(port)}\s")
            for line in (r.stdout or "").splitlines():
                if not pattern.search(line) or "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if not parts:
                    continue
                try:
                    pids.append(int(parts[-1]))
                except ValueError:
                    continue
        except (OSError, subprocess.TimeoutExpired):
            pass
        return sorted(set(pids))

    try:
        r = subprocess.run(
            ["lsof", "-ti", f"tcp:{int(port)}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, check=False,
        )
        for token in (r.stdout or "").split():
            try:
                pids.append(int(token))
            except ValueError:
                continue
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return sorted(set(pids))


def _kill_port(
    port: int,
    *,
    project_root: str | Path | None = None,
    allowed_pids: set[int] | None = None,
) -> None:
    """Force-kill the listener on *port* only when it belongs to this install."""
    if port <= 0:
        return
    for pid in _listener_pids(port):
        if not _pid_alive(pid):
            continue
        if _pid_is_managed(pid, project_root=project_root, allowed_pids=allowed_pids):
            _kill_pid(pid, force=True)
            continue
        command = _pid_command_line(pid)
        logger.warning(
            "端口 %d 被非 ExcelManus 进程占用 (PID %d)，跳过清理: %s",
            port, pid, command[:160],
        )


def stop_supervised(
    runtime: dict[str, Any] | None,
    *,
    wait_s: float = 20.0,
    project_root: str | Path | None = None,
) -> None:
    """停掉 start.sh/ps1 监督的进程组，并清理端口。

    监督进程已不在则不 killpg（避免陈旧 runtime.json 误杀无关进程组）；
    端口清理也会校验监听进程的命令行/运行时 PID，避免杀掉后来占用同端口的无关服务。
    """
    runtime = runtime or {}
    me = os.getpid()
    my_pgid = os.getpgrp() if hasattr(os, "getpgrp") else None

    pids: list[int] = []
    for key in ("supervisor_pid", "backend_pid", "frontend_pid"):
        raw = runtime.get(key)
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid != me:
            pids.append(pid)

    pgid = runtime.get("pgid")
    try:
        pgid_i = int(pgid) if pgid is not None else None
    except (TypeError, ValueError):
        pgid_i = None

    supervisor_raw = runtime.get("supervisor_pid")
    try:
        supervisor_pid = int(supervisor_raw) if supervisor_raw is not None else None
    except (TypeError, ValueError):
        supervisor_pid = None
    supervisor_alive = bool(supervisor_pid and _pid_alive(supervisor_pid))

    if (
        sys.platform != "win32"
        and supervisor_alive
        and pgid_i is not None
        and pgid_i != my_pgid
        and pgid_i > 0
    ):
        try:
            os.killpg(pgid_i, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    # stale runtime.json 中的 PID 可能已被系统复用；只有监督进程还活着时才按
    # PID 杀，否则只按端口清理由 start 脚本真正监听的进程。
    if supervisor_alive:
        for pid in pids:
            if _pid_alive(pid):
                _kill_pid(pid, force=False)

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not any(_pid_alive(pid) for pid in pids):
            break
        time.sleep(0.4)

    if (
        sys.platform != "win32"
        and supervisor_alive
        and pgid_i is not None
        and pgid_i != my_pgid
    ):
        try:
            os.killpg(pgid_i, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if supervisor_alive:
        for pid in pids:
            if _pid_alive(pid):
                _kill_pid(pid, force=True)

    ports: list[int] = []
    for key in ("backend_port", "frontend_port"):
        raw = runtime.get(key)
        try:
            port = int(raw)
        except (TypeError, ValueError):
            continue
        if port > 0:
            ports.append(port)
    if not ports:
        ports = [
            int(
                (os.environ.get("EXCELMANUS_API_PORT", "") or "").strip()
                or (os.environ.get("EXCELMANUS_BACKEND_PORT", "") or "").strip()
                or "8000"
            ),
            int((os.environ.get("EXCELMANUS_FRONTEND_PORT", "") or "").strip() or "3000"),
        ]
    managed_pids: set[int] = set()
    if supervisor_alive:
        managed_pids = set(pids)
        if supervisor_pid and supervisor_pid > 0:
            managed_pids.add(supervisor_pid)
    for port in ports:
        if _port_busy(port):
            _kill_port(
                port,
                project_root=project_root,
                allowed_pids=managed_pids,
            )

    time.sleep(0.5)


def spawn_detached_helper(project_root: Path) -> None:
    """从当前 API 进程拉起独立 helper（新会话，避免随后杀进程组时被带走）。"""
    log_path = Path(
        os.environ.get("TMPDIR")
        or os.environ.get("TEMP")
        or os.environ.get("TMP")
        or tempfile.gettempdir()
    ) / "excelmanus-upgrade.log"
    log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    popen_kwargs: dict = {
        "args": [sys.executable, "-m", "excelmanus.upgrade", "--project-root", str(project_root)],
        "cwd": str(project_root),
        "stdout": log_f,
        "stderr": log_f,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(**popen_kwargs)
    try:
        log_f.close()
    except OSError:
        pass
    logger.info("升级 helper 已启动，日志: %s", log_path)


def _start_command(runtime: dict[str, Any], project_root: Path) -> list[str]:
    start_script = runtime.get("start_script")
    script = Path(start_script) if start_script else None
    if script is None or not script.is_file():
        if sys.platform == "win32":
            candidate = project_root / "deploy" / "start.ps1"
        else:
            candidate = project_root / "deploy" / "start.sh"
        script = candidate if candidate.is_file() else None
    if script is None:
        raise FileNotFoundError("未找到 deploy/start.sh 或 start.ps1")

    args: list[str] = []
    if script.suffix.lower() == ".ps1":
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if runtime.get("production"):
            args.append("-Production")
        if runtime.get("backend_only"):
            args.append("-BackendOnly")
        if runtime.get("frontend_only"):
            args.append("-FrontendOnly")
        if runtime.get("backend_port"):
            args.extend(["-BackendPort", str(runtime["backend_port"])])
        if runtime.get("frontend_port"):
            args.extend(["-FrontendPort", str(runtime["frontend_port"])])
        if runtime.get("workers"):
            args.extend(["-Workers", str(runtime["workers"])])
        args.append("-NoOpen")
    else:
        args = ["bash", str(script)]
        if runtime.get("production"):
            args.append("--prod")
        if runtime.get("backend_only"):
            args.append("--backend-only")
        if runtime.get("frontend_only"):
            args.append("--frontend-only")
        if runtime.get("backend_port"):
            args.extend(["--backend-port", str(runtime["backend_port"])])
        if runtime.get("frontend_port"):
            args.extend(["--frontend-port", str(runtime["frontend_port"])])
        if runtime.get("backend_host"):
            args.extend(["--host", str(runtime["backend_host"])])
        if runtime.get("workers"):
            args.extend(["--workers", str(runtime["workers"])])
        args.append("--no-open")
    return args


def exec_start(runtime: dict[str, Any], project_root: Path) -> None:
    cmd = _start_command(runtime, project_root)
    logger.info("拉起服务: %s", " ".join(cmd))
    os.chdir(str(project_root))
    os.execvp(cmd[0], cmd)


def run_helper(
    project_root: str | Path | None = None,
    *,
    skip_stop: bool = False,
    skip_start: bool = False,
) -> int:
    """读取 upgrade-request.json，停机 → 备份/恢复/apply → 再拉起。"""
    from excelmanus.updater import (
        UpgradeOutcome,
        backup_user_data,
        cleanup_old_backups,
        find_backup_dir,
        restore_from_backup,
    )
    from excelmanus.upgrade.apply import apply_on_stopped_tree

    if project_root is None:
        rt = read_runtime() or {}
        project_root = rt.get("project_root") or Path(__file__).resolve().parents[2]
    project_root = Path(project_root)
    request = read_request() or {"action": "upgrade"}
    runtime = read_runtime() or {}
    action = str(request.get("action") or "upgrade")

    def _finish(ok: bool, error: str = "", extra: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"request_id": request.get("request_id"), "ok": ok, "action": action, "error": error or None,
                                   "phase": "更新完成，等待服务恢复" if ok else "更新未完成", "progress": 100}
        if extra:
            payload.update(extra)
        try:
            write_upgrade_status(payload)
        except OSError:
            logger.warning("无法写入 upgrade-status.json", exc_info=True)

    def _progress(message: str, percent: int) -> None:
        write_upgrade_status({"request_id": request.get("request_id"), "action": action,
                              "ok": None, "phase": message, "progress": percent})

    exit_code = 0
    if not skip_stop:
        _progress("正在停止当前服务", 5)
        logger.info("停止当前服务...")
        time.sleep(1.0)
        stop_supervised(runtime, project_root=project_root)

    if action == "restore":
        name = str(request.get("backup_name") or "")
        backup_dir = find_backup_dir(name, project_root)
        if backup_dir is None:
            logger.error("备份不存在: %s", name)
            exit_code = 1
            _finish(False, f"备份不存在: {name}", {
                "outcome": UpgradeOutcome.RESTORE_FAILED.value,
            })
        else:
            ok = restore_from_backup(str(backup_dir), str(project_root))
            if not ok:
                logger.error("恢复失败")
                exit_code = 1
                _finish(False, "恢复失败", {
                    "outcome": UpgradeOutcome.RESTORE_FAILED.value,
                })
            else:
                _finish(True, "", {"outcome": UpgradeOutcome.RESTORE_OK.value})
        clear_request()
        if skip_start:
            return exit_code
        try:
            exec_start(runtime, project_root)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1
        return exit_code

    skip_backup = bool(request.get("skip_backup"))
    skip_deps = bool(request.get("skip_deps"))
    use_mirror = bool(request.get("use_mirror"))
    if not skip_backup:
        _progress("正在备份 ExcelManus 设置和会话，用户文件保留原位", 10)
        logger.info("备份用户数据...")
        bk = backup_user_data(project_root)
        if not bk.success:
            logger.error("备份失败: %s", bk.error)
            clear_request()
            _finish(False, f"备份失败: {bk.error}", {
                "outcome": UpgradeOutcome.BACKUP_FAILED.value,
            })
            if skip_start:
                return 1
            try:
                exec_start(runtime, project_root)
            except FileNotFoundError:
                return 1
            return 1
        cleanup_old_backups(project_root, max_keep=2)

    try:
        result = apply_on_stopped_tree(
            project_root,
            skip_deps=skip_deps,
            use_mirror=use_mirror,
            progress_cb=lambda message, percent: _progress(message, 15 + round(percent * .8)),
        )
    except Exception as exc:
        from excelmanus.updater import UpdateResult
        logger.exception("更新进程失败")
        result = UpdateResult(outcome=UpgradeOutcome.FAILED, error=str(exc))
    clear_request()
    extra = {
        "outcome": result.outcome.value,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "steps_completed": result.steps_completed,
    }
    if result.success:
        _finish(True, result.error, extra)
    else:
        logger.error("更新失败: %s", result.error)
        exit_code = 1
        _finish(False, result.error or "更新失败", extra)
    if skip_start:
        return exit_code
    try:
        exec_start(runtime, project_root)
    except OSError as exc:
        logger.error("%s", exc)
        _finish(False, f"无法恢复服务: {exc}", extra)
        return 1
    return exit_code
