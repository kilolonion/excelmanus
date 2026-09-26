"""代码执行工具：写入文本文件与运行 Python 代码。"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from excelmanus.engine_core.error_payload import (
    RUN_CODE_FAILED,
    RUN_CODE_PUBLISH_FAILED,
    RUN_CODE_TIMEOUT,
    VERSION_CONFLICT,
)
from excelmanus.engine_core.tool_result import (
    ToolError,
    ToolResult,
    ToolUiMeta,
    error_result,
    ok_result,
)
from excelmanus.security import FileAccessGuard
from excelmanus.prompt.canonical import TOOL_DESCRIPTIONS
from excelmanus.tools.context import bind_workspace, require_guard
from excelmanus.tools.registry import ToolDef


def _get_guard() -> FileAccessGuard:
    return require_guard()


def init_guard(workspace_root: str) -> None:
    bind_workspace(workspace_root)


import contextvars as _contextvars

_current_sandbox_env: _contextvars.ContextVar[Any] = _contextvars.ContextVar(
    "_current_sandbox_env", default=None,
)

_current_readonly_exec: _contextvars.ContextVar[bool] = _contextvars.ContextVar(
    "_current_readonly_exec", default=False,
)


def set_readonly_exec(flag: bool) -> _contextvars.Token:
    """标记当前异步上下文为只读执行（沙盒 read-only 档）。

    只读执行仍允许 run_code 计算，但发布步骤会丢弃全部 pending 写入。
    """
    return _current_readonly_exec.set(bool(flag))


def _is_readonly_exec() -> bool:
    """返回当前执行是否处于只读档。"""
    return bool(_current_readonly_exec.get(False))


def set_sandbox_env(env: Any) -> _contextvars.Token:
    """为当前异步上下文设置每会话的 SandboxEnv。

    返回可用于恢复 contextvar 的 token。
    """
    return _current_sandbox_env.set(env)


def _get_active_sandbox_env() -> Any:
    """返回当前生效的 SandboxEnv，若无则返回 None。"""
    return _current_sandbox_env.get(None)


def _apply_code_mode_env(
    env: dict[str, str],
    *,
    workspace_root: Path,
) -> None:
    """把 Code Mode 文件桥路径注入子进程环境。

    session 存在时 ``prepare()`` 失败必须显式抛出：静默返回会让脚本在
    没有 SDK 环境的情况下继续执行，最后以谜之 NO_BRIDGE 收场。
    """
    try:
        from excelmanus.code_mode import get_code_mode_session
    except Exception:
        return
    session = get_code_mode_session()
    if session is None:
        return
    session.prepare()
    bridge = Path(session.bridge_dir)
    sdk = Path(session.sdk_path)
    env["EXCELMANUS_CODE_MODE_BRIDGE"] = str(bridge)
    env["EXCELMANUS_CODE_MODE_SDK"] = str(sdk)
    env["EXCELMANUS_CODE_MODE_ROOT_CALL_ID"] = session.root_call_id
    env["EXCELMANUS_CODE_MODE_TIMEOUT"] = str(int(session.call_timeout))
    if session.deadline_wall is not None:
        env["EXCELMANUS_CODE_MODE_DEADLINE"] = str(session.deadline_wall)


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
    # 缩短标记不得含 truncat/截断 等 ToolErrorKind 分类关键词，否则被缩短的
    # 错误一律被误判为 CONTEXT_OVERFLOW（如解释器探测失败）。
    return text[:limit] + "…"


# ── 截断代码检测 ─────────────────────────────────────────

# 匹配独立行上的 Ellipsis（`...`），排除字符串内和注释
_ELLIPSIS_LINE_RE = re.compile(r"^\s*\.\.\.\s*$")


def _detect_truncated_code(code: str) -> list[str]:
    """检测 LLM 生成的代码是否包含截断/占位符模式。

    返回警告消息列表（空列表表示未检测到问题）。
    """
    warnings: list[str] = []
    lines = code.splitlines()
    if not lines:
        return warnings

    ellipsis_positions: list[int] = []
    for i, line in enumerate(lines, 1):
        if _ELLIPSIS_LINE_RE.match(line):
            ellipsis_positions.append(i)

    if ellipsis_positions:
        # 过滤掉在 class/function 定义体中作为 pass 替代的合法用法
        # 合法：`def foo(): ...` 或 `class Foo: ...`（单行）
        # 可疑：独立行 `...` 出现在较长的代码块中
        suspicious = []
        for pos in ellipsis_positions:
            idx = pos - 1
            # 检查上一行是否是 def/class 声明（合法 stub 用法）
            if idx > 0:
                prev = lines[idx - 1].strip()
                if prev.endswith(":") and (prev.startswith("def ") or prev.startswith("class ") or prev.startswith("async def ")):
                    continue
            suspicious.append(pos)

        if suspicious:
            positions_str = ", ".join(str(p) for p in suspicious[:5])
            warnings.append(
                f"⚠️ 代码中检测到可疑的 Ellipsis (`...`) 占位符（第 {positions_str} 行）。"
                "这通常表示代码未完整生成。`...` 在 Python 中是合法的 no-op 表达式，"
                "会导致代码'成功'执行但跳过实际逻辑。请确保生成完整代码，不要用 `...` 省略。"
            )

    # 检测代码是否以不完整的语句结尾
    stripped_last = lines[-1].rstrip()
    if stripped_last.endswith((",", "(", "[", "{", "+", "\\")):
        warnings.append(
            f"⚠️ 代码最后一行以不完整的语句结尾（'{stripped_last[-20:]}'）。"
            "代码可能在生成过程中被截断，请重新生成完整代码。"
        )

    return warnings


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


# 探针首轮超时（秒）。Windows 冷启动下首次 import pandas——杀软首扫
# 随包运行时、随包不带 .pyc 需全量源码编译——可能超过首轮预算，
# 超时后按 _PROBE_TIMEOUT_RETRY_SECONDS 宽限再试一次。
_PROBE_TIMEOUT_SECONDS = 20
_PROBE_TIMEOUT_RETRY_SECONDS = 90


def _resolve_candidate_executable(command: list[str]) -> str | None:
    """返回候选命令实际启动的解释器路径（规范化），无法解析时返回 None。

    桌面包会把随包 python 目录前置进 PATH：`python` 与
    EXCELMANUS_RUN_PYTHON 指向同一二进制。按真实路径去重可避免对同一
    解释器重复探测（每次探测超时都是数十秒级等待）。
    """
    executable = command[0]
    try:
        if _is_path_like(executable):
            resolved = str(Path(executable).expanduser().resolve())
        else:
            resolved = shutil.which(executable)
            if resolved:
                # ``shutil.which`` may return a symlink (the desktop runtime
                # commonly exposes python/python3 as aliases).  Normalize
                # both explicit and PATH candidates to the same real binary
                # before de-duplicating probes.
                resolved = str(Path(resolved).expanduser().resolve())
    except OSError:
        return None
    if not resolved:
        return None
    return os.path.normcase(os.path.normpath(resolved))


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

    # 与真实执行一致的隔离标志（-B -I -X utf8）：-I 忽略 PYTHONPATH/
    # PYTHONHOME/用户 site，否则宿主机环境污染会让探针误杀可用解释器。
    isolated_command, _ = _ensure_isolated_python(command)
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
            probe_command = [*isolated_command, "-c", probe_code]
        else:
            probe_command = [*isolated_command, "-c", "import pandas,openpyxl"]
    else:
        probe_command = [*isolated_command, "-c", "import sys; print(sys.version_info[0])"]

    completed: subprocess.CompletedProcess[str] | None = None
    error_detail = ""
    error_status = "error"
    for timeout in (_PROBE_TIMEOUT_SECONDS, _PROBE_TIMEOUT_RETRY_SECONDS):
        try:
            completed = subprocess.run(
                probe_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            break
        except subprocess.TimeoutExpired as exc:
            # 超时（多为冷启动）时进入下一轮，用更长预算重试一次
            error_status = "timeout"
            error_detail = f"探测超时（{timeout} 秒）：{_shorten(str(exc))}"
        except Exception as exc:  # noqa: BLE001
            error_status = "error"
            error_detail = _shorten(str(exc))
            break
    if completed is None:
        return _InterpreterProbe(
            command=command,
            status=error_status,
            detail=error_detail or "探测进程未能完成",
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


# 解释器探测进程级缓存：run_code 每次调用都重探既慢又留出抖动窗口
# （Windows 冷启动 import pandas 可能超过探测预算）。成功条目常驻，
# 失败条目 60s 内复用，防止每次调用都全候选扫描一遍。
_ResolveKey = tuple[str, bool, str, str]
_RESOLVE_CACHE: dict[_ResolveKey, tuple[list[str] | None, str, float]] = {}
_RESOLVE_FAIL_TTL_SECONDS = 60.0
# 每个配置独立串行化缓存读写与探测，防止预热和前台同时探测后互相覆盖。
# 创建锁时只短暂持有全局锁，不让其他解释器等待一次缓慢的探测。
_RESOLVE_LOCKS: dict[_ResolveKey, Any] = {}
_RESOLVE_LOCKS_GUARD = threading.Lock()


def _resolve_python_command(
    python_command: str, *,
    require_excel_deps: bool,
    sandbox_tier: str = "RED",
    _cache_failures: bool = True,
) -> tuple[list[str], list[_InterpreterProbe], str]:
    import time as _time

    cache_key = (
        str(python_command or "auto"),
        bool(require_excel_deps),
        str(sandbox_tier or "RED"),
        str(os.environ.get("EXCELMANUS_RUN_PYTHON") or ""),
    )
    with _RESOLVE_LOCKS_GUARD:
        resolve_lock = _RESOLVE_LOCKS.setdefault(cache_key, threading.Lock())
    with resolve_lock:
        cached = _RESOLVE_CACHE.get(cache_key)
        if cached is not None:
            command, mode_or_err, expiry = cached
            if command is not None:
                return list(command), [], mode_or_err
            if _time.monotonic() < expiry:
                raise RuntimeError(f"{mode_or_err}（60s 内已探测失败，未重试）")
            _RESOLVE_CACHE.pop(cache_key, None)
        try:
            resolved = _resolve_python_command_uncached(
                python_command,
                require_excel_deps=require_excel_deps,
                sandbox_tier=sandbox_tier,
            )
        except RuntimeError as exc:
            if _cache_failures:
                _RESOLVE_CACHE[cache_key] = (
                    None, str(exc), _time.monotonic() + _RESOLVE_FAIL_TTL_SECONDS,
                )
            raise
        _RESOLVE_CACHE[cache_key] = (list(resolved[0]), resolved[2], 0.0)
        return resolved


def _resolve_python_command_uncached(
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
    if sys.executable and not getattr(sys, "frozen", False):
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
    seen_targets: set[tuple[str, tuple[str, ...]]] = set()
    for command in candidates:
        key = tuple(item.lower() for item in command)
        if key in seen:
            continue
        seen.add(key)
        resolved = _resolve_candidate_executable(command)
        if resolved is not None:
            target = (resolved, tuple(item.lower() for item in command[1:]))
            if target in seen_targets:
                continue
            seen_targets.add(target)
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
    timeout_hint = ""
    if any(probe.status == "timeout" for probe in probes):
        timeout_hint = (
            "提示：探测超时可能与运行时首次加载或安全软件扫描有关，"
            "可等待 60 秒后重试。\n"
        )
    raise RuntimeError(
        "自动探测 Python 解释器失败，未找到可用环境。\n"
        f"尝试记录：\n{details}\n"
        f"{timeout_hint}"
        "可选方案：\n"
        "1) 使用 python_command 显式指定解释器\n"
        "2) 设置环境变量 EXCELMANUS_RUN_PYTHON\n"
        "3) 在目标解释器中安装依赖（pandas/openpyxl）"
    )


def warmup_interpreter() -> None:
    """后台预热解释器解析：填充 ``_RESOLVE_CACHE`` 并加热 OS 文件缓存。

    随包运行时不带 .pyc，首次 import pandas 在杀软扫描下可能超过常规
    探测预算；分别预热各安全等级，保留其各自的 Import Guard 校验。
    预热不缓存失败，也不清理其他请求的缓存，让首次真实调用可重新探测。
    某个等级失败不阻止其他等级预热，最终由调用方记录失败信息。
    """
    failures: list[str] = []
    # 普通审批下的文件写入使用 YELLOW，优先预热，让启动后的首个写入
    # 尽早共享同一探测；其他等级仍单独验证，不能复用 RED 的宽松结果。
    for tier in ("YELLOW", "GREEN", "RED"):
        try:
            _resolve_python_command(
                "auto", require_excel_deps=True, sandbox_tier=tier,
                _cache_failures=False,
            )
        except RuntimeError as exc:
            failures.append(f"{tier}: {exc}")
    if failures:
        raise RuntimeError("解释器预热失败：\n" + "\n".join(failures))


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
    # 限制 BLAS 线程数，避免在 RLIMIT_NPROC 受限的环境中崩溃
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
}


def _build_sandbox_env(*, allow_network: bool = False) -> tuple[dict[str, str], list[str]]:
    """构建最小环境变量白名单。"""
    sandbox_env: dict[str, str] = {}
    warnings: list[str] = []
    for key in _SANDBOX_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            sandbox_env[key] = value
    if allow_network:
        for key in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "all_proxy", "no_proxy",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        ):
            value = os.environ.get(key)
            if value:
                sandbox_env[key] = value

    if os.name == "nt" and "SYSTEMROOT" not in sandbox_env:
        warnings.append("缺少 SYSTEMROOT，Windows 子进程可能无法启动。")

    sandbox_env["PYTHONNOUSERSITE"] = "1"
    sandbox_env["PYTHONDONTWRITEBYTECODE"] = "1"
    sandbox_env["PYTHONIOENCODING"] = "utf-8"
    sandbox_env["PYTHONUTF8"] = "1"
    # TMPDIR/TMP/TEMP 由 _execute_script 注入工作区本地目录
    return sandbox_env, warnings


def _ensure_isolated_python(command: list[str]) -> tuple[list[str], bool]:
    """确保 Python 调用启用 -I 隔离，并打开 UTF-8 模式（-I 会忽略 PYTHONUTF8）。"""
    out = list(command)
    if "-B" not in out[1:]:
        out.append("-B")
    if "-I" not in out[1:]:
        out.append("-I")
    has_utf8 = False
    for index, item in enumerate(out):
        if item == "-X" and index + 1 < len(out) and str(out[index + 1]).startswith("utf8"):
            has_utf8 = True
            break
        text = str(item)
        if text in {"utf8", "-Xutf8"} or text.startswith("-Xutf8"):
            has_utf8 = True
            break
    if not has_utf8:
        out.extend(["-X", "utf8"])
    return out, True


def _build_unix_limits_preexec(
    timeout_seconds: int,
    *, allow_subprocess: bool = False,
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
    ]
    # NPROC counts every process owned by the OS user, not just this runner.
    # On a desktop that user commonly already owns >32 processes. Full-access
    # calls explicitly permit subprocesses, so inherit the host process limit
    # instead of making all authorized forks fail with EAGAIN.
    if not allow_subprocess:
        limit_plan.append(("RLIMIT_NPROC", 32))
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


def _generate_text_diff(
    old_text: str,
    new_text: str,
    file_path: str,
    *,
    max_lines: int = 300,
) -> dict | None:
    """生成统一 diff（unified diff），返回用于 _text_diff 事件的字典。

    Returns:
        ``None`` if no changes, otherwise dict with keys:
        ``file_path``, ``hunks`` (list of diff line strings),
        ``additions``, ``deletions``.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        lineterm="",
    ))
    if not diff_lines:
        return None

    additions = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    # 截断过长 diff
    truncated = len(diff_lines) > max_lines
    if truncated:
        diff_lines = diff_lines[:max_lines]

    return {
        "file_path": file_path,
        "hunks": [l.rstrip("\n\r") for l in diff_lines],
        "additions": additions,
        "deletions": deletions,
        "truncated": truncated,
    }


