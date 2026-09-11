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
        docker_mode: 保留参数，向后兼容。RED tier 现在无论 Docker 与否
            都注入文件系统守卫（含敏感目录读取保护）。
    """
    if tier == "RED":
        return _RED_FS_GUARD_TEMPLATE.format(
            workspace_root=repr(workspace_root),
            code_mode_inject=_CODE_MODE_INJECT,
        ).replace("<<<PENDING_WRITE_RUNTIME>>>", _PENDING_WRITE_RUNTIME)

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
        code_mode_inject=_CODE_MODE_INJECT,
    ).replace("<<<PENDING_WRITE_RUNTIME>>>", _PENDING_WRITE_RUNTIME)


_CODE_MODE_INJECT = """
# ── Code Mode SDK 注入（无桥环境变量时为 no-op）──
_em_mod = None
_sdk_file = os.environ.get("EXCELMANUS_CODE_MODE_SDK")
if _sdk_file:
    import types as _types_cm
    _sdk_ns = {
        "__name__": "em",
        "__file__": _sdk_file,
        "__builtins__": globals().get("_restricted_builtins", __builtins__),
    }
    with _original_open(_sdk_file, encoding="utf-8") as _sf_cm:
        _sdk_src = _sf_cm.read()
    _sdk_exec = globals().get("_real_exec", exec)
    _sdk_exec(compile(_sdk_src, _sdk_file, "exec"), _sdk_ns)
    _em_mod = _types_cm.ModuleType("em")
    for _sk, _sv in _sdk_ns.items():
        if _sk == "__builtins__":
            continue
        setattr(_em_mod, _sk, _sv)
    _em_mod.__name__ = "em"
    _em_mod.__file__ = _sdk_file
    sys.modules["em"] = _em_mod
    sys.modules["excelmanus_sdk"] = _em_mod
