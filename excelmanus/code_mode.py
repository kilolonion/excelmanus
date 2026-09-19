"""P4 Code Mode：从 ToolDef 生成 Python SDK，经宿主文件桥调用 ToolDispatcher。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import inspect
import json
import keyword
import re
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolError, ToolResult
from excelmanus.logger import get_logger
from excelmanus.tools.registry import ToolDef

logger = get_logger("code_mode")

LOCAL_SANDBOX_DISCLAIMER = "本机受限子进程：禁网络、禁起进程、禁出工作区。"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SKIP_SDK_TOOLS = frozenset({"run_code"})

# 问答/审批窗口：DEFAULT_INTERACTION_TIMEOUT（600s）+ slack。
# Hook ASK 可打在任意工具上，不能只给 ask_user/run_shell/delete_file 加长等待。
_INTERACTIVE_CALL_SLACK_S = 60.0
_DEFAULT_RUN_TIMEOUT_S = 900.0
_MAX_RUN_TIMEOUT_S = 1800.0
_DEFAULT_DELEGATE_TIMEOUT_S = 600.0

_sessions_by_root: dict[str, "CodeModeSession"] = {}
_sessions_lock = threading.Lock()


def interaction_wait_window() -> float:
    from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

    return float(DEFAULT_INTERACTION_TIMEOUT) + _INTERACTIVE_CALL_SLACK_S


def timeout_seconds_from_args(arguments: dict[str, Any] | None) -> float:
    """从 run_code 参数取出父墙钟预算；非法值回落到默认 900s。"""
    if not isinstance(arguments, dict):
        return _DEFAULT_RUN_TIMEOUT_S
    raw = arguments.get("timeout_seconds", _DEFAULT_RUN_TIMEOUT_S)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RUN_TIMEOUT_S
    if value <= 0:
        return _DEFAULT_RUN_TIMEOUT_S
    return min(value, _MAX_RUN_TIMEOUT_S)


def register_code_mode_session(session: "CodeModeSession") -> None:
    root = session.root_call_id or ""
    if not root:
        return
    with _sessions_lock:
        _sessions_by_root[root] = session


def unregister_code_mode_session(session: "CodeModeSession") -> None:
    root = session.root_call_id or ""
    if not root:
        return
    with _sessions_lock:
        current = _sessions_by_root.get(root)
        if current is session:
            _sessions_by_root.pop(root, None)


def session_for_root(root_call_id: str | None) -> "CodeModeSession | None":
    if not root_call_id:
        return None
    with _sessions_lock:
        return _sessions_by_root.get(root_call_id)


def reject_new_foreground_commit() -> str | None:
    """父 run 已关闭新 dispatch 时，拒绝该 run 的新前台提交。"""
    from excelmanus.tools.context import current_call

    session = get_code_mode_session()
    if session is not None and session.dispatch_closed:
        return "父 run_code 已结束，拒绝新的前台提交"
    ctx = current_call()
    if ctx is None:
        return None
    parent = getattr(ctx, "parent_call_id", None) or None
    if not parent:
        return None
    bound = session_for_root(str(parent))
    if bound is not None and bound.dispatch_closed:
        return "父 run_code 已结束，拒绝新的前台提交"
    return None


@dataclass
class SdkCallRecord:
    tool: str
    success: bool
    content_version: str | None = None


class CodeModeUnavailable(RuntimeError):
    """Code Mode 会话无法建立（目录推导 / 桥准备 / 启动失败）。"""

    error_code = "CODE_MODE_UNAVAILABLE"


@dataclass
class CodeModeSession:
    """宿主侧 Code Mode 会话：文件桥 + 调用摘要。"""

    dispatcher: Any
    root_call_id: str
    bridge_dir: Path
    tool_defs: list[ToolDef] = field(default_factory=list)
    tool_scope: Sequence[str] | None = None
    call_timeout: float = _DEFAULT_RUN_TIMEOUT_S
    deadline_mono: float | None = None
    deadline_wall: float | None = None
    delegate_timeout: float = _DEFAULT_DELEGATE_TIMEOUT_S
    on_event: Any = None
    # None = 未配置绑定快照（兼容旧构造）；frozenset（可为空）= 已绑定集合。
    bound_names: frozenset[str] | None = None
    _calls: list[SdkCallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _processed: set[str] = field(default_factory=set)
    _subcall_seq: int = 0
    _inflight: Any = None
    # 父取消传播：stop() 置位，引擎侧正在等待（如审批）的子调用借此退出。
    _subcall_cancel: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def sdk_path(self) -> Path:
        return self.bridge_dir / "em.py"

    @property
    def dispatch_closed(self) -> bool:
        return self._stop.is_set()

    def prepare(self) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.sdk_path.write_text(
            render_sdk_source(
                self.tool_defs,
                delegate_timeout=self.delegate_timeout,
            ),
            encoding="utf-8",
        )

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.prepare()
        self._stop.clear()
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        register_code_mode_session(self)
        self._thread = threading.Thread(
            target=self._serve_loop,
            name="excelmanus-code-mode-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """关闭新 dispatch、取消未启动 req、传播取消。不要在事件循环里 join。"""
        self._stop.set()
        self._signal_subcall_cancel()
        inflight = self._inflight
        if inflight is not None:
            try:
                inflight.cancel()
            except Exception:
                logger.debug("Code Mode 在飞子调用取消失败", exc_info=True)
        self._cancel_pending_requests()
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            if not self.has_unsettled_work():
                unregister_code_mode_session(self)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is None:
            self._join_bridge_thread(timeout=2.0)
            return
        threading.Thread(
            target=self._join_bridge_thread,
            args=(2.0,),
            name="excelmanus-code-mode-join",
            daemon=True,
        ).start()

    def has_unsettled_work(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        inflight = self._inflight
        if inflight is not None and not getattr(inflight, "done", lambda: True)():
            return True
        return False

    def _join_bridge_thread(self, timeout: float) -> bool:
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            unregister_code_mode_session(self)
            return True
        thread.join(timeout=timeout)
        if thread.is_alive():
            return False
        self._thread = None
        unregister_code_mode_session(self)
        return True

    async def wait_settlement(self, timeout: float = 2.0) -> bool:
        """在线程池里 join 桥线程，避免堵住事件循环。"""
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            unregister_code_mode_session(self)
            return True
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: thread.join(timeout=timeout))
        if thread.is_alive():
            return False
        self._thread = None
        unregister_code_mode_session(self)
        return True

    def remaining_budget(self) -> float:
        if self.deadline_mono is not None:
            return max(0.0, self.deadline_mono - time.monotonic())
        return max(0.0, float(self.call_timeout))

    def timeout_for(self, tool_name: str) -> float:
        """子调用等待 = min(父剩余, 自身窗口)。"""
        return min(self.remaining_budget(), self._own_window(tool_name))

    def _timeout_for(self, tool_name: str) -> float:
        return self.timeout_for(tool_name)

    def _own_window(self, tool_name: str) -> float:
        if tool_name == "delegate":
            return max(0.0, float(self.delegate_timeout))
        return interaction_wait_window()

    def _signal_subcall_cancel(self) -> None:
        """通知引擎侧等待中的子调用：父 run_code 已结束/取消。"""
        event = self._subcall_cancel
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            event.set()
        elif loop is not None:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:
                event.set()
        else:
            event.set()

    def _cancel_pending_requests(self) -> None:
        """父结束：给未处理的 req 文件补 CANCELLED 响应，不留悬挂等待。"""
        try:
            req_files = sorted(self.bridge_dir.glob("*.req.json"))
        except OSError:
            return
        for req_path in req_files:
            if req_path.name in self._processed:
                continue
            resp_name = req_path.name.replace(".req.json", ".resp.json")
            resp_path = req_path.with_name(resp_name)
            if resp_path.exists():
                continue
            try:
                self._write_resp(
                    resp_path,
                    {
                        "ok": False,
                        "error": {
                            "code": "CANCELLED",
                            "message": "父 run_code 已结束，子调用未执行",
                        },
                    },
                )
            except OSError:
                logger.debug("Code Mode 写入取消响应失败: %s", resp_path, exc_info=True)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            calls = list(self._calls)
        writes = [
            {"tool": rec.tool, "content_version": rec.content_version}
            for rec in calls
            if rec.content_version
        ]
        return {
            "count": len(calls),
            "succeeded": sum(1 for rec in calls if rec.success),
            "failed": sum(1 for rec in calls if not rec.success),
            "writes": writes,
        }

    def allocate_subcall_id(self, tool: str, root_call_id: str) -> str:
        with self._lock:
            self._subcall_seq += 1
            seq = self._subcall_seq
        prefix = root_call_id or self.root_call_id or "sub"
        return f"{prefix}:{tool}:{seq}"

    def _record(self, rec: SdkCallRecord) -> None:
        with self._lock:
            self._calls.append(rec)

    def _serve_loop(self) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        while not self._stop.wait(0.02):
            self._drain_requests()
        self._drain_requests()

    def _drain_requests(self) -> None:
        try:
            req_files = sorted(self.bridge_dir.glob("*.req.json"))
        except OSError:
            return
        closed = self._stop.is_set()
        for req_path in req_files:
            name = req_path.name
            if name in self._processed:
                continue
            if closed:
                self._processed.add(name)
                resp_name = name.replace(".req.json", ".resp.json")
                resp_path = req_path.with_name(resp_name)
                if resp_path.exists():
                    continue
                try:
                    self._write_resp(
                        resp_path,
                        {
                            "ok": False,
                            "error": {
                                "code": "CANCELLED",
                                "message": "父 run_code 已结束，子调用未执行",
                            },
                        },
                    )
                except OSError:
                    logger.debug("Code Mode 写入取消响应失败: %s", resp_path, exc_info=True)
                continue
            try:
                raw = req_path.read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            self._processed.add(name)
            resp_name = req_path.name.replace(".req.json", ".resp.json")
            resp_path = req_path.with_name(resp_name)
            self._handle_request(payload, resp_path)

    def _handle_request(self, payload: dict[str, Any], resp_path: Path) -> None:
        tool = str(payload.get("tool") or "")
        arguments = payload.get("arguments")
        # root_call_id 一律取宿主指定值，不信任子进程传值。
        root_call_id = self.root_call_id or ""
        if not tool:
            self._write_resp(
                resp_path,
                {"ok": False, "error": {"code": "BAD_REQUEST", "message": "missing tool"}},
            )
            self._record(SdkCallRecord(tool="", success=False))
            return
        if not isinstance(arguments, dict):
            self._write_resp(
                resp_path,
                {
                    "ok": False,
                    "error": {
                        "code": "BAD_REQUEST",
                        "message": f"arguments 必须是对象，当前类型: {type(arguments).__name__}",
                    },
                },
            )
            self._record(SdkCallRecord(tool=tool, success=False))
            return
        arguments = _revive_typed_args(arguments)
        if tool == "run_code":
            self._write_resp(
                resp_path,
                {
                    "ok": False,
                    "error": {
                        "code": "NESTED_RUN_CODE",
                        "message": "Code Mode SDK 禁止嵌套调用 run_code；请修改当前程序，不要另起 run_code。",
                    },
                },
            )
            self._record(SdkCallRecord(tool=tool, success=False))
            return
        if self.bound_names is not None and not self._is_bound_tool(tool):
            self._write_resp(
                resp_path,
                {
                    "ok": False,
                    "error": {
                        "code": "TOOL_NOT_IN_SDK",
                        "message": (
                            f"工具 `{tool}` 不在本次 SDK 绑定目录内。"
                            "可调用能力以 SDK 声明为准；参数细节可 introspect_capability 查询。"
                        ),
                    },
                },
            )
            self._record(SdkCallRecord(tool=tool, success=False))
            return
        try:
            result = self._call_dispatcher(
                tool_name=tool,
                arguments=arguments,
                root_call_id=root_call_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Code Mode 子调用失败 tool=%s", tool, exc_info=True)
            self._record(SdkCallRecord(tool=tool, success=False))
            self._write_resp(
                resp_path,
                {
                    "ok": False,
                    "error": {"code": "DISPATCH_ERROR", "message": str(exc)},
                },
            )
            return
        version = _content_version_from_result(result)
        success = bool(getattr(result, "success", False))
        payload = _payload_from_tool_result(
            result, tool_name=tool, arguments=arguments
        )
        if not payload.get("ok"):
            # 返回合同违约：工具虽 success 但对 SDK 是集成失败，如实记账。
            success = False
        self._record(
            SdkCallRecord(tool=tool, success=success, content_version=version),
        )
        self._write_resp(resp_path, payload)

    def _is_bound_tool(self, tool: str) -> bool:
        """绑定快照成员检查：规范名与别名都接受。"""
        bound = self.bound_names or frozenset()
        if tool in bound:
            return True
        try:
            from excelmanus.tools.registry import canonical_tool_name

            return canonical_tool_name(tool) in bound
        except Exception:
            return False

    def _call_dispatcher(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        root_call_id: str,
    ) -> ToolResult:
        checker = getattr(self.dispatcher, "is_cancelled", None)
        cancelled = False
        if callable(checker) and not inspect.iscoroutinefunction(checker):
            try:
                flag = checker()
            except Exception:
                flag = False
            cancelled = flag is True
        if cancelled:
            return ToolResult(
                success=False,
                model_text="任务已取消",
                error=ToolError(code="CANCELLED", message="任务已取消"),
            )
        execute_subcall = getattr(type(self.dispatcher), "execute_subcall", None)
        if callable(execute_subcall):
            call = execute_subcall(
                self.dispatcher,
                tool_name=tool_name,
                arguments=arguments,
                tool_scope=self.tool_scope,
                root_call_id=root_call_id,
                call_id=self.allocate_subcall_id(tool_name, root_call_id),
                on_event=self.on_event,
            )
        else:
            call = self.dispatcher.call_registry_tool(
                tool_name=tool_name,
                arguments=arguments,
                tool_scope=self.tool_scope,
                root_call_id=root_call_id,
            )
        if not hasattr(call, "__await__"):
            return call
        loop = self._loop
        if loop is not None:
            future = asyncio.run_coroutine_threadsafe(call, loop)
            self._inflight = future
            try:
                return future.result(timeout=self.timeout_for(tool_name))
            except TimeoutError:
                return ToolResult(
                    success=False,
                    model_text="子调用等待超时",
                    error=ToolError(code="TIMEOUT", message="子调用等待超时"),
                )
            except (concurrent.futures.CancelledError, asyncio.CancelledError):
                return ToolResult(
                    success=False,
                    model_text="任务已取消",
                    error=ToolError(code="CANCELLED", message="任务已取消"),
                )
            finally:
                self._inflight = None
        return asyncio.run(call)

    @staticmethod
    def _write_resp(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(path)


def _revive_typed_args(value: Any) -> Any:
    """还原 SDK 侧 `_em_json_default` 写出的 {"$em_type", "v"} 类型标记。"""
    from excelmanus.json_typed import revive_typed_args

    return revive_typed_args(value)


_current_session: contextvars.ContextVar[CodeModeSession | None] = contextvars.ContextVar(
    "excelmanus_code_mode_session",
    default=None,
)

# 桥启动失败但脚本不依赖 SDK 时的降级标记：run_code 结果必须如实带
# sdk_unavailable，而不是假装一切正常。
_sdk_unavailable: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "excelmanus_code_mode_unavailable",
    default=None,
)


def set_sdk_unavailable(reason: str | None) -> contextvars.Token[str | None]:
    return _sdk_unavailable.set(reason)


def sdk_unavailable_reason() -> str | None:
    return _sdk_unavailable.get()


def reset_sdk_unavailable(token: contextvars.Token[str | None]) -> None:
    _sdk_unavailable.reset(token)


class HostToolError(Exception):
    """沙盒脚本侧看到的宿主工具错误。"""

    def __init__(self, message: str, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code


def get_code_mode_session() -> CodeModeSession | None:
    return _current_session.get()


def set_code_mode_session(session: CodeModeSession | None) -> contextvars.Token[CodeModeSession | None]:
    return _current_session.set(session)


def reset_code_mode_session(token: contextvars.Token[CodeModeSession | None]) -> None:
    _current_session.reset(token)


def build_session_for_run_code(
    dispatcher: Any,
    *,
    root_call_id: str,
    tool_scope: Sequence[str] | None = None,
    on_event: Any = None,
    timeout_seconds: float | None = None,
) -> CodeModeSession:
    """构建本次 run_code 的宿主桥会话。

    目录推导失败抛出 ``CodeModeUnavailable``，不再静默降级为无 SDK 会话。
    每次调用生成唯一 bridge 目录：同 call_id 的重试与审批重放不会消费
    上一次运行遗留的 req/resp 文件。
    ``timeout_seconds`` 写入 ``call_timeout`` 与 monotonic/wall deadline。
    """
    engine = getattr(dispatcher, "_engine", None)
    config = getattr(engine, "config", None)
    workspace = Path(str(getattr(config, "workspace_root", ".") or "."))
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", root_call_id or "run")[:80] or "run"
    from excelmanus.tools.catalog import execution_catalog_from_engine

    try:
        catalog = execution_catalog_from_engine(engine)
    except Exception as exc:
        raise CodeModeUnavailable(f"Code Mode 执行目录推导失败: {exc}") from exc
    if catalog is None:
        raise CodeModeUnavailable("Code Mode 执行目录推导失败：引擎缺少有效工具注册表。")
    tool_defs = [
        tool
        for tool in catalog.tools
        if getattr(tool, "name", "") not in _SKIP_SDK_TOOLS
    ]
    bound = frozenset(
        str(getattr(tool, "name", "") or "")
        for tool in tool_defs
        if getattr(tool, "name", "")
    )
    timeout = (
        _DEFAULT_RUN_TIMEOUT_S
        if timeout_seconds is None
        else min(max(float(timeout_seconds), 0.0), _MAX_RUN_TIMEOUT_S)
    )
    delegate_timeout = float(
        getattr(config, "subagent_timeout_seconds", _DEFAULT_DELEGATE_TIMEOUT_S)
        or _DEFAULT_DELEGATE_TIMEOUT_S
    )
    now_mono = time.monotonic()
    now_wall = time.time()
    return CodeModeSession(
        dispatcher=dispatcher,
        root_call_id=root_call_id,
        bridge_dir=workspace / ".tmp" / "code_mode" / f"{safe}-{uuid.uuid4().hex[:8]}",
        tool_defs=tool_defs,
        tool_scope=tool_scope,
        call_timeout=timeout,
        deadline_mono=now_mono + timeout,
        deadline_wall=now_wall + timeout,
        delegate_timeout=delegate_timeout,
        on_event=on_event,
        bound_names=bound,
    )


_SDK_DEP_RE = re.compile(r"(?:^|\s)(?:import|from)\s+em\b|\bem\s*\.\s*[A-Za-z_]", re.MULTILINE)


def script_uses_sdk(arguments: dict[str, Any] | None, workspace_root: str | Path | None = None) -> bool:
    """可靠判定脚本是否依赖 em SDK（import em / em.*）。

    无法可靠判定（script_path 不可读等）时返回 True——宁 fail-loud 也不
    降级为无 SDK 执行后再产生谜之 NO_BRIDGE。
    """
    if not isinstance(arguments, dict):
        return True
    code = arguments.get("code")
    if isinstance(code, str) and code.strip():
        return bool(_SDK_DEP_RE.search(code))
    script_path = arguments.get("script_path")
    if not isinstance(script_path, str) or not script_path.strip():
        return True
    text: str | None = None
    try:
        candidate = Path(script_path.strip())
        if not candidate.is_absolute() and workspace_root is not None:
            candidate = Path(workspace_root) / candidate
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = None
    if text is None:
        return True
    return bool(_SDK_DEP_RE.search(text))


def apply_sdk_calls_summary(result_json: str, session: CodeModeSession) -> str:
    """把 sdk_calls 摘要写入 run_code JSON；已提交写入只列出，不回滚。"""
    try:
        data = json.loads(result_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return result_json
    if not isinstance(data, dict):
        return result_json
    data["sdk_calls"] = session.summary()
    data["sandbox_note"] = LOCAL_SANDBOX_DISCLAIMER
    return json.dumps(data, ensure_ascii=False, indent=2)


def attach_sdk_calls(result: Any, session: CodeModeSession) -> Any:
    """结算后把 sdk_calls 写进 ToolResult / JSON 字符串。"""
    if isinstance(result, str):
        return apply_sdk_calls_summary(result, session)
    if not isinstance(result, ToolResult):
        return result
    value = result.value
    if isinstance(value, dict):
        payload = dict(value)
    else:
        payload = {}
        text = result.model_text or ""
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    payload["sdk_calls"] = session.summary()
    payload["sandbox_note"] = LOCAL_SANDBOX_DISCLAIMER
    return replace(
        result,
        value=payload,
        model_text=json.dumps(payload, ensure_ascii=False, indent=2),
    )


def render_sdk_section(tool_defs: list[ToolDef]) -> str:
    """生成 ``tools:sdk`` 段：当前可见绑定的 Python 签名，不是 em.py 教程。"""
    lines: list[str] = ["在 `run_code` 程序内可调用的 SDK："]
    for tool in tool_defs:
        name = getattr(tool, "name", "") or ""
        if name in _SKIP_SDK_TOOLS:
            continue
        line = _sdk_signature_line(tool)
        if line:
            lines.append(line)
            lines.append("  " + str(tool.description or ""))
    if len(lines) == 1:
        return ""
    if any(tool.name == "introspect_capability" for tool in tool_defs):
        lines.append('参数不清楚时在程序内调用 introspect_capability(query_type="tool_detail", query="工具名.字段")；不要读取 em.py 猜参数。')
    lines.append("大结果中间数据可写 scripts/temp/*.json 供后续 run_code 复用；不要为同一数据反复全量拉取。")
    lines.append("读取 `spill:` 句柄返回原始 payload：JSON 对象→dict，其余→原始 str；按返回类型分支处理，不要假设必是 dict。")
    return "\n".join(lines)


_JSON_TYPE_TO_PY: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "array": "list",
}


# F1 冻结：这两个参数的嵌套合同不进 SDK 签名，仍走 introspect_capability。
_SPEC_CONTAINER_PROPS = frozenset({"operations", "workbook_spec"})


def _py_type_of(spec: dict[str, Any], *, prop_name: str = "") -> str:
    """schema → SDK 签名容器类型；枚举值另行标注，嵌套结构不展开。"""
    if not isinstance(spec, dict):
        return "Any"
    raw = spec.get("type")
    types = raw if isinstance(raw, list) else [raw]
    parts = [str(t) for t in types if t and t != "null"]
    # array+string 双形态（operations 等）：声明列表合同，字符串是序列化逃生口。
    if "array" in parts:
        items = spec.get("items")
        item_props = items.get("properties") if isinstance(items, dict) else None
        if (
            isinstance(item_props, dict)
            and item_props
            and prop_name not in _SPEC_CONTAINER_PROPS
        ):
            # 对象数组只提示字段名清单（不展开内层类型），引导模型一次写对
            # 嵌套字段形状；operations/workbook_spec 细节仍走 tool_detail。
            keys = list(item_props)[:6]
            suffix = "…" if len(item_props) > 6 else ""
            base = "list[dict{" + ", ".join(keys) + suffix + "}]"
        else:
            base = "list"
    elif "object" in parts:
        base = "dict"
    elif parts:
        base = _JSON_TYPE_TO_PY.get(parts[0], "Any")
    else:
        base = "Any"
    if "null" in types and base != "Any":
        return f"{base} | None"
    return base


def _sdk_signature_line(tool: ToolDef) -> str:
    schema = getattr(tool, "input_schema", None) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    names = [name for name in properties if not _sdk_skip_property(str(name), properties)]
    req_names = [name for name in names if name in required]
    opt_names = [name for name in names if name not in required]
    params: list[str] = []
    for name in [*req_names, *opt_names]:
        py_name = _py_name(str(name))
        spec = properties.get(name) if isinstance(properties.get(name), dict) else {}
        py_type = _py_type_of(spec, prop_name=str(name))
        if name in required:
            params.append(f"{py_name}: {py_type}" if py_type != "Any" else py_name)
        else:
            default = spec.get("default") if "default" in spec else None
            if py_type != "Any":
                params.append(f"{py_name}: {py_type} = {default!r}")
            else:
                params.append(f"{py_name}={default!r}")
    from excelmanus.tools.output_contracts import return_hint_for

    line = (
        f"- {_py_name(str(tool.name))}({', '.join(params)})"
        f" -> {return_hint_for(str(tool.name))}"
    )
    enum_lines = _schema_enum_lines(schema)
    if enum_lines:
        return line + "\n" + "\n".join(enum_lines)
    return line


def render_sdk_source(
    tool_defs: list[ToolDef],
    **kwargs: Any,
) -> str:
    """从 ToolDef 生成可执行 Python 模块文本（不在沙盒内直接调 func）。"""
    parts: list[str] = [
        _SDK_PREAMBLE.replace("{disclaimer}", LOCAL_SANDBOX_DISCLAIMER),
    ]
    interactive = interaction_wait_window()
    delegate_timeout = float(
        kwargs.get("delegate_timeout", _DEFAULT_DELEGATE_TIMEOUT_S)
        or _DEFAULT_DELEGATE_TIMEOUT_S
    )
    tool_timeouts = {"delegate": delegate_timeout}
    parts.append(f"_EM_INTERACTIVE_WINDOW = {interactive!r}\n")
    parts.append(f"_EM_TOOL_TIMEOUTS = {tool_timeouts!r}\n")
    exported: list[str] = []
    emitted: set[str] = set()
    for tool in tool_defs:
        name = getattr(tool, "name", "") or ""
        if name in _SKIP_SDK_TOOLS:
            continue
        fn_src = _render_tool_function(tool)
        if fn_src:
            fn_name = _py_name(name)
            if fn_name in emitted:
                # 合法化名称碰撞（如 foo-bar 与 foo_bar）：保留先序声明，
                # 被跳过的工具仍可在宿主侧按规范名检查绑定成员。
                logger.warning("Code Mode SDK 名称碰撞，跳过重复函数: %s -> %s", name, fn_name)
                continue
            emitted.add(fn_name)
            parts.append(fn_src)
            exported.append(fn_name)
    from excelmanus.tools.registry import _TOOL_NAME_ALIASES

    visible = set(exported)
    for alias, canonical in _TOOL_NAME_ALIASES.items():
        py_alias, py_canonical = _py_name(alias), _py_name(canonical)
        if py_canonical in visible and py_alias not in visible:
            parts.append(f"{py_alias} = {py_canonical}  # 兼容别名\n")
            exported.append(py_alias)
    if exported:
        all_list = ", ".join(repr(name) for name in exported)
        parts.append(f"__all__ = [{all_list}]\n")
    return "\n".join(parts)


def _content_version_from_result(result: Any) -> str | None:
    """优先 ui_meta，其次结构化 value / JSON 文本里的 content_version。"""
    ui_meta = getattr(result, "ui_meta", None)
    version = getattr(ui_meta, "content_version", None) if ui_meta is not None else None
    if isinstance(version, str) and version:
        return version
    value = getattr(result, "value", None)
    if isinstance(value, dict):
        nested = value.get("content_version")
        if isinstance(nested, str) and nested:
            return nested
    text = getattr(result, "model_text", None)
    if isinstance(text, str) and text.startswith("{") and "content_version" in text:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            nested = parsed.get("content_version")
            if isinstance(nested, str) and nested:
                return nested
    return None


# 桥错误 details 不再白名单：剔除的只是顶层已渲染/信封噪音键，
# errors/available_sheets/conflicts/expected_version 等结构化字段全部透传。
_BRIDGE_ERROR_SKIP_KEYS = frozenset({
    "status", "ok", "error", "code", "error_code", "error_kind", "message", "msg",
})
_BRIDGE_DETAILS_MAX_CHARS = 6000
_BRIDGE_DETAIL_VALUE_MAX_CHARS = 1000


def _bridge_error_details(fields: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {
        key: val
        for key, val in fields.items()
        if key not in _BRIDGE_ERROR_SKIP_KEYS and val not in (None, "", [], {})
    }
    if not details:
        return {}
    try:
        if len(json.dumps(details, ensure_ascii=False, default=str)) <= _BRIDGE_DETAILS_MAX_CHARS:
            return details
    except (TypeError, ValueError):
        return {"note": "details 无法序列化"}
    compact = {}
    for key, val in details.items():
        try:
            if len(json.dumps(val, ensure_ascii=False, default=str)) <= _BRIDGE_DETAIL_VALUE_MAX_CHARS:
                compact[key] = val
        except (TypeError, ValueError):
            continue
    return compact or {"note": "details 过大已省略"}


def _payload_from_tool_result(
    result: ToolResult,
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not getattr(result, "success", False):
        code = "TOOL_ERROR"
        message = getattr(result, "model_text", None) or "tool failed"
        error = getattr(result, "error", None)
        fields: dict[str, Any] = {}
        if error is not None:
            code = str(getattr(error, "code", None) or code)
            message = str(getattr(error, "message", None) or message)
            raw_fields = getattr(error, "fields", None)
            if isinstance(raw_fields, dict):
                fields = raw_fields
        value = getattr(result, "value", None)
        if isinstance(value, dict):
            fields = {**fields, **value}
        error_obj: dict[str, Any] = {"code": code, "message": message}
        for key in ("accepted_fields", "violations", "required_fields"):
            if key in fields:
                error_obj[key] = fields[key]
        details = _bridge_error_details(fields)
        if details:
            error_obj["details"] = details
        return {"ok": False, "error": error_obj}
    value = getattr(result, "value", None)
    if value is None:
        text = getattr(result, "model_text", None) or ""
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            value = text
    # 返回合同运行时校验：已登记工具的成功结果必须满足顶层键声明，
    # 违约不静默放过——脚本拿到结构化错误而不是形状错误的返回值。
    # spill 句柄读取是宿主投影（value 可为原始字符串），不走工具自身合同。
    coverage = getattr(result, "coverage", None)
    if not (isinstance(coverage, dict) and coverage.get("spill_retrieve")):
        from excelmanus.tools.output_contracts import validate_output

        violations = validate_output(tool_name, value, arguments=arguments)
    else:
        violations = []
    if violations:
        return {
            "ok": False,
            "error": {
                "code": "SDK_CONTRACT_VIOLATION",
                "message": f"{tool_name} 返回值不符合声明合同",
                "details": {"tool": tool_name, "violations": violations},
            },
        }
    return {"ok": True, "value": value}


# schema 为同一参数声明的双别名：签名只保留规范名，
# 但宿主 _op_get 同时接受别名，故 SDK 需把别名 kwargs 归一为规范名再分发。
_SDK_ALIAS_PAIRS: tuple[tuple[str, str], ...] = (
    ("path", "file_path"),
    ("sheet", "sheet_name"),
    ("column", "by_column"),
    ("content_version", "expected_version"),
    ("cell_range", "range"),
    ("other_path", "file_b"),
    ("max_rows", "max_lines"),
    ("limit", "max_rows"),
)


def _sdk_aliases(properties: dict[str, Any]) -> dict[str, str]:
    return {
        alias: canonical
        for alias, canonical in _SDK_ALIAS_PAIRS
        if alias in properties and canonical in properties
    }


def _sdk_skip_property(name: str, properties: dict[str, Any]) -> bool:
    return name in _sdk_aliases(properties)


def _schema_enum_lines(schema: dict[str, Any]) -> list[str]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return []
    lines: list[str] = []
    for key in ("mode", "kind", "action", "alignment"):
        spec = properties.get(key)
        if isinstance(spec, dict) and spec.get("enum"):
            lines.append(f"  {key}: " + "|".join(str(item) for item in spec["enum"]))
    operations = properties.get("operations")
    if isinstance(operations, dict):
        items = operations.get("items")
        if isinstance(items, dict):
            item_props = items.get("properties") if isinstance(items.get("properties"), dict) else {}
            kind_spec = item_props.get("kind")
            if isinstance(kind_spec, dict) and kind_spec.get("enum"):
                lines.append("  operations.kind: " + "|".join(str(item) for item in kind_spec["enum"]))
    return lines


def _py_name(name: str) -> str:
    candidate = name.replace("-", "_")
    if not _IDENT_RE.match(candidate) or keyword.iskeyword(candidate):
        candidate = "tool_" + re.sub(r"[^A-Za-z0-9_]", "_", candidate)
    return candidate


def _render_tool_function(tool: ToolDef) -> str:
    schema = getattr(tool, "input_schema", None) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    names = [name for name in properties if not _sdk_skip_property(str(name), properties)]
    req_names = [name for name in names if name in required]
    opt_names = [name for name in names if name not in required]
    params: list[str] = []
    arg_items: list[str] = []
    for name in [*req_names, *opt_names]:
        py_name = _py_name(str(name))
        spec = properties.get(name) if isinstance(properties.get(name), dict) else {}
        if name in required:
            params.append(py_name)
        else:
            default = spec.get("default") if "default" in spec else None
            params.append(f"{py_name}={default!r}")
        arg_items.append(f"{str(name)!r}: {py_name}")
    aliases = _sdk_aliases(properties)
    if aliases:
        params.append("**_kw")
    signature = ", ".join(params)
    description = str(getattr(tool, "description", "") or "")
    args_literal = ", ".join(arg_items)
    call_args = f"{{{args_literal}}}"
    if aliases:
        call_args = f"_with_alias({call_args}, _kw, {aliases!r})"
    fn_name = _py_name(str(tool.name))
    enum_comment = "".join(
        f"    # {line.strip()}\n" for line in _schema_enum_lines(schema)
    )
    return (
        f"def {fn_name}({signature}) -> dict:\n"
        f"{enum_comment}"
        f"    return _call_host({tool.name!r}, {call_args})\n"
        f"{fn_name}.__doc__ = {description!r}\n"
    )


_SDK_PREAMBLE = '''\
"""ExcelManus Code Mode SDK.

