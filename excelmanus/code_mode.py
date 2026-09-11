"""P4 Code Mode：从 ToolDef 生成 Python SDK，经宿主文件桥调用 ToolDispatcher。"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import keyword
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from excelmanus.engine_core.tool_result import ToolError, ToolResult
from excelmanus.logger import get_logger
from excelmanus.tools.registry import ToolDef

logger = get_logger("code_mode")

DOCKER_OFF_DISCLAIMER = (
    "Docker 未启用：仅暴露宿主 SDK 绑定与受限 builtins，"
    "不宣称任意代码已被隔离。"
)
DOCKER_ON_DISCLAIMER = (
    "当前用户脚本在 Docker 容器中执行；工具副作用仍经宿主 ToolDispatcher。"
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SKIP_SDK_TOOLS = frozenset({"run_code"})


@dataclass
class SdkCallRecord:
    tool: str
    success: bool
    content_version: str | None = None


@dataclass
class CodeModeSession:
    """宿主侧 Code Mode 会话：文件桥 + 调用摘要。"""

    dispatcher: Any
    root_call_id: str
    bridge_dir: Path
    tool_defs: list[ToolDef] = field(default_factory=list)
    tool_scope: Sequence[str] | None = None
    docker_sandbox: bool = False
    call_timeout: float = 120.0
    on_event: Any = None
    _calls: list[SdkCallRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _loop: asyncio.AbstractEventLoop | None = None
    _processed: set[str] = field(default_factory=set)
    _subcall_seq: int = 0

    @property
    def sdk_path(self) -> Path:
        return self.bridge_dir / "em.py"

    def prepare(self) -> None:
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self.sdk_path.write_text(
            render_sdk_source(self.tool_defs, docker_sandbox=self.docker_sandbox),
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
        self._thread = threading.Thread(
            target=self._serve_loop,
            name="excelmanus-code-mode-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

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
        for req_path in req_files:
            name = req_path.name
            if name in self._processed:
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
        if not isinstance(arguments, dict):
            arguments = {}
        root_call_id = str(payload.get("root_call_id") or self.root_call_id or "")
        if not tool:
            self._write_resp(
                resp_path,
                {"ok": False, "error": {"code": "BAD_REQUEST", "message": "missing tool"}},
            )
            self._record(SdkCallRecord(tool="", success=False))
            return
        if tool == "run_code":
            self._write_resp(
                resp_path,
                {
                    "ok": False,
                    "error": {
                        "code": "NESTED_RUN_CODE",
                        "message": "Code Mode SDK 禁止嵌套调用 run_code",
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
        self._record(
            SdkCallRecord(tool=tool, success=success, content_version=version),
        )
        self._write_resp(resp_path, _payload_from_tool_result(result))

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
            return future.result(timeout=self.call_timeout)
        return asyncio.run(call)

    @staticmethod
    def _write_resp(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp.replace(path)


_current_session: contextvars.ContextVar[CodeModeSession | None] = contextvars.ContextVar(
    "excelmanus_code_mode_session",
    default=None,
)


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
) -> CodeModeSession:
    engine = getattr(dispatcher, "_engine", None)
    config = getattr(engine, "config", None)
    workspace = Path(str(getattr(config, "workspace_root", ".") or "."))
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", root_call_id or "run")[:80] or "run"
    tool_defs: list[ToolDef] = []
    registry = getattr(engine, "registry", None)
    getter = getattr(registry, "get_all_tools", None)
    if callable(getter):
        try:
            tool_defs = [
                tool for tool in getter()
                if getattr(tool, "name", "") not in _SKIP_SDK_TOOLS
            ]
        except Exception:
            logger.debug("Code Mode 读取 registry 工具失败", exc_info=True)
            tool_defs = []
    docker = False
    try:
        from excelmanus.tools.code_tools import _is_docker_sandbox

        docker = bool(_is_docker_sandbox())
    except Exception:
        docker = False
    return CodeModeSession(
        dispatcher=dispatcher,
        root_call_id=root_call_id,
        bridge_dir=workspace / ".tmp" / "code_mode" / safe,
        tool_defs=tool_defs,
        tool_scope=tool_scope,
        docker_sandbox=docker,
        on_event=on_event,
    )


def apply_sdk_calls_summary(result_json: str, session: CodeModeSession) -> str:
    """把 sdk_calls 摘要写入 run_code JSON；已提交写入只列出，不回滚。"""
    try:
        data = json.loads(result_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return result_json
    if not isinstance(data, dict):
        return result_json
    data["sdk_calls"] = session.summary()
    if not session.docker_sandbox:
        data["sandbox_note"] = DOCKER_OFF_DISCLAIMER
    return json.dumps(data, ensure_ascii=False, indent=2)


def render_sdk_source(
    tool_defs: list[ToolDef],
    *,
    docker_sandbox: bool | None = None,
) -> str:
    """从 ToolDef 生成可执行 Python 模块文本（不在沙盒内直接调 func）。"""
    if docker_sandbox is None:
        try:
            from excelmanus.tools.code_tools import _is_docker_sandbox

            docker_sandbox = bool(_is_docker_sandbox())
        except Exception:
            docker_sandbox = False

    disclaimer = DOCKER_ON_DISCLAIMER if docker_sandbox else DOCKER_OFF_DISCLAIMER
    parts: list[str] = [
        _SDK_PREAMBLE.format(disclaimer=disclaimer),
    ]
    exported: list[str] = []
    for tool in tool_defs:
        name = getattr(tool, "name", "") or ""
        if name in _SKIP_SDK_TOOLS:
            continue
        fn_src = _render_tool_function(tool)
        if fn_src:
            parts.append(fn_src)
            exported.append(_py_name(name))
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


def _payload_from_tool_result(result: ToolResult) -> dict[str, Any]:
    if not getattr(result, "success", False):
        code = "TOOL_ERROR"
        message = getattr(result, "model_text", None) or "tool failed"
        error = getattr(result, "error", None)
        if error is not None:
            code = str(getattr(error, "code", None) or code)
            message = str(getattr(error, "message", None) or message)
        return {"ok": False, "error": {"code": code, "message": message}}
    value = getattr(result, "value", None)
    if value is None:
        text = getattr(result, "model_text", None) or ""
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            value = text
    return {"ok": True, "value": value}


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
    req_names = [name for name in properties if name in required]
    opt_names = [name for name in properties if name not in required]
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
    signature = ", ".join(params)
    description = str(getattr(tool, "description", "") or "").replace('"""', "'''")
    args_literal = ", ".join(arg_items)
    fn_name = _py_name(str(tool.name))
    return (
        f"def {fn_name}({signature}):\n"
        f'    """{description}"""\n'
        f"    return _call_host({tool.name!r}, {{{args_literal}}})\n"
    )