def _publish_failures(published: Any) -> list[dict[str, Any]]:
    """返回 published 中未 committed 的字典项（原集合不改）。"""
    if not isinstance(published, list):
        return []
    return [
        item
        for item in published
        if isinstance(item, dict) and item.get("status") != "committed"
    ]


def _pack_run_code_result(payload: dict[str, Any]) -> ToolResult:
    payload.pop("cow_mapping", None)
    payload.pop("cow_hint", None)
    ui = ToolUiMeta()
    published = payload.get("published")
    publish_failures = _publish_failures(published)
    if isinstance(published, list):
        for item in published:
            if not isinstance(item, dict):
                continue
            if item.get("status") != "committed":
                continue
            path = str(item.get("path") or "").strip()
            if path and path not in ui.files:
                ui.files.append(path)
            version = item.get("content_version")
            if isinstance(version, str) and version and not ui.content_version:
                ui.content_version = version
    model_text = json.dumps(payload, ensure_ascii=False, indent=2)
    status = str(payload.get("status") or "")
    status_l = status.lower()

    def _fail(code: str, message: str) -> ToolResult:
        return ToolResult(
            success=False,
            model_text=model_text,
            value=payload,
            ui_meta=ui,
            error=ToolError(code=code, message=message, fields=payload),
        )

    if status_l == "cancelled":
        return _fail("CANCELLED", "代码执行已取消，子进程及其待发布写入已终止。")
    if status_l in {"timed_out", "timeout"}:
        message = str(
            payload.get("recovery_hint") or payload.get("stderr_tail") or status or "timed_out"
        )
        return _fail(RUN_CODE_TIMEOUT, message)
    if status_l in {"failed", "error", "fail"}:
        message = str(payload.get("recovery_hint") or payload.get("stderr_tail") or status)
        return _fail(RUN_CODE_FAILED, message)
    if publish_failures:
        conflict = any(
            str(item.get("error") or item.get("status") or "") == VERSION_CONFLICT
            for item in publish_failures
        )
        first = publish_failures[0]
        message = str(
            first.get("message") or first.get("error") or first.get("status") or "publish failed"
        )
        return _fail(VERSION_CONFLICT if conflict else RUN_CODE_PUBLISH_FAILED, message)
    return ok_result(payload, ui_meta=ui, model_text=model_text)


