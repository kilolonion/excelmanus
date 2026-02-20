"""运行时沙盒钩子：生成注入子进程的 wrapper 脚本。"""
from __future__ import annotations


# GREEN 模式禁止导入的模块
_GREEN_BLOCKED: tuple[str, ...] = (
    "subprocess", "socket", "ssl",
    "http.client", "http.server", "http.cookiejar",
    "urllib.request", "urllib.error",
    "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "imaplib", "poplib",
    "xmlrpc", "xmlrpc.client", "xmlrpc.server",
    "websocket", "websockets",
    "ctypes", "signal", "multiprocessing",
    "pty", "pexpect",
    "webbrowser", "antigravity",
)

# YELLOW 模式禁止导入的模块（子集）
_YELLOW_BLOCKED: tuple[str, ...] = (
    "subprocess", "ctypes", "signal", "multiprocessing",
    "pty", "pexpect",
)


def generate_wrapper_script(tier: str, workspace_root: str) -> str:
    """生成对应风险等级的沙盒 wrapper Python 脚本源码。"""
    if tier == "RED":
        return _RED_WRAPPER_TEMPLATE

    blocked = _GREEN_BLOCKED if tier == "GREEN" else _YELLOW_BLOCKED
    blocked_repr = repr(blocked)
    workspace_repr = repr(workspace_root)

    return _SANDBOX_WRAPPER_TEMPLATE.format(
        blocked_modules=blocked_repr,
        workspace_root=workspace_repr,
        tier=repr(tier),
    )


_RED_WRAPPER_TEMPLATE = '''\
import sys
if len(sys.argv) < 2:
    print("Usage: wrapper.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)
_script = sys.argv[1]
sys.argv = sys.argv[1:]
exec(compile(open(_script, encoding="utf-8").read(), _script, "exec"),
     {"__name__": "__main__", "__file__": _script, "__builtins__": __builtins__})
'''

_SANDBOX_WRAPPER_TEMPLATE = '''\
"""ExcelManus sandbox wrapper (auto-generated)."""
import sys
import os
import importlib.abc
import importlib.machinery
import builtins

# ── config ──
_BLOCKED_MODULES = {blocked_modules}
_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = {tier}

# ── save original refs before monkey-patch ──
_original_open = builtins.open
_real_exec = builtins.exec
_real_compile = builtins.compile

# ── Layer 1: Import Guard ──
# Remove already-cached blocked modules from sys.modules FIRST
_to_remove = []
for _name in list(sys.modules):
    for _blocked in _BLOCKED_MODULES:
        if _name == _blocked or _name.startswith(_blocked + "."):
            _to_remove.append(_name)
            break
for _name in _to_remove:
    del sys.modules[_name]

class _SandboxImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        for blocked in _BLOCKED_MODULES:
            if fullname == blocked or fullname.startswith(blocked + "."):
                raise ImportError(
                    f"模块 {{fullname}} 被安全策略禁止 [等级: {{_TIER}}]"
                )
        return None

sys.meta_path.insert(0, _SandboxImportBlocker())

# ── Layer 2: Filesystem Guard ──
def _guarded_open(file, mode="r", *args, **kwargs):
    if any(c in str(mode) for c in "wax+"):
        resolved = os.path.realpath(str(file))
        ws = _WORKSPACE_ROOT + os.sep
        if not resolved.startswith(ws) and resolved != _WORKSPACE_ROOT:
            raise PermissionError(
                f"文件写入被安全策略禁止：路径不在工作区内 [等级: {{_TIER}}]"
            )
    return _original_open(file, mode, *args, **kwargs)

builtins.open = _guarded_open

# ── Layer 3: os.system / os.popen Guard ──
if hasattr(os, "system"):
    def _b(*a, **kw):
        raise RuntimeError("os.system() 被安全策略禁止 [等级: " + _TIER + "]")
    os.system = _b
if hasattr(os, "popen"):
    def _b2(*a, **kw):
        raise RuntimeError("os.popen() 被安全策略禁止 [等级: " + _TIER + "]")
    os.popen = _b2

# ── Layer 4: Build restricted __builtins__ for user code ──
# We do NOT patch builtins.exec/eval globally (breaks import machinery).
# Instead we provide a restricted __builtins__ dict to the user script.
def _blocked_exec(*args, **kwargs):
    raise RuntimeError("exec() 被安全策略禁止 [等级: " + _TIER + "]")

def _safe_eval(*args, **kwargs):
    import ast as _ast
    if args and isinstance(args[0], str):
        try:
            return _ast.literal_eval(args[0])
        except (ValueError, SyntaxError):
            pass
    raise RuntimeError("eval() 被安全策略禁止（仅允许字面量求值）[等级: " + _TIER + "]")

_restricted_builtins = {{k: v for k, v in vars(builtins).items()}}
_restricted_builtins["exec"] = _blocked_exec
_restricted_builtins["eval"] = _safe_eval
_restricted_builtins["open"] = _guarded_open
_restricted_builtins["compile"] = _real_compile

# ── execute user script ──
if len(sys.argv) < 2:
    print("Usage: wrapper.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)

_script = sys.argv[1]
sys.argv = sys.argv[1:]

with _original_open(_script, encoding="utf-8") as _f:
    _code = _f.read()

_compiled = _real_compile(_code, _script, "exec")
_real_exec(_compiled, {{
    "__name__": "__main__",
    "__file__": _script,
    "__builtins__": _restricted_builtins,
}})
'''