"""


# Injected after format() so braces stay as Python. Subprocess must not os.replace
# user xlsx: spreadsheet saves land in .excelmanus/pending/; host Runtime publishes.
_PENDING_WRITE_RUNTIME = r'''
import json as _json_mod
_PENDING = {}
_SPREADSHEET_EXTS = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb")
_SAVE_VERSIONS = {}
_orig_os_open = os.open
_orig_os_remove = os.remove
_orig_os_unlink = os.unlink
_orig_os_replace = os.replace
_orig_os_rename = os.rename
_EXPECTED_VERSIONS_RAW = os.environ.get("EXCELMANUS_EXPECTED_VERSIONS", "{}")
try:
    _EXPECTED_VERSIONS = _json_mod.loads(_EXPECTED_VERSIONS_RAW)
    if not isinstance(_EXPECTED_VERSIONS, dict):
        _EXPECTED_VERSIONS = {}
except (ValueError, TypeError):
    _EXPECTED_VERSIONS = {}
_PENDING_RUN_ID = "".join(
    c for c in os.environ.get("EXCELMANUS_PENDING_RUN_ID", "")
    if c in "0123456789abcdefABCDEF"
)[:64]
if not _PENDING_RUN_ID:
    _PENDING_RUN_ID = "orphan"

def _rel_of(resolved):
    ws = _WORKSPACE_ROOT + os.sep
    if resolved.startswith(ws):
        return resolved[len(ws):].replace("\\", "/")
    return os.path.basename(resolved)

def _is_spreadsheet(resolved):
    return os.path.splitext(resolved)[1].lower() in _SPREADSHEET_EXTS

def _is_bench_protected(resolved):
    for protected in _BENCH_PROTECTED_DIRS:
        prefix = protected + os.sep
        if resolved.startswith(prefix) or resolved == protected:
            return True
    return False

def _pending_root():
    return os.path.join(_WORKSPACE_ROOT, ".excelmanus", "pending", _PENDING_RUN_ID)

def _is_under_pending(resolved):
    root = os.path.realpath(os.path.join(_WORKSPACE_ROOT, ".excelmanus", "pending"))
    prefix = root + os.sep
    return resolved.startswith(prefix) or resolved == root

def _pending_path(resolved):
    rel = _rel_of(resolved)
    os.makedirs(_pending_root(), exist_ok=True)
    import hashlib as _hl
    digest = _hl.sha256(rel.encode("utf-8")).hexdigest()[:16]
    base = os.path.basename(rel).replace("/", "_").replace("\\", "_")
    return os.path.join(_pending_root(), digest + "_" + base)

def _append_pending_manifest(rel, dest):
    name = os.path.basename(dest)
    rec = _json_mod.dumps({"rel": rel, "name": name}, ensure_ascii=False)
    man = os.path.join(_pending_root(), "manifest.jsonl")
    with _original_open(man, "a", encoding="utf-8") as _mf:
        _mf.write(rec + "\n")

def _prepare_open_path(resolved, mode):
    writing = any(c in str(mode) for c in "wax+")
    if writing and _is_bench_protected(resolved):
        raise PermissionError(
            "文件写入被安全策略禁止：路径位于受保护的 bench 目录内 [等级: %s]" % _TIER
        )
    if _is_under_pending(resolved):
        return resolved
    if resolved in _PENDING:
        return _PENDING[resolved]
    if writing and _is_spreadsheet(resolved):
        pending = _pending_path(resolved)
        parent = os.path.dirname(pending)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.isfile(resolved) and not os.path.exists(pending):
            with _original_open(resolved, "rb") as _sf:
                with _original_open(pending, "wb") as _df:
                    while True:
                        _chunk = _sf.read(1024 * 1024)
                        if not _chunk:
                            break
                        _df.write(_chunk)
        _PENDING[resolved] = pending
        return pending
    return resolved

def _acquire_em_lock(resolved):
    lock_path = resolved + ".em-lock"
    fh = _original_open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        fh.close()
        raise
    return fh

def _release_em_lock(fh):
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass

def _record_save_version(logical, bytes_path=None):
    try:
        import hashlib as _hl
        path = bytes_path or logical
        with _original_open(path, "rb") as _vf:
            _digest = _hl.sha256(_vf.read()).hexdigest()
        _ver = "sha256:" + _digest
        _SAVE_VERSIONS[logical] = _ver
        print("EXCELMANUS_SAVE_VERSION\t" + logical + "\t" + _ver, file=sys.stderr)
        _ver_log = os.environ.get("EXCELMANUS_SAVE_VERSIONS_LOG")
        if _ver_log:
            try:
                with _original_open(_ver_log, "a", encoding="utf-8") as _lf:
                    _lf.write(logical + "\t" + _ver + "\n")
            except Exception:
                pass
    except Exception:
        pass

def _patch_openpyxl_save():
    try:
        from openpyxl.workbook import Workbook as _Wb
    except ImportError:
        return
    _original_save = _Wb.save
    def _atomic_save(self, filename):
        import tempfile
        resolved = os.path.realpath(str(filename))
        ws = _WORKSPACE_ROOT + os.sep
        if not (
            resolved.startswith(ws)
            or resolved == _WORKSPACE_ROOT
            or _is_under_pending(resolved)
        ):
            raise PermissionError(
                "文件写入被安全策略禁止：路径不在工作区内 [等级: %s]" % _TIER
            )
        if _is_bench_protected(resolved):
            raise PermissionError(
                "文件写入被安全策略禁止：路径位于受保护的 bench 目录内 [等级: %s]" % _TIER
            )
        if _is_under_pending(resolved):
            dest = resolved
            logical = resolved
        else:
            dest = _PENDING.get(resolved) or _pending_path(resolved)
            _PENDING[resolved] = dest
            logical = resolved
        fh = _acquire_em_lock(logical)
        try:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            dir_name = parent or "."
            fd, tmp_path = tempfile.mkstemp(suffix=".xlsx", dir=dir_name)
            os.close(fd)
            try:
                _original_save(self, tmp_path)
                _orig_os_replace(tmp_path, dest)
            except BaseException:
                try:
                    _orig_os_unlink(tmp_path)
                except OSError:
                    pass
                raise
            _record_save_version(logical, dest)
            _rel = _rel_of(logical)
            try:
                _append_pending_manifest(_rel, dest)
            except Exception:
                pass
            print(
                "EXCELMANUS_PENDING_WRITE\t" + _rel + "\t" + dest,
                file=sys.stderr,
            )
        finally:
            _release_em_lock(fh)
    _Wb.save = _atomic_save
_patch_openpyxl_save()

def _flags_write(flags):
    return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))

def _deny_forbidden_read(resolved):
    for _pd in _PRODUCT_SOURCE_DIRS:
        if resolved == _pd or resolved.startswith(_pd + os.sep):
            raise PermissionError(
                "PRODUCT_SOURCE_FORBIDDEN: 禁止读取产品源码 [等级: %s]" % _TIER
            )

def _guard_write_target(path):
    resolved = os.path.realpath(str(path))
    _deny_forbidden_read(resolved)
    ws = _WORKSPACE_ROOT + os.sep
    _in_workspace = resolved.startswith(ws) or resolved == _WORKSPACE_ROOT
    if not _in_workspace:
        _tmp_prefix = _SYSTEM_TMPDIR + os.sep
        if resolved.startswith(_tmp_prefix) or resolved == _SYSTEM_TMPDIR:
            return resolved
        raise PermissionError(
            "文件写入被安全策略禁止：路径不在工作区内 [等级: %s]" % _TIER
        )
    return _prepare_open_path(resolved, "w")

def _guarded_os_open(path, flags, *args, **kwargs):
    if _flags_write(flags):
        path = _guard_write_target(path)
    else:
        resolved = os.path.realpath(str(path))
        _deny_forbidden_read(resolved)
        path = resolved
    return _orig_os_open(path, flags, *args, **kwargs)

def _guarded_os_remove(path, *args, **kwargs):
    return _orig_os_remove(_guard_write_target(path), *args, **kwargs)

def _guarded_os_unlink(path, *args, **kwargs):
    return _orig_os_unlink(_guard_write_target(path), *args, **kwargs)

def _guarded_os_replace(src, dst, *args, **kwargs):
    return _orig_os_replace(_guard_write_target(src), _guard_write_target(dst), *args, **kwargs)

def _guarded_os_rename(src, dst, *args, **kwargs):
    return _orig_os_rename(_guard_write_target(src), _guard_write_target(dst), *args, **kwargs)

os.open = _guarded_os_open
os.remove = _guarded_os_remove
os.unlink = _guarded_os_unlink
os.replace = _guarded_os_replace
os.rename = _guarded_os_rename

import shutil as _shutil_mod
_orig_copy = _shutil_mod.copy
_orig_copy2 = _shutil_mod.copy2
_orig_copyfile = _shutil_mod.copyfile
_orig_move = _shutil_mod.move
_orig_rmtree = _shutil_mod.rmtree
_orig_copytree = _shutil_mod.copytree

def _guarded_copy(src, dst, *args, **kwargs):
    return _orig_copy(src, _guard_write_target(dst), *args, **kwargs)

def _guarded_copy2(src, dst, *args, **kwargs):
    return _orig_copy2(src, _guard_write_target(dst), *args, **kwargs)

def _guarded_copyfile(src, dst, *args, **kwargs):
    return _orig_copyfile(src, _guard_write_target(dst), *args, **kwargs)

def _guarded_move(src, dst, *args, **kwargs):
    return _orig_move(_guard_write_target(src), _guard_write_target(dst), *args, **kwargs)

def _guarded_rmtree(path, *args, **kwargs):
    return _orig_rmtree(_guard_write_target(path), *args, **kwargs)

def _guarded_copytree(src, dst, *args, **kwargs):
    return _orig_copytree(src, _guard_write_target(dst), *args, **kwargs)

_shutil_mod.copy = _guarded_copy
_shutil_mod.copy2 = _guarded_copy2
_shutil_mod.copyfile = _guarded_copyfile
_shutil_mod.move = _guarded_move
_shutil_mod.rmtree = _guarded_rmtree
_shutil_mod.copytree = _guarded_copytree

import pathlib as _pathlib_mod
_OrigPath = _pathlib_mod.Path
_orig_path_open = _OrigPath.open
_orig_path_write_bytes = _OrigPath.write_bytes
_orig_path_write_text = _OrigPath.write_text
_orig_path_touch = _OrigPath.touch
_orig_path_unlink = _OrigPath.unlink
_orig_path_replace = _OrigPath.replace
_orig_path_rename = _OrigPath.rename

def _path_write_target(self):
    return _OrigPath(_guard_write_target(str(self)))

def _guarded_path_open(self, mode="r", *args, **kwargs):
    writing = any(c in str(mode) for c in "wax+")
    target = _path_write_target(self) if writing else self
    return _orig_path_open(target, mode, *args, **kwargs)

def _guarded_path_write_bytes(self, data, *args, **kwargs):
    return _orig_path_write_bytes(_path_write_target(self), data, *args, **kwargs)

def _guarded_path_write_text(self, data, *args, **kwargs):
    return _orig_path_write_text(_path_write_target(self), data, *args, **kwargs)

def _guarded_path_touch(self, *args, **kwargs):
    return _orig_path_touch(_path_write_target(self), *args, **kwargs)

def _guarded_path_unlink(self, *args, **kwargs):
    return _orig_path_unlink(_path_write_target(self), *args, **kwargs)

def _guarded_path_replace(self, target, *args, **kwargs):
    return _orig_path_replace(_path_write_target(self), _OrigPath(_guard_write_target(str(target))), *args, **kwargs)

def _guarded_path_rename(self, target, *args, **kwargs):
    return _orig_path_rename(_path_write_target(self), _OrigPath(_guard_write_target(str(target))), *args, **kwargs)

_OrigPath.open = _guarded_path_open
_OrigPath.write_bytes = _guarded_path_write_bytes
_OrigPath.write_text = _guarded_path_write_text
_OrigPath.touch = _guarded_path_touch
_OrigPath.unlink = _guarded_path_unlink
_OrigPath.replace = _guarded_path_replace
_OrigPath.rename = _guarded_path_rename
'''



_RED_FS_GUARD_TEMPLATE = '''\
"""ExcelManus RED 文件系统守卫包装（自动生成）。

