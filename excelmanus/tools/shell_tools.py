"""受限 Shell 工具：白名单命令模式的安全 shell 执行。"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

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


# ── 白名单 / 黑名单 ─────────────────────────────────────

# 允许执行的命令（首个 token 必须在此集合中）
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    # 文件探查
    "ls", "cat", "head", "tail", "wc", "file", "du", "stat",
    # 搜索与文本处理
    "find", "grep", "egrep", "fgrep", "sort", "uniq", "cut",
    "awk", "sed", "tr", "diff", "comm",
    # 环境与信息
    "python", "python3", "pip", "pip3", "which", "echo",
    "env", "printenv", "uname", "date", "whoami", "pwd",
    # 数据工具
    "jq", "csvtool", "xsv",
})

# 硬拦截命令（无论如何不允许）
BLOCKED_COMMANDS: frozenset[str] = frozenset({
    "rm", "rmdir", "mv", "cp",
    "curl", "wget", "ssh", "scp", "rsync", "nc", "ncat",
    "sudo", "su", "doas",
    "chmod", "chown", "chgrp",
    "kill", "killall", "pkill",
    "dd", "mkfs", "mount", "umount",
    "reboot", "shutdown", "halt", "poweroff",
    "apt", "apt-get", "yum", "dnf", "brew", "pacman",
    "pip install", "pip3 install",
    "export", "unset", "source",
    "bash", "sh", "zsh", "fish", "csh", "tcsh", "dash",
})

# 危险 shell 元字符模式（防注入）
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"`"),                   # 反引号命令替换
    re.compile(r"\$\("),               # $() 命令替换
    re.compile(r"\$\{"),               # ${} 变量替换
    re.compile(r";\s*"),               # 分号链式执行
    re.compile(r">>\s*"),              # 追加重定向
    re.compile(r">\s*"),               # 覆盖重定向
    re.compile(r"<\s*"),               # 输入重定向
]


def _tail(text: str, lines: int) -> str:
    """取文本尾部指定行数。"""
    if lines <= 0:
        return ""
    parts = text.splitlines()
    return "\n".join(parts[-lines:])


def _split_pipeline(command: str) -> list[str]:
    """将管道命令按 ``|`` 安全拆分（考虑引号）。

    返回子命令列表；无管道时返回单元素列表。
    """
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    chars = command
    while i < len(chars):
        ch = chars[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "\\" and i + 1 < len(chars) and not in_single:
            current.append(ch)
            current.append(chars[i + 1])
            i += 1
        elif ch == "|" and not in_single and not in_double:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def _validate_single_command(segment: str) -> tuple[bool, str]:
    """校验单条命令（不含管道）的安全性，返回 (通过, 原因)。"""
    stripped = segment.strip()
    if not stripped:
        return False, "命令不能为空"

    # 检测危险元字符
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return False, f"检测到危险字符模式: {pattern.pattern}"

    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        return False, f"命令解析失败: {exc}"
    if not tokens:
        return False, "命令不能为空"

    cmd_name = Path(tokens[0]).name  # 提取命令名（去除路径前缀）

    # 黑名单检查
    if cmd_name in BLOCKED_COMMANDS:
        return False, f"命令被禁止: {cmd_name}"

    # 白名单检查
    if cmd_name not in ALLOWED_COMMANDS:
        return False, f"命令不在白名单中: {cmd_name}"

    # python/pip 子命令限制
    if cmd_name in ("python", "python3") and len(tokens) > 1:
        sub = tokens[1]
        if sub not in ("--version", "-V", "-c"):
            return False, (
                f"run_shell 仅允许 {cmd_name} --version / -V，"
                "执行 Python 代码请使用 run_code 工具"
            )

    if cmd_name in ("pip", "pip3") and len(tokens) > 1:
        sub = tokens[1]
        if sub not in ("list", "show", "freeze", "--version", "-V"):
            return False, (
                f"run_shell 仅允许 {cmd_name} list/show/freeze/--version"
            )

    return True, "ok"


def _split_chain(command: str) -> list[tuple[str, str]]:
    """将命令按 ``&&`` / ``||`` 拆分为链式段（考虑引号和管道）。

    返回 [(segment, operator), ...] 列表。
    第一段的 operator 为 ""，后续段的 operator 为 "&&" 或 "||"。
    """
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    chars = command
    while i < len(chars):
        ch = chars[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "\\" and i + 1 < len(chars) and not in_single:
            current.append(ch)
            current.append(chars[i + 1])
            i += 1
        elif not in_single and not in_double:
            # 检测 && 和 ||
            if ch == "&" and i + 1 < len(chars) and chars[i + 1] == "&":
                parts.append(("".join(current), "" if not parts else parts[-1][1]))
                # 修正：用占位方式记录 operator 归属于下一段
                # 重新组织：parts 记录的是 (segment_text, leading_operator)
                # 第一段 leading_operator = ""
                current = []
                i += 2
                # 为下一段标记 operator
                parts_rewrite = parts
                parts = []
                for seg_text, _ in parts_rewrite:
                    parts.append((seg_text, "" if len(parts) == 0 else "&&"))
                # 当前 current 属于新段，其 operator 是 &&
                # 我们在最后统一处理
                parts.append(("", "&&"))  # 占位符
                continue
            elif ch == "|" and i + 1 < len(chars) and chars[i + 1] == "|":
                parts.append(("".join(current), "" if not parts else parts[-1][1]))
                current = []
                i += 2
                parts_rewrite = parts
                parts = []
                for seg_text, _ in parts_rewrite:
                    parts.append((seg_text, "" if len(parts) == 0 else "||"))
                parts.append(("", "||"))  # 占位符
                continue
            else:
                current.append(ch)
        else:
            current.append(ch)
        i += 1

    # 处理最后一个 current
    if parts and parts[-1][0] == "":
        # 替换最后一个 placeholder
        op = parts[-1][1]
        parts[-1] = ("".join(current), op)
    else:
        parts.append(("".join(current), ""))

    return parts


def _split_chain_simple(command: str) -> list[tuple[str, str]]:
    """将命令按 ``&&`` / ``||`` 拆分（考虑引号）。

    返回 [(segment, operator), ...] 列表。
    operator 是该段**前面**的运算符，第一段为 ""。
    """
    result: list[tuple[str, str]] = []
    current: list[str] = []
    pending_op = ""  # 下一段的前导运算符
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "\\" and i + 1 < len(command) and not in_single:
            current.append(ch)
            current.append(command[i + 1])
            i += 2
            continue
        elif not in_single and not in_double:
            if ch == "&" and i + 1 < len(command) and command[i + 1] == "&":
                result.append(("".join(current), pending_op))
                current = []
                pending_op = "&&"
                i += 2
                continue
            elif ch == "|" and i + 1 < len(command) and command[i + 1] == "|":
                result.append(("".join(current), pending_op))
                current = []
                pending_op = "||"
                i += 2
                continue
            else:
                current.append(ch)
        else:
            current.append(ch)
        i += 1
    result.append(("".join(current), pending_op))
    return result


def _validate_command(command: str) -> tuple[bool, str]:
    """校验命令安全性（支持管道和链式运算符 && / ||），返回 (通过, 原因)。"""
    stripped = command.strip()
    if not stripped:
        return False, "命令不能为空"

    # 按 && / || 拆分为链式段
    chain_segments = _split_chain_simple(stripped)

    for seg_text, _op in chain_segments:
        seg_stripped = seg_text.strip()
        if not seg_stripped:
            return False, "链式命令中存在空段"
        # 每段按管道拆分并逐段验证
        pipe_segments = _split_pipeline(seg_stripped)
        for pipe_seg in pipe_segments:
            valid, reason = _validate_single_command(pipe_seg)
            if not valid:
                return False, reason

    return True, "ok"


def run_shell(
    command: str,
    workdir: str = ".",
    timeout_seconds: int = 30,
    tail_lines: int = 80,
) -> str:
    """执行受限 shell 命令（仅允许白名单内命令）。

    适用于文件探查、搜索、环境信息查询等只读场景。
    写操作和网络请求被严格禁止。
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    if timeout_seconds > 120:
        raise ValueError("timeout_seconds 不能超过 120 秒")
    if tail_lines < 0:
        raise ValueError("tail_lines 不能小于 0")

    guard = _get_guard()
    workdir_safe = guard.resolve_and_validate(workdir)
    if not workdir_safe.exists() or not workdir_safe.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {workdir_safe}")

    # 安全校验
    valid, reason = _validate_command(command)
    if not valid:
        return json.dumps(
            {"status": "blocked", "reason": reason, "command": command},
            ensure_ascii=False,
            indent=2,
        )

    # 构建最小环境
    sandbox_env = _build_shell_env()

    # 按 && / || 拆分为链式段
    chain_segments = _split_chain_simple(command.strip())

    started = time.time()
    timed_out = False
    return_code = 1
    stdout = ""
    stderr = ""

    try:
        for chain_idx, (seg_text, chain_op) in enumerate(chain_segments):
            # 链式运算符语义
            if chain_idx > 0:
                if chain_op == "&&" and return_code != 0:
                    break  # 前一段失败，停止执行
                if chain_op == "||" and return_code == 0:
                    break  # 前一段成功，停止执行

            seg_stripped = seg_text.strip()
            segments = _split_pipeline(seg_stripped)

            seg_stdout = ""
            seg_stderr = ""

            if len(segments) == 1:
                # 单命令，直接执行
                tokens = shlex.split(segments[0].strip())
                completed = subprocess.run(
                    tokens,
                    cwd=workdir_safe,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    env=sandbox_env,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                    shell=False,
                )
                return_code = completed.returncode
                seg_stdout = completed.stdout or ""
                seg_stderr = completed.stderr or ""
            else:
                # 管道链：用 subprocess.PIPE 连接
                procs: list[subprocess.Popen[str]] = []
                prev_stdout: Any = subprocess.DEVNULL
                for idx, seg in enumerate(segments):
                    tokens = shlex.split(seg.strip())
                    stdin_src = prev_stdout if idx > 0 else subprocess.DEVNULL
                    p = subprocess.Popen(
                        tokens,
                        cwd=workdir_safe,
                        stdin=stdin_src,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=sandbox_env,
                        close_fds=True,
                        start_new_session=True,
                    )
                    # 关闭上一个进程的 stdout（已被当前进程接管）
                    if idx > 0 and prev_stdout is not None:
                        prev_stdout.close()
                    prev_stdout = p.stdout
                    procs.append(p)

                # 等待最后一个进程完成
                last = procs[-1]
                try:
                    out, err = last.communicate(timeout=timeout_seconds)
                    seg_stdout = out or ""
                    seg_stderr = err or ""
                    return_code = last.returncode
                finally:
                    # 清理所有进程
                    for p in procs:
                        try:
                            p.kill()
                        except OSError:
                            pass
                        p.wait()

            # 累积输出
            if seg_stdout:
                stdout += ("\n" if stdout else "") + seg_stdout
            if seg_stderr:
                stderr += ("\n" if stderr else "") + seg_stderr

    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = 124
        stdout += "\n" + (
            (
                exc.stdout.decode(errors="replace")
                if isinstance(exc.stdout, bytes)
                else exc.stdout
            ) or ""
        )
        stderr += "\n" + (
            (
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr
            ) or ""
        )
    except FileNotFoundError:
        seg0 = _split_pipeline(chain_segments[0][0].strip())
        return json.dumps(
            {
                "status": "error",
                "error": f"命令未找到: {shlex.split(seg0[0].strip())[0]}",
                "command": command,
            },
            ensure_ascii=False,
            indent=2,
        )

    if timed_out:
        status = "timed_out"
    elif return_code == 0:
        status = "success"
    else:
        status = "failed"

    result: dict[str, Any] = {
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(time.time() - started, 3),
        "command": command,
        "workdir": str(workdir_safe.relative_to(guard.workspace_root)),
        "stdout_tail": _tail(stdout, tail_lines),
        "stderr_tail": _tail(stderr, tail_lines),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _build_shell_env() -> dict[str, str]:
    """构建最小 shell 环境变量。"""
    import os

    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER",
                "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def get_tools() -> list[ToolDef]:
    """返回受限 Shell 工具定义。"""
    return [
        ToolDef(
            name="run_shell",
            description=(
                "执行受限 shell 命令（仅允许白名单内的只读命令，"
                "如 ls/cat/head/tail/grep/find/wc 等）"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令（仅允许白名单命令）",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "执行工作目录",
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "超时秒数（最大 120）",
                        "default": 30,
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "返回日志尾部行数",
                        "default": 80,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            func=run_shell,
            write_effect="dynamic",
        ),
    ]