def _text_write_conflict_result(
    exc: Any,
    *,
    tool_name: str,
    rel_path: str,
    safe_path: Path,
) -> ToolResult:
    """VERSION_CONFLICT（已有文件缺 expected_version）的机器可读下一步。

    write_text_file / edit_text_file 的版本参数来自最近一次读写回执；把回执里的
    content_version 直接填成 expected_version 就能继续，不必额外猜参数名。
    """
    from excelmanus.workbook_commit import content_version_of_file

    fields = dict(getattr(exc, "fields", None) or {})
    path = str(fields.get("path") or rel_path or safe_path.name)
    current = str(fields.get("content_version") or "")
    if not current:
        try:
            current = str(content_version_of_file(safe_path) or "")
        except OSError:
            current = ""
    message = (
        f"{path} 已存在，缺少 expected_version：把你上一次写入回执里的 content_version "
        f"原样传成 expected_version（见 next_call.arguments.expected_version）再调用 {tool_name}。"
    )
    if current:
        fields.update({
            "path": path,
            "content_version": current,
            "expected_version": current,
            "needed_args": ["expected_version"],
            "next_call": {
                "tool": tool_name,
                "arguments": {"file_path": path, "expected_version": current},
            },
            "next_call_note": "其余参数沿用上一次调用；只补 expected_version。",
        })
        remediation = (
            f"把上一次写 {path} 的回执里的 content_version 作为 expected_version 传进 {tool_name}"
            "（见 next_call）；无法确认文件仍是那次写入的内容时，先 read_text_file 重新观察再改。"
        )
    else:
        fields.update({
            "path": path,
            "needed_args": ["expected_version"],
            "next_call": {"tool": "read_text_file", "arguments": {"file_path": path}},
            "next_call_note": (
                f"先用 read_text_file 取回执里的 content_version，再作为 expected_version "
                f"传进 {tool_name}。"
            ),
        })
        message += " 当前版本读取失败，next_call 先用 read_text_file 取回执。"
        remediation = (
            "先 read_text_file 重新观察文件并拿到 content_version，再把它作为 expected_version "
            "重试；不要不带版本直接覆盖。"
        )
    return error_result(message, code=VERSION_CONFLICT, fields=fields, remediation=remediation)