RED 层级无 import/exec/socket 限制，仅保留：
- Filesystem Guard（工作区范围 + bench 拒绝写入 + 表格 pending 提交）
- 敏感目录读取保护（~/.excelmanus/ 等）
- openpyxl 保存到 .excelmanus/pending（禁止 os.replace 用户 xlsx）
"""
import sys
import os
import tempfile as _tmpmod
import builtins

_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = "RED"
_SYSTEM_TMPDIR = os.path.realpath(_tmpmod.gettempdir())

_original_open = builtins.open

# ── 敏感目录读取保护 ──
_HOME_DIR = os.path.expanduser("~")
_SENSITIVE_DIRS = [
    os.path.realpath(os.path.join(_HOME_DIR, ".excelmanus")),
]
_PRODUCT_SOURCE_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "excelmanus")),
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "tests")),
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "docs")),
]

# ── 文件系统守卫 ──
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

<<<PENDING_WRITE_RUNTIME>>>

def _guarded_open(file, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(file))
    for _pd in _PRODUCT_SOURCE_DIRS:
        if resolved == _pd or resolved.startswith(_pd + os.sep):
            raise PermissionError(
                "PRODUCT_SOURCE_FORBIDDEN: 禁止读取产品源码 [等级: {{_TIER}}]"
            )
    # ── 敏感路径读取保护 ──
    for _sd in _SENSITIVE_DIRS:
        _sd_prefix = _sd + os.sep
        if resolved.startswith(_sd_prefix) or resolved == _sd:
            raise PermissionError(
                f"文件访问被安全策略禁止：路径位于敏感目录内 [等级: {{_TIER}}]"
            )
    _basename = os.path.basename(resolved)
    if _basename == ".env":
        _ws = _WORKSPACE_ROOT + os.sep
        if not (resolved.startswith(_ws) or resolved == _WORKSPACE_ROOT):
            raise PermissionError(
                f"文件访问被安全策略禁止：禁止访问工作区外的 .env 文件 [等级: {{_TIER}}]"
            )
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
    resolved = _prepare_open_path(resolved, mode)
    return _original_open(resolved, mode, *args, **kwargs)

builtins.open = _guarded_open

{code_mode_inject}

# ── 执行用户脚本 ──
if len(sys.argv) < 2:
    print("Usage: wrapper.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)

_script = sys.argv[1]
sys.argv = sys.argv[1:]

with _original_open(_script, encoding="utf-8") as _f:
    _code = _f.read()

_user_ns = {{
    "__name__": "__main__",
    "__file__": _script,
    "__builtins__": __builtins__,
}}
if _em_mod is not None:
    _user_ns["em"] = _em_mod
exec(compile(_code, _script, "exec"), _user_ns)
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
_PRODUCT_SOURCE_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "excelmanus")),
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "tests")),
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, "docs")),
]
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

<<<PENDING_WRITE_RUNTIME>>>

def _guarded_open(file, mode="r", *args, **kwargs):
    resolved = os.path.realpath(str(file))
    for _pd in _PRODUCT_SOURCE_DIRS:
        if resolved == _pd or resolved.startswith(_pd + os.sep):
            raise PermissionError(
                "PRODUCT_SOURCE_FORBIDDEN: 禁止读取产品源码 [等级: {{_TIER}}]"
            )
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
    resolved = _prepare_open_path(resolved, mode)
    return _original_open(resolved, mode, *args, **kwargs)

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

{code_mode_inject}

# ── 执行用户脚本 ──
if len(sys.argv) < 2:
    print("Usage: wrapper.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)

_script = sys.argv[1]
sys.argv = sys.argv[1:]

with _original_open(_script, encoding="utf-8") as _f:
    _code = _f.read()

_compiled = _real_compile(_code, _script, "exec")
_user_ns = {{
    "__name__": "__main__",
    "__file__": _script,
    "__builtins__": _restricted_builtins,
}}
if _em_mod is not None:
    _user_ns["em"] = _em_mod
_real_exec(_compiled, _user_ns)
'''