通过宿主 ToolDispatcher 调用已注册工具（不在沙盒内直接执行工具 func）。
{disclaimer}

用法::

    from em import inspect_spreadsheet, edit_spreadsheet, split_spreadsheet

首次用某工具前可 introspect_capability(query_type="tool_detail", query="工具名")。
"""
from __future__ import annotations

import json
import os
import time


class HostToolError(Exception):
    def __init__(self, message, code="TOOL_ERROR", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def __str__(self):
        if not self.details:
            return super().__str__()
        try:
            blob = json.dumps(self.details, ensure_ascii=False, default=str)
        except Exception:
            blob = str(self.details)
        return "%s\\n详细信息: %s" % (super().__str__(), blob)


_SEQ = 0

# _EM_INTERACTIVE_WINDOW / _EM_TOOL_TIMEOUTS 由宿主在生成时注入。
# 子调用等待 = min(父剩余, 自身窗口)；问答/审批窗口覆盖任意工具上的 Hook ASK。

def _with_alias(args, extra, aliases):
    """归一 schema 别名参数（如 sheet→sheet_name）后并入 args；未知参数报错。"""
    for key, val in extra.items():
        canon = aliases.get(key)
        if canon is None:
            raise TypeError(
                "未知参数 %r；可用参数以工具 schema 为准（introspect_capability 可查）" % key
            )
        current = args.get(canon)
        if current is not None and current != val:
            raise TypeError("参数 %r 与其别名 %r 同时给出且值不同" % (canon, key))
        args[canon] = val
    return args


def _em_json_default(o):
    """json default：datetime/date/time 用 $em_type 标记保类型，宿主端还原；
    其余非 JSON 类型尽力转换（Decimal→float、set→list、numpy→tolist/item）。"""
    import datetime as _dt
    import decimal as _dec
    if isinstance(o, _dt.datetime):
        if o != o:  # NaT
            return None
        return {"$em_type": "datetime", "v": o.isoformat()}
    if isinstance(o, (_dt.date, _dt.time)):
        return {"$em_type": type(o).__name__, "v": o.isoformat()}
    if isinstance(o, _dec.Decimal):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=repr)
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        return tolist()
    item = getattr(o, "item", None)
    if callable(item):
        return item()
    return str(o)


def _call_host(tool, arguments):
    global _SEQ
    bridge = os.environ.get("EXCELMANUS_CODE_MODE_BRIDGE")
    if not bridge:
        raise HostToolError("Code Mode 宿主桥未启动", "NO_BRIDGE")
    _SEQ += 1
    seq = _SEQ
    req_path = os.path.join(bridge, "%08d.req.json" % seq)
    resp_path = os.path.join(bridge, "%08d.resp.json" % seq)
    payload = {
        "id": seq,
        "tool": tool,
        "arguments": {k: v for k, v in arguments.items() if v is not None},
        "root_call_id": os.environ.get("EXCELMANUS_CODE_MODE_ROOT_CALL_ID") or "",
    }
    tmp_path = req_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as _rf:
        json.dump(payload, _rf, ensure_ascii=False, default=_em_json_default)
        _rf.flush()
        os.fsync(_rf.fileno())
    os.replace(tmp_path, req_path)
    own = _EM_TOOL_TIMEOUTS.get(tool, _EM_INTERACTIVE_WINDOW)
    wall = os.environ.get("EXCELMANUS_CODE_MODE_DEADLINE")
    if wall:
        remaining = float(wall) - time.time()
    else:
        remaining = float(os.environ.get("EXCELMANUS_CODE_MODE_TIMEOUT") or "900")
    timeout = min(max(0.0, remaining), own)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(resp_path):
            with open(resp_path, encoding="utf-8") as _vf:
                resp = json.load(_vf)
            if not resp.get("ok"):
                err = resp.get("error") or {}
                details = dict(err.get("details") or {})
                for key in ("accepted_fields", "violations", "required_fields"):
                    if key in err:
                        details.setdefault(key, err[key])
                raise HostToolError(
                    err.get("message") or "tool failed",
                    err.get("code") or "TOOL_ERROR",
                    details,
                )
            return resp.get("value")
        time.sleep(0.02)
    raise HostToolError("等待宿主工具响应超时", "BRIDGE_TIMEOUT")

'''
