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
FULL_ACCESS_SANDBOX_DISCLAIMER = (
    "已开启完全访问：允许工作区外文件、网络、子进程和本机命令执行；"
    "仅在信任当前任务时使用。"
)

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
    request_id: str = ""
    retry_of: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    publications: list[dict[str, Any]] = field(default_factory=list)
    error_code: str = ""
    message: str = ""


def _recovered_requests(calls: list[SdkCallRecord]) -> set[str]:
    by_id = {rec.request_id: rec for rec in calls if rec.request_id}
    recovered: set[str] = set()
    for rec in calls:
        if rec.success:
            parent = rec.retry_of
            while parent and parent in by_id and parent not in recovered:
                recovered.add(parent)
                parent = by_id[parent].retry_of
    return recovered


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
    sandbox_note: str = LOCAL_SANDBOX_DISCLAIMER
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
                disclaimer=self.sandbox_note,
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
        recovered = _recovered_requests(calls)
        failures = [rec for rec in calls if not rec.success and rec.request_id not in recovered]
        writes = [{"tool": rec.tool, **target} for rec in calls for target in rec.publications]
        return {
            "count": len(calls),
            "succeeded": sum(1 for rec in calls if rec.success),
            "failed": sum(1 for rec in calls if not rec.success),
            "writes": writes,
            "recovered": len(recovered),
            "unresolved": [{"request_id": rec.request_id, "tool": rec.tool,
                            "error_code": rec.error_code, "message": rec.message[:500]}
                           for rec in failures],
            "outcome": "failed" if failures else "success",
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
        request_id = str(payload.get("id") or resp_path.stem)
        retry_of = str(payload.get("retry_of") or "")
        if retry_of:
            previous = next((rec for rec in self._calls if rec.request_id == retry_of), None)
            from excelmanus.engine_core.error_payload import failure_class_for_error_code
            identity_fields = ("file_path", "output_path", "source", "destination")
            if (previous is None or previous.success or previous.tool != tool or previous.publications
                    or failure_class_for_error_code(previous.error_code) not in {"invalid_args", "conflict", "not_found"}
                    or retry_of in _recovered_requests(self._calls)
                    or any(previous.arguments.get(k) not in (None, "") and previous.arguments.get(k) != arguments.get(k) for k in identity_fields)):
                self._record(SdkCallRecord(tool=tool, success=False, request_id=request_id,
                                          error_code="SDK_RETRY_UNSAFE", message="重试必须对应同目标、未提交的失败调用"))
                self._write_resp(resp_path, {"ok": False, "error": {"code": "SDK_RETRY_UNSAFE",
                                 "message": "只可直接重试同目标、明确未提交的参数/版本/查找失败；已提交、取消、超时或结果不确定时先检查回执。"}})
                return
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
            result, tool_name=tool, arguments=arguments,
            tool_def=next((definition for definition in self.tool_defs if definition.name == tool), None),
        )
        if not payload.get("ok"):
            # 返回合同违约：工具虽 success 但对 SDK 是集成失败，如实记账。
            success = False
        from excelmanus.engine_core.execution_facts import tool_publications

        self._record(
            SdkCallRecord(
                tool=tool, success=success, content_version=version,
                request_id=request_id, retry_of=retry_of, arguments=arguments,
                publications=tool_publications(tool, result.value, success=success),
                error_code=str((payload.get("error") or {}).get("code") or ""),
                message=str((payload.get("error") or {}).get("message") or ""),
            ),
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
    parent_remaining_fn = getattr(getattr(dispatcher, "_engine", None), "_driver", None)
    parent_remaining = (
        parent_remaining_fn.remaining_turn_seconds()
        if parent_remaining_fn is not None
        and callable(getattr(parent_remaining_fn, "remaining_turn_seconds", None))
        else None
    )
    if parent_remaining is not None:
        timeout = min(timeout, parent_remaining)
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
        sandbox_note=(
            FULL_ACCESS_SANDBOX_DISCLAIMER
            if bool(getattr(engine, "_full_access_enabled", False))
            else LOCAL_SANDBOX_DISCLAIMER
        ),
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
    from excelmanus.engine_core.tool_result import from_payload
    return attach_sdk_calls(from_payload(data), session).model_text


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
    payload["sandbox_note"] = getattr(session, "sandbox_note", LOCAL_SANDBOX_DISCLAIMER)
    if result.success and payload["sdk_calls"]["unresolved"]:
        from excelmanus.engine_core.tool_result import error_result
        payload["process_status"] = payload.get("status", "success")
        payload.pop("status", None)
        failed = error_result(
            "脚本退出正常，但存在未恢复的 SDK 子调用失败；已提交的文件仍然保留。",
            code="SDK_SUBCALL_FAILED", fields=payload,
            remediation="检查 sdk_calls.unresolved 和 writes；不要重放已提交操作。可对未提交失败使用捕获异常的 retry(**修正参数)，或在下一轮按当前版本修复。",
        )
        result = replace(failed, ui_meta=result.ui_meta)
        payload = result.value
    from excelmanus.engine_core.execution_facts import project_publications
    return project_publications(replace(
        result,
        value=payload,
        model_text=json.dumps(payload, ensure_ascii=False, indent=2),
    ), "run_code")


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
        lines.append('参数不清楚时，先直接调用 introspect_capability(query_type="tool_detail", query="工具名.字段")，读取详情后再生成程序；不要读取 em.py 猜参数。')
    if any(tool.name == "observe_spreadsheet" for tool in tool_defs):
        lines.append("已有文件的 expected_version 取自 observe_spreadsheet 返回的 content_version 字段；核对读到的目标内容后再写入。")
    lines.append("大结果中间数据可写 scripts/temp/*.json 供后续 run_code 复用；不要为同一数据反复全量拉取。")
    lines.append("读取 `spill:` 句柄返回原始 payload：JSON 对象→dict，数组→list，其余→原始 str；按返回类型分支处理，不要假设必是 dict。")
    lines.append("捕获 SDK 异常不代表成功。对同目标且未提交的失败，可用 exc.retry(**修正参数) 重试；成功后登记恢复。已提交部分先检查 sdk_calls.writes，不重放。")
    return "\n".join(lines)


_JSON_TYPE_TO_PY: dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "array": "list",
    "null": "None",
}


