"""运行时沙盒钩子：生成注入子进程的 wrapper 脚本。"""
from __future__ import annotations

from excelmanus.security.module_manifest import RAW_SOCKET_MODULE_BLOCKED_CALLS
from excelmanus.security.module_manifest import SOCKET_CONSTRUCTOR_NAMES
from excelmanus.security.module_manifest import SOCKET_MODULE_BLOCKED_CALLS


# GREEN 模式禁止导入的模块
# 说明：ctypes 已移除 — pandas/numpy 等数据处理库间接依赖 ctypes，
# 禁止会导致 GREEN tier 下 pandas 完全不可用。ctypes 的理论风险（FFI 调用）
# 在 LLM agent 场景下极低，且仍有 os.system 等多层防护兜底。
#
# 说明：subprocess/signal/multiprocessing 已从 import 封禁列表移除 —
# pandas 3.x 初始化链中 _config/localization.py 顶层 import subprocess，
# matplotlib 的后端检测也依赖 subprocess。signal/multiprocessing 被 numpy
# 等库间接使用。改为 Layer 6 函数级 monkey-patch：允许 import，但禁止
# 用户脚本直接调用 subprocess.Popen/run/call 等进程创建函数。
#
# 说明：socket 已从 import 封禁列表移除 —
# matplotlib.pyplot 初始化链中 backend_bases.py 顶层 import socket。
# 改为 Layer 6 函数级 monkey-patch：允许 import，但禁止创建 socket
# 实例（即禁止实际网络通信）。gethostname 等只读信息函数仍可用。
_GREEN_BLOCKED: tuple[str, ...] = (
    "ssl",
    "http.client", "http.server", "http.cookiejar",
    "urllib.request", "urllib.error",
    "requests", "httpx", "aiohttp",
    "ftplib", "smtplib", "imaplib", "poplib",
    "xmlrpc", "xmlrpc.client", "xmlrpc.server",
    "websocket", "websockets",
    "pty", "pexpect",
    "webbrowser", "antigravity",
)

# YELLOW 模式禁止导入的模块（子集）
# 说明：subprocess/signal/multiprocessing 已移除，理由同 GREEN。
_YELLOW_BLOCKED: tuple[str, ...] = (
    "pty", "pexpect",
)


