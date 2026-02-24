"""代码执行工具：写入文本文件与运行 Python 代码。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from excelmanus.security import FileAccessGuard
from excelmanus.tools._guard_ctx import get_guard as _get_ctx_guard
from excelmanus.tools.registry import ToolDef

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard（优先 per-session contextvar）。"""
    ctx_guard = _get_ctx_guard()
    if ctx_guard is not None:
        return ctx_guard
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。"""
    global _guard
    _guard = FileAccessGuard(workspace_root)


# ── Docker 沙盒开关 ──────────────────────────────────────
# 全局标志为部署级设置（非用户级）。
# 每会话 SandboxEnv contextvar 在工具调度器设置后优先生效。

import contextvars as _contextvars

_docker_sandbox_enabled: bool = False

_current_sandbox_env: _contextvars.ContextVar[Any] = _contextvars.ContextVar(
    "_current_sandbox_env", default=None,
)


def init_docker_sandbox(enabled: bool) -> None:
    """设置 Docker 沙盒模式开关（由 API 层在 lifespan 中调用）。"""
    global _docker_sandbox_enabled
    _docker_sandbox_enabled = enabled


def set_sandbox_env(env: Any) -> _contextvars.Token:
    """Set the per-session SandboxEnv for the current async context.

    Returns a token that can be used to reset the contextvar.
    """
    return _current_sandbox_env.set(env)


def _get_active_sandbox_env() -> Any:
    """Return the active SandboxEnv, or None."""
    return _current_sandbox_env.get(None)


def _is_docker_sandbox() -> bool:
    """Check if Docker sandbox is active, preferring per-session env."""
    env = _get_active_sandbox_env()
    if env is not None:
        return getattr(env, "docker_enabled", False)
    return _docker_sandbox_enabled


# ── 解释器探测 ───────────────────────────────────────────


@dataclass
class _InterpreterProbe:
    command: list[str]
    status: str
    detail: str


def _tail(text: str, lines: int) -> str:
    if lines <= 0:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def _shorten(text: str, limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _command_to_text(command: list[str]) -> str:
    return " ".join(command)


def _is_path_like(command: str) -> bool:
    return any(token in command for token in ("/", "\\", ":"))


def _parse_python_command(spec: str) -> list[str]:
    value = spec.strip()
    if not value:
        raise ValueError("python_command 不能为空")
    lowered = value.lower()
    if lowered == "py -3":
        return ["py", "-3"]
    if lowered == "py -2":
        return ["py", "-2"]
    return [value]


def _command_exists(command: list[str]) -> bool:
    executable = command[0]
    if _is_path_like(executable):
        return Path(executable).expanduser().exists()
    return shutil.which(executable) is not None


def _probe_environment(
    command: list[str], *,
    require_excel_deps: bool,
    sandbox_tier: str = "RED",
) -> _InterpreterProbe:
    if not _command_exists(command):
        return _InterpreterProbe(
            command=command,
            status="not_found",
            detail="可执行文件不存在",
        )

    if require_excel_deps:
        if sandbox_tier in ("GREEN", "YELLOW"):
            # 模拟实际沙盒的 Import Guard，避免探针通过但实际执行失败
            from excelmanus.security.sandbox_hook import (
                _GREEN_BLOCKED,
                _YELLOW_BLOCKED,
            )
            blocked = _GREEN_BLOCKED if sandbox_tier == "GREEN" else _YELLOW_BLOCKED
            blocked_repr = repr(blocked)
            probe_code = (
                "import sys\n"
                "class _B:\n"
                "    def find_spec(self, n, *a):\n"
                "        for b in " + blocked_repr + ":\n"
                "            if n == b or n.startswith(b + '.'):\n"
                "                raise ImportError('sandbox blocks ' + n)\n"
                "sys.meta_path.insert(0, _B())\n"
                "import pandas,openpyxl\n"
            )
            probe_command = [*command, "-c", probe_code]
        else:
            probe_command = [*command, "-c", "import pandas,openpyxl"]
    else:
        probe_command = [*command, "-c", "import sys; print(sys.version_info[0])"]

    try:
        completed = subprocess.run(
            probe_command,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _InterpreterProbe(
            command=command,
            status="error",
            detail=_shorten(str(exc)),
        )

    if completed.returncode == 0:
        return _InterpreterProbe(
            command=command,
            status="ok",
            detail="依赖检查通过",
        )

    detail = (completed.stderr or completed.stdout or "").strip()
    if not detail:
        detail = f"退出码 {completed.returncode}"
    return _InterpreterProbe(
        command=command,
        status="missing_deps",
        detail=_shorten(detail),
    )


def _resolve_python_command(
    python_command: str, *,
    require_excel_deps: bool,
    sandbox_tier: str = "RED",
) -> tuple[list[str], list[_InterpreterProbe], str]:
    if python_command != "auto":
        command = _parse_python_command(python_command)
        probe = _probe_environment(command, require_excel_deps=require_excel_deps, sandbox_tier=sandbox_tier)
        if probe.status != "ok":
            raise RuntimeError(
                f"指定解释器不可用: {_command_to_text(command)}; {probe.status}: {probe.detail}"
            )
        return command, [probe], "explicit"

    candidates: list[list[str]] = []
    env_python = os.environ.get("EXCELMANUS_RUN_PYTHON")
    if env_python:
        candidates.append(_parse_python_command(env_python))
    if sys.executable:
        candidates.append([sys.executable])
    candidates.extend(
        [
            ["python"],
            ["python3"],
            ["py", "-3"],
            ["py"],
        ]
    )

    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in candidates:
        key = tuple(item.lower() for item in command)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)

    probes: list[_InterpreterProbe] = []
    for command in deduped:
        probe = _probe_environment(command, require_excel_deps=require_excel_deps, sandbox_tier=sandbox_tier)
        probes.append(probe)
        if probe.status == "ok":
            return command, probes, "auto"

    details = "\n".join(
        f"- {_command_to_text(probe.command)} => {probe.status}: {probe.detail}"
        for probe in probes
    )
    raise RuntimeError(
        "自动探测 Python 解释器失败，未找到可用环境。\n"
        f"尝试记录：\n{details}\n"
        "可选方案：\n"
        "1) 使用 python_command 显式指定解释器\n"
        "2) 设置环境变量 EXCELMANUS_RUN_PYTHON\n"
        "3) 在目标解释器中安装依赖（pandas/openpyxl）"
    )


# ── 软沙盒 ───────────────────────────────────────────────

_SANDBOX_ENV_ALLOWLIST = {
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TMP",
    "TEMP",
}


def _build_sandbox_env() -> tuple[dict[str, str], list[str]]:
    """构建最小环境变量白名单。"""
    sandbox_env: dict[str, str] = {}
    warnings: list[str] = []
    for key in _SANDBOX_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            sandbox_env[key] = value

    if os.name == "nt" and "SYSTEMROOT" not in sandbox_env:
        warnings.append("缺少 SYSTEMROOT，Windows 子进程可能无法启动。")

    sandbox_env["PYTHONNOUSERSITE"] = "1"
    sandbox_env["PYTHONDONTWRITEBYTECODE"] = "1"
    # TMPDIR/TMP/TEMP 由 _execute_script 注入工作区本地目录
    return sandbox_env, warnings


def _ensure_isolated_python(command: list[str]) -> tuple[list[str], bool]:
    """确保 Python 调用启用 -I 隔离模式。"""
    if any(item == "-I" for item in command[1:]):
        return command, True
    return [*command, "-I"], True


def _build_unix_limits_preexec(
    timeout_seconds: int,
) -> tuple[Callable[[], None] | None, bool, list[str]]:
    """构建 Unix 平台资源限制 preexec_fn。"""
    warnings: list[str] = []
    if os.name == "nt":
        warnings.append("当前平台不支持 Unix 资源限制，已跳过。")
        return None, False, warnings

    try:
        import resource  # type: ignore
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"加载 resource 模块失败，已跳过资源限制：{exc}")
        return None, False, warnings

    candidates: list[tuple[int, int, str]] = []
    limit_plan = [
        ("RLIMIT_CPU", max(1, min(timeout_seconds, 300))),
        ("RLIMIT_AS", 512 * 1024 * 1024),
        ("RLIMIT_NOFILE", 64),
        ("RLIMIT_NPROC", 32),
    ]
    for name, value in limit_plan:
        if hasattr(resource, name):
            candidates.append((getattr(resource, name), value, name))
        else:
            warnings.append(f"{name} 不可用，已跳过。")

    if not candidates:
        warnings.append("无可用资源限制项，已跳过。")
        return None, False, warnings

    def _preexec() -> None:
        for res_code, desired, _name in candidates:
            try:
                soft, hard = resource.getrlimit(res_code)
                target = desired
                if soft != resource.RLIM_INFINITY:
                    target = min(target, int(soft))
                if hard != resource.RLIM_INFINITY:
                    target = min(target, int(hard))
                resource.setrlimit(res_code, (target, target))
            except Exception:
                continue

    return _preexec, True, warnings


# ── 工具函数 ──────────────────────────────────────────────


def write_text_file(
    file_path: str,
    content: str,
    overwrite: bool = True,
    encoding: str = "utf-8",
) -> str:
    """写入文本文件（默认覆盖）。"""
    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    existed_before = safe_path.exists()

    if existed_before and not overwrite:
        return json.dumps(
            {
                "status": "error",
                "error": f"文件已存在且 overwrite=false: {safe_path.name}",
            },
            ensure_ascii=False,
        )

    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding=encoding)
    return json.dumps(
        {
            "status": "success",
            "file": str(safe_path.relative_to(guard.workspace_root)),
            "bytes": len(content.encode(encoding, errors="ignore")),
            "encoding": encoding,
            "overwritten": existed_before,
        },
        ensure_ascii=False,
    )


def run_code(
    code: str | None = None,
    script_path: str | None = None,
    args: list[str] | None = None,
    workdir: str = ".",
    timeout_seconds: int = 120,
    python_command: str = "auto",
    tail_lines: int = 80,
    require_excel_deps: bool = True,
    stdout_file: str | None = None,
    stderr_file: str | None = None,
    sandbox_tier: str = "RED",
) -> str:
    """执行 Python 代码。支持内联代码片段或磁盘脚本文件。

    两种模式（互斥，必须且只能指定其一）：
    - **内联模式**：传入 ``code`` 参数，内部写临时文件执行后清理。
    - **文件模式**：传入 ``script_path`` 参数，直接执行已有 ``.py`` 文件。
    """
    # ── 参数规范化：空字符串 / 纯空白视为未传 ──
    # LLM 生成 JSON 时常传 "" 或 "  "，在互斥校验前统一转为 None
    code = None if not code or not code.strip() else code
    script_path = None if not script_path or not script_path.strip() else script_path.strip()
    stdout_file = None if not stdout_file or not stdout_file.strip() else stdout_file.strip()
    stderr_file = None if not stderr_file or not stderr_file.strip() else stderr_file.strip()

    # ── 参数校验 ──
    if code is not None and script_path is not None:
        # 两者都传了非空值，优先使用 script_path
        code = None
    if code is None and script_path is None:
        raise ValueError("必须指定 code 或 script_path 其中之一")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if tail_lines < 0:
        raise ValueError("tail_lines 不能小于 0")

    guard = _get_guard()
    workdir_safe = guard.resolve_and_validate(workdir)
    if not workdir_safe.exists() or not workdir_safe.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {workdir_safe}")

    # ── 确定脚本路径 ──
    inline_mode = code is not None
    temp_script: Path | None = None
    if inline_mode:
        temp_dir = guard.workspace_root / "scripts" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_name = f"_rc_{uuid.uuid4().hex[:12]}.py"
        temp_script = temp_dir / temp_name
        temp_script.write_text(code, encoding="utf-8")
        script_safe = temp_script
    else:
        assert script_path is not None
        script_safe = guard.resolve_and_validate(script_path)
        if not script_safe.exists() or not script_safe.is_file():
            raise FileNotFoundError(f"脚本不存在: {script_safe}")
        if script_safe.suffix.lower() != ".py":
            raise ValueError(f"仅允许运行 .py 文件: {script_safe}")

    try:
        result_json = _execute_script(
            guard=guard,
            script_safe=script_safe,
            workdir_safe=workdir_safe,
            args=args,
            timeout_seconds=timeout_seconds,
            python_command=python_command,
            tail_lines=tail_lines,
            require_excel_deps=require_excel_deps,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            inline_mode=inline_mode,
            sandbox_tier=sandbox_tier,
        )
    finally:
        if temp_script is not None and temp_script.exists():
            try:
                temp_script.unlink()
            except OSError:
                pass
    return result_json


def _execute_script_docker(
    *,
    guard: FileAccessGuard,
    script_safe: Path,
    workdir_safe: Path,
    args: list[str] | None,
    timeout_seconds: int,
    tail_lines: int,
    stdout_file: str | None,
    stderr_file: str | None,
    inline_mode: bool,
    sandbox_tier: str = "RED",
) -> str:
    """Docker 容器内执行脚本（OS 级隔离）。"""
    from excelmanus.security.docker_sandbox import (
        CONTAINER_WORKSPACE,
        host_to_container_path,
        run_in_container,
    )

    workspace_root = guard.workspace_root
    safe_args = [str(item) for item in (args or [])]
    sandbox_warnings: list[str] = []

    sandbox_tmpdir = workspace_root / ".tmp"
    sandbox_tmpdir.mkdir(parents=True, exist_ok=True)
    cow_log_name = f"_cow_{uuid.uuid4().hex[:12]}.log"
    cow_log_path = sandbox_tmpdir / cow_log_name
    container_tmpdir = f"{CONTAINER_WORKSPACE}/.tmp"

    env_vars = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "TMPDIR": container_tmpdir,
        "TMP": container_tmpdir,
        "TEMP": container_tmpdir,
        "HOME": "/tmp",
        "MPLCONFIGDIR": "/tmp/mpl",
        "EXCELMANUS_COW_LOG": host_to_container_path(cow_log_path, workspace_root),
    }

    container_script = host_to_container_path(script_safe, workspace_root)

    temp_wrapper: Path | None = None
    if sandbox_tier in ("GREEN", "YELLOW"):
        from excelmanus.security.sandbox_hook import generate_wrapper_script

        wrapper_src = generate_wrapper_script(sandbox_tier, CONTAINER_WORKSPACE)
        temp_dir = workspace_root / "scripts" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_wrapper = temp_dir / f"_sw_{uuid.uuid4().hex[:12]}.py"
        temp_wrapper.write_text(wrapper_src, encoding="utf-8")
        container_wrapper = host_to_container_path(temp_wrapper, workspace_root)
        command_parts = ["python", "-I", container_wrapper, container_script, *safe_args]
    else:
        command_parts = ["python", "-I", container_script, *safe_args]

    try:
        docker_result = run_in_container(
            command_parts=command_parts,
            workspace_root=workspace_root,
            workdir=workdir_safe,
            env_vars=env_vars,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        sandbox_warnings.append(f"Docker 执行异常: {exc}")
        docker_result = {
            "return_code": 1,
            "timed_out": False,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": 0.0,
        }
    finally:
        if temp_wrapper is not None and temp_wrapper.exists():
            try:
                temp_wrapper.unlink()
            except OSError:
                pass

    return_code = docker_result["return_code"]
    timed_out = docker_result["timed_out"]
    stdout = docker_result["stdout"]
    stderr = docker_result["stderr"]

    stdout_saved: str | None = None
    stderr_saved: str | None = None
    if stdout_file:
        stdout_safe = guard.resolve_and_validate(stdout_file)
        stdout_safe.parent.mkdir(parents=True, exist_ok=True)
        stdout_safe.write_text(stdout, encoding="utf-8")
        stdout_saved = str(stdout_safe.relative_to(workspace_root))
    if stderr_file:
        stderr_safe = guard.resolve_and_validate(stderr_file)
        stderr_safe.parent.mkdir(parents=True, exist_ok=True)
        stderr_safe.write_text(stderr, encoding="utf-8")
        stderr_saved = str(stderr_safe.relative_to(workspace_root))

    if timed_out:
        status = "timed_out"
    elif return_code == 0:
        status = "success"
    else:
        status = "failed"

    cow_mapping: dict[str, str] = {}
    if cow_log_path.exists():
        try:
            for line in cow_log_path.read_text(encoding="utf-8").splitlines():
                if "\t" in line:
                    src, dst = line.split("\t", 1)
                    try:
                        src_rel = src.replace(CONTAINER_WORKSPACE + "/", "", 1)
                        dst_rel = dst.replace(CONTAINER_WORKSPACE + "/", "", 1)
                        cow_mapping[src_rel] = dst_rel
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            try:
                cow_log_path.unlink()
            except OSError:
                pass

    result: dict[str, Any] = {
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": docker_result["duration_seconds"],
        "mode": "inline" if inline_mode else "file",
        "script": str(script_safe.relative_to(workspace_root)),
        "workdir": str(workdir_safe.relative_to(workspace_root)),
        "command": command_parts,
        "python_command": ["python", "-I"],
        "python_resolve_mode": "docker",
        "python_probe_results": [],
        "stdout_tail": _tail(stdout, tail_lines),
        "stderr_tail": _tail(stderr, tail_lines),
        "stdout_file": stdout_saved,
        "stderr_file": stderr_saved,
        "cow_mapping": cow_mapping,
        "sandbox": {
            "mode": "docker",
            "tier": sandbox_tier,
            "auto_approved": sandbox_tier in ("GREEN", "YELLOW"),
            "isolated_python": True,
            "limits_applied": True,
            "warnings": sandbox_warnings,
        },
    }

    if cow_mapping:
        _cow_lines = [f"  {src} → {dst}" for src, dst in cow_mapping.items()]
        result["cow_hint"] = (
            "⚠️ 原始文件受保护，已自动复制到 outputs/ 目录。"
            "后续对该文件的读取和写入请使用副本路径：\n"
            + "\n".join(_cow_lines)
        )

    if status == "failed":
        stderr_text = stderr or ""
        hints: list[str] = []
        if sandbox_tier in ("GREEN", "YELLOW") and "安全策略禁止" in stderr_text:
            if "路径不在工作区内" in stderr_text:
                hints.append(
                    "库内部临时文件写入被拦截。"
                    "尝试使用 mcp_excel 工具写入，或通过 delegate_to_subagent 完成。"
                )
        if "ModuleNotFoundError" in stderr_text or "ImportError" in stderr_text or "安全策略禁止" in stderr_text:
            if any(
                m in stderr_text
                for m in [
                    "requests", "urllib", "http", "socket",
                    "os", "sys", "subprocess", "No module named",
                ]
            ):
                hints.append(
                    "安全沙盒拦截：系统禁止在 run_code 中使用网络或系统级模块。"
                    "请放弃尝试网络请求，或仅使用安全的数据处理库（pandas/numpy）。"
                )
        if hints:
            result["recovery_hint"] = " ".join(hints)

    return json.dumps(result, ensure_ascii=False, indent=2)


def _execute_script(
    *,
    guard: FileAccessGuard,
    script_safe: Path,
    workdir_safe: Path,
    args: list[str] | None,
    timeout_seconds: int,
    python_command: str,
    tail_lines: int,
    require_excel_deps: bool,
    stdout_file: str | None,
    stderr_file: str | None,
    inline_mode: bool,
    sandbox_tier: str = "RED",
) -> str:
    """内部执行脚本核心逻辑（供 run_code 调用）。"""
    if _is_docker_sandbox():
        return _execute_script_docker(
            guard=guard,
            script_safe=script_safe,
            workdir_safe=workdir_safe,
            args=args,
            timeout_seconds=timeout_seconds,
            tail_lines=tail_lines,
            stdout_file=stdout_file,
            stderr_file=stderr_file,
            inline_mode=inline_mode,
            sandbox_tier=sandbox_tier,
        )

    python_cmd, probes, mode = _resolve_python_command(
        python_command,
        require_excel_deps=require_excel_deps,
        sandbox_tier=sandbox_tier,
    )
    sandbox_python_cmd, isolated_python = _ensure_isolated_python(python_cmd)
    sandbox_env, env_warnings = _build_sandbox_env()
    preexec_fn, limits_applied, limit_warnings = _build_unix_limits_preexec(
        timeout_seconds
    )
    sandbox_warnings = [*env_warnings, *limit_warnings]
    safe_args = [str(item) for item in (args or [])]

    # ── 注入工作区本地临时目录（确保 et_xmlfile 等库的 temp 文件在工作区内） ──
    sandbox_tmpdir = guard.workspace_root / ".tmp"
    sandbox_tmpdir.mkdir(parents=True, exist_ok=True)
    sandbox_env["TMPDIR"] = str(sandbox_tmpdir)
    sandbox_env["TMP"] = str(sandbox_tmpdir)
    sandbox_env["TEMP"] = str(sandbox_tmpdir)

    # ── CoW 日志 ──
    cow_log_path = sandbox_tmpdir / f"_cow_{uuid.uuid4().hex[:12]}.log"
    sandbox_env["EXCELMANUS_COW_LOG"] = str(cow_log_path)

    # ── 沙盒 wrapper 注入（GREEN/YELLOW 模式） ──
    temp_wrapper: Path | None = None
    if sandbox_tier in ("GREEN", "YELLOW"):
        from excelmanus.security.sandbox_hook import generate_wrapper_script
        wrapper_src = generate_wrapper_script(sandbox_tier, str(guard.workspace_root))
        temp_dir = guard.workspace_root / "scripts" / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_wrapper = temp_dir / f"_sw_{uuid.uuid4().hex[:12]}.py"
        temp_wrapper.write_text(wrapper_src, encoding="utf-8")
        command = [*sandbox_python_cmd, str(temp_wrapper), str(script_safe), *safe_args]
    else:
        command = [*sandbox_python_cmd, str(script_safe), *safe_args]

    started = time.time()
    timed_out = False
    return_code = 1
    stdout = ""
    stderr = ""

    try:
        run_kwargs: dict[str, Any] = {
            "cwd": workdir_safe,
            "capture_output": True,
            "text": True,
            "timeout": timeout_seconds,
            "check": False,
            "env": sandbox_env,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
            "start_new_session": True,
        }
        if preexec_fn is not None:
            run_kwargs["preexec_fn"] = preexec_fn
        completed = subprocess.run(
            command,
            **run_kwargs,
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

    stdout_saved: str | None = None
    stderr_saved: str | None = None
    if stdout_file:
        stdout_safe = guard.resolve_and_validate(stdout_file)
        stdout_safe.parent.mkdir(parents=True, exist_ok=True)
        stdout_safe.write_text(stdout, encoding="utf-8")
        stdout_saved = str(stdout_safe.relative_to(guard.workspace_root))
    if stderr_file:
        stderr_safe = guard.resolve_and_validate(stderr_file)
        stderr_safe.parent.mkdir(parents=True, exist_ok=True)
        stderr_safe.write_text(stderr, encoding="utf-8")
        stderr_saved = str(stderr_safe.relative_to(guard.workspace_root))

    if timed_out:
        status = "timed_out"
    elif return_code == 0:
        status = "success"
    else:
        status = "failed"
        
    cow_mapping = {}
    if cow_log_path.exists():
        try:
            for line in cow_log_path.read_text(encoding="utf-8").splitlines():
                if "\t" in line:
                    src, dst = line.split("\t", 1)
                    try:
                        rel_src = str(Path(src).relative_to(guard.workspace_root))
                        rel_dst = str(Path(dst).relative_to(guard.workspace_root))
                        cow_mapping[rel_src] = rel_dst
                    except ValueError:
                        pass
        except Exception:
            pass
        finally:
            try:
                cow_log_path.unlink()
            except OSError:
                pass

    result: dict[str, Any] = {
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(time.time() - started, 3),
        "mode": "inline" if inline_mode else "file",
        "script": str(script_safe.relative_to(guard.workspace_root)),
        "workdir": str(workdir_safe.relative_to(guard.workspace_root)),
        "command": command,
        "python_command": sandbox_python_cmd,
        "python_resolve_mode": mode,
        "python_probe_results": [
            {
                "command": probe.command,
                "status": probe.status,
                "detail": probe.detail,
            }
            for probe in probes
        ],
        "stdout_tail": _tail(stdout, tail_lines),
        "stderr_tail": _tail(stderr, tail_lines),
        "stdout_file": stdout_saved,
        "stderr_file": stderr_saved,
        "cow_mapping": cow_mapping,
        "sandbox": {
            "mode": "soft" if sandbox_tier == "RED" else "policy_engine",
            "tier": sandbox_tier,
            "auto_approved": sandbox_tier in ("GREEN", "YELLOW"),
            "isolated_python": isolated_python,
            "limits_applied": limits_applied,
            "warnings": sandbox_warnings,
        },
    }
    # CoW 路径提示：bench/external 文件被保护时，提醒使用副本路径
    if cow_mapping:
        _cow_lines = [f"  {src} → {dst}" for src, dst in cow_mapping.items()]
        result["cow_hint"] = (
            "⚠️ 原始文件受保护，已自动复制到 outputs/ 目录。"
            "后续对该文件的读取和写入请使用副本路径：\n"
            + "\n".join(_cow_lines)
        )

    # 检测沙盒权限错误，追加恢复提示
    if status == "failed":
        stderr_text = stderr or ""
        hints: list[str] = []
        if sandbox_tier in ("GREEN", "YELLOW") and "安全策略禁止" in stderr_text:
            if "路径不在工作区内" in stderr_text:
                hints.append(
                    "库内部临时文件写入被拦截。"
                    "尝试使用 mcp_excel 工具写入，或通过 delegate_to_subagent 完成。"
                )
        
        if "ModuleNotFoundError" in stderr_text or "ImportError" in stderr_text or "安全策略禁止" in stderr_text:
            if any(m in stderr_text for m in ["requests", "urllib", "http", "socket", "os", "sys", "subprocess", "No module named"]):
                hints.append(
                    "安全沙盒拦截：系统禁止在 run_code 中使用网络或系统级模块。请放弃尝试网络请求，或仅使用安全的数据处理库（pandas/numpy）。"
                )

        if hints:
            result["recovery_hint"] = " ".join(hints)

    # 清理临时 wrapper
    if temp_wrapper is not None and temp_wrapper.exists():
        try:
            temp_wrapper.unlink()
        except OSError:
            pass

    return json.dumps(result, ensure_ascii=False, indent=2)


def get_tools() -> list[ToolDef]:
    """返回代码执行工具定义。"""
    return [
        ToolDef(
            name="write_text_file",
            description="写入文本文件（常用于生成 Python 脚本）",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                    "overwrite": {
                        "type": "boolean",
                        "description": "文件存在时是否覆盖",
                        "default": True,
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文本编码",
                        "default": "utf-8",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
            func=write_text_file,
            write_effect="workspace_write",
        ),
        ToolDef(
            name="run_code",
            description="执行 Python 代码（支持内联代码片段或磁盘脚本文件，二选一）。适用于复杂数据变换（透视、转置、分组聚合、跨表匹配填充、条件行删除等）、批量计算、以及专用工具难以一步完成的多步逻辑。优先编写小步可验证脚本，执行后立即检查结果。",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "内联 Python 代码（与 script_path 互斥）",
                    },
                    "script_path": {
                        "type": "string",
                        "description": "磁盘脚本路径 .py（与 code 互斥）",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "传递给脚本的位置参数（仅文件模式）",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "执行工作目录",
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "超时秒数",
                        "default": 120,
                    },
                    "python_command": {
                        "type": "string",
                        "description": "解释器命令，默认 auto 自动探测",
                        "default": "auto",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "返回日志尾部行数",
                        "default": 80,
                    },
                    "require_excel_deps": {
                        "type": "boolean",
                        "description": "是否要求解释器具备 pandas/openpyxl",
                        "default": True,
                    },
                    "stdout_file": {
                        "type": "string",
                        "description": "完整 stdout 日志输出路径（可选）",
                    },
                    "stderr_file": {
                        "type": "string",
                        "description": "完整 stderr 日志输出路径（可选）",
                    },
                },
                "additionalProperties": False,
            },
            func=run_code,
            max_result_chars=3000,
            truncate_head_chars=2000,
            truncate_tail_chars=1000,
            write_effect="dynamic",
        ),
    ]
