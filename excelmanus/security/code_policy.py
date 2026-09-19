"""Code Policy Engine：AST 静态分析 + 风险分级。"""
from __future__ import annotations

import ast
import enum
from dataclasses import dataclass, field

from excelmanus.excel_extensions import EXCEL_EXTENSIONS as _EXCEL_EXTENSIONS_BASE
from excelmanus.security.module_manifest import MODULE_ROOT_ALIASES, NETWORK_MODULES


class CodeRiskTier(enum.Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass(frozen=True)
class CodeAnalysisResult:
    tier: CodeRiskTier
    capabilities: frozenset[str]
    details: list[str] = field(default_factory=list)
    analysis_error: str | None = None


# ── 模块 → 能力映射 ──────────────────────────────────────

_SAFE_COMPUTE_MODULES: frozenset[str] = frozenset({
    "pandas", "numpy", "openpyxl", "xlsxwriter", "xlrd",
    "matplotlib", "seaborn", "plotly", "scipy", "sklearn",
    "re", "math", "cmath", "datetime", "time", "calendar",
    "collections", "itertools", "functools", "operator",
    "json", "csv", "typing", "dataclasses", "decimal",
    "statistics", "textwrap", "string", "copy", "pprint",
    "enum", "abc", "numbers", "fractions", "struct",
    "hashlib", "hmac", "secrets", "uuid",
    "warnings", "logging", "traceback",
    "unicodedata", "locale", "codecs",
    "bisect", "heapq", "array",
    "contextlib", "weakref",
    "sys", "builtins", "types",
    # 自家 SDK shim：由沙箱启动时注入 sys.modules（sandbox_hook），
    # import 不写盘不触网；判 UNKNOWN_MODULE 会把官方写法必送进审批。
    "em", "excelmanus_sdk", "excelmanus",
})

_SAFE_IO_MODULES: frozenset[str] = frozenset({
    "pathlib", "os.path", "os", "shutil", "tempfile",
    "glob", "fnmatch", "io", "zipfile", "gzip", "bz2", "lzma",
    "tarfile", "fileinput", "mmap",
})

_NETWORK_MODULES: frozenset[str] = NETWORK_MODULES

_SUBPROCESS_MODULES: frozenset[str] = frozenset({
    "subprocess", "pty", "pexpect",
})

_SYSTEM_CONTROL_MODULES: frozenset[str] = frozenset({
    "ctypes", "signal", "resource", "multiprocessing",
    "webbrowser", "antigravity",
})

_DESERIALIZE_MODULES: frozenset[str] = frozenset({
    "pickle", "_pickle", "cPickle", "marshal", "shelve",
    "dill", "cloudpickle",
})

_FS_WRITE_EXPORTS: frozenset[str] = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs",
    "rename", "renames", "replace", "truncate",
    "symlink", "link", "chmod", "chown", "chroot",
    "mkfifo", "mknod", "write", "writev", "pwrite",
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
    "copyfileobj", "make_archive", "unpack_archive",
    "write_text", "write_bytes", "touch", "symlink_to", "hardlink_to",
    "to_csv", "to_excel", "to_json", "to_pickle", "to_parquet", "to_html",
    "to_markdown", "to_xml", "to_feather", "to_hdf", "to_stata",
})

# x.<attr>() 调用里语义模糊的方法名：os/shutil/pathlib 上是写操作，
# 但 pandas DataFrame.copy()/Series.rename() 是纯内存操作——
# 仅当 receiver 解析到 FS 模块或 Path 类时才记 FS_WRITE。
_FS_WRITE_AMBIGUOUS_ATTRS: frozenset[str] = frozenset({
    "remove", "unlink", "rmdir", "removedirs", "mkdir", "makedirs",
    "rename", "renames", "replace",
    "symlink", "link", "chmod", "chown", "chroot",
    "mkfifo", "mknod", "writev", "pwrite",
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
    "copyfileobj", "make_archive", "unpack_archive",
    "symlink_to", "hardlink_to",
})

# 无论 receiver 都记 FS_WRITE：落盘语义强（文件对象写入、pandas to_* 写出）。
_FS_WRITE_STRONG_ATTRS: frozenset[str] = frozenset({
    "truncate", "write", "writelines", "write_text", "write_bytes", "touch",
    "to_csv", "to_excel", "to_json", "to_pickle", "to_parquet", "to_html",
    "to_markdown", "to_xml", "to_feather", "to_hdf", "to_stata",
})