def write_text_file(
    file_path: str,
    content: str,
    overwrite: bool = True,
    encoding: str = "utf-8",
    expected_version: str | None = None,
) -> ToolResult:
    """写入文本文件（默认覆盖）。已有文件必须提供 expected_version 或本轮 peek_seen。"""
    from excelmanus.excel_extensions import SPREADSHEET_WRITE_EXTENSIONS
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import (
        CommitError,
        commit_bytes,
        remember_content_version,
        resolve_expected_version,
    )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)
    if safe_path.suffix.lower() in SPREADSHEET_WRITE_EXTENSIONS:
        return error_result(
            "表格文件禁止当纯文本覆盖，请使用 workbook 提交路径",
            code="PATH_INVALID",
            fields={"file_path": file_path},
        )

    existed_before = safe_path.is_file()
    if existed_before and not overwrite:
        return error_result(
            f"文件已存在且 overwrite=false: {safe_path.name}",
            code="FILE_EXISTS",
        )

    rel_path = str(safe_path.relative_to(guard.workspace_root)).replace("\\", "/")
    try:
        seen = resolve_expected_version(rel_path, expected_version, exists=existed_before, abs_path=safe_path)
    except CommitError as exc:
        if str(getattr(exc, "code", "")) == VERSION_CONFLICT and not str(expected_version or "").strip():
            return _text_write_conflict_result(
                exc, tool_name="write_text_file", rel_path=rel_path, safe_path=safe_path,
            )
        return commit_error_result(exc)

    old_text = ""
    if existed_before:
        try:
            old_text = safe_path.read_text(encoding=encoding)
        except Exception:
            pass

    try:
        from excelmanus.tools.context import operation_id_for
        cr = commit_bytes(
            guard=guard,
            file_path=rel_path,
            data=content.encode(encoding, errors="strict"),
            expected_version=seen,
            operation_id=operation_id_for(rel_path),
        )
    except CommitError as exc:
        return commit_error_result(exc)
    remember_content_version(rel_path, cr.content_version)

    result: dict[str, Any] = {
        "status": "success",
        "file_path": cr.path,
        "bytes": cr.bytes_written,
        "encoding": encoding,
        "overwritten": existed_before,
        "content_version": cr.content_version,
    }

    diff_data = _generate_text_diff(old_text, content, rel_path)
    ui = ToolUiMeta(files=[cr.path], content_version=cr.content_version)
    if diff_data is not None:
        ui.text_diff = diff_data
    return ok_result(result, ui_meta=ui)


