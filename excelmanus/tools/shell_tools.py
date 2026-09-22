"""受限 Shell 工具：白名单命令模式的安全 shell 执行。"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
import os
import inspect
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolResult, error_result, from_payload
from excelmanus.security import FileAccessGuard, SecurityViolationError
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools.registry import ToolDef

_ORIGINAL_SUBPROCESS_RUN = subprocess.run


def _run_killable_process(
    command: Any,
    *,
    execution: Any,
    execution_id: str | None,
    **kwargs: Any,
) -> tuple[int, str, str, bool]:
    """运行单个 shell 子进程，并把取消/超时传播到整棵进程树。"""
    from excelmanus.tools.runtime import (
        register_killable_process,
        terminate_killable_processes,
        unregister_killable_process,
    )

    timeout = kwargs.pop("timeout")
    kwargs.pop("check", None)
    if execution is None or subprocess.run is not _ORIGINAL_SUBPROCESS_RUN:
        # 直接调用保持 subprocess.run 的可测试/兼容行为；受 ToolRuntime
        # 管理的调用才需要 Popen 句柄与进程树终止。
        completed = subprocess.run(
            command,
            timeout=timeout,
            check=False,
            **kwargs,
        )
        return (
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
            False,
        )
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if os.name == "nt":
        kwargs["creationflags"] = (
            kwargs.get("creationflags", 0)
            | subprocess.CREATE_NO_WINDOW
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    process = subprocess.Popen(command, **kwargs)
    register_killable_process(execution_id, process)
    cancelled = False
    try:
        if execution is not None and execution.cancel_requested:
            cancelled = True
            terminate_killable_processes(execution_id)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_killable_processes(execution_id)
            stdout, stderr = process.communicate()
            if not stdout:
                stdout = (
                    exc.stdout.decode(errors="replace")
                    if isinstance(exc.stdout, bytes)
                    else exc.stdout
                ) or ""
            if not stderr:
                stderr = (
                    exc.stderr.decode(errors="replace")
                    if isinstance(exc.stderr, bytes)
                    else exc.stderr
                ) or ""
            raise subprocess.TimeoutExpired(
                command,
                timeout,
                output=stdout,
                stderr=stderr,
            )
        if execution is not None and execution.cancel_requested:
            cancelled = True
        return process.returncode, stdout or "", stderr or "", cancelled
    finally:
        unregister_killable_process(execution_id, process)


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


# ── 白名单 / 黑名单 ─────────────────────────────────────

# 允许执行的命令（首个 token 必须在此集合中）
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    # 文件探查
    "ls", "cat", "head", "tail", "wc", "file", "du", "stat",
    # 搜索与文本处理
    "grep", "egrep", "fgrep", "sort", "uniq", "cut",
    "tr", "diff", "comm",
    # 环境与信息
    "which", "echo",
    "uname", "date", "whoami", "pwd",
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

# 可能接受文件路径参数的命令（需检查敏感路径）
_FILE_ARG_COMMANDS: frozenset[str] = frozenset({
    "cat", "head", "tail", "wc", "file", "du", "stat",
    "grep", "egrep", "fgrep", "diff", "comm",
    "sort", "uniq", "cut", "tr", "ls",
})

# 敏感目录名（相对于 HOME）
_SENSITIVE_HOME_DIRS: tuple[str, ...] = (
    ".excelmanus",
)

# 工作区外禁止访问的文件名
_SENSITIVE_FILENAMES: frozenset[str] = frozenset({
    ".env", "config.env", ".secret_key",
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


def _split_command(command: str) -> list[str]:
    """Restricted grammar: on Windows backslashes are path characters.

    This is intentionally not cmd/PowerShell evaluation; shell=False remains.
    Both quote styles group arguments; shell expansions remain prohibited.
    """
    if os.name != "nt":
        return shlex.split(command)
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


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
        tokens = _split_command(stripped)
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

    write_flag = _write_output_flag(tokens)
    if write_flag:
        return False, f"禁止会写盘的参数: {write_flag}"

    return True, "ok"


def _write_output_flag(tokens: list[str]) -> str | None:
    """Detect flags that write to a named output file (e.g. sort -o)."""
    for tok in tokens[1:]:
        if tok in {"-o", "--output", "--out"}:
            return tok
        if tok.startswith("--output=") or tok.startswith("--out="):
            return tok.split("=", 1)[0]
        if tok.startswith("-o") and len(tok) > 2 and not tok.startswith("--"):
            return "-o"
    return None


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


def preflight_command(command: str) -> tuple[bool, str]:
    """在审批或执行前验证命令契约与主机可用性。

    ``ls`` 等 PowerShell 别名不是 ``shell=False`` 下的可执行文件；提前给出
    可行动的提示，避免用户先批准一个必然失败的调用。
    """
    if not isinstance(command, str):
        return False, "command 必须是字符串"
    valid, reason = _validate_command(command)
    if not valid:
        return False, reason
    for chain_text, _ in _split_chain_simple(command.strip()):
        for segment in _split_pipeline(chain_text.strip()):
            tokens = _split_command(segment.strip())
            if not tokens:
                continue
            name = Path(tokens[0]).name
            if shutil.which(tokens[0]) is None:
                if os.name == "nt":
                    return False, (
                        f"当前 Windows 主机没有可执行命令: {name}（PowerShell 别名不能由 shell=False 执行）。"
                        "文件浏览请使用 list_directory/read_text_file；表格处理请使用原生表格工具；计算和文本处理请使用 run_code。"
                    )
                return False, f"当前主机找不到可执行命令: {name}"
    return True, "ok"


def preflight_shell(arguments: dict[str, Any], guard: FileAccessGuard) -> ToolResult | None:
    """共用审批前校验，不运行子进程、不创建审批记录。"""
    try:
        inspect.signature(run_shell).bind(**arguments)
    except TypeError as exc:
        return error_result(f"run_shell 参数不匹配：{exc}", code="INVALID_ARGS", fields={"preflight": True})
    from excelmanus.tools.context import call_has_full_access

    command = arguments.get("command", "")
    unrestricted = call_has_full_access()
    if not isinstance(command, str) or not command.strip():
        valid, reason = False, "命令不能为空"
    elif unrestricted:
        valid, reason = True, "ok"
    else:
        valid, reason = preflight_command(command)
    if valid:
        try:
            timeout = arguments.get("timeout_seconds", 30)
            tail = arguments.get("tail_lines", 80)
            if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 120:
                raise ValueError("timeout_seconds 必须为 1~120 的整数")
            if not isinstance(tail, int) or isinstance(tail, bool) or tail < 0:
                raise ValueError("tail_lines 必须为非负整数")
            workdir = guard.resolve_and_validate(arguments.get("workdir", "."))
            if not workdir.is_dir():
                raise ValueError("workdir 必须是存在的目录")
            if not unrestricted:
                from excelmanus.security.source_isolation import command_touches_product_source

                if command_touches_product_source(command, guard.workspace_root):
                    raise ValueError("禁止用 shell 读取产品源码")
                valid, reason = _check_sensitive_paths(command, workdir, guard.workspace_root)
        except (ValueError, TypeError, OSError, SecurityViolationError) as exc:
            valid, reason = False, str(exc)
    if valid:
        return None
    return error_result(
        f"run_shell 预检失败：{reason}", code="INVALID_ARGS",
        fields={"command": command, "preflight": True,
                "remediation": "目录浏览用 list_directory，文本读取用 read_text_file；确认参数与主机命令后重试。"},
    )


def _check_sensitive_paths(
    command: str, workdir: Path, workspace_root: Path,
) -> tuple[bool, str]:
    """检查命令参数是否引用了敏感路径（~/.excelmanus/ 等）。

    对可能接受文件路径参数的命令，解析其非 flag 参数并校验。
    """
    import os

    home = os.path.expanduser("~")
    sensitive_dirs = [
        os.path.realpath(os.path.join(home, d))
        for d in _SENSITIVE_HOME_DIRS
    ]
    ws_prefix = os.path.realpath(str(workspace_root))

    chain_segments = _split_chain_simple(command.strip())
    for seg_text, _op in chain_segments:
        seg_stripped = seg_text.strip()
        if not seg_stripped:
            continue
        pipe_segments = _split_pipeline(seg_stripped)
        for pipe_seg in pipe_segments:
            try:
                tokens = _split_command(pipe_seg.strip())
            except ValueError:
                continue
            if not tokens:
                continue
            cmd_name = Path(tokens[0]).name
            if cmd_name not in _FILE_ARG_COMMANDS:
                continue
            for token in tokens[1:]:
                if token.startswith("-"):
                    continue
                expanded = os.path.expanduser(token)
                if not os.path.isabs(expanded):
                    expanded = str(workdir / expanded)
                resolved = os.path.realpath(expanded)
                # 检查敏感目录
                for sd in sensitive_dirs:
                    sd_prefix = sd + os.sep
                    if resolved.startswith(sd_prefix) or resolved == sd:
                        return False, f"安全策略禁止访问敏感目录: {token}"
                # 检查工作区外的敏感文件名
                basename = os.path.basename(resolved)
                if basename in _SENSITIVE_FILENAMES:
                    if not (resolved.startswith(ws_prefix + os.sep)
                            or resolved == ws_prefix):
                        return False, f"安全策略禁止访问工作区外的敏感文件: {token}"
                from excelmanus.security.source_isolation import (
                    PRODUCT_SOURCE_FORBIDDEN,
                    is_product_source_path,
                )

                if is_product_source_path(resolved, workspace_root):
                    return False, f"{PRODUCT_SOURCE_FORBIDDEN}: 禁止访问产品源码 {token}"

    return True, "ok"


def run_shell(
    command: str,
    workdir: str = ".",
    timeout_seconds: int = 30,
    tail_lines: int = 80,
) -> ToolResult:
    """执行 shell 命令。

    「询问」模式仅允许白名单内的只读命令。「跳过」模式由宿主签发
    full_access 能力，直接交给系统 shell，可联网、起子进程和写入文件。
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

    from excelmanus.tools.context import call_has_full_access

    unrestricted = call_has_full_access()
    if not isinstance(command, str) or not command.strip():
        return from_payload(
            {"status": "blocked", "reason": "命令不能为空", "command": command},
        )

    from excelmanus.security.source_isolation import (
        PRODUCT_SOURCE_FORBIDDEN,
        command_touches_product_source,
        is_product_source_path,
    )

    if not unrestricted:
        # 「询问」模式保留原有白名单、源码和敏感路径约束。
        valid, reason = preflight_command(command)
        if not valid:
            return from_payload(
                {"status": "blocked", "reason": reason, "command": command},
            )
        if command_touches_product_source(command, guard.workspace_root):
            return from_payload(
                {
                    "status": "blocked",
                    "reason": f"{PRODUCT_SOURCE_FORBIDDEN}: 禁止用 shell 读取产品源码",
                    "command": command,
                },
            )

        path_ok, path_reason = _check_sensitive_paths(
            command, workdir_safe, guard.workspace_root,
        )
        if not path_ok:
            return from_payload(
                {"status": "blocked", "reason": path_reason, "command": command},
            )

    # 构建最小环境
    sandbox_env = _build_shell_env(allow_network=unrestricted)

    # 受限路径继续用 shell=False 的小语法；跳过审批时才交给系统 shell。
    chain_segments = [] if unrestricted else _split_chain_simple(command.strip())

    started = time.time()
    timed_out = False
    cancelled = False
    return_code = 1
    stdout = ""
    stderr = ""
    from excelmanus.tools.runtime import current_execution

    execution = current_execution()
    execution_id = getattr(execution, "execution_id", None)

    try:
        if unrestricted:
            return_code, stdout, stderr, cancelled = _run_killable_process(
                command,
                execution=execution,
                execution_id=execution_id,
                cwd=workdir_safe,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                env=sandbox_env,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                shell=True,
            )
        for chain_idx, (seg_text, chain_op) in enumerate(chain_segments):
            if cancelled:
                break
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
                tokens = _split_command(segments[0].strip())
                return_code, seg_stdout, seg_stderr, cancelled = _run_killable_process(
                    tokens,
                    execution=execution,
                    execution_id=execution_id,
                    cwd=workdir_safe,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    check=False,
                    env=sandbox_env,
                    stdin=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=True,
                    shell=False,
                )
                if cancelled:
                    break
            else:
                # 管道链：用 subprocess.PIPE 连接
                procs: list[subprocess.Popen[str]] = []
                prev_stdout: Any = subprocess.DEVNULL
                for idx, seg in enumerate(segments):
                    tokens = _split_command(seg.strip())
                    stdin_src = prev_stdout if idx > 0 else subprocess.DEVNULL
                    p = subprocess.Popen(
                        tokens,
                        cwd=workdir_safe,
                        stdin=stdin_src,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=sandbox_env,
                        close_fds=True,
                        start_new_session=True,
                        **(
                            {
                                "creationflags": (
                                    subprocess.CREATE_NO_WINDOW
                                    | subprocess.CREATE_NEW_PROCESS_GROUP
                                )
                            }
                            if os.name == "nt"
                            else {}
                        ),
                    )
                    from excelmanus.tools.runtime import register_killable_process
                    register_killable_process(execution_id, p)
                    # 关闭上一个进程的 stdout（已被当前进程接管）
                    if idx > 0 and prev_stdout is not None:
                        prev_stdout.close()
                    prev_stdout = p.stdout
                    procs.append(p)

                # 等待最后一个进程完成
                last = procs[-1]
                try:
                    if execution is not None and execution.cancel_requested:
                        cancelled = True
                        from excelmanus.tools.runtime import terminate_killable_processes
                        terminate_killable_processes(execution_id)
                    out, err = last.communicate(timeout=timeout_seconds)
                    seg_stdout = out or ""
                    seg_stderr = err or ""
                    return_code = last.returncode
                    if execution is not None and execution.cancel_requested:
                        cancelled = True
                finally:
                    # 清理所有进程
                    from excelmanus.tools.runtime import (
                        terminate_killable_processes,
                        unregister_killable_process,
                    )
                    terminate_killable_processes(execution_id)
                    for p in procs:
                        try:
                            p.kill()
                        except OSError:
                            pass
                        p.wait()
                        unregister_killable_process(execution_id, p)

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
        if unrestricted:
            missing = command.strip().split(maxsplit=1)[0]
        else:
            seg0 = _split_pipeline(chain_segments[0][0].strip())
            missing = _split_command(seg0[0].strip())[0]
        return error_result(
            f"命令未找到: {missing}",
            code="NOT_FOUND",
            fields={"command": command},
        )

    if cancelled:
        status = "cancelled"
    elif timed_out:
        status = "timed_out"
    elif return_code == 0:
        status = "success"
    else:
        status = "failed"

    result: dict[str, Any] = {
        "status": status,
        "return_code": return_code,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "duration_seconds": round(time.time() - started, 3),
        "command": command,
        "workdir": str(workdir_safe.relative_to(guard.workspace_root)),
        "stdout_tail": _tail(stdout, tail_lines),
        "stderr_tail": _tail(stderr, tail_lines),
    }
    if cancelled:
        result["ok"] = False
        result["error"] = "CANCELLED"
        result["message"] = "命令执行已取消，子进程及其后代已终止。"
    return from_payload(result)