def generate_wrapper_script(
    tier: str, workspace_root: str, *, docker_mode: bool = False,
) -> str:
    """生成对应风险等级的沙盒 wrapper Python 脚本源码。

    Args:
        tier: 代码风险等级 (GREEN/YELLOW/RED)
        workspace_root: 工作区根目录绝对路径
        docker_mode: 是否在 Docker 容器内运行。Docker RED tier
            也注入最小 filesystem guard（bench 保护 + staging 重定向）。
    """
    if tier == "RED" and not docker_mode:
        return _RED_WRAPPER_TEMPLATE
    if tier == "RED" and docker_mode:
        return _RED_FS_GUARD_TEMPLATE.format(
            workspace_root=repr(workspace_root),
        )

    blocked = _GREEN_BLOCKED if tier == "GREEN" else _YELLOW_BLOCKED
    blocked_repr = repr(blocked)
    workspace_repr = repr(workspace_root)
    socket_ctor_repr = repr(SOCKET_CONSTRUCTOR_NAMES)
    socket_blocked_calls_repr = repr(SOCKET_MODULE_BLOCKED_CALLS)
    raw_socket_blocked_calls_repr = repr(RAW_SOCKET_MODULE_BLOCKED_CALLS)

    return _SANDBOX_WRAPPER_TEMPLATE.format(
        blocked_modules=blocked_repr,
        workspace_root=workspace_repr,
        tier=repr(tier),
        socket_constructor_names=socket_ctor_repr,
        socket_module_blocked_calls=socket_blocked_calls_repr,
        raw_socket_module_blocked_calls=raw_socket_blocked_calls_repr,
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

_RED_FS_GUARD_TEMPLATE = '''\
"""ExcelManus RED 文件系统守卫包装（Docker 模式，自动生成）。

RED 层级无 import/exec/socket 限制，仅保留：
- Filesystem Guard（工作区范围 + bench CoW + staging 重定向）
- openpyxl atomic save 保护
"""
import sys
import os
import tempfile as _tmpmod
import builtins

_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = "RED"
_SYSTEM_TMPDIR = os.path.realpath(_tmpmod.gettempdir())

# ── 暂存映射（事务感知重定向）──
import json as _json_mod
_STAGING_MAP_RAW = os.environ.get("EXCELMANUS_STAGING_MAP", "{{}}")
try:
    _STAGING_MAP = _json_mod.loads(_STAGING_MAP_RAW)
    if not isinstance(_STAGING_MAP, dict):
        _STAGING_MAP = {{}}
except (ValueError, TypeError):
    _STAGING_MAP = {{}}
_STAGING_LOOKUP = {{os.path.realpath(k): os.path.realpath(v) for k, v in _STAGING_MAP.items()}}

_original_open = builtins.open

# ── 文件系统守卫 ──
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

_COW_MAPPING = {{}}

def _apply_cow(resolved):
    if resolved in _COW_MAPPING:
        return _COW_MAPPING[resolved]
    redirect_dir = os.path.join(_WORKSPACE_ROOT, "outputs", "backups")
    os.makedirs(redirect_dir, exist_ok=True)
    redirect_path = os.path.join(redirect_dir, os.path.basename(resolved))
    if os.path.exists(resolved):
        try:
            with _original_open(resolved, "rb") as src, _original_open(redirect_path, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
        except Exception:
            pass
    _COW_MAPPING[resolved] = redirect_path
    cow_log = os.environ.get("EXCELMANUS_COW_LOG")
    if cow_log:
        try:
            with _original_open(cow_log, "a", encoding="utf-8") as f:
                f.write(resolved + "\\t" + redirect_path + "\\n")
        except Exception:
            pass
    return redirect_path

def _guarded_open(file, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(file))
    if resolved in _COW_MAPPING:
        resolved = _COW_MAPPING[resolved]
        file = resolved
    if resolved in _STAGING_LOOKUP:
        resolved = _STAGING_LOOKUP[resolved]
        file = resolved
    if any(c in str(mode) for c in "wax+"):
        ws = _WORKSPACE_ROOT + os.sep
        _in_workspace = resolved.startswith(ws) or resolved == _WORKSPACE_ROOT
        if not _in_workspace:
            _tmp_prefix = _SYSTEM_TMPDIR + os.sep
            if resolved.startswith(_tmp_prefix) or resolved == _SYSTEM_TMPDIR:
                return _original_open(file, mode, *args, **kwargs)
            raise PermissionError(
                f"文件写入被安全策略禁止：路径不在工作区内 [等级: {{_TIER}}]"
            )
        for protected in _BENCH_PROTECTED_DIRS:
            protected_prefix = protected + os.sep
            if resolved.startswith(protected_prefix) or resolved == protected:
                resolved = _apply_cow(resolved)
                file = resolved
                break
    return _original_open(file, mode, *args, **kwargs)

builtins.open = _guarded_open

# ── openpyxl 保存原子写入保护 ──
def _patch_openpyxl_save():
    try:
        from openpyxl.workbook import Workbook as _Wb
    except ImportError:
        return
    _original_save = _Wb.save
    def _atomic_save(self, filename):
        import tempfile
        resolved = os.path.realpath(str(filename))
        if resolved in _STAGING_LOOKUP:
            resolved = _STAGING_LOOKUP[resolved]
            filename = resolved
        if resolved in _COW_MAPPING:
            resolved = _COW_MAPPING[resolved]
            filename = resolved
        for _p in _BENCH_PROTECTED_DIRS:
            _pp = _p + os.sep
            if resolved.startswith(_pp) or resolved == _p:
                resolved = _apply_cow(resolved)
                filename = resolved
                break
        if not os.path.exists(resolved):
            return _original_save(self, filename)
        dir_name = os.path.dirname(resolved)
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=dir_name)
        os.close(fd)
        try:
            _original_save(self, tmp_path)
            os.replace(tmp_path, resolved)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    _Wb.save = _atomic_save
_patch_openpyxl_save()

# ── 执行用户脚本 ──
if len(sys.argv) < 2:
    print("Usage: wrapper.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)

_script = sys.argv[1]
sys.argv = sys.argv[1:]

with _original_open(_script, encoding="utf-8") as _f:
    _code = _f.read()

exec(compile(_code, _script, "exec"), {{
    "__name__": "__main__",
    "__file__": _script,
    "__builtins__": __builtins__,
}})
'''

_SANDBOX_WRAPPER_TEMPLATE = '''\
"""ExcelManus 沙盒包装（自动生成）。"""
import sys
import os
import tempfile as _tmpmod
import importlib.abc
import importlib.machinery
import builtins

# ── 配置 ──
_BLOCKED_MODULES = {blocked_modules}
_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = {tier}
_SYSTEM_TMPDIR = os.path.realpath(_tmpmod.gettempdir())

# ── 暂存映射（事务感知重定向）──
import json as _json_mod
_STAGING_MAP_RAW = os.environ.get("EXCELMANUS_STAGING_MAP", "{{}}")
try:
    _STAGING_MAP = _json_mod.loads(_STAGING_MAP_RAW)
    if not isinstance(_STAGING_MAP, dict):
        _STAGING_MAP = {{}}
except (ValueError, TypeError):
    _STAGING_MAP = {{}}
_STAGING_LOOKUP = {{os.path.realpath(k): os.path.realpath(v) for k, v in _STAGING_MAP.items()}}

# ── monkey-patch 前保存原始引用 ──
_original_open = builtins.open
_real_exec = builtins.exec
_real_compile = builtins.compile

# ── Layer 1: Import Guard ──
# 先移除 sys.modules 中已缓存的被封禁模块
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
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

_COW_MAPPING = {{}}

def _apply_cow(resolved):
    if resolved in _COW_MAPPING:
        return _COW_MAPPING[resolved]
        
    redirect_dir = os.path.join(_WORKSPACE_ROOT, "outputs", "backups")
    os.makedirs(redirect_dir, exist_ok=True)
    redirect_path = os.path.join(redirect_dir, os.path.basename(resolved))
    
    # 写时复制
    if os.path.exists(resolved):
        try:
            with _original_open(resolved, "rb") as src, _original_open(redirect_path, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
        except Exception:
            pass
            
    _COW_MAPPING[resolved] = redirect_path
    
    cow_log = os.environ.get("EXCELMANUS_COW_LOG")
    if cow_log:
        try:
            with _original_open(cow_log, "a", encoding="utf-8") as f:
                f.write(resolved + "\\t" + redirect_path + "\\n")
        except Exception:
            pass
            
    return redirect_path

def _guarded_open(file, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(file))
    
    # 同一脚本内已触发 CoW 的文件，读写都重定向到副本
    if resolved in _COW_MAPPING:
        resolved = _COW_MAPPING[resolved]
        file = resolved
        
    # staging 映射重定向（读模式也重定向，确保读到最新 staged 副本）
    if resolved in _STAGING_LOOKUP:
        resolved = _STAGING_LOOKUP[resolved]
        file = resolved
        
    if any(c in str(mode) for c in "wax+"):
        # 先检查工作区内路径（走正常的 workspace + bench 保护逻辑）
        ws = _WORKSPACE_ROOT + os.sep
        _in_workspace = resolved.startswith(ws) or resolved == _WORKSPACE_ROOT
        if not _in_workspace:
            # 允许系统临时目录写入（库内部如 et_xmlfile/openpyxl 需要）
            _tmp_prefix = _SYSTEM_TMPDIR + os.sep
            if resolved.startswith(_tmp_prefix) or resolved == _SYSTEM_TMPDIR:
                return _original_open(file, mode, *args, **kwargs)
            # 其他工作区外路径禁止
            raise PermissionError(
                f"文件写入被安全策略禁止：路径不在工作区内 [等级: {{_TIER}}]"
            )
        # bench 保护目录检查 (触发 Auto CoW)
        for protected in _BENCH_PROTECTED_DIRS:
            protected_prefix = protected + os.sep
            if resolved.startswith(protected_prefix) or resolved == protected:
                resolved = _apply_cow(resolved)
                file = resolved
                break
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

# ── 第 4 层：为用户代码构建受限的 __builtins__ ──
# 不全局 patch builtins.exec/eval（会破坏 import 机制）。
# 改为向用户脚本提供受限的 __builtins__ 字典。
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

# ── Layer 5: openpyxl save 原子写入保护 ──
def _patch_openpyxl_save():
    try:
        from openpyxl.workbook import Workbook as _Wb
    except ImportError:
        return
    _original_save = _Wb.save
    def _atomic_save(self, filename):
        import tempfile
        resolved = os.path.realpath(str(filename))
        
        # staging 映射重定向（transaction 感知）
        if resolved in _STAGING_LOOKUP:
            resolved = _STAGING_LOOKUP[resolved]
            filename = resolved
        
        # 处理同一次运行中已被 CoW 的文件
        if resolved in _COW_MAPPING:
            resolved = _COW_MAPPING[resolved]
            filename = resolved
            
        # bench 保护检查 (触发 Auto CoW)
        for _p in _BENCH_PROTECTED_DIRS:
            _pp = _p + os.sep
            if resolved.startswith(_pp) or resolved == _p:
                resolved = _apply_cow(resolved)
                filename = resolved
                break
                
        if not os.path.exists(resolved):
            return _original_save(self, filename)
        dir_name = os.path.dirname(resolved)
        fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=dir_name)
        os.close(fd)
        try:
            _original_save(self, tmp_path)
            os.replace(tmp_path, resolved)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    _Wb.save = _atomic_save
_patch_openpyxl_save()

# ── 第 6 层：subprocess / socket 函数守卫 ──
# subprocess 模块允许导入（pandas/matplotlib 等库初始化链依赖），
# 但禁止用户脚本直接调用进程创建函数。
try:
    import subprocess as _subprocess_mod
    _SUBPROCESS_BLOCKED_ATTRS = ('Popen', 'run', 'call', 'check_call', 'check_output')
    def _make_subprocess_blocker(_name):
        def _blocked_fn(*_args, **_kwargs):
            raise RuntimeError(
                "subprocess." + _name + "() 被安全策略禁止 [等级: " + _TIER + "]。"
                "允许 import subprocess（库内部依赖），但禁止直接调用进程创建函数。"
            )
        return _blocked_fn
    for _attr in _SUBPROCESS_BLOCKED_ATTRS:
        if hasattr(_subprocess_mod, _attr):
            setattr(_subprocess_mod, _attr, _make_subprocess_blocker(_attr))
except ImportError:
    pass

# os 模块的进程创建函数同样需要拦截，防止 from os import execv 等绕过。
import os as _os_mod
_OS_BLOCKED_ATTRS = (
    'system', 'popen',
    'execl', 'execle', 'execlp', 'execlpe',
    'execv', 'execve', 'execvp', 'execvpe',
    'spawnl', 'spawnle', 'spawnlp', 'spawnlpe',
    'spawnv', 'spawnve', 'spawnvp', 'spawnvpe',
)
def _make_os_blocker(_name):
    def _blocked_fn(*_args, **_kwargs):
        raise RuntimeError(
            "os." + _name + "() 被安全策略禁止 [等级: " + _TIER + "]。"
            "禁止通过 os 模块创建子进程。"
        )
    return _blocked_fn
for _attr in _OS_BLOCKED_ATTRS:
    if hasattr(_os_mod, _attr):
        setattr(_os_mod, _attr, _make_os_blocker(_attr))

# socket 模块允许导入（matplotlib.pyplot 初始化链依赖），
# 但禁止创建 socket 实例（即禁止实际网络通信）。
# gethostname/getfqdn 等只读信息函数仍可用。
def _make_socket_blocker(_label):
    def _blocked(*a, **kw):
        raise RuntimeError(
            _label + " 被安全策略禁止 [等级: " + _TIER + "]。"
            "允许 import socket/_socket（库内部依赖），但禁止创建网络连接。"
        )
    return _blocked

class _BlockedSocket:
    def __init__(self, *a, **kw):
        raise RuntimeError(
            "socket.socket() 被安全策略禁止 [等级: " + _TIER + "]。"
            "允许 import socket/_socket（库内部依赖），但禁止创建网络连接。"
        )

try:
    import socket as _socket_mod
    for _ctor in {socket_constructor_names}:
        if hasattr(_socket_mod, _ctor):
            setattr(_socket_mod, _ctor, _BlockedSocket)
    for _fn in {socket_module_blocked_calls}:
        if hasattr(_socket_mod, _fn):
            setattr(_socket_mod, _fn, _make_socket_blocker("socket." + _fn + "()"))
except ImportError:
    pass

# 低层 _socket 模块同样打补丁，防止绕过 socket 模块封装。
try:
    import _socket as _raw_socket_mod
    for _ctor in {socket_constructor_names}:
        if hasattr(_raw_socket_mod, _ctor):
            setattr(_raw_socket_mod, _ctor, _BlockedSocket)
    for _fn in {raw_socket_module_blocked_calls}:
        if hasattr(_raw_socket_mod, _fn):
            setattr(_raw_socket_mod, _fn, _make_socket_blocker("_socket." + _fn + "()"))
except ImportError:
    pass

# ── 执行用户脚本 ──
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