_SDK_PREAMBLE = '''\
"""ExcelManus Code Mode SDK.

通过宿主 ToolDispatcher 调用已注册工具（不在沙盒内直接执行工具 func）。
{disclaimer}

用法::

    from em import inspect_spreadsheet, edit_spreadsheet, format_spreadsheet
"""
from __future__ import annotations

import json
import os
import time


class HostToolError(Exception):
    def __init__(self, message, code="TOOL_ERROR"):
        super().__init__(message)
        self.code = code


_SEQ = 0


def _call_host(tool, arguments):
    global _SEQ
    bridge = os.environ.get("EXCELMANUS_CODE_MODE_BRIDGE")
    if not bridge:
        raise HostToolError("Code Mode 宿主桥未启动", "NO_BRIDGE")
    _SEQ += 1
    seq = _SEQ
    req_path = os.path.join(bridge, "%08d.req.json" % seq)
    resp_path = os.path.join(bridge, "%08d.resp.json" % seq)
    payload = {{
        "id": seq,
        "tool": tool,
        "arguments": {{k: v for k, v in arguments.items() if v is not None}},
        "root_call_id": os.environ.get("EXCELMANUS_CODE_MODE_ROOT_CALL_ID") or "",
    }}
    tmp_path = req_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as _rf:
        json.dump(payload, _rf, ensure_ascii=False)
        _rf.flush()
        os.fsync(_rf.fileno())
    os.replace(tmp_path, req_path)
    timeout = float(os.environ.get("EXCELMANUS_CODE_MODE_TIMEOUT") or "120")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.isfile(resp_path):
            with open(resp_path, encoding="utf-8") as _vf:
                resp = json.load(_vf)
            if not resp.get("ok"):
                err = resp.get("error") or {{}}
                raise HostToolError(
                    err.get("message") or "tool failed",
                    err.get("code") or "TOOL_ERROR",
                )
            return resp.get("value")
        time.sleep(0.02)
    raise HostToolError("等待宿主工具响应超时", "BRIDGE_TIMEOUT")

'''