_FS_RECEIVER_ROOTS: frozenset[str] = frozenset({"os", "shutil", "pathlib"})

_FS_ESCAPE_ATTRS: frozenset[str] = frozenset({
    "symlink", "link", "symlink_to", "hardlink_to",
})

# ── 危险函数调用 ──────────────────────────────────────────

_DANGEROUS_CALLS: frozenset[str] = frozenset({
    "exec", "eval", "compile", "__import__",
})

_DANGEROUS_ATTR_CALLS: frozenset[tuple[str, str]] = frozenset({
    ("os", "system"),
    ("os", "popen"),
    ("os", "execl"), ("os", "execle"), ("os", "execlp"), ("os", "execlpe"),
    ("os", "execv"), ("os", "execve"), ("os", "execvp"), ("os", "execvpe"),
    ("os", "spawnl"), ("os", "spawnle"), ("os", "spawnlp"), ("os", "spawnlpe"),
    ("os", "spawnv"), ("os", "spawnve"), ("os", "spawnvp"), ("os", "spawnvpe"),
    ("os", "kill"), ("os", "_exit"),
    ("sys", "exit"),
})


_MODULE_ROOT_ALIASES: dict[str, str] = MODULE_ROOT_ALIASES


def _module_root(name: str) -> str:
    return name.split(".")[0]


def _normalize_module_root(root: str) -> str:
    return _MODULE_ROOT_ALIASES.get(root, root)