# F1 冻结：这两个参数的嵌套合同不进 SDK 签名，仍走 introspect_capability。
_SPEC_CONTAINER_PROPS = frozenset({"operations", "workbook_spec"})


def _py_type_of(
    spec: dict[str, Any], *, prop_name: str = "", root: dict[str, Any] | None = None,
    _seen_refs: frozenset[str] = frozenset(),
) -> str:
    """canonical schema → Python 提示；保留所有 union/null/enum 分支。"""
    if not isinstance(spec, dict):
        return "Any"
    ref = spec.get("$ref")
    if isinstance(ref, str) and root is not None and ref not in _seen_refs:
        from excelmanus.tools.schema_walk import resolve_local_ref

        resolved = resolve_local_ref(root, ref)
        if resolved:
            return _py_type_of(
                {**resolved, **{k: v for k, v in spec.items() if k != "$ref"}},
                prop_name=prop_name, root=root, _seen_refs=_seen_refs | {ref},
            )
    values = spec.get("enum")
    if "const" in spec:
        values = [spec["const"]]
    if isinstance(values, list) and values and all(
        value is None or isinstance(value, (str, int, float, bool)) for value in values
    ):
        return "Literal[" + ", ".join(repr(value) for value in values) + "]"
    for union_key in ("anyOf", "oneOf"):
        options = spec.get(union_key)
        if isinstance(options, list) and options:
            parts = [
                _py_type_of(option, prop_name=prop_name, root=root, _seen_refs=_seen_refs)
                for option in options
            ]
            return " | ".join(dict.fromkeys(parts))
    raw = spec.get("type")
    types = raw if isinstance(raw, list) else [raw]
    parts: list[str] = []
    for item_type in types:
        base = _JSON_TYPE_TO_PY.get(str(item_type), "Any")
        items = spec.get("items")
        if item_type == "array" and isinstance(items, dict) and prop_name not in _SPEC_CONTAINER_PROPS:
            item_hint = _py_type_of(items, root=root, _seen_refs=_seen_refs)
            base = f"list[{item_hint}]"
        parts.append(base)
    return " | ".join(dict.fromkeys(parts)) or "Any"