def edit_text_file(
    file_path: str,
    old_string: str,
    new_string: str,
    encoding: str = "utf-8",
    replace_all: bool = False,
    expected_version: str | None = None,
) -> ToolResult:
    """精准编辑文本文件：查找 old_string 并替换为 new_string。

    类似于 IDE 的查找替换功能。支持单次替换或全部替换。
    """
    from excelmanus.excel_extensions import SPREADSHEET_WRITE_EXTENSIONS
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import (
        CommitError,
        commit_bytes,
        remember_content_version,
        resolve_expected_version,
    )

    guard = _get_guard()
    safe_path = guard.resolve_and_validate(file_path)

    if not safe_path.is_file():
        return error_result(f"文件不存在: {file_path}", code="PATH_INVALID")
    if safe_path.suffix.lower() in SPREADSHEET_WRITE_EXTENSIONS:
        return error_result(
            "表格文件禁止当纯文本覆盖，请使用 workbook 提交路径",
            code="PATH_INVALID",
            fields={"file_path": file_path},
        )

    rel_path = str(safe_path.relative_to(guard.workspace_root)).replace("\\", "/")
    try:
        seen = resolve_expected_version(rel_path, expected_version, exists=True, abs_path=safe_path)
    except CommitError as exc:
        if str(getattr(exc, "code", "")) == VERSION_CONFLICT and not str(expected_version or "").strip():
            return _text_write_conflict_result(
                exc, tool_name="edit_text_file", rel_path=rel_path, safe_path=safe_path,
            )
        return commit_error_result(exc)

    try:
        old_text = safe_path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        return error_result(f"无法以 {encoding} 编码读取文件", code="DECODE_ERROR")

    if old_string not in old_text:
        return error_result(
            "old_string 未在文件中找到，请检查内容是否精确匹配",
            code="NOT_FOUND",
        )

    if old_string == new_string:
        return error_result("old_string 与 new_string 相同，无需修改", code="NOOP")

    if not replace_all and old_text.count(old_string) > 1:
        return error_result(
            f"old_string 在文件中出现 {old_text.count(old_string)} 次，"
            "请提供更多上下文使其唯一，或设置 replace_all=true",
            code="AMBIGUOUS_MATCH",
        )

    if replace_all:
        new_text = old_text.replace(old_string, new_string)
        match_count = old_text.count(old_string)
    else:
        new_text = old_text.replace(old_string, new_string, 1)
        match_count = 1

    try:
        from excelmanus.tools.context import operation_id_for
        cr = commit_bytes(
            guard=guard,
            file_path=rel_path,
            data=new_text.encode(encoding, errors="strict"),
            expected_version=seen,
            operation_id=operation_id_for(rel_path),
        )
    except CommitError as exc:
        return commit_error_result(exc)
    remember_content_version(rel_path, cr.content_version)

    result: dict[str, Any] = {
        "status": "success",
        "file_path": cr.path,
        "replacements": match_count,
        "bytes": cr.bytes_written,
        "content_version": cr.content_version,
    }

    diff_data = _generate_text_diff(old_text, new_text, rel_path)
    ui = ToolUiMeta(files=[cr.path], content_version=cr.content_version)
    if diff_data is not None:
        ui.text_diff = diff_data
    return ok_result(result, ui_meta=ui)