class _ASTVisitor(ast.NodeVisitor):
    """遍历 AST 收集能力标签和危险模式。"""

    def __init__(self, extra_safe: frozenset[str], extra_blocked: frozenset[str]) -> None:
        self.capabilities: set[str] = set()
        self.details: list[str] = []
        self._extra_safe = extra_safe
        self._extra_blocked = extra_blocked
        self._imported_names: dict[str, str] = {}
        self._has_base64 = False
        self._has_exec_call = False

    def _classify_module(self, module_name: str) -> None:
        root = _module_root(module_name)
        normalized_root = _normalize_module_root(root)

        if (
            root in self._extra_blocked
            or normalized_root in self._extra_blocked
            or module_name in self._extra_blocked
        ):
            self.capabilities.add("SUBPROCESS")
            self.details.append(f"blocked by extra_blocked: {module_name}")
            return

        if (
            root in self._extra_safe
            or normalized_root in self._extra_safe
            or module_name in self._extra_safe
        ):
            self.capabilities.add("SAFE_COMPUTE")
            return

        if module_name in _SAFE_COMPUTE_MODULES or normalized_root in _SAFE_COMPUTE_MODULES:
            self.capabilities.add("SAFE_COMPUTE")
        elif module_name in _SAFE_IO_MODULES or normalized_root in _SAFE_IO_MODULES:
            self.capabilities.add("SAFE_IO")
        elif module_name in _NETWORK_MODULES or normalized_root in _NETWORK_MODULES:
            self.capabilities.add("NETWORK")
            self.details.append(f"network module: {module_name}")
        elif module_name in _SUBPROCESS_MODULES or normalized_root in _SUBPROCESS_MODULES:
            self.capabilities.add("SUBPROCESS")
            self.details.append(f"subprocess module: {module_name}")
        elif module_name in _SYSTEM_CONTROL_MODULES or normalized_root in _SYSTEM_CONTROL_MODULES:
            self.capabilities.add("SYSTEM_CONTROL")
            self.details.append(f"system control module: {module_name}")
        elif module_name in _DESERIALIZE_MODULES or normalized_root in _DESERIALIZE_MODULES:
            self.capabilities.add("DESERIALIZE")
            self.details.append(f"deserialize module: {module_name}")
        else:
            self.capabilities.add("UNKNOWN_MODULE")
            self.details.append(f"unknown module: {module_name}")

        if root == "base64":
            self._has_base64 = True

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._classify_module(alias.name)
            local_name = alias.asname or alias.name
            self._imported_names[local_name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._classify_module(module)
        root = _normalize_module_root(_module_root(module)) if module else ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            local_name = alias.asname or alias.name
            self._imported_names[local_name] = full
            if alias.name in _FS_ESCAPE_ATTRS:
                self.capabilities.add("SYSTEM_CONTROL")
                self.details.append(f"fs escape import: {full}")
            elif root in {"os", "shutil", "pathlib"} and alias.name in _FS_WRITE_EXPORTS:
                self.capabilities.add("FS_WRITE")
                self.details.append(f"fs write import: {full}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_CALLS:
                self.capabilities.add("DYNAMIC_EXEC")
                self.details.append(f"dangerous call: {node.func.id}()")
                if node.func.id == "exec":
                    self._has_exec_call = True

            resolved = self._imported_names.get(node.func.id)
            if isinstance(resolved, str) and "." in resolved:
                root = _module_root(resolved)
                attr_name = resolved.rsplit(".", 1)[1]
                if (root, attr_name) in _DANGEROUS_ATTR_CALLS:
                    self.capabilities.add("SUBPROCESS")
                    self.details.append(
                        f"dangerous imported call: {root}.{attr_name}()"
                    )

        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if self._open_mode_is_write(node):
                self.capabilities.add("FS_WRITE")
                self.details.append("builtin open() write mode")

        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name in _FS_ESCAPE_ATTRS:
                self.capabilities.add("SYSTEM_CONTROL")
                self.details.append(f"fs escape call: {attr_name}()")
            elif attr_name in _FS_WRITE_STRONG_ATTRS:
                self.capabilities.add("FS_WRITE")
                self.details.append(f"fs write call: {attr_name}()")
            elif attr_name in _FS_WRITE_AMBIGUOUS_ATTRS and self._receiver_is_fs(node.func.value):
                self.capabilities.add("FS_WRITE")
                self.details.append(f"fs write call: {attr_name}()")
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                real_module = self._imported_names.get(obj_name, obj_name)
                root = _module_root(real_module)
                if (root, attr_name) in _DANGEROUS_ATTR_CALLS:
                    self.capabilities.add("SUBPROCESS")
                    self.details.append(f"dangerous attr call: {root}.{attr_name}()")
                if root == "os" and attr_name == "open" and self._os_open_is_write(node):
                    self.capabilities.add("FS_WRITE")
                    self.details.append("os.open() write flags")

        self.generic_visit(node)

    def _receiver_is_fs(self, value: ast.expr) -> bool:
        """receiver 是否解析到文件系统模块/Path 类（区分 df.copy() 与 shutil.copy()）。"""
        name: str | None = None
        if isinstance(value, ast.Name):
            name = value.id
        elif isinstance(value, ast.Attribute):
            node: ast.expr = value
            while isinstance(node, ast.Attribute):
                node = node.value
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            name = value.func.id
        if name is None:
            return False
        resolved = self._imported_names.get(name, name)
        return _module_root(resolved) in _FS_RECEIVER_ROOTS

    @staticmethod
    def _open_mode_is_write(node: ast.Call) -> bool:
        mode = None
        if len(node.args) >= 2:
            mode = node.args[1]
        for kw in node.keywords:
            if kw.arg == "mode":
                mode = kw.value
                break
        if mode is None:
            return False
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            return any(c in mode.value for c in "wax+")
        return True

    @staticmethod
    def _os_open_is_write(node: ast.Call) -> bool:
        flags = None
        if len(node.args) >= 2:
            flags = node.args[1]
        for kw in node.keywords:
            if kw.arg == "flags":
                flags = kw.value
                break
        if flags is None:
            return True
        text = ast.dump(flags)
        if "O_RDONLY" in text and not any(
            tok in text for tok in ("O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC")
        ):
            return False
        return True

    def check_obfuscation(self) -> None:
        if self._has_base64 and self._has_exec_call:
            self.capabilities.add("OBFUSCATION")
            self.details.append("obfuscation: base64 + exec combination")


@dataclass(frozen=True)
class ExcelTarget:
    """run_code 脚本中识别出的 Excel 操作目标。"""

    file_path: str
    sheet_name: str | None = None
    operation: str = "unknown"  # 取值："read" | "write" | "unknown"
    source: str = ""           # 示例："pd.read_excel" | "df.to_excel" | ...


# ── Excel 目标提取 ──────────────────────────────────────

_EXCEL_EXTENSIONS = _EXCEL_EXTENSIONS_BASE | frozenset({".csv"})


def _is_excel_literal(value: str) -> bool:
    """判断字符串字面量是否像 Excel 文件路径。"""
    lower = value.lower()
    return any(lower.endswith(ext) for ext in _EXCEL_EXTENSIONS)


class _ExcelTargetVisitor(ast.NodeVisitor):
    """从 AST 中提取 Excel 文件操作目标。"""

    def __init__(self) -> None:
        self.targets: list[ExcelTarget] = []

    def visit_Call(self, node: ast.Call) -> None:
        self._check_pandas_read(node)
        self._check_pandas_write(node)
        self._check_openpyxl_load(node)
        self._check_openpyxl_save(node)
        self.generic_visit(node)

    def _get_str_literal(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _get_keyword_str(self, node: ast.Call, name: str) -> str | None:
        for kw in node.keywords:
            if kw.arg == name:
                return self._get_str_literal(kw.value)
        return None

    def _check_pandas_read(self, node: ast.Call) -> None:
        """pd.read_excel("file.xlsx", sheet_name="Sheet1")"""
        if not self._is_attr_call(node, ("read_excel", "read_csv")):
            return
        if not node.args:
            return
        file_path = self._get_str_literal(node.args[0])
        if not file_path:
            return
        sheet_name = self._get_keyword_str(node, "sheet_name")
        if not sheet_name and len(node.args) > 1:
            sheet_name = self._get_str_literal(node.args[1])
        func_name = "pd.read_excel" if self._is_attr_call(node, ("read_excel",)) else "pd.read_csv"
        self.targets.append(ExcelTarget(
            file_path=file_path,
            sheet_name=sheet_name,
            operation="read",
            source=func_name,
        ))

    def _check_pandas_write(self, node: ast.Call) -> None:
        """df.to_excel("file.xlsx", sheet_name="Result") or df.to_excel(var)"""
        if not self._is_attr_call(node, ("to_excel", "to_csv")):
            return
        if not node.args:
            return
        file_path = self._get_str_literal(node.args[0])
        sheet_name = self._get_keyword_str(node, "sheet_name")
        func_name = "df.to_excel" if self._is_attr_call(node, ("to_excel",)) else "df.to_csv"
        self.targets.append(ExcelTarget(
            file_path=file_path or "<variable>",
            sheet_name=sheet_name,
            operation="write",
            source=func_name,
        ))

    def _check_openpyxl_load(self, node: ast.Call) -> None:
        """openpyxl.load_workbook("file.xlsx")"""
        if not self._is_attr_call(node, ("load_workbook",)):
            if not (isinstance(node.func, ast.Name) and node.func.id == "load_workbook"):
                return
        if not node.args:
            return
        file_path = self._get_str_literal(node.args[0])
        if not file_path:
            return
        self.targets.append(ExcelTarget(
            file_path=file_path,
            sheet_name=None,
            operation="unknown",
            source="openpyxl.load_workbook",
        ))

    def _check_openpyxl_save(self, node: ast.Call) -> None:
        """wb.save("file.xlsx") or wb.save(file_path)"""
        if not self._is_attr_call(node, ("save",)):
            return
        if not node.args:
            return
        file_path = self._get_str_literal(node.args[0])
        # 字面量路径须匹配 Excel 后缀；变量参数保守视为写入
        if file_path is not None and not _is_excel_literal(file_path):
            return
        self.targets.append(ExcelTarget(
            file_path=file_path or "<variable>",
            sheet_name=None,
            operation="write",
            source="wb.save",
        ))

    @staticmethod
    def _is_attr_call(node: ast.Call, attr_names: tuple[str, ...]) -> bool:
        if isinstance(node.func, ast.Attribute):
            return node.func.attr in attr_names
        return False


def extract_excel_targets(code: str) -> list[ExcelTarget]:
    """从 Python 代码中提取 Excel 操作目标（仅字面量路径）。"""
    if not code or not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    visitor = _ExcelTargetVisitor()
    visitor.visit(tree)
    return visitor.targets


class CodePolicyEngine:
    """AST 静态分析 + 风险分级引擎。"""

    _RED_CAPABILITIES: frozenset[str] = frozenset({
        "SUBPROCESS", "DYNAMIC_EXEC", "SYSTEM_CONTROL", "OBFUSCATION",
        "UNKNOWN_MODULE", "DESERIALIZE",
    })

    def __init__(
        self,
        *,
        extra_safe_modules: tuple[str, ...] = (),
        extra_blocked_modules: tuple[str, ...] = (),
    ) -> None:
        self._extra_safe = frozenset(extra_safe_modules)
        self._extra_blocked = frozenset(extra_blocked_modules)

    def analyze(self, code: str) -> CodeAnalysisResult:
        if not code or not code.strip():
            return CodeAnalysisResult(
                tier=CodeRiskTier.GREEN,
                capabilities=frozenset({"SAFE_COMPUTE"}),
                details=["empty or whitespace-only code"],
            )

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return CodeAnalysisResult(
                tier=CodeRiskTier.RED,
                capabilities=frozenset(),
                details=[f"syntax error: {exc}"],
                analysis_error=str(exc),
            )

        visitor = _ASTVisitor(
            extra_safe=self._extra_safe,
            extra_blocked=self._extra_blocked,
        )
        visitor.visit(tree)
        visitor.check_obfuscation()

        capabilities = frozenset(visitor.capabilities)

        if capabilities & self._RED_CAPABILITIES:
            tier = CodeRiskTier.RED
        elif "NETWORK" in capabilities or "FS_WRITE" in capabilities:
            tier = CodeRiskTier.YELLOW
        else:
            tier = CodeRiskTier.GREEN

        return CodeAnalysisResult(
            tier=tier,
            capabilities=capabilities,
            details=visitor.details,
        )


def allows_auto_run(
    analysis: CodeAnalysisResult,
    *,
    green_auto: bool,
    yellow_auto: bool,
) -> bool:
    """Whether run_code may execute without a confirmation dialog.

    FS_WRITE auto-approves: the sandbox wrapper physically confines workspace
    writes — ``open()``/``os.*``/openpyxl saves redirect to
    ``.excelmanus/pending/<run>`` and publish through the versioned commit
    pipeline, while out-of-workspace writes raise PermissionError. Approval
    cannot add safety the boundary does not already provide. NETWORK has no
    equivalent confinement (module blocklists only), so it stays gated by
    ``yellow_auto``. ApprovalPolicy.never is applied by the caller, not here.
    """
    if analysis.tier == CodeRiskTier.GREEN:
        return bool(green_auto)
    if analysis.tier == CodeRiskTier.YELLOW:
        if "NETWORK" in analysis.capabilities:
            return bool(yellow_auto)
        return True
    return False


# ── 可自动清洗的退出调用模式 ──────────────────────────────

# sys.exit(...) / os._exit(...) 作为独立表达式语句
_EXIT_ATTR_PATTERNS: frozenset[tuple[str, str]] = frozenset({
    ("sys", "exit"),
    ("os", "_exit"),
})

# exit() / quit() 作为独立表达式语句
_EXIT_BUILTIN_NAMES: frozenset[str] = frozenset({"exit", "quit"})


class _ExitCallRemover(ast.NodeTransformer):
    """AST 变换器：将 sys.exit() / exit() 等退出调用替换为 pass。"""

    def __init__(self, imported_names: dict[str, str]) -> None:
        self.removed: int = 0
        self._imported_names = imported_names

    def _is_exit_call(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        # sys.exit(...) / os._exit(...)
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            real_module = self._imported_names.get(func.value.id, func.value.id)
            root = _module_root(real_module)
            if (root, func.attr) in _EXIT_ATTR_PATTERNS:
                return True
        # exit(...) / quit(...)
        if isinstance(func, ast.Name) and func.id in _EXIT_BUILTIN_NAMES:
            return True
        return False

    def visit_Expr(self, node: ast.Expr) -> ast.AST | None:
        if self._is_exit_call(node.value):
            self.removed += 1
            # 替换为 pass 以防空块语法错误
            return ast.copy_location(ast.Pass(), node)
        return node


def strip_exit_calls(code: str) -> str | None:
    """移除代码中的 sys.exit() / exit() / os._exit() / quit() 调用。

    Returns:
        清洗后的代码字符串；如果代码不含退出调用或解析失败则返回 None。
    """
    if not code or not code.strip():
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    # 先收集 import 映射（与 _ASTVisitor 逻辑一致）
    imported: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imported[alias.asname or alias.name] = f"{module}.{alias.name}" if module else alias.name

    remover = _ExitCallRemover(imported)
    new_tree = remover.visit(tree)
    if remover.removed == 0:
        return None

    ast.fix_missing_locations(new_tree)
    try:
        return ast.unparse(new_tree)
    except Exception:
        return None
