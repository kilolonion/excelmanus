"""代码执行工具：写入脚本并运行 Python 脚本。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。"""
    global _guard
    _guard = FileAccessGuard(workspace_root)


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
) -> _InterpreterProbe:
    if not _command_exists(command):
        return _InterpreterProbe(
            command=command,
            status="not_found",
            detail="可执行文件不存在",
        )

    if require_excel_deps:
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
) -> tuple[list[str], list[_InterpreterProbe], str]:
    if python_command != "auto":
        command = _parse_python_command(python_command)
        probe = _probe_environment(command, require_excel_deps=require_excel_deps)
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
        probe = _probe_environment(command, require_excel_deps=require_excel_deps)
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


def run_python_script(
    script_path: str,
    args: list[str] | None = None,
    workdir: str = ".",
    timeout_seconds: int = 300,
    python_command: str = "auto",
    tail_lines: int = 80,
    require_excel_deps: bool = True,
    stdout_file: str | None = None,
    stderr_file: str | None = None,
) -> str:
    """运行 Python 脚本并返回结构化结果。"""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if tail_lines < 0:
        raise ValueError("tail_lines 不能小于 0")

    guard = _get_guard()
    script_safe = guard.resolve_and_validate(script_path)
    workdir_safe = guard.resolve_and_validate(workdir)

    if not script_safe.exists() or not script_safe.is_file():
        raise FileNotFoundError(f"脚本不存在: {script_safe}")
    if script_safe.suffix.lower() != ".py":
        raise ValueError(f"仅允许运行 .py 文件: {script_safe}")
    if not workdir_safe.exists() or not workdir_safe.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {workdir_safe}")

    python_cmd, probes, mode = _resolve_python_command(
        python_command,
        require_excel_deps=require_excel_deps,
    )
    safe_args = [str(item) for item in (args or [])]
    command = [*python_cmd, str(script_safe), *safe_args]

    started = time.time()
    timed_out = False
    return_code = 1
    stdout = ""
    stderr = ""

    try:
        completed = subprocess.run(
            command,
            cwd=workdir_safe,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
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

    result = {
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(time.time() - started, 3),
        "script": str(script_safe.relative_to(guard.workspace_root)),
        "workdir": str(workdir_safe.relative_to(guard.workspace_root)),
        "command": command,
        "python_command": python_cmd,
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
    }
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
        ),
        ToolDef(
            name="run_python_script",
            description="运行 Python 脚本并返回执行结果（支持自动探测解释器）",
            input_schema={
                "type": "object",
                "properties": {
                    "script_path": {"type": "string", "description": "脚本路径（.py）"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "传递给脚本的位置参数",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "执行工作目录",
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "超时秒数",
                        "default": 300,
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
                "required": ["script_path"],
                "additionalProperties": False,
            },
            func=run_python_script,
        ),
    ]
