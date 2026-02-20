"""Code Policy Engine：AST 静态分析 + 风险分级。"""
from __future__ import annotations

import ast
import enum
from dataclasses import dataclass, field


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
})

_SAFE_IO_MODULES: frozenset[str] = frozenset({
    "pathlib", "os.path", "os", "shutil", "tempfile",
    "glob", "fnmatch", "io", "zipfile", "gzip", "bz2", "lzma",
    "tarfile", "fileinput", "mmap",
})

_NETWORK_MODULES: frozenset[str] = frozenset({
    "requests", "urllib", "urllib.request", "urllib.parse", "urllib.error",
    "httpx", "aiohttp", "socket", "ssl",
    "http", "http.client", "http.server", "http.cookiejar",
    "ftplib", "smtplib", "imaplib", "poplib",
    "xmlrpc", "xmlrpc.client", "xmlrpc.server",
    "websocket", "websockets",
})

_SUBPROCESS_MODULES: frozenset[str] = frozenset({
    "subprocess", "pty", "pexpect",
})

_SYSTEM_CONTROL_MODULES: frozenset[str] = frozenset({
    "ctypes", "signal", "resource", "multiprocessing",
    "webbrowser", "antigravity",
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
    ("importlib", "import_module"),
})


def _module_root(name: str) -> str:
    return name.split(".")[0]


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

        if root in self._extra_blocked or module_name in self._extra_blocked:
            self.capabilities.add("SUBPROCESS")
            self.details.append(f"blocked by extra_blocked: {module_name}")
            return

        if root in self._extra_safe or module_name in self._extra_safe:
            self.capabilities.add("SAFE_COMPUTE")
            return

        if module_name in _SAFE_COMPUTE_MODULES or root in _SAFE_COMPUTE_MODULES:
            self.capabilities.add("SAFE_COMPUTE")
        elif module_name in _SAFE_IO_MODULES or root in _SAFE_IO_MODULES:
            self.capabilities.add("SAFE_IO")
        elif module_name in _NETWORK_MODULES or root in _NETWORK_MODULES:
            self.capabilities.add("NETWORK")
            self.details.append(f"network module: {module_name}")
        elif module_name in _SUBPROCESS_MODULES or root in _SUBPROCESS_MODULES:
            self.capabilities.add("SUBPROCESS")
            self.details.append(f"subprocess module: {module_name}")
        elif module_name in _SYSTEM_CONTROL_MODULES or root in _SYSTEM_CONTROL_MODULES:
            self.capabilities.add("SYSTEM_CONTROL")
            self.details.append(f"system control module: {module_name}")
        else:
            self.capabilities.add("SAFE_IO")

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
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            local_name = alias.asname or alias.name
            self._imported_names[local_name] = full
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in _DANGEROUS_CALLS:
                self.capabilities.add("DYNAMIC_EXEC")
                self.details.append(f"dangerous call: {node.func.id}()")
                if node.func.id == "exec":
                    self._has_exec_call = True

        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                obj_name = node.func.value.id
                attr_name = node.func.attr
                real_module = self._imported_names.get(obj_name, obj_name)
                root = _module_root(real_module)
                if (root, attr_name) in _DANGEROUS_ATTR_CALLS:
                    self.capabilities.add("SUBPROCESS")
                    self.details.append(f"dangerous attr call: {root}.{attr_name}()")

        self.generic_visit(node)

    def check_obfuscation(self) -> None:
        if self._has_base64 and self._has_exec_call:
            self.capabilities.add("OBFUSCATION")
            self.details.append("obfuscation: base64 + exec combination")


class CodePolicyEngine:
    """AST 静态分析 + 风险分级引擎。"""

    _RED_CAPABILITIES: frozenset[str] = frozenset({
        "SUBPROCESS", "DYNAMIC_EXEC", "SYSTEM_CONTROL", "OBFUSCATION",
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
        elif "NETWORK" in capabilities:
            tier = CodeRiskTier.YELLOW
        else:
            tier = CodeRiskTier.GREEN

        return CodeAnalysisResult(
            tier=tier,
            capabilities=capabilities,
            details=visitor.details,
        )