def run_code(
    code: str | None = None,
    script_path: str | None = None,
    args: list[str] | None = None,
    workdir: str = ".",
    timeout_seconds: int = 900,
    python_command: str = "auto",
    tail_lines: int = 80,
    require_excel_deps: bool = False,
    dependency_profile: str | None = None,
    stdout_file: str | None = None,
    stderr_file: str | None = None,
    sandbox_tier: str = "RED",
) -> ToolResult:
    """执行 Python 代码。支持内联代码片段或磁盘脚本文件。

    两种模式（互斥，必须且只能指定其一）：
    - **内联模式**：传入 ``code`` 参数，内部写临时文件执行后清理。
    - **文件模式**：传入 ``script_path`` 参数，直接执行已有 ``.py`` 文件。

    **路径处理指南**：
    - 代码在本机子进程中执行，「询问」使用受限沙盒，「跳过」允许网络和子进程。
    - 工作目录由 ``workdir`` 参数决定（默认为当前目录）。
    - 可通过 ``os.environ.get("EXCELMANUS_WORKSPACE_ROOT")`` 获取沙盒工作区根目录。
    - 可通过 ``os.environ.get("EXCELMANUS_WORKDIR")`` 获取当前工作目录。
    - 推荐使用绝对路径或相对于工作区的相对路径（如 ``./outputs/file.txt``）。
    """
    # ``dependency_profile`` separates pure Python/basic file work from the
    # optional spreadsheet runtime.  ``require_excel_deps`` remains a wire
    # compatibility alias for older callers.
    profile = str(dependency_profile or "").strip().lower()
    if profile:
        if profile not in {"pure_python", "basic_files", "excel"}:
            raise ValueError("dependency_profile 必须是 pure_python、basic_files 或 excel")
        require_excel_deps = profile == "excel"
    else:
        profile = "excel" if require_excel_deps else "basic_files"

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
    if timeout_seconds > 1800:
        raise ValueError("timeout_seconds 最大 1800 秒")
    if tail_lines < 0:
        raise ValueError("tail_lines 不能小于 0")

    from excelmanus.tools.context import call_has_full_access

    full_access = call_has_full_access()
    if full_access:
        # 「跳过」是宿主签发的调用能力，不信任模型回显的 sandbox_tier。
        sandbox_tier = "RED"

    guard = _get_guard()
    workdir_safe = guard.resolve_and_validate(workdir)
    if not workdir_safe.exists() or not workdir_safe.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {workdir_safe}")

    # ── 确定脚本路径 ──
    inline_mode = code is not None
    temp_script: Path | None = None
    truncation_warnings: list[str] = []
    if inline_mode:
        assert code is not None
        truncation_warnings = _detect_truncated_code(code)
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

    from dataclasses import replace as _dc_replace

    try:
        result = _execute_script(
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
            allow_network=full_access,
            allow_external_files=full_access,
        )
    finally:
        if temp_script is not None and temp_script.exists():
            try:
                temp_script.unlink()
            except OSError:
                pass

    payload = dict(result.value) if isinstance(result.value, dict) else {}
    payload["dependency_profile"] = profile
    if truncation_warnings:
        payload["truncation_warning"] = " ".join(truncation_warnings)
    try:
        from excelmanus.code_mode import (
            get_code_mode_session,
            sdk_unavailable_reason,
        )

        _cm_session = get_code_mode_session()
        if _cm_session is None:
            _unavailable = sdk_unavailable_reason()
            if _unavailable:
                payload["sdk_unavailable"] = True
                payload["sdk_unavailable_reason"] = _unavailable
    except Exception:
        pass
    if payload != result.value:
        return _dc_replace(
            result,
            value=payload,
            model_text=json.dumps(payload, ensure_ascii=False, indent=2),
        )
    return result


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
    allow_network: bool = False,
    allow_external_files: bool = False,
) -> ToolResult:
    """内部执行脚本核心逻辑（供 run_code 调用）。始终走本机子进程围栏。"""
    python_cmd, probes, mode = _resolve_python_command(
        python_command,
        require_excel_deps=require_excel_deps,
        sandbox_tier=sandbox_tier,
    )
    sandbox_python_cmd, isolated_python = _ensure_isolated_python(python_cmd)
    sandbox_env, env_warnings = _build_sandbox_env(allow_network=allow_network)
    preexec_fn, limits_applied, limit_warnings = _build_unix_limits_preexec(
        timeout_seconds, allow_subprocess=allow_network and allow_external_files
    )
    _sandbox_warnings = [*env_warnings, *limit_warnings]  # noqa: F841 — reserved for future logging
    safe_args = [str(item) for item in (args or [])]

    # ── 注入工作区本地临时目录（确保 et_xmlfile 等库的 temp 文件在工作区内） ──
    _sandbox_env_obj = _get_active_sandbox_env()
    if _sandbox_env_obj is not None:
        sandbox_tmpdir = _sandbox_env_obj.get_tmp_dir()
    else:
        sandbox_tmpdir = guard.workspace_root / ".tmp"
        sandbox_tmpdir.mkdir(parents=True, exist_ok=True)
    sandbox_env["TMPDIR"] = str(sandbox_tmpdir)
    sandbox_env["TMP"] = str(sandbox_tmpdir)
    sandbox_env["TEMP"] = str(sandbox_tmpdir)

    # ── 路径上下文：帮助Agent理解sandbox中的工作目录 ──
    sandbox_env["EXCELMANUS_WORKSPACE_ROOT"] = str(guard.workspace_root)
    sandbox_env["EXCELMANUS_WORKDIR"] = str(workdir_safe)

    _apply_code_mode_env(sandbox_env, workspace_root=guard.workspace_root)
    from excelmanus.workbook_commit import export_seen_versions
    from excelmanus.workspace.runtime import (
        PENDING_RUN_ID_ENV,
        allocate_pending_run_id,
        prepare_pending_run_dir,
    )

    pending_run_id = allocate_pending_run_id()
    pending_dir = prepare_pending_run_dir(guard.workspace_root, pending_run_id)
    sandbox_env[PENDING_RUN_ID_ENV] = pending_run_id
    sandbox_env["EXCELMANUS_PENDING_DIR"] = str(pending_dir)

    # ── 沙盒 wrapper 注入（所有安全等级均注入） ──
    temp_wrapper: Path | None = None
    from excelmanus.execution_isolation import docker_enabled, prepare_command

    # The wrapper performs path checks itself.  Its root must match the mount
    # point when Docker is enabled rather than the host's absolute path.
    wrapper_workspace_root = "/workspace" if docker_enabled() else str(guard.workspace_root)
    from excelmanus.security.sandbox_hook import generate_wrapper_script
    wrapper_src = generate_wrapper_script(
        sandbox_tier,
        wrapper_workspace_root,
        allow_external_files=allow_external_files,
    )
    temp_dir = guard.workspace_root / "scripts" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_wrapper = temp_dir / f"_sw_{uuid.uuid4().hex[:12]}.py"
    temp_wrapper.write_text(wrapper_src, encoding="utf-8")
    command = [*sandbox_python_cmd, str(temp_wrapper), str(script_safe), *safe_args]
    command, process_cwd, process_env, _containerized = prepare_command(
        command,
        workspace_root=guard.workspace_root,
        workdir=workdir_safe,
        env=sandbox_env,
        allow_network=allow_network,
    )

    started = time.time()
    timed_out = False
    cancelled = False
    return_code = 1
    stdout = ""
    stderr = ""

    from excelmanus.tools.runtime import (
        current_execution,
        register_killable_process,
        _terminate_process_tree,
        terminate_killable_processes,
        unregister_killable_process,
    )
    execution = current_execution()
    execution_id = getattr(execution, "execution_id", None)

    try:
        run_kwargs: dict[str, Any] = {
            "cwd": process_cwd,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout_seconds,
            "check": False,
            "env": process_env if _containerized else sandbox_env,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
            "start_new_session": True,
        }
        if os.name == "nt":
            run_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        if preexec_fn is not None:
            run_kwargs["preexec_fn"] = preexec_fn
        timeout = run_kwargs.pop("timeout")
        run_kwargs.pop("check")
        if run_kwargs.pop("capture_output", False):
            run_kwargs["stdout"] = subprocess.PIPE
            run_kwargs["stderr"] = subprocess.PIPE
        process = subprocess.Popen(command, **run_kwargs)
        register_killable_process(execution_id, process)
        try:
            if execution is not None and execution.cancel_requested:
                cancelled = True
                terminate_killable_processes(execution_id)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                if execution_id:
                    terminate_killable_processes(execution_id)
                else:
                    _terminate_process_tree(process)
                stdout, stderr = process.communicate()
                if not stdout:
                    stdout = (
                        exc.stdout.decode(errors="replace")
                        if isinstance(exc.stdout, bytes)
                        else exc.stdout
                    ) or ""
                return_code = 124
                if not stderr:
                    stderr = (
                        exc.stderr.decode(errors="replace")
                        if isinstance(exc.stderr, bytes)
                        else exc.stderr
                    ) or ""
            if not timed_out:
                return_code = process.returncode
            if execution is not None and execution.cancel_requested:
                cancelled = True
        finally:
            unregister_killable_process(execution_id, process)
    except subprocess.TimeoutExpired:
        # Popen.communicate 的超时分支已负责终止并收集输出；保留兜底，
        # 兼容极少数自定义 Popen 实现。
        timed_out = True
        return_code = 124

    stdout_saved: str | None = None
    stderr_saved: str | None = None
    from excelmanus.tools._helpers import commit_error_result
    from excelmanus.workbook_commit import (
        CommitError,
        remember_content_version,
        resolve_expected_version,
    )
    from excelmanus.workspace.file_service import TargetSpec, WorkspaceFileService
    from excelmanus.workspace.runtime import discard_pending_run_dir, publish_pending_writes

    readonly_exec = _is_readonly_exec()
    discarded_targets: list[str] = []
    pending_discarded = False
    published: list[dict[str, Any]] = []
    discarded_writes = 0

    if cancelled:
        status = "cancelled"
    elif timed_out:
        status = "timed_out"
    elif return_code == 0:
        status = "success"
    else:
        status = "failed"

    if readonly_exec or timed_out or cancelled:
        discarded_writes = discard_pending_run_dir(guard.workspace_root, pending_run_id)
        pending_discarded = True
        if stdout_file:
            discarded_targets.append(str(stdout_file))
        if stderr_file:
            discarded_targets.append(str(stderr_file))
    else:
        specs: list[TargetSpec] = []
        capture_rels: dict[str, str] = {}

        def _capture_spec(user_path: str, text: str) -> None:
            safe = guard.resolve_and_validate(user_path)
            rel = str(safe.relative_to(guard.workspace_root)).replace("\\", "/")
            seen = resolve_expected_version(rel, None, exists=safe.is_file(), abs_path=safe)
            data = text.encode("utf-8")
            if safe.is_file():
                specs.append(TargetSpec(op="update", path=rel, data=data, expected_version=seen))
            else:
                specs.append(TargetSpec(op="create", path=rel, data=data))
            capture_rels[rel] = "stdout" if user_path == stdout_file else "stderr"

        if stdout_file:
            try:
                _capture_spec(stdout_file, stdout)
            except CommitError as exc:
                discard_pending_run_dir(guard.workspace_root, pending_run_id)
                return commit_error_result(exc)
        if stderr_file:
            try:
                _capture_spec(stderr_file, stderr)
            except CommitError as exc:
                discard_pending_run_dir(guard.workspace_root, pending_run_id)
                return commit_error_result(exc)
        if specs:
            try:
                receipt = WorkspaceFileService(guard.workspace_root).apply_batch(specs, actor="run_code")
                WorkspaceFileService(guard.workspace_root).raise_if_failed(receipt)
            except CommitError as exc:
                discard_pending_run_dir(guard.workspace_root, pending_run_id)
                return commit_error_result(exc)
            for target in receipt.targets:
                if target.after_version:
                    remember_content_version(target.path, target.after_version)
                role = capture_rels.get(target.path)
                if role == "stdout":
                    stdout_saved = target.path
                elif role == "stderr":
                    stderr_saved = target.path
        if execution is not None and execution.cancel_requested:
            cancelled = True
            discarded_writes += discard_pending_run_dir(
                guard.workspace_root,
                pending_run_id,
            )
            pending_discarded = True
            published = []
        else:
            published = publish_pending_writes(
                guard.workspace_root,
                stderr,
                run_id=pending_run_id,
                expected_versions=export_seen_versions(),
            )
    if cancelled:
        status = "cancelled"
    save_versions: dict[str, str] = {}
    for item in published:
        path = str(item.get("path") or "").strip()
        version = item.get("content_version")
        if item.get("status") == "committed" and path and isinstance(version, str):
            remember_content_version(path, version)
            save_versions[path] = version

    result: dict[str, Any] = {
        "stdout_tail": _tail(stdout, tail_lines),
        "stderr_tail": _tail(stderr, tail_lines),
        "status": status,
        "return_code": return_code,
        "duration_seconds": round(time.time() - started, 3),
        "mode": "inline" if inline_mode else "file",
        "script": str(script_safe.relative_to(guard.workspace_root)),
        "workdir": str(workdir_safe.relative_to(guard.workspace_root)),
        "timed_out": timed_out,
        "cancelled": cancelled,
        "stdout_file": stdout_saved,
        "stderr_file": stderr_saved,
        "save_versions": save_versions,
        "published": published,
        "pending_discarded": pending_discarded,
        "sandbox_tier": sandbox_tier,
    }
    from excelmanus.execution_isolation import isolation_mode
    result["execution_isolation"] = isolation_mode()

    if readonly_exec:
        notes = list(discarded_targets)
        if discarded_writes:
            notes.append(f"{discarded_writes} 个沙盒内文件写入")
        if notes:
            result["readonly_note"] = (
                "只读模式：以下写入已丢弃，未落盘 —— " + "；".join(notes)
            )

    # 检测沙盒权限错误，追加恢复提示
    if status == "failed":
        stderr_text = stderr or ""
        hints: list[str] = []
        if "安全策略禁止" in stderr_text or "PATH_OUTSIDE_WORKSPACE" in stderr_text:
            if "路径不在工作区内" in stderr_text or "PATH_OUTSIDE_WORKSPACE" in stderr_text:
                hints.append(
                    "文件路径不在当前工作区。受限模式只能访问工作区内文件；"
                    "需要访问外部文件时请开启完全访问，或先把文件放入当前工作区。"
                )
            if (
                "敏感目录" in stderr_text
                or "SENSITIVE_FILE" in stderr_text
                or "禁止访问工作区外的 .env" in stderr_text
            ):
                hints.append(
                    "目标命中了受保护的敏感目录或配置文件；请改用工作区副本，"
                    "不要直接读取 ExcelManus 的凭证、数据库或内部状态。"
                )

        _blocked_import = (
            "模块 " in stderr_text and "被安全策略禁止" in stderr_text
        )
        if _blocked_import and any(
            m in stderr_text for m in ["requests", "urllib", "http", "socket", "ssl", "websocket"]
        ):
            hints.append(
                "受限模式禁止网络模块；请开启完全访问后重试，或改用不联网的数据处理库。"
            )
        elif "ModuleNotFoundError" in stderr_text and "No module named" in stderr_text:
            hints.append(
                "当前 Python 环境缺少代码依赖；这不是权限错误。请换用已安装的解释器，"
                "或在应用运行时环境中补齐该依赖。"
            )

        if hints:
            result["recovery_hint"] = " ".join(hints)

    # ── 空输出诊断：stdout+stderr 均为空时追加排错提示 ──
    if not stdout.strip() and not stderr.strip():
        diag_parts: list[str] = []
        if status == "success":
            diag_parts.append(
                "代码返回成功(exit 0)但无任何输出。"
                "可能原因：(1) 代码中的 print 语句被 try/except 吞掉或未执行；"
                "(2) 代码包含 `...`(Ellipsis) 占位符导致实际逻辑被跳过；"
                "(3) 所有输出逻辑在异常后的代码路径中。"
            )
        elif status == "failed":
            diag_parts.append(
                f"代码执行失败(exit {return_code})且无错误输出。"
                "可能原因：(1) 依赖未安装(如 sklearn)导致 ImportError 被 try/except 静默捕获；"
                "(2) 沙盒环境变量不完整导致解释器初始化异常；"
                "(3) 代码语法不完整(被截断)导致 SyntaxError。"
            )
        if diag_parts:
            diag_parts.append(
                "建议：确保代码顶层有 print 输出验证，"
                "except 块中使用 traceback.print_exc() 而非 pass，"
                "并检查所有依赖是否已安装。"
            )
            result["empty_output_diagnostic"] = " ".join(diag_parts)

    # 清理临时 wrapper
    if temp_wrapper is not None and temp_wrapper.exists():
        try:
            temp_wrapper.unlink()
        except OSError:
            pass

    return _pack_run_code_result(result)


