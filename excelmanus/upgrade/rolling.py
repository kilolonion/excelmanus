"""Small blue/green process handoff used by production deployment scripts.

The controller never stops the old process before the candidate answers its
health endpoint.  Switching the reverse proxy is intentionally injected as a
callback so systemd, nginx and a managed platform can use their own reload
primitive without coupling the Python API to one supervisor.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class RollingResult:
    ok: bool
    candidate_pid: int | None = None
    switched: bool = False
    error: str = ""


def _healthy(url: str, timeout: float) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
            return payload.get("status") in {"ok", "ready"}
    except Exception:
        return False


def _stop(process: subprocess.Popen[Any], *, force: bool = False) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=8,
            )
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL if force else signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass


def roll_forward(
    command: Sequence[str],
    *,
    health_url: str,
    switch_upstream: Callable[[], bool],
    old_pid: int | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 60.0,
    poll_seconds: float = 0.5,
) -> RollingResult:
    """Start candidate, wait for health, switch traffic, then drain old PID."""
    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        candidate = subprocess.Popen(list(command), **kwargs)
    except OSError as exc:
        return RollingResult(False, error=f"candidate start failed: {exc}")

    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if candidate.poll() is not None:
            return RollingResult(False, candidate.pid, error="candidate exited before health")
        if _healthy(health_url, min(2.0, max(0.2, poll_seconds * 2))):
            break
        time.sleep(max(0.05, poll_seconds))
    else:
        _stop(candidate, force=True)
        return RollingResult(False, candidate.pid, error="candidate health timeout")

    try:
        switched = bool(switch_upstream())
    except Exception as exc:
        switched = False
        switch_error = str(exc)
    else:
        switch_error = ""
    if not switched:
        _stop(candidate, force=True)
        return RollingResult(False, candidate.pid, error=switch_error or "upstream switch failed")

    if old_pid and old_pid != candidate.pid:
        # Direct PID signalling is enough after the proxy switched; the
        # supervisor remains responsible for an optional process-group drain.
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(old_pid), "/T"], check=False, timeout=8)
            else:
                os.kill(old_pid, signal.SIGTERM)
        except (OSError, subprocess.SubprocessError):
            pass
    return RollingResult(True, candidate.pid, switched=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ExcelManus blue/green candidate runner")
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--old-pid", type=int, default=0)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    root = Path(args.candidate_root).resolve()
    url = args.health_url or f"http://127.0.0.1:{args.port}/api/v1/health"

    # CLI mode only validates readiness.  It must never claim that traffic was
    # switched (or stop the old PID) because it has no proxy reload callback.
    # Supervisors should call ``roll_forward`` directly with an atomic
    # ``switch_upstream`` implementation when they can perform the cutover.
    command = [args.python, "-m", "excelmanus.api", "--host", "127.0.0.1", "--port", str(args.port)]
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        candidate = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        result = RollingResult(False, error=f"candidate start failed: {exc}")
    else:
        deadline = time.monotonic() + 60.0
        ready = False
        while time.monotonic() < deadline:
            if candidate.poll() is not None:
                break
            if _healthy(url, 2.0):
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            _stop(candidate, force=True)
            result = RollingResult(False, candidate.pid, error="candidate health timeout")
        else:
            _stop(candidate, force=True)
            result = RollingResult(True, candidate.pid, switched=False)
    print(json.dumps(result.__dict__, ensure_ascii=False))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