def _build_shell_env(*, allow_network: bool = False) -> dict[str, str]:
    """构建最小 shell 环境变量。"""
    import os

    env: dict[str, str] = {}
    for key in ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER",
                "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if allow_network:
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        ):
            value = os.environ.get(key)
            if value:
                env[key] = value
    return env


def get_tools() -> list[ToolDef]:
    """返回按当前审批策略执行的 Shell 工具定义。"""
    return [
        ToolDef(
            name="run_shell",
            description=(
                "执行本机 shell 命令。「询问」模式只允许已安装的白名单只读可执行文件；"
                "「跳过」模式自动批准并允许任意系统 shell 命令，包括网络请求和子进程。"
                "受限模式不解释 PowerShell 别名或 cmd 内置命令。"
                f"白名单：{', '.join(sorted(ALLOWED_COMMANDS))}。"
                "目录浏览优先用 list_directory，文本读取用 read_text_file。参数与主机可用性在审批前校验。"
                "「询问」模式仅适合文件探查、搜索和环境信息查询。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "shell 命令（询问模式仅白名单；跳过模式允许任意系统命令）",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "执行工作目录（相对于工作目录）",
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "超时秒数（1~120）",
                        "default": 30,
                        "minimum": 1,
                        "maximum": 120,
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "返回 stdout/stderr 尾部行数",
                        "default": 80,
                        "minimum": 0,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            func=run_shell,
            write_effect="dynamic",
        ),
    ]