def get_tools() -> list[ToolDef]:
    """返回代码执行工具定义。"""
    return [
        ToolDef(
            name="write_text_file",
            description=(
                "写入文本文件（常用于生成 Python 脚本后交给 run_code 执行）。"
                "适用场景：创建或覆盖 .py/.txt/.csv 文本文件。"
                "不适用：直接写入 Excel 数据（改用 SDK：apply_spreadsheet_changes）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "目标文件路径（相对于工作目录）"},
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
                    "expected_version": {
                        "type": "string",
                        "description": "已有文件的本轮已读 content_version；缺省且文件已存在则 VERSION_CONFLICT",
                    },
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
            func=write_text_file,
            write_effect="workspace_write",
        ),
        ToolDef(
            name="edit_text_file",
            description=(
                "精准编辑文本文件：查找 old_string 并替换为 new_string（类似 IDE 查找替换）。"
                "适用场景：修改已有 .py/.txt/.csv/.json 等文本文件中的特定片段，无需重写整个文件。"
                "old_string 必须在文件中唯一匹配（除非 replace_all=true）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "目标文件路径（相对于工作目录）"},
                    "old_string": {"type": "string", "description": "要被替换的原始文本（必须精确匹配）"},
                    "new_string": {"type": "string", "description": "替换后的新文本"},
                    "encoding": {
                        "type": "string",
                        "description": "文本编码",
                        "default": "utf-8",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "是否替换所有匹配项（默认仅替换首个且要求唯一）",
                        "default": False,
                    },
                    "expected_version": {
                        "type": "string",
                        "description": "本轮已读 content_version；缺省则 VERSION_CONFLICT",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
                "additionalProperties": False,
            },
            func=edit_text_file,
            write_effect="workspace_write",
        ),
        ToolDef(
            name="run_code",
            description=TOOL_DESCRIPTIONS["run_code"],
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "内联 Python 代码（与 script_path 互斥，同时传时忽略 code）",
                    },
                    "script_path": {
                        "type": "string",
                        "description": "磁盘 .py 脚本路径（与 code 互斥，相对于工作目录）",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "脚本位置参数（仅文件模式）",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "执行工作目录（相对于工作目录）",
                        "default": ".",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "脚本墙钟预算秒数（1~1800），默认 900 以容纳交互窗口",
                        "default": 900,
                        "minimum": 1,
                        "maximum": 1800,
                    },
                    "python_command": {
                        "type": "string",
                        "description": "解释器命令，默认 auto（自动探测）",
                        "default": "auto",
                    },
                    "tail_lines": {
                        "type": "integer",
                        "description": "返回 stdout/stderr 尾部行数",
                        "default": 80,
                        "minimum": 0,
                    },
                "require_excel_deps": {
                        "type": "boolean",
                        "description": "是否要求 pandas/openpyxl",
                        "default": False,
                    },
                    "dependency_profile": {
                        "type": "string",
                        "enum": ["pure_python", "basic_files", "excel"],
                        "description": "运行时档位：纯 Python、基础文件处理或 Excel 依赖；传入后覆盖 require_excel_deps",
                        "default": "basic_files",
                    },
                    "stdout_file": {
                        "type": "string",
                        "description": "stdout 日志输出路径",
                    },
                    "stderr_file": {
                        "type": "string",
                        "description": "stderr 日志输出路径",
                    },
                },
                "additionalProperties": False,
            },
            func=run_code,
            max_result_chars=8000,
            truncate_head_chars=5000,
            truncate_tail_chars=3000,
            write_effect="dynamic",
        ),
    ]
