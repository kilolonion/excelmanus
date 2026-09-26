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
    tier: str,
    workspace_root: str,
    *,
    allow_external_files: bool = False,
) -> str:
    """生成对应风险等级的沙盒 wrapper Python 脚本源码。

    Args:
        tier: 代码风险等级 (GREEN/YELLOW/RED)
        workspace_root: 工作区根目录绝对路径
    """
    from excelmanus.security.source_isolation import protected_source_paths

    source_paths = repr(protected_source_paths(workspace_root))
    if tier == "RED":
        return _RED_FS_GUARD_TEMPLATE.format(
            workspace_root=repr(workspace_root),
            allow_external_files=repr(bool(allow_external_files)),
            code_mode_inject=_CODE_MODE_INJECT,
            utf8_stdio=_UTF8_STDIO,
            product_source_paths=source_paths,
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
        allow_external_files=repr(False),
        tier=repr(tier),
        socket_constructor_names=socket_ctor_repr,
        socket_module_blocked_calls=socket_blocked_calls_repr,
        raw_socket_module_blocked_calls=raw_socket_blocked_calls_repr,
        code_mode_inject=_CODE_MODE_INJECT,
        utf8_stdio=_UTF8_STDIO,
        product_source_paths=source_paths,
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
# user files: workspace writes land in .excelmanus/pending/; host Runtime publishes.
_PENDING_WRITE_RUNTIME = r'''
import json as _json_mod
_PENDING = {}
_SPREADSHEET_EXTS = (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls", ".xlsb")
_WORKBOOK_SAVE_EXTS = (".xlsx", ".xlsm", ".xltx", ".xltm")
_SAVE_VERSIONS = {}
_orig_os_open = os.open
_orig_os_remove = os.remove
_orig_os_unlink = os.unlink
_orig_os_replace = os.replace
_orig_os_rename = os.rename
_orig_os_stat = os.stat
_orig_os_lstat = os.lstat if hasattr(os, "lstat") else None


def _safe_realpath(path):
    path = str(path).replace("\\", "/")
    # posixpath.realpath 内部会调 os.lstat；当 os.lstat 被守卫后，
    # 直接调 realpath 会与本守卫互递归。这里临时还原原实现。
    if _orig_os_lstat is not None and getattr(os, "lstat", None) is not _orig_os_lstat:
        _cur = os.lstat
        os.lstat = _orig_os_lstat
        try:
            return os.path.realpath(str(path))
        finally:
            os.lstat = _cur
    return os.path.realpath(str(path))
_PENDING_RUN_ID = "".join(
    c for c in os.environ.get("EXCELMANUS_PENDING_RUN_ID", "")
    if c in "0123456789abcdefABCDEF"
)[:64]
if not _PENDING_RUN_ID:
    _PENDING_RUN_ID = "orphan"
_PENDING_ROOT = _safe_realpath(
    os.path.join(_WORKSPACE_ROOT, ".excelmanus", "pending", _PENDING_RUN_ID)
)
_PENDING_TREE_ROOT = _safe_realpath(
    os.path.join(_WORKSPACE_ROOT, ".excelmanus", "pending")
)
_EXCELMANUS_ROOT = _safe_realpath(
    os.path.join(_WORKSPACE_ROOT, ".excelmanus")
)
_PENDING_DIR_ENV = os.environ.get("EXCELMANUS_PENDING_DIR", "")
if _PENDING_DIR_ENV:
    _got_pending = _safe_realpath(_PENDING_DIR_ENV)
    if _got_pending == _PENDING_ROOT:
        pass

def _path_is_inside(root, resolved):
    root = _safe_realpath(str(root))
    try:
        resolved = _safe_realpath(str(resolved))
    except OSError:
        resolved = os.path.normpath(str(resolved))
    try:
        return os.path.commonpath([os.path.normcase(root), os.path.normcase(resolved)]) == os.path.normcase(root)
    except ValueError:
        return False

def _rel_of(resolved):
    ws = _safe_realpath(_WORKSPACE_ROOT)
    try:
        resolved = _safe_realpath(str(resolved))
    except OSError:
        resolved = os.path.normpath(str(resolved))
    if _path_is_inside(ws, resolved):
        rel = os.path.relpath(resolved, ws).replace("\\", "/")
        return "" if rel == "." else rel
    raise PermissionError(
        "文件写入被安全策略禁止：路径不在工作区内 [等级: %s]" % _TIER
    )

def _is_spreadsheet(resolved):
    return os.path.splitext(resolved)[1].lower() in _SPREADSHEET_EXTS

def _is_workbook_file(resolved):
    return os.path.splitext(resolved)[1].lower() in _WORKBOOK_SAVE_EXTS

def _is_file_like(obj):
    return hasattr(obj, "write") and not isinstance(obj, (str, bytes, bytearray))

_XLSX_BYPASS_MSG = (
    "工作区表格禁止直接保存：%s 生成或覆盖工作区内的 xlsx 被安全策略拒绝。"
)
_EM_WRITER_TOOL = "apply_spreadsheet_changes"
_EM_BOOTSTRAP_TOOLS = ("convert_spreadsheet", "query_spreadsheet", "split_spreadsheet")

def _em_api_has(name):
    """本次 run_code 的 em API 是否真的提供该函数；无 SDK 时返回 None。"""
    module = globals().get("_em_mod")
    if module is None:
        return None
    return callable(getattr(module, name, None))

def _scan_table_dir(path, depth, found, recursive):
    """目录列举用补丁前保存的原始实现，避免与扫描期的元数据守卫互相干扰。"""
    lister = globals().get("_orig_listdir") or os.listdir
    try:
        names = lister(path)
    except OSError:
        return
    for name in names:
        full = os.path.join(path, name)
        try:
            if os.path.isdir(full):
                if recursive and depth < 6:
                    _scan_table_dir(full, depth + 1, found, recursive)
                continue
            if not os.path.isfile(full):
                continue
        except OSError:
            continue
        suffix = os.path.splitext(name)[1].lower()
        if suffix in _SPREADSHEET_EXTS:
            found.add("xlsx")
        elif suffix == ".csv":
            found.add("csv")

def _scan_workspace_table_kinds():
    """工作区顶层 + uploads/** + outputs/** 的表格族（与宿主目录扫描同口径）。"""
    found = set()
    _scan_table_dir(_WORKSPACE_ROOT, 0, found, False)
    _scan_table_dir(os.path.join(_WORKSPACE_ROOT, "uploads"), 0, found, True)
    _scan_table_dir(os.path.join(_WORKSPACE_ROOT, "outputs"), 0, found, True)
    return found

def _workbook_bypass_message(label="save"):
    """按真实可用 API 生成拒绝文案：只推荐本次 em 里确实存在的替代工具。"""
    label = str(label or "save")
    kinds = _scan_workspace_table_kinds()
    csv_only = "csv" in kinds and "xlsx" not in kinds
    has_sdk = globals().get("_em_mod") is not None
    usable = [name for name in _EM_BOOTSTRAP_TOOLS if _em_api_has(name)]
    parts = [_XLSX_BYPASS_MSG % label]
    if _em_api_has(_EM_WRITER_TOOL):
        parts.append("请用 em." + _EM_WRITER_TOOL + " 提交工作簿变更（新建用 workbook_spec）。")
        if "split_spreadsheet" in usable:
            parts.append("按列拆分可用 em.split_spreadsheet。")
        return "".join(parts) + " [等级: " + _TIER + "]"

    if not has_sdk:
        parts.append("本次 run_code 未注入 em SDK，em." + _EM_WRITER_TOOL + " 也不可用。")
    else:
        parts.append("em." + _EM_WRITER_TOOL + " 不在本次 em API 中。")
    if csv_only:
        parts.append(
            "工作区只有 CSV、尚无 xlsx：工作簿写工具被 csv-only profile 门控（不是被删除）。"
        )
    elif has_sdk:
        parts.append("它被当前 mode/profile 或会话授权门控（不是被删除）。")

    if csv_only:
        parts.append(
            "确定性解锁：先用仍可用的表格工具写出第一个 xlsx"
            "（必须落在 outputs/ 或工作区顶层，才会被目录扫描到）"
        )
        if "convert_spreadsheet" in usable:
            parts.append(
                "，例如 em.convert_spreadsheet(file_path='uploads/<数据>.csv', "
                "output_path='outputs/<结果>.xlsx', mode='preserve')"
            )
        if "query_spreadsheet" in usable:
            parts.append(
                " 或 em.query_spreadsheet(sources=[{'file_path': 'uploads/<数据>.csv'}], "
                "sql='SELECT * FROM data1', output_path='outputs/<结果>.xlsx')"
            )
        parts.append("；出现 xlsx 后下一轮目录会重新推导并解锁工作簿写工具。")
    elif usable:
        parts.append("当前可用：" + "、".join("em." + name for name in usable) + "。")

    parts.append("写工具是被门控还是不存在，可用 introspect_capability 确认。")
    return "".join(parts) + " [等级: " + _TIER + "]"

def _deny_workbook_bypass(path, label="save"):
    if path is None or _is_file_like(path):
        return
    resolved = _safe_realpath(str(path))
    if _is_workbook_file(resolved) and _path_is_inside(_WORKSPACE_ROOT, resolved):
        raise PermissionError(_workbook_bypass_message(label))

def _is_sandbox_ephemeral(resolved):
    tmp = _safe_realpath(os.path.join(_WORKSPACE_ROOT, ".tmp"))
    scripts_temp = _safe_realpath(os.path.join(_WORKSPACE_ROOT, "scripts", "temp"))
    if _path_is_inside(tmp, resolved) or resolved == tmp:
        return True
    if _path_is_inside(scripts_temp, resolved) or resolved == scripts_temp:
        return True
    base = os.path.basename(resolved)
    if base.endswith(".pyc") or base == "__pycache__":
        return True
    parent = os.path.basename(os.path.dirname(resolved))
    if parent == "__pycache__":
        return True
    return False

def _should_pending_write(resolved):
    if _FULL_ACCESS_FILES:
        return False
    if _is_under_pending(resolved):
        return False
    if _is_sandbox_ephemeral(resolved):
        return False
    if not _path_is_inside(_WORKSPACE_ROOT, resolved):
        return False
    return True

def _is_bench_protected(resolved):
    for protected in _BENCH_PROTECTED_DIRS:
        if _path_is_inside(protected, resolved):
            return True
    return False

def _pending_root():
    return _PENDING_ROOT

def _is_under_pending(resolved):
    return _path_is_inside(_PENDING_ROOT, resolved)

def _is_under_foreign_pending(resolved):
    if not _path_is_inside(_PENDING_TREE_ROOT, resolved):
        return False
    return not _is_under_pending(resolved)

def _is_excelmanus_write_forbidden(resolved):
    if not _path_is_inside(_EXCELMANUS_ROOT, resolved):
        return False
    return not _is_under_pending(resolved)

def _pending_dest(resolved):
    """pending 落点的确定性计算（纯函数，无副作用）。"""
    rel = _rel_of(resolved)
    import hashlib as _hl
    digest = _hl.sha256(rel.encode("utf-8")).hexdigest()[:16]
    base = os.path.basename(rel).replace("/", "_").replace("\\", "_")
    return os.path.join(_pending_root(), digest + "_" + base)

def _pending_path(resolved):
    os.makedirs(_pending_root(), exist_ok=True)
    return _pending_dest(resolved)

def _append_pending_manifest(rel, dest):
    name = os.path.basename(dest)
    rec = _json_mod.dumps({"rel": rel, "name": name}, ensure_ascii=False)
    man = os.path.join(_pending_root(), "manifest.jsonl")
    with _original_open(man, "a", encoding="utf-8") as _mf:
        _mf.write(rec + "\n")

def _prepare_open_path(resolved, mode):
    writing = any(c in str(mode) for c in "wax+")
    if _is_under_foreign_pending(resolved):
        raise PermissionError(
            "PENDING_ISOLATION: 禁止访问其他 run 的 pending [等级: %s]" % _TIER
        )
    if writing and _is_excelmanus_write_forbidden(resolved):
        raise PermissionError(
            "文件写入被安全策略禁止：保留目录 [.excelmanus] [等级: %s]" % _TIER
        )
    if writing and _is_bench_protected(resolved):
        raise PermissionError(
            "文件写入被安全策略禁止：路径位于受保护的 bench 目录内 [等级: %s]" % _TIER
        )
    if writing:
        _deny_workbook_bypass(resolved, "open")
    if _is_under_pending(resolved):
        return resolved
    if resolved in _PENDING:
        return _PENDING[resolved]
    if writing and _should_pending_write(resolved):
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
        try:
            _append_pending_manifest(_rel_of(resolved), pending)
        except Exception:
            pass
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
        if _is_file_like(filename):
            return _original_save(self, filename)
        _deny_workbook_bypass(filename, "Workbook.save")
        import tempfile
        resolved = _safe_realpath(str(filename))
        if not (_path_is_inside(_WORKSPACE_ROOT, resolved) or _is_under_pending(resolved)):
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

def _patch_pandas_excel():
    try:
        import pandas as _pd
    except ImportError:
        return
    _orig_to_excel = _pd.DataFrame.to_excel
    def _guarded_to_excel(self, excel_writer, *args, **kwargs):
        target = excel_writer
        if not _is_file_like(target):
            path = getattr(target, "path", None) or getattr(target, "_path", None) or target
            _deny_workbook_bypass(path, "DataFrame.to_excel")
        return _orig_to_excel(self, excel_writer, *args, **kwargs)
    _pd.DataFrame.to_excel = _guarded_to_excel
    try:
        from pandas.io.excel import ExcelWriter as _EW
    except ImportError:
        return
    _orig_ew_init = _EW.__init__
    def _guarded_ew_init(self, path, *args, **kwargs):
        _deny_workbook_bypass(path, "ExcelWriter")
        return _orig_ew_init(self, path, *args, **kwargs)
    _EW.__init__ = _guarded_ew_init
_patch_pandas_excel()

def _flags_write(flags):
    return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))

# ── 数据路径规则（与宿主 FileAccessGuard 同口径）──
# 读侧：工作区内拒绝保留/敏感/产品源；工作区外仅放行解释器/依赖库与字体等
# 运行环境文件，用户数据一律拒绝。元数据探查（stat/exists/listdir）对被拒
# 路径表现为"不存在"，不泄露内部结构；内容读取（open）则抛 PermissionError。
_SENSITIVE_BASENAMES = (
    ".secret_key", ".env", "config.env", "excelmanus.db", "installations.json",
)
_RESERVED_FIRST_SEGMENTS = (".excelmanus", ".versions")
_RESERVED_PREFIXES = (
    ".excelmanus", ".versions",
    "outputs/backups", "outputs/.versions", "outputs/audits",
)


def _compute_env_read_roots():
    roots = []
    for _attr in ("base_prefix", "prefix", "exec_prefix", "base_exec_prefix"):
        _v = getattr(sys, _attr, "")
        if _v:
            roots.append(_v)
    try:
        import sysconfig as _sc
        for _key in (
            "stdlib", "platstdlib", "purelib", "platlib",
            "include", "platinclude", "scripts", "data",
        ):
            try:
                _v = _sc.get_path(_key)
                if _v:
                    roots.append(_v)
            except Exception:
                pass
    except Exception:
        pass
    try:
        import site as _site_mod
        try:
            roots.extend(_site_mod.getsitepackages() or [])
        except Exception:
            pass
        try:
            roots.append(_site_mod.getusersitepackages())
        except Exception:
            pass
    except Exception:
        pass
    _home = os.path.expanduser("~")
    roots.extend([
        # 渲染字体与 matplotlib 缓存/配置：图表渲染需要，非工作区数据。
        os.path.join(os.sep, "System", "Library", "Fonts"),
        os.path.join(os.sep, "Library", "Fonts"),
        os.path.join(os.sep, "usr", "share", "fonts"),
        os.path.join(os.sep, "usr", "local", "share", "fonts"),
        os.path.join(_home, "Library", "Fonts"),
        os.path.join(_home, ".local", "share", "fonts"),
        os.path.join(_home, ".fonts"),
        os.path.join(_home, ".matplotlib"),
        os.path.join(_home, ".cache", "matplotlib"),
        os.path.join(_home, ".cache", "fontconfig"),
        os.path.join(_home, "Library", "Caches", "matplotlib"),
    ])
    seen = set()
    out = []
    for _r in roots:
        try:
            _rp = _safe_realpath(_r)
        except Exception:
            continue
        if _rp not in seen:
            seen.add(_rp)
            out.append(_rp)
    return out


_ENV_READ_ROOTS = tuple(_compute_env_read_roots())
_DEV_ROOT = os.path.join(os.sep, "dev") + os.sep


def _env_read_allowed(resolved):
    if resolved.startswith(_DEV_ROOT):
        return True
    for _root in _ENV_READ_ROOTS:
        if resolved == _root or resolved.startswith(_root + os.sep):
            return True
    return False


def _workspace_rel(resolved):
    if _path_is_inside(_WORKSPACE_ROOT, resolved):
        return _rel_of(resolved)
    return None


def _is_reserved_data_path(resolved):
    if _is_under_pending(resolved):
        return False
    rel = _workspace_rel(resolved)
    if not rel:
        return False
    if rel.split("/", 1)[0] in _RESERVED_FIRST_SEGMENTS:
        return True
    for _p in _RESERVED_PREFIXES:
        if rel == _p or rel.startswith(_p + "/"):
            return True
    return False


def _is_sensitive_data_path(resolved):
    return os.path.basename(resolved) in _SENSITIVE_BASENAMES


def _deny_protected_target(resolved):
    """读写两侧共用的拒绝规则（不含"区外数据读取"，写侧区外另有 tmpdir 放行）。"""
    for _pd in _PRODUCT_SOURCE_DIRS:
        if _path_is_inside(_pd, resolved):
            raise PermissionError(
                "PRODUCT_SOURCE_FORBIDDEN: 禁止读取产品源码 [等级: %s]" % _TIER
            )
    if _is_under_foreign_pending(resolved):
        raise PermissionError(
            "PENDING_ISOLATION: 禁止访问其他 run 的 pending [等级: %s]" % _TIER
        )
    if _is_sensitive_data_path(resolved):
        raise PermissionError(
            "SENSITIVE_FILE: 禁止访问敏感文件 %s [等级: %s]"
            % (os.path.basename(resolved), _TIER)
        )
    if _is_reserved_data_path(resolved):
        raise PermissionError(
            "RESERVED_NAMESPACE: 禁止访问保留命名空间"
            "（.excelmanus/.versions/outputs 内部工件）[等级: %s]" % _TIER
        )


def _deny_forbidden_read(resolved):
    _deny_protected_target(resolved)
    if (
        not _FULL_ACCESS_FILES
        and not _path_is_inside(_WORKSPACE_ROOT, resolved)
        and not _env_read_allowed(resolved)
    ):
        raise PermissionError(
            "PATH_OUTSIDE_WORKSPACE: 数据文件必须在工作区内；"
            "工作区外仅放行解释器与依赖库文件 [等级: %s]" % _TIER
        )


def _metadata_hidden(resolved):
    """元数据探查视角的不可见判定：被拒路径表现为不存在。"""
    if _FULL_ACCESS_FILES:
        return False
    if _is_under_pending(resolved) or resolved == _PENDING_TREE_ROOT:
        return False
    if _is_under_foreign_pending(resolved):
        # 不隐藏：stat/exists 继续走 _resolve_pending_read 抛 PermissionError；
        # listdir 对 pending 根的裁剪另有 _PENDING_TREE_ROOT 分支处理。
        return False
    if _path_is_inside(_SYSTEM_TMPDIR, resolved):
        # 写侧放行系统临时目录；存在性探查保持一致可见，内容读取仍被 open 拒。
        return False
    if _is_sensitive_data_path(resolved) or _is_reserved_data_path(resolved):
        return True
    for _pd in _PRODUCT_SOURCE_DIRS:
        if _path_is_inside(_pd, resolved):
            return True
    if not _path_is_inside(_WORKSPACE_ROOT, resolved):
        return not _env_read_allowed(resolved)
    return False

def _guard_write_target(path):
    resolved = _safe_realpath(str(path))
    if _FULL_ACCESS_FILES:
        return resolved
    _deny_protected_target(resolved)
    if _is_excelmanus_write_forbidden(resolved):
        raise PermissionError(
            "文件写入被安全策略禁止：保留目录 [.excelmanus] [等级: %s]" % _TIER
        )
    if not _path_is_inside(_WORKSPACE_ROOT, resolved):
        if _path_is_inside(_SYSTEM_TMPDIR, resolved):
            return resolved
        raise PermissionError(
            "文件写入被安全策略禁止：路径不在工作区内 [等级: %s]" % _TIER
        )
    return _prepare_open_path(resolved, "w")

def _guarded_os_open(path, flags, *args, **kwargs):
    resolved = _safe_realpath(str(path))
    if _flags_write(flags):
        path = _guard_write_target(path)
    else:
        _deny_forbidden_read(resolved)
        path = _prepare_open_path(resolved, "r")
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

def _blocked_symlink(*_a, **_kw):
    raise PermissionError("os.symlink/link 被安全策略禁止 [等级: %s]" % _TIER)
os.symlink = _blocked_symlink
if hasattr(os, "link"):
    os.link = _blocked_symlink

def _mkdir_allowed(dest):
    dest = _safe_realpath(str(dest))
    if dest in (_EXCELMANUS_ROOT, _PENDING_TREE_ROOT, _PENDING_ROOT):
        return True
    if _path_is_inside(_PENDING_ROOT, dest):
        return True
    if _is_reserved_data_path(dest):
        return False
    return True

_orig_mkdir = os.mkdir
def _guarded_mkdir(path, *args, **kwargs):
    parent = _safe_realpath(os.path.dirname(os.path.abspath(str(path))))
    dest = os.path.join(parent, os.path.basename(str(path)))
    if not _mkdir_allowed(dest):
        raise PermissionError(
            "文件写入被安全策略禁止：保留目录 [.excelmanus] [等级: %s]" % _TIER
        )
    return _orig_mkdir(path, *args, **kwargs)
os.mkdir = _guarded_mkdir

def _pending_children(resolved_dir):
    """resolved_dir 的直接子项中的 pending 投影。

    返回 {子项名: (logical_path, backing)}：backing 为 pending 副本路径；
    虚拟中间目录（仅 pending 后代、无对应 pending 文件）backing 为 None。
    """
    prefix = resolved_dir + os.sep
    children = {}
    for logical, pend in _PENDING.items():
        if not logical.startswith(prefix):
            continue
        rest = logical[len(prefix):]
        head, sep, tail = rest.partition(os.sep)
        if sep:
            children.setdefault(head, (os.path.join(resolved_dir, head), None))
        else:
            children[head] = (logical, pend)
    return children


class _PendingDirEntry:
    """os.scandir 兼容条目：把 pending 副本/虚拟目录投影回 logical 名称。"""

    __slots__ = ("name", "path", "_backing")

    def __init__(self, logical, backing):
        self.name = os.path.basename(logical)
        self.path = logical
        self._backing = backing

    def is_dir(self, *, follow_symlinks=True):
        return os.path.isdir(self._backing)

    def is_file(self, *, follow_symlinks=True):
        return os.path.isfile(self._backing)

    def is_symlink(self):
        return False

    def is_junction(self):
        return False

    def stat(self, *, follow_symlinks=True):
        return _orig_os_stat(self._backing)

    def inode(self):
        return _orig_os_stat(self._backing).st_ino


def _merge_pending_entries(resolved, names):
    """把 resolved 目录下的 pending 子项名并入真实列出的名字。"""
    children = _pending_children(resolved)
    if not children:
        return names
    merged = list(names)
    existing = {os.path.normcase(n) for n in merged}
    for name in children:
        if os.path.normcase(name) not in existing:
            merged.append(name)
    return merged


def _is_internal_artifact(name):
    """工作簿建议锁残留 `<file>.em-lock` 等内部工件——枚举层对模型不可见。

    与 file_tools 的 `*.em-lock` 默认排除同口径；只影响枚举结果，
    单路径访问（exists/stat/open）不拦截。
    """
    return str(name).endswith(".em-lock")


class _MergedScandir:
    """os.scandir 兼容迭代器：真实条目之后接续 pending 投影条目。

    os.scandir 结果是带 close() 与上下文管理协议的迭代器，
    用 list 顶替会破坏 `with os.scandir(...)` 等惯用法。
    工作区内过滤 .em-lock 内部工件。
    """

    def __init__(self, it, extras, *, hide_artifacts=True):
        self._it = it
        self._extras = extras
        self._seen = set()
        self._hide = hide_artifacts

    def __iter__(self):
        return self

    def __next__(self):
        while self._it is not None:
            try:
                entry = next(self._it)
            except StopIteration:
                self._it = None
                break
            if self._hide and _is_internal_artifact(entry.name):
                continue
            if _metadata_hidden(_safe_realpath(entry.path)):
                continue
            self._seen.add(os.path.normcase(entry.name))
            return entry
        while self._extras:
            norm, entry = self._extras.pop(0)
            if norm not in self._seen:
                return entry
        raise StopIteration

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        if self._it is not None:
            try:
                self._it.close()
            finally:
                self._it = None


_orig_listdir = os.listdir
def _guarded_listdir(path):
    resolved = _safe_realpath(str(path))
    if resolved == _PENDING_TREE_ROOT:
        names = _orig_listdir(path)
        return [n for n in names if n == _PENDING_RUN_ID]
    if _is_under_foreign_pending(resolved):
        raise PermissionError(
            "PENDING_ISOLATION: 禁止访问其他 run 的 pending [等级: %s]" % _TIER
        )
    if _metadata_hidden(resolved):
        raise FileNotFoundError(2, "路径不存在或不可见", str(path))
    try:
        names = _orig_listdir(path)
    except FileNotFoundError:
        # 目录尚未物化但可能只有 pending 后代
        names = []
    merged = _merge_pending_entries(resolved, names)
    if _path_is_inside(_WORKSPACE_ROOT, resolved):
        merged = [
            n for n in merged
            if not _is_internal_artifact(n)
            and not _metadata_hidden(os.path.join(resolved, n))
        ]
    return merged
os.listdir = _guarded_listdir
if hasattr(os, "scandir"):
    _orig_scandir = os.scandir
    def _guarded_scandir(path="."):
        resolved = _safe_realpath(str(path))
        if resolved == _PENDING_TREE_ROOT:
            return [e for e in _orig_scandir(path) if e.name == _PENDING_RUN_ID]
        if _is_under_foreign_pending(resolved):
            raise PermissionError(
                "PENDING_ISOLATION: 禁止访问其他 run 的 pending [等级: %s]" % _TIER
            )
        if _metadata_hidden(resolved):
            raise FileNotFoundError(2, "路径不存在或不可见", str(path))
        # 区外无 pending/锁工件：原样返回真实迭代器，不加包装开销
        if not _path_is_inside(_WORKSPACE_ROOT, resolved):
            return _orig_scandir(path)
        children = _pending_children(resolved)
        if not children:
            it = _orig_scandir(path)  # 缺失目录照常抛 FileNotFoundError
            return _MergedScandir(it, [])
        try:
            it = _orig_scandir(path)
        except FileNotFoundError:
            it = None
        extras = [
            (
                os.path.normcase(name),
                _PendingDirEntry(logical, pend if pend is not None else _pending_root()),
            )
            for name, (logical, pend) in children.items()
            if not _is_internal_artifact(name)
        ]
        return _MergedScandir(it, extras)
    os.scandir = _guarded_scandir

# ── pending 读侧投影 ──
# 同一 run 内经 open/Path.write_* 落盘的写入都在 pending 副本上；
# os.stat 家族（exists/isfile/getsize/getmtime、Path.stat/exists/is_file 全走它）
# 必须看到 pending 副本，否则“写完即验”惯用法（getsize/exists）对刚写的文件报错。
def _resolve_pending_read(resolved):
    """logical 路径有本 run 的 pending 写入时，返回 pending 副本路径。"""
    # 容器目录本身可探（makedirs/exists 会经过它）；foreign 判定留给其子路径。
    if resolved != _PENDING_TREE_ROOT and _is_under_foreign_pending(resolved):
        raise PermissionError(
            "PENDING_ISOLATION: 禁止访问其他 run 的 pending [等级: %s]" % _TIER
        )
    try:
        if resolved in _PENDING:
            return _PENDING[resolved]
        if _is_under_pending(resolved):
            return resolved
        # pending 落点只可能在工作区内：区外路径早退，避免给
        # import 期海量 stat（site-packages）叠加 sha256+二次 stat 开销。
        if not _path_is_inside(_WORKSPACE_ROOT, resolved):
            return resolved
        pend = _pending_dest(resolved)
        _orig_os_stat(pend)
        return pend
    except (OSError, PermissionError):
        pass
    # 只有 pending 后代（目录尚未物化）：投影到 pending 根这个真实目录，
    # 使 exists/isdir 为真、isfile 为假，与"目录已存在"语义一致。
    if _pending_children(resolved):
        return _pending_root()
    return resolved

def _guarded_os_stat(path, *args, **kwargs):
    # os.stat(fd) is fstat, not a lookup of a file named after the descriptor.
    # Opening the descriptor was already guarded; preserve native validation
    # of dir_fd/follow_symlinks and invalid/closed descriptors as well.
    if isinstance(path, int):
        return _orig_os_stat(path, *args, **kwargs)
    resolved = _safe_realpath(str(path))
    if _metadata_hidden(resolved):
        raise FileNotFoundError(2, "路径不存在或不可见", str(path))
    return _orig_os_stat(_resolve_pending_read(resolved), *args, **kwargs)

os.stat = _guarded_os_stat
if _orig_os_lstat is not None:
    def _guarded_os_lstat(path, *args, **kwargs):
        if isinstance(path, int):
            return _orig_os_lstat(path, *args, **kwargs)
        resolved = _safe_realpath(str(path))
        if _metadata_hidden(resolved):
            raise FileNotFoundError(2, "路径不存在或不可见", str(path))
        return _orig_os_lstat(_resolve_pending_read(resolved), *args, **kwargs)
    os.lstat = _guarded_os_lstat

# os.path.exists/isfile/isdir/lexists 在 Windows 走 C 加速实现，不经 Python os.stat，
# 需逐个投影；getsize/getmtime 等 Python 实现已由上面的 os.stat 补丁覆盖。
def _wrap_path_probe(fn):
    def _guarded(path, *args, **kwargs):
        if isinstance(path, int):
            return fn(path, *args, **kwargs)
        resolved = _safe_realpath(str(path))
        if _metadata_hidden(resolved):
            return False
        return fn(_resolve_pending_read(resolved), *args, **kwargs)
    return _guarded

for _probe_name in ("exists", "isfile", "isdir", "lexists"):
    _probe_fn = getattr(os.path, _probe_name, None)
    if callable(_probe_fn):
        setattr(os.path, _probe_name, _wrap_path_probe(_probe_fn))

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
    if writing:
        target = _path_write_target(self)
        return _orig_path_open(target, mode, *args, **kwargs)
    resolved = _safe_realpath(str(self))
    _deny_forbidden_read(resolved)
    dest = _prepare_open_path(resolved, mode)
    if dest != resolved:
        return _orig_path_open(_OrigPath(dest), mode, *args, **kwargs)
    return _orig_path_open(self, mode, *args, **kwargs)

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
_orig_path_mkdir = _OrigPath.mkdir
def _guarded_path_mkdir(self, *args, **kwargs):
    parent = _safe_realpath(str(self.parent) if str(self.parent) else ".")
    dest = os.path.join(parent, self.name)
    if not _mkdir_allowed(dest):
        raise PermissionError(
            "文件写入被安全策略禁止：保留目录 [.excelmanus] [等级: %s]" % _TIER
        )
    return _orig_path_mkdir(self, *args, **kwargs)
_OrigPath.mkdir = _guarded_path_mkdir
def _blocked_path_symlink(self, *a, **kw):
    raise PermissionError("Path.symlink_to 被安全策略禁止 [等级: %s]" % _TIER)
_OrigPath.symlink_to = _blocked_path_symlink
if hasattr(_OrigPath, "hardlink_to"):
    _OrigPath.hardlink_to = _blocked_path_symlink
'''



_UTF8_STDIO = """\
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
"""

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

{utf8_stdio}

_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = "RED"
_FULL_ACCESS_FILES = {allow_external_files}
_SYSTEM_TMPDIR = os.path.realpath(_tmpmod.gettempdir())

_original_open = builtins.open

# ── 敏感目录读取保护 ──
_HOME_DIR = os.path.expanduser("~")
_SENSITIVE_DIRS = [
    os.path.realpath(os.path.join(_HOME_DIR, ".excelmanus")),
]
_PRODUCT_SOURCE_DIRS = {product_source_paths}

# ── 文件系统守卫 ──
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

<<<PENDING_WRITE_RUNTIME>>>

def _guarded_open(file, mode="r", *args, **kwargs):
    # Pipes and descriptors opened through guarded os.open already have an
    # owner. Preserve Python's fd semantics (including closefd and EBADF).
    if isinstance(file, int):
        return _original_open(file, mode, *args, **kwargs)
    resolved = _safe_realpath(str(file))
    if any(c in str(mode) for c in "wax+"):
        _deny_protected_target(resolved)
    else:
        _deny_forbidden_read(resolved)
    # ── 敏感路径读取保护 ──
    for _sd in _SENSITIVE_DIRS:
        if _path_is_inside(_sd, resolved) and not _path_is_inside(_WORKSPACE_ROOT, resolved):
            raise PermissionError(
                f"文件访问被安全策略禁止：路径位于敏感目录内 [等级: {{_TIER}}]"
            )
    _basename = os.path.basename(resolved)
    if _basename == ".env":
        if not _FULL_ACCESS_FILES and not _path_is_inside(_WORKSPACE_ROOT, resolved):
            raise PermissionError(
                f"文件访问被安全策略禁止：禁止访问工作区外的 .env 文件 [等级: {{_TIER}}]"
            )
    if any(c in str(mode) for c in "wax+"):
        if not _FULL_ACCESS_FILES and not _path_is_inside(_WORKSPACE_ROOT, resolved):
            if _path_is_inside(_SYSTEM_TMPDIR, resolved):
                return _original_open(file, mode, *args, **kwargs)
            raise PermissionError(
                f"文件写入被安全策略禁止：路径不在工作区内 [等级: {{_TIER}}]"
            )
    resolved = _prepare_open_path(resolved, mode)
    return _original_open(resolved, mode, *args, **kwargs)

builtins.open = _guarded_open
import io as _io_mod
_io_mod.open = _guarded_open

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

{utf8_stdio}

# ── 配置 ──
_BLOCKED_MODULES = {blocked_modules}
_WORKSPACE_ROOT = os.path.realpath({workspace_root})
_TIER = {tier}
_FULL_ACCESS_FILES = {allow_external_files}
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
_PRODUCT_SOURCE_DIRS = {product_source_paths}
_BENCH_PROTECTED_DIRS_RAW = os.environ.get("EXCELMANUS_BENCH_PROTECTED_DIRS", "bench/external")
_BENCH_PROTECTED_DIRS = [
    os.path.realpath(os.path.join(_WORKSPACE_ROOT, d.strip()))
    for d in _BENCH_PROTECTED_DIRS_RAW.split(",")
    if d.strip()
]

<<<PENDING_WRITE_RUNTIME>>>

def _guarded_open(file, mode="r", *args, **kwargs):
    if isinstance(file, int):
        return _original_open(file, mode, *args, **kwargs)
    resolved = _safe_realpath(str(file))
    if any(c in str(mode) for c in "wax+"):
        _deny_protected_target(resolved)
    else:
        _deny_forbidden_read(resolved)
    if any(c in str(mode) for c in "wax+"):
        if not _path_is_inside(_WORKSPACE_ROOT, resolved):
            if _path_is_inside(_SYSTEM_TMPDIR, resolved):
                return _original_open(file, mode, *args, **kwargs)
            raise PermissionError(
                f"文件写入被安全策略禁止：路径不在工作区内 [等级: {{_TIER}}]"
            )
    resolved = _prepare_open_path(resolved, mode)
    return _original_open(resolved, mode, *args, **kwargs)

builtins.open = _guarded_open
import io as _io_mod
_io_mod.open = _guarded_open

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
    _SUBPROCESS_BLOCKED_ATTRS = ('run', 'call', 'check_call', 'check_output')
    def _make_subprocess_blocker(_name):
        def _blocked_fn(*_args, **_kwargs):
            # Matplotlib treats missing external font-discovery tools as an
            # optional dependency and falls back to local/bundled fonts. Its
            # cold-cache probes catch OSError, not our usual RuntimeError.
            # Only change the error for these internal probes; never execute
            # a command or relax the guard based on its name (e.g. fc-list).
            if _name == 'check_output':
                _caller = sys._getframe(1)
                _font_probe = (
                    _caller.f_globals.get('__name__') == 'matplotlib.font_manager'
                    and _caller.f_code.co_name in (
                        '_get_fontconfig_fonts', '_get_macos_fonts',
                    )
                )
                del _caller
                if _font_probe:
                    raise FileNotFoundError(
                        'Matplotlib external font discovery is disabled in the sandbox'
                    )
            raise RuntimeError(
                "subprocess." + _name + "() 被安全策略禁止 [等级: " + _TIER + "]。"
                "允许 import subprocess（库内部依赖），但禁止直接调用进程创建函数。"
            )
        return _blocked_fn
    # asyncio.windows_utils subclasses Popen during import (also reached by
    # joblib/sklearn). Keep a class-shaped guard without inheriting any actual
    # process-launching implementation. Subclass construction is still denied.
    class _BlockedPopen:
        __init__ = _make_subprocess_blocker('Popen')
    _subprocess_mod.Popen = _BlockedPopen
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
