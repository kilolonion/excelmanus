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
        code_mode_inject=_CODE_MODE_INJECT,
    )


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


_RED_FS_GUARD_TEMPLATE = '''\
"""ExcelManus RED 文件系统守卫包装（自动生成）。

RED 层级无 import/exec/socket 限制，仅保留：
- Filesystem Guard（工作区范围 + bench CoW + staging 重定向）
- 敏感目录读取保护（~/.excelmanus/ 等）
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

# ── 敏感目录读取保护 ──
_HOME_DIR = os.path.expanduser("~")
_SENSITIVE_DIRS = [
    os.path.realpath(os.path.join(_HOME_DIR, ".excelmanus")),
]

# ── 文件系统守卫 ──
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

_COW_MAPPING = {{}}
# save 后的 content_version：_SAVE_VERSIONS[resolved] = "sha256:" + hex
# 不导入宿主 workbook_commit。可选 EXCELMANUS_EXPECTED_VERSIONS JSON
# （realpath 或工作区相对路径 → sha256:...）在覆盖已有文件前做哈希比较。
# 宿主 code_tools 只从 EXCELMANUS_COW_LOG 解析两列 path 映射，不会把
# content_version 提升进 run_code JSON；版本经 stderr 结构化行过重。
_SAVE_VERSIONS = {{}}
_EXPECTED_VERSIONS_RAW = os.environ.get("EXCELMANUS_EXPECTED_VERSIONS", "{{}}")
try:
    _EXPECTED_VERSIONS = _json_mod.loads(_EXPECTED_VERSIONS_RAW)
    if not isinstance(_EXPECTED_VERSIONS, dict):
        _EXPECTED_VERSIONS = {{}}
except (ValueError, TypeError):
    _EXPECTED_VERSIONS = {{}}

def _lookup_expected_version(resolved):
    if resolved in _EXPECTED_VERSIONS:
        return _EXPECTED_VERSIONS[resolved]
    ws = _WORKSPACE_ROOT + os.sep
    if resolved.startswith(ws):
        rel = resolved[len(ws):].replace("\\\\", "/")
        if rel in _EXPECTED_VERSIONS:
            return _EXPECTED_VERSIONS[rel]
    return None

def _remember_expected_after_save(resolved):
    ver = _SAVE_VERSIONS.get(resolved)
    if not ver:
        return
    _EXPECTED_VERSIONS[resolved] = ver
    ws = _WORKSPACE_ROOT + os.sep
    if resolved.startswith(ws):
        rel = resolved[len(ws):].replace("\\\\", "/")
        _EXPECTED_VERSIONS[rel] = ver

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

def _check_expected_version(resolved):
    expected = _lookup_expected_version(resolved)
    if not expected or not os.path.exists(resolved):
        return
    import hashlib as _hl
    with _original_open(resolved, "rb") as _vf:
        current = "sha256:" + _hl.sha256(_vf.read()).hexdigest()
    if current != expected:
        raise RuntimeError(
            "VERSION_CONFLICT: expected_version mismatch for " + resolved
        )

def _record_save_version(resolved):
    try:
        import hashlib as _hl
        with _original_open(resolved, "rb") as _vf:
            _digest = _hl.sha256(_vf.read()).hexdigest()
        _ver = "sha256:" + _digest
        _SAVE_VERSIONS[resolved] = _ver
        print("EXCELMANUS_SAVE_VERSION\\t" + resolved + "\\t" + _ver, file=sys.stderr)
        _ver_log = os.environ.get("EXCELMANUS_SAVE_VERSIONS_LOG")
        if _ver_log:
            try:
                with _original_open(_ver_log, "a", encoding="utf-8") as _lf:
                    _lf.write(resolved + "\\t" + _ver + "\\n")
            except Exception:
                pass
    except Exception:
        pass

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
        for protected in _BENCH_PROTECTED_DIRS:
            protected_prefix = protected + os.sep
            if resolved.startswith(protected_prefix) or resolved == protected:
                resolved = _apply_cow(resolved)
                file = resolved
                break
    return _original_open(file, mode, *args, **kwargs)

builtins.open = _guarded_open

# ── openpyxl 保存原子写入保护 ──
# 覆盖已有文件前按 EXCELMANUS_EXPECTED_VERSIONS 比较哈希；不走宿主 commit 模块。
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
        fh = _acquire_em_lock(resolved)
        try:
            _check_expected_version(resolved)
            if not os.path.exists(resolved):
                _ret = _original_save(self, filename)
                _record_save_version(resolved)
                _remember_expected_after_save(resolved)
                return _ret
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
            _record_save_version(resolved)
            _remember_expected_after_save(resolved)
        finally:
            _release_em_lock(fh)
    _Wb.save = _atomic_save
_patch_openpyxl_save()

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
# save 后的 content_version：_SAVE_VERSIONS[resolved] = "sha256:" + hex
# 不导入宿主 workbook_commit。可选 EXCELMANUS_EXPECTED_VERSIONS JSON
# （realpath 或工作区相对路径 → sha256:...）在覆盖已有文件前做哈希比较。
# 宿主 code_tools 只从 EXCELMANUS_COW_LOG 解析两列 path 映射，不会把
# content_version 提升进 run_code JSON；版本经 stderr 结构化行过重。
_SAVE_VERSIONS = {{}}
_EXPECTED_VERSIONS_RAW = os.environ.get("EXCELMANUS_EXPECTED_VERSIONS", "{{}}")
try:
    _EXPECTED_VERSIONS = _json_mod.loads(_EXPECTED_VERSIONS_RAW)
    if not isinstance(_EXPECTED_VERSIONS, dict):
        _EXPECTED_VERSIONS = {{}}
except (ValueError, TypeError):
    _EXPECTED_VERSIONS = {{}}

def _lookup_expected_version(resolved):
    if resolved in _EXPECTED_VERSIONS:
        return _EXPECTED_VERSIONS[resolved]
    ws = _WORKSPACE_ROOT + os.sep
    if resolved.startswith(ws):
        rel = resolved[len(ws):].replace("\\\\", "/")
        if rel in _EXPECTED_VERSIONS:
            return _EXPECTED_VERSIONS[rel]
    return None

def _remember_expected_after_save(resolved):
    ver = _SAVE_VERSIONS.get(resolved)
    if not ver:
        return
    _EXPECTED_VERSIONS[resolved] = ver
    ws = _WORKSPACE_ROOT + os.sep
    if resolved.startswith(ws):
        rel = resolved[len(ws):].replace("\\\\", "/")
        _EXPECTED_VERSIONS[rel] = ver

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

def _check_expected_version(resolved):
    expected = _lookup_expected_version(resolved)
    if not expected or not os.path.exists(resolved):
        return
    import hashlib as _hl
    with _original_open(resolved, "rb") as _vf:
        current = "sha256:" + _hl.sha256(_vf.read()).hexdigest()
    if current != expected:
        raise RuntimeError(
            "VERSION_CONFLICT: expected_version mismatch for " + resolved
        )

def _record_save_version(resolved):
    try:
        import hashlib as _hl
        with _original_open(resolved, "rb") as _vf:
            _digest = _hl.sha256(_vf.read()).hexdigest()
        _ver = "sha256:" + _digest
        _SAVE_VERSIONS[resolved] = _ver
        print("EXCELMANUS_SAVE_VERSION\\t" + resolved + "\\t" + _ver, file=sys.stderr)
        _ver_log = os.environ.get("EXCELMANUS_SAVE_VERSIONS_LOG")
        if _ver_log:
            try:
                with _original_open(_ver_log, "a", encoding="utf-8") as _lf:
                    _lf.write(resolved + "\\t" + _ver + "\\n")
            except Exception:
                pass
    except Exception:
        pass

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
# 覆盖已有文件前按 EXCELMANUS_EXPECTED_VERSIONS 比较哈希；不走 workbook_commit。
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
                
        fh = _acquire_em_lock(resolved)
        try:
            _check_expected_version(resolved)
            if not os.path.exists(resolved):
                _ret = _original_save(self, filename)
                _record_save_version(resolved)
                _remember_expected_after_save(resolved)
                return _ret
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
            _record_save_version(resolved)
            _remember_expected_after_save(resolved)
        finally:
            _release_em_lock(fh)
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