def _schema_allows_null(
    spec: dict[str, Any], root: dict[str, Any], *, _seen_refs: frozenset[str] = frozenset(),
) -> bool:
    """仅本地判定是否需要保留 null；未知合同也保留，由宿主校验。

    这不是第二个参数校验器：oneOf 排他性和 not 等约束仍由注册表检查。
    不解析远程引用，也不让某个 MCP 的未知引用阻断全部 SDK 生成。
    """
    ref = spec.get("$ref")
    if isinstance(ref, str):
        if not ref.startswith("#/") or ref in _seen_refs:
            return True
        from excelmanus.tools.schema_walk import resolve_local_ref

        resolved = resolve_local_ref(root, ref)
        if not resolved:
            return True
        return _schema_allows_null(resolved, root, _seen_refs=_seen_refs | {ref})
    raw_type = spec.get("type")
    if raw_type is not None:
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if "null" not in types:
            return False
    if "enum" in spec and None not in spec["enum"]:
        return False
    if "const" in spec and spec["const"] is not None:
        return False
    for key in ("anyOf", "oneOf", "allOf"):
        options = spec.get(key)
        if isinstance(options, list):
            possibilities = [
                _schema_allows_null(item, root, _seen_refs=_seen_refs)
                if isinstance(item, dict) else item is not False
                for item in options
            ]
            if not (all(possibilities) if key == "allOf" else any(possibilities)):
                return False
    return True


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
        py_type = _py_type_of(spec, prop_name=str(name), root=schema)
        if name in required:
            params.append(f"{py_name}: {py_type}" if py_type != "Any" else py_name)
        else:
            default_text = repr(spec["default"]) if "default" in spec else "..."
            if py_type != "Any":
                params.append(f"{py_name}: {py_type} = {default_text}")
            else:
                params.append(f"{py_name}={default_text}")
    from excelmanus.tools.output_contracts import return_hint_for

    line = (
        f"- {_py_name(str(tool.name))}({', '.join(params)})"
        f" -> {return_hint_for(str(tool.name), tool_def=tool)}"
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
    disclaimer = str(kwargs.get("disclaimer") or LOCAL_SANDBOX_DISCLAIMER)
    parts: list[str] = [
        _SDK_PREAMBLE.replace("{disclaimer}", disclaimer),
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
    *,
    tool_def: ToolDef | None = None,
) -> dict[str, Any]:
    from excelmanus.tools.output_contracts import enforce_output_contract
    from excelmanus.engine_core.tool_result import result_value

    result = enforce_output_contract(result, tool_name, arguments or {}, tool_def=tool_def)
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
    return {"ok": True, "value": result_value(result)}


# Share the host's alias folds even when only the canonical field is exposed
# in its schema. SDK callers must not fail earlier than equivalent native calls.
from excelmanus.tools.registry import _ALIAS_FOLDS

_SDK_ALIAS_PAIRS: tuple[tuple[str, str], ...] = (*_ALIAS_FOLDS, ("max_rows", "max_lines"))


def _sdk_aliases(properties: dict[str, Any]) -> dict[str, str]:
    aliases = {}
    for alias, canonical in _SDK_ALIAS_PAIRS:
        if canonical in properties:
            aliases.setdefault(alias, canonical)
    for alias, target in list(aliases.items()):
        seen = {alias}
        while target in aliases and target not in seen:
            seen.add(target)
            target = aliases[target]
        aliases[alias] = target
    return aliases


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
    nullable_names: list[str] = []
    aliases = _sdk_aliases(properties)
    # Host validation still enforces required fields after alias resolution.
    # Python must allow path=... to supply a required file_path.
    aliased_required = bool(required & set(aliases.values()))
    for name in [*req_names, *opt_names]:
        py_name = _py_name(str(name))
        spec = properties.get(name) if isinstance(properties.get(name), dict) else {}
        nullable = _schema_allows_null(spec, schema)
        if nullable:
            nullable_names.append(str(name))
        if name in required and not aliased_required:
            params.append(py_name)
        elif name in aliases.values() and "default" in spec:
            # Let the host apply omitted defaults. Otherwise an alias such as
            # max_rows=20 falsely conflicts with max_lines' implicit 500.
            params.append(f"{py_name}=_EM_UNSET")
        elif nullable and "default" not in spec:
            params.append(f"{py_name}=_EM_UNSET")
        else:
            default = spec.get("default") if "default" in spec else None
            params.append(f"{py_name}={default!r}")
        # 保留旧的非 nullable None=省略约定；nullable 参数则必须传递 null。
        value = py_name if nullable else f"({py_name} if {py_name} is not None else _EM_UNSET)"
        arg_items.append(f"{str(name)!r}: {value}")
    aliases = _sdk_aliases(properties)
    if aliases:
        params.append("**_kw")
    signature = ", ".join(params)
    description = str(getattr(tool, "description", "") or "")
    args_literal = ", ".join(arg_items)
    call_args = f"{{{args_literal}}}"
    if aliases:
        call_args = f"_with_alias({call_args}, _kw, {aliases!r}, {tuple(nullable_names)!r})"
    fn_name = _py_name(str(tool.name))
    enum_comment = "".join(
        f"    # {line.strip()}\n" for line in _schema_enum_lines(schema)
    )
    from excelmanus.tools.output_contracts import output_schema_for, return_hint_for

    output_schema = output_schema_for(str(tool.name), tool_def=tool)
    return_type = (
        "dict" if output_schema is not None and output_schema.get("type") == "object"
        else _py_type_of(output_schema, root=output_schema) if output_schema is not None else "Any"
    )
    if output_schema is not None and output_schema.get("x-spill-result-types"):
        return_type = _py_type_of({"type": output_schema["x-spill-result-types"]})
    description += "\n返回: " + return_hint_for(str(tool.name), tool_def=tool)
    return (
        f"def {fn_name}({signature}) -> {return_type}:\n"
        f"{enum_comment}"
        f"    return _call_host({tool.name!r}, {call_args})\n"
        f"{fn_name}.__doc__ = {description!r}\n"
    )


_SDK_PREAMBLE = '''\
"""ExcelManus Code Mode SDK.

通过宿主 ToolDispatcher 调用已注册工具（不在沙盒内直接执行工具 func）。
{disclaimer}

用法::

    from em import observe_spreadsheet, apply_spreadsheet_changes, split_spreadsheet

首次用某工具前可 introspect_capability(query_type="tool_detail", query="工具名")。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Literal


def __getattr__(name):
    if name == "content_version":
        raise AttributeError(
            "em.content_version 不是 SDK 方法。先调用当前可用的 "
            "observe_spreadsheet(file_path=..., mode='overview')，"
            "从返回字典读取 content_version；核对目标内容后作为 expected_version 使用。"
        )
    raise AttributeError("module 'em' has no attribute %r" % name)


class HostToolError(Exception):
    def __init__(self, message, code="TOOL_ERROR", details=None, request=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self._request = request

    def retry(self, **corrected_arguments):
        """Retry this uncommitted call; successful recovery is host-recorded."""
        if not self._request:
            raise TypeError("此异常没有可重试的宿主调用")
        tool, arguments, request_id = self._request
        return _call_host(tool, {**arguments, **corrected_arguments}, retry_of=request_id)

    def __str__(self):
        if not self.details:
            return super().__str__()
        try:
            blob = json.dumps(self.details, ensure_ascii=False, default=str)
        except Exception:
            blob = str(self.details)
        return "%s\\n详细信息: %s" % (super().__str__(), blob)


_SEQ = 0
_EM_UNSET = object()

# _EM_INTERACTIVE_WINDOW / _EM_TOOL_TIMEOUTS 由宿主在生成时注入。
# 子调用等待 = min(父剩余, 自身窗口)；问答/审批窗口覆盖任意工具上的 Hook ASK。

def _with_alias(args, extra, aliases, nullable_names=()):
    """归一 schema 别名参数（如 sheet→sheet_name）后并入 args；未知参数报错。"""
    for key, val in extra.items():
        canon = aliases.get(key)
        if canon is None:
            raise TypeError(
                "unexpected keyword argument %r；可用参数以工具 schema 为准（introspect_capability 可查）" % key
            )
        current = args.get(canon, _EM_UNSET)
        if current is not _EM_UNSET and current != val:
            raise TypeError("参数 %r 与其别名 %r 同时给出且值不同" % (canon, key))
        args[canon] = _EM_UNSET if val is None and canon not in nullable_names else val
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


def _call_host(tool, arguments, *, retry_of=None):
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
        "arguments": {k: v for k, v in arguments.items() if v is not _EM_UNSET},
        "root_call_id": os.environ.get("EXCELMANUS_CODE_MODE_ROOT_CALL_ID") or "",
        "retry_of": retry_of,
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
                    (tool, arguments, str(seq)),
                )
            return resp.get("value")
        time.sleep(0.02)
    raise HostToolError("等待宿主工具响应超时", "BRIDGE_TIMEOUT")

'''
