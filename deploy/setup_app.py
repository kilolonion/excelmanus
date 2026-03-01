"""ExcelManus Setup Tool — Python edition.

Deployment tool for ExcelManus — clean, maintainable single-file implementation.
Features: embedded HTTP server + Web UI, environment detection, deploy pipeline,
service management, system tray icon, and update functionality.

Build EXE (Nuitka):
    pip install -r requirements-setup.txt
    python -m nuitka --standalone --onefile --windows-console-mode=disable \
        --windows-icon-from-ico=icon.ico --include-data-files=icon.ico=icon.ico \
        --include-data-files=setup-ui/dist/index.html=index.html \
        --output-filename=ExcelManusSetup.exe --mingw64 --assume-yes-for-downloads \
        setup_app.py
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ═══════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════

REPO_URL = "https://github.com/kilolonion/excelmanus.git"
REPO_URL_GITEE = "https://gitee.com/kilolonion/excelmanus.git"
REPO_DIR_NAME = "excelmanus"
DEFAULT_BE_PORT = "8000"
DEFAULT_FE_PORT = "3000"
SETUP_PORTS = [18921, 18922, 18923, 18924, 18925]

IS_WINDOWS = platform.system() == "Windows"
_domestic_cache: bool | None = None

# Hide console windows for all subprocess calls on Windows (GUI app)
# Note: only use STARTUPINFO (not CREATE_NO_WINDOW) — the latter breaks .cmd scripts
if IS_WINDOWS:
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _si.wShowWindow = 0  # SW_HIDE
    _NOWIN: dict = {"startupinfo": _si}
else:
    _NOWIN: dict = {}


# ═══════════════════════════════════════════════════════════
#  Utilities
# ═══════════════════════════════════════════════════════════

def _resource_path(filename: str) -> Path:
    """Resolve bundled resource path (PyInstaller-compatible)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / filename


def _run(cmd: list[str], cwd: str | Path | None = None,
         timeout: int = 60) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout, env=env,
                           **_NOWIN)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时 ({timeout}s)"
    except FileNotFoundError:
        return -1, "", f"命令未找到: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _run_stream(cmd: list[str], cwd: str | Path | None = None,
                timeout: int = 600, on_line=None, log=None) -> bool:
    """Run command with streaming output. Returns True on success."""
    cmd_str = " ".join(str(c) for c in cmd)
    if log:
        log.info(f"  → 执行: {cmd_str}")
        if cwd:
            log.info(f"    工作目录: {cwd}")
    try:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=env, **_NOWIN)
        deadline = time.monotonic() + timeout
        for line in p.stdout:
            line = line.rstrip()
            if line and on_line:
                on_line(line)
            if time.monotonic() > deadline:
                p.kill()
                if log:
                    log.err(f"  命令超时 ({timeout}s): {cmd_str}")
                return False
        p.wait(timeout=10)
        if p.returncode != 0 and log:
            log.err(f"  命令失败 (exit={p.returncode}): {cmd_str}")
        return p.returncode == 0
    except FileNotFoundError:
        if log:
            log.err(f"  命令未找到: {cmd[0]}")
        return False
    except Exception as e:
        if log:
            log.err(f"  命令异常: {type(e).__name__}: {e}")
        return False


def _is_domestic_network() -> bool:
    """Detect if user is on a domestic (Chinese) network via TCP ping race."""
    global _domestic_cache
    if _domestic_cache is not None:
        return _domestic_cache

    def _tcp_ping(host: str) -> float:
        try:
            t0 = time.monotonic()
            with socket.create_connection((host, 443), timeout=3):
                return time.monotonic() - t0
        except Exception:
            return float("inf")

    try:
        with ThreadPoolExecutor(2) as pool:
            f1 = pool.submit(_tcp_ping, "pypi.tuna.tsinghua.edu.cn")
            f2 = pool.submit(_tcp_ping, "pypi.org")
            t_mirror, t_pypi = f1.result(5), f2.result(5)
        _domestic_cache = t_mirror < 5 and (t_pypi > 5 or t_mirror < t_pypi * 0.8)
    except Exception:
        _domestic_cache = False
    return _domestic_cache


def _validate_port(port: str, default: str) -> str:
    try:
        p = int(port.strip())
        return str(p) if 1 <= p <= 65535 else default
    except (ValueError, AttributeError):
        return default


def _read_version_from_toml(toml_path: Path) -> str:
    if not toml_path.is_file():
        return "unknown"
    try:
        for line in toml_path.read_text("utf-8").splitlines():
            if line.strip().startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


def _parse_version_from_toml_content(content: str) -> str | None:
    for line in content.splitlines():
        if line.strip().startswith("version") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _parse_major_version(ver: str) -> int:
    m = re.match(r"v?(\d+)", ver.strip())
    return int(m.group(1)) if m else -1


def _kill_port(port: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        p = int(port)
        subprocess.run(
            f'for /f "tokens=5" %a in (\'netstat -ano ^| findstr :{p} ^| findstr LISTENING\') '
            f"do taskkill /F /PID %a",
            shell=True, capture_output=True, timeout=10, **_NOWIN)
    except Exception:
        pass


def _kill_process_tree(pid: int) -> None:
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5, **_NOWIN)
        except Exception:
            pass
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def _refresh_path() -> None:
    if not IS_WINDOWS:
        return
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('PATH','Machine')+';'+"
             "[Environment]::GetEnvironmentVariable('PATH','User')"],
            capture_output=True, text=True, timeout=10, **_NOWIN)
        if r.returncode == 0 and r.stdout.strip():
            os.environ["PATH"] = r.stdout.strip()
    except Exception:
        pass


def _load_html() -> str:
    """Load the UI HTML page. Vite build → bundled fallback → inline minimal."""
    for candidate in [
        _resource_path("index.html"),
        Path(__file__).parent / "setup-ui" / "dist" / "index.html",
    ]:
        if candidate.is_file():
            try:
                return candidate.read_text("utf-8")
            except Exception:
                continue
    fallback = _resource_path("setup_page.html")
    if fallback.is_file():
        try:
            return fallback.read_text("utf-8")
        except Exception:
            pass
    return MINIMAL_HTML


# ═══════════════════════════════════════════════════════════
#  LogStore — Thread-safe log buffer
# ═══════════════════════════════════════════════════════════

class LogStore:
    __slots__ = ("_items", "_lock")

    def __init__(self):
        self._items: list[dict] = []
        self._lock = threading.Lock()

    def add(self, text: str, level: str = "info") -> None:
        with self._lock:
            self._items.append({"text": text, "level": level, "idx": len(self._items)})

    def since(self, idx: int) -> list[dict]:
        with self._lock:
            return self._items[idx:] if idx < len(self._items) else []

    def info(self, t): self.add(t, "info")
    def ok(self, t):   self.add(t, "ok")
    def err(self, t):  self.add(t, "err")
    def warn(self, t): self.add(t, "warn")
    def hl(self, t):   self.add(t, "hl")


# ═══════════════════════════════════════════════════════════
#  Engine — Deploy & Service Management
# ═══════════════════════════════════════════════════════════

class Engine:
    def __init__(self):
        self.log = LogStore()
        self._lock = threading.Lock()
        self._be_port = DEFAULT_BE_PORT
        self._fe_port = DEFAULT_FE_PORT
        self._python_exe = "python"
        self._custom_paths: dict[str, str] = {}
        self._checks: dict[str, int] = {}
        self._details: dict[str, str] = {}
        self._progress = 0
        self._running = False
        self._deploying = False
        self._deploy_error: str | None = None
        self._force_mode = False
        self._needs_clone = False
        self._root = Path(".")
        self._proc_be: subprocess.Popen | None = None
        self._proc_fe: subprocess.Popen | None = None

        for k in ("python", "node", "npm", "git", "repo", "backend", "frontend"):
            self._checks[k] = 0
            self._details[k] = "待检测"

        self._detect_root()
        self._load_config()

    # ── Root detection ──

    def _detect_root(self) -> None:
        exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        self.log.info(f"可执行文件目录: {exe_dir}")
        # Search: current dir, parent, grandparent, great-grandparent, and child
        candidates = [exe_dir, exe_dir.parent, exe_dir.parent.parent,
                      exe_dir.parent.parent.parent,
                      exe_dir / REPO_DIR_NAME, exe_dir.parent / REPO_DIR_NAME]
        for d in candidates:
            try:
                if (d / "pyproject.toml").is_file():
                    self._root = d
                    self._needs_clone = False
                    self.log.hl(f"项目根目录: {self._root}")
                    return
            except Exception:
                continue
        self._root = exe_dir
        self._needs_clone = True
        self.log.warn(f"未找到项目文件 (pyproject.toml)，已搜索: {[str(c) for c in candidates]}")
        self.log.warn("将在部署时自动从 Gitee/GitHub 克隆")

    @property
    def _env_path(self) -> Path:
        return self._root / ".env"

    @property
    def _install_complete_path(self) -> Path:
        return self._root / ".install_complete"

    @property
    def _pid_dir(self) -> Path:
        return self._root / ".pids"

    @property
    def _vpy(self) -> Path:
        scripts = "Scripts" if IS_WINDOWS else "bin"
        return self._root / ".venv" / scripts / ("python.exe" if IS_WINDOWS else "python")

    # ── Config ──

    def _load_config(self) -> None:
        if not self._env_path.is_file():
            return
        try:
            for line in self._env_path.read_text("utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"')
                if k == "EXCELMANUS_BACKEND_PORT":
                    self._be_port = _validate_port(v, self._be_port)
                elif k == "EXCELMANUS_FRONTEND_PORT":
                    self._fe_port = _validate_port(v, self._fe_port)
            self.log.info(f"已加载 .env (后端={self._be_port}, 前端={self._fe_port})")
        except Exception as e:
            self.log.warn(f"读取 .env 失败: {e}")

    def _save_env(self) -> None:
        try:
            lines, seen = [], set()
            cors = f"http://localhost:{self._fe_port},http://localhost:5173"
            if self._env_path.is_file():
                for raw in self._env_path.read_text("utf-8").splitlines():
                    ln = raw.strip()
                    if not ln or ln.startswith("#") or "=" not in ln:
                        lines.append(raw)
                        continue
                    k = ln.split("=", 1)[0].strip()
                    seen.add(k)
                    if k == "EXCELMANUS_CORS_ALLOW_ORIGINS":
                        lines.append(f"{k}={cors}")
                    elif k == "EXCELMANUS_AUTO_UPDATE":
                        lines.append(f"{k}={'true' if self._auto_update else 'false'}")
                    else:
                        lines.append(raw)
            for k, v in [("EXCELMANUS_CORS_ALLOW_ORIGINS", cors),
                         ("EXCELMANUS_AUTH_ENABLED", "false"),
                         ("EXCELMANUS_EXTERNAL_SAFE_MODE", "false"),
                         ("EXCELMANUS_AUTO_UPDATE", "true" if self._auto_update else "false")]:
                if k not in seen:
                    lines.append(f"{k}={v}")
            self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.log.ok("已保存 .env")
        except Exception as e:
            self.log.err(f"保存 .env 失败: {e}")

    def _write_install_complete(self) -> None:
        try:
            ver = _read_version_from_toml(self._root / "pyproject.toml")
            self._install_complete_path.write_text(ver, encoding="utf-8")
            self.log.ok(f"安装完成标记已写入 (v{ver})")
        except Exception:
            pass

    # ── JSON API responses ──

    def is_ready_for_quick_start(self) -> bool:
        if self._needs_clone or not self._env_path.is_file():
            return False
        if not self._install_complete_path.is_file():
            return False
        installed = self._install_complete_path.read_text("utf-8").strip()
        current = _read_version_from_toml(self._root / "pyproject.toml")
        if installed and current and installed != current:
            self.log.warn(f"版本不一致: 已安装={installed}, 当前={current}，需重新部署")
            return False
        return (self._vpy.is_file()
                and (self._root / "web" / "node_modules").is_dir()
                and (self._root / "web" / ".next").is_dir())

    def get_config_json(self) -> str:
        return json.dumps({"bePort": self._be_port, "fePort": self._fe_port,
                           "quickStart": self.is_ready_for_quick_start()})

    def get_status_json(self) -> str:
        with self._lock:
            return json.dumps({"running": self._running, "deploying": self._deploying,
                               "progress": self._progress, "checks": dict(self._checks),
                               "details": dict(self._details),
                               "deploy_error": self._deploy_error})

    def get_logs_json(self, since: int) -> str:
        return json.dumps({"logs": self.log.since(since)})

    def get_version(self) -> str:
        return _read_version_from_toml(self._root / "pyproject.toml")

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    # ── Environment Check ──

    def check_env(self) -> None:
        with self._lock:
            for k in ("python", "node", "git"):
                self._checks[k] = 3
                self._details[k] = "检测中..."
        threading.Thread(target=self._run_check_env, daemon=True).start()

    def _check_tool(self, exe: str, ver_arg: str, winget_id: str,
                    ver_contains: str | None = None) -> str | None:
        rc, out, _ = _run([exe, ver_arg])
        if rc == 0 and out and (ver_contains is None or ver_contains in out):
            return out
        if IS_WINDOWS and self._try_auto_install(exe, winget_id):
            rc, out, _ = _run([exe, ver_arg])
            if rc == 0 and out and (ver_contains is None or ver_contains in out):
                return out
        return None

    def _try_auto_install(self, name: str, winget_id: str) -> bool:
        self.log.warn(f"尝试自动安装 {name} ...")
        ok = _run_stream(
            ["winget", "install", winget_id, "--silent",
             "--accept-source-agreements", "--accept-package-agreements"],
            timeout=600, on_line=lambda l: self.log.info(l), log=self.log)
        _refresh_path()
        self.log.ok(f"{name} 安装成功") if ok else self.log.err(f"{name} 安装失败")
        return ok

    def _run_check_env(self) -> None:
        def _log_ck(name, ok, d):
            (self.log.ok if ok else self.log.err)(f"  {'✓' if ok else '✗'}  {name}: {d or '未找到'}")

        # Python
        py_ver = self._check_tool("python", "--version", "Python.Python.3.11", "Python 3")
        py_ok = py_ver is not None
        if py_ok:
            self._python_exe = "python"
        else:
            rc, out, _ = _run(["py", "-3", "--version"])
            if rc == 0 and "Python 3" in out:
                py_ver, py_ok, self._python_exe = out, True, "py"
        with self._lock:
            self._checks["python"] = 1 if py_ok else 2
            self._details["python"] = py_ver.replace("Python ", "v") if py_ok else "未找到"
        _log_ck("Python", py_ok, py_ver)

        # Node.js
        nd_ver = self._check_tool("node", "--version", "OpenJS.NodeJS.LTS")
        nd_ok = nd_ver is not None
        if nd_ok:
            major = _parse_major_version(nd_ver)
            if 0 <= major < 18:
                self.log.warn(f"Node.js 版本过低，需要 v18+ (当前: v{major})")
                nd_ok = False
        with self._lock:
            self._checks["node"] = 1 if nd_ok else 2
            self._details["node"] = nd_ver if nd_ok else "未找到"
        _log_ck("Node.js", nd_ok, nd_ver)

        # Git
        gt_ver = self._check_tool("git", "--version", "Git.Git")
        gt_ok = gt_ver is not None
        with self._lock:
            self._checks["git"] = 1 if gt_ok else 2
            self._details["git"] = gt_ver.replace("git version ", "v") if gt_ok else "未找到"
        _log_ck("Git", gt_ok, gt_ver)

    def verify_custom_path(self, tool: str, path: str) -> str:
        if not tool or not path:
            return json.dumps({"ok": False, "error": "参数不能为空"})
        path = path.strip().strip('"')
        rc, ver, _ = _run([path, "--version"])
        if rc != 0 and IS_WINDOWS:
            rc, ver, _ = _run(["cmd.exe", "/c", f'"{path}" --version'])
        if rc == 0 and ver:
            valid = not (tool == "python" and "Python 3" not in ver)
            if valid:
                with self._lock:
                    self._custom_paths[tool] = path
                    self._checks[tool] = 1
                    self._details[tool] = ver
                    if tool == "python":
                        self._python_exe = path
                self.log.ok(f"自定义路径验证成功: {tool} = {path} ({ver})")
                return json.dumps({"ok": True, "version": ver})
        return json.dumps({"ok": False, "error": "无法获取版本信息，请检查路径是否正确"})

    # ── Deploy ──

    def start_deploy(self) -> None:
        with self._lock:
            if self._deploying or self._running:
                return
            self._deploying, self._progress, self._deploy_error = True, 0, None
            for k in self._checks:
                self._checks[k], self._details[k] = 3, "检测中..."
        self._save_env()
        self.log.hl("══════════ 开始部署 ══════════")
        if IS_WINDOWS and "Temp" in str(self._root):
            self.log.warn("⚠ 检测到项目位于 TEMP 目录，Windows Defender 实时扫描可能严重拖慢安装速度")
            self.log.warn("  建议：将 TEMP 目录添加到 Defender 排除项，或将项目部署到非临时目录")
        threading.Thread(target=self._run_deploy, daemon=True).start()

    def start_force_deploy(self) -> None:
        with self._lock:
            if self._deploying or self._running:
                return
            self._deploying, self._force_mode, self._progress, self._deploy_error = True, True, 0, None
        self._save_env()
        self.log.warn("══════════ 强行部署（跳过环境检测） ══════════")
        threading.Thread(target=self._run_deploy, daemon=True).start()

    def quick_start(self) -> None:
        with self._lock:
            if self._running or self._deploying:
                return
            self._deploying, self._progress = True, 85
        self.log.hl("═══ 快速启动模式 ═══")
        self.log.info("检测到已部署的项目，跳过向导直接启动服务...")

        def _do():
            try:
                self._save_env()
                with self._lock:
                    self._progress, self._deploying = 100, False
                self._start_services()
            except Exception as e:
                self.log.err(f"快速启动失败: {e}")
                with self._lock:
                    self._deploying = False
        threading.Thread(target=_do, daemon=True).start()

    def _run_deploy(self) -> None:
        done, total = [0], 7
        force = self._force_mode
        self._force_mode = False

        def _advance(key, ok, detail=None):
            if ok:
                done[0] += 1
            with self._lock:
                self._checks[key] = 1 if ok else 2
                self._details[key] = detail or ("OK" if ok else "缺失")
                self._progress = min(int(done[0] / total * 100), 99 if not ok else 100)

        def _log_ck(name, ok, d):
            (self.log.ok if ok else self.log.err)(
                f"  {'✓' if ok else '✗'}  {name}: {d or '未找到'}")

        # ── Python ──
        py_exe = self._custom_paths.get("python", "python")
        py_ver, py_ok = None, False
        for exe in ([py_exe] if py_exe != "python" else []) + ["python"]:
            rc, out, _ = _run([exe, "--version"])
            if rc == 0 and "Python 3" in out:
                py_ver, py_ok, self._python_exe = out, True, exe
                break
        if not py_ok:
            py_ver = self._check_tool("python", "--version", "Python.Python.3.11", "Python 3")
            py_ok = py_ver is not None
            if py_ok:
                self._python_exe = "python"
            else:
                rc, out, _ = _run(["py", "-3", "--version"])
                if rc == 0 and "Python 3" in out:
                    py_ver, py_ok, self._python_exe = out, True, "py"
        _advance("python", py_ok, py_ver.replace("Python ", "v") if py_ok and py_ver else None)
        _log_ck("Python", py_ok, py_ver)

        # ── Node.js + npm ──
        nd_exe = self._custom_paths.get("node", "node")
        nd_ver = None
        for exe in ([nd_exe] if nd_exe != "node" else []) + ["node"]:
            rc, out, _ = _run([exe, "--version"])
            if rc == 0 and out:
                nd_ver = out
                break
        if not nd_ver:
            nd_ver = self._check_tool("node", "--version", "OpenJS.NodeJS.LTS")
        nd_ok = nd_ver is not None
        if nd_ok:
            major = _parse_major_version(nd_ver)
            if 0 <= major < 18:
                self.log.warn(f"Node.js 版本过低，需要 v18+ (当前: v{major})")
                nd_ok = False
        rc_npm, npm_ver, _ = _run(["npm", "--version"])
        if rc_npm != 0 and IS_WINDOWS:
            rc_npm, npm_ver, _ = _run(["npm.cmd", "--version"])
        npm_ok = rc_npm == 0 and bool(npm_ver)
        _advance("node", nd_ok, nd_ver)
        _log_ck("Node.js", nd_ok, nd_ver)
        _advance("npm", npm_ok, f"v{npm_ver}" if npm_ok else None)
        _log_ck("npm", npm_ok, npm_ver)

        # ── Git ──
        gt_exe = self._custom_paths.get("git", "git")
        gt_ver = None
        for exe in ([gt_exe] if gt_exe != "git" else []) + ["git"]:
            rc, out, _ = _run([exe, "--version"])
            if rc == 0 and "git" in out:
                gt_ver = out
                break
        if not gt_ver:
            gt_ver = self._check_tool("git", "--version", "Git.Git")
        gt_ok = gt_ver is not None
        _advance("git", gt_ok, gt_ver.replace("git version ", "v") if gt_ok else None)
        _log_ck("Git", gt_ok, gt_ver)

        if not (py_ok and nd_ok and npm_ok):
            if force:
                self.log.warn("环境检测未通过，但已启用强行部署模式，继续...")
            else:
                self.log.err("缺少必要环境组件，请手动安装后重试")
                with self._lock:
                    self._deploy_error = "缺少必要环境组件"
                    self._deploying = False
                return

        # ── Clone repo ──
        if self._needs_clone:
            if not gt_ok:
                self.log.err("单独运行模式需要 Git 来克隆仓库，请先安装 Git")
                _advance("repo", False, "需要 Git")
                with self._lock:
                    self._deploy_error = "需要 Git"
                    self._deploying = False
                return
            clone_ok = self._clone_repo()
            _advance("repo", clone_ok, "就绪" if clone_ok else "克隆失败")
            if not clone_ok:
                with self._lock:
                    self._deploy_error = "仓库克隆失败"
                    self._deploying = False
                return
        else:
            _advance("repo", True, "本地已存在")

        # ── Backend + Frontend in parallel ──
        self.log.hl("并行安装后端 + 前端依赖...")
        results = {}
        t_be = threading.Thread(target=lambda: results.__setitem__("be", self._setup_backend()))
        t_fe = threading.Thread(target=lambda: results.__setitem__("fe", self._setup_frontend()))
        t_be.start(); t_fe.start()
        t_be.join(); t_fe.join()

        _advance("backend", results.get("be", False), "就绪" if results.get("be") else "失败")
        if not results.get("be"):
            with self._lock:
                self._deploy_error = "后端依赖安装失败"
                self._deploying = False
            return
        _advance("frontend", results.get("fe", False), "就绪" if results.get("fe") else "失败")
        if not results.get("fe"):
            with self._lock:
                self._deploy_error = "前端依赖安装失败 (npm install)"
                self._deploying = False
            return

        self._write_install_complete()
        with self._lock:
            self._progress, self._deploying = 100, False
        self._start_services()

    def _clone_repo(self) -> bool:
        exe_dir = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
        target = exe_dir / REPO_DIR_NAME

        if target.is_dir() and (target / "pyproject.toml").is_file():
            self.log.ok(f"仓库已存在: {target}，执行 git pull 更新...")
            _run(["git", "-C", str(target), "pull", "--ff-only"], timeout=60)
            self._root = target
            self._needs_clone = False
            self._load_config()
            return True

        if target.is_dir():
            self.log.warn(f"发现不完整的目录，清理后重新克隆...")
            shutil.rmtree(target, ignore_errors=True)

        for url, label in [(REPO_URL_GITEE, "Gitee"), (REPO_URL, "GitHub")]:
            self.log.hl(f"正在从 {label} 克隆仓库...")
            ok = _run_stream(["git", "clone", "--depth", "1", url, str(target)],
                             timeout=300, on_line=lambda l: self.log.info(f"  [git] {l}"),
                             log=self.log)
            if ok and (target / "pyproject.toml").is_file():
                self._root, self._needs_clone = target, False
                self.log.ok("仓库克隆成功")
                self._load_config()
                return True
            shutil.rmtree(target, ignore_errors=True)
            self.log.warn(f"{label} 克隆失败")

        self.log.err("仓库克隆失败，请检查网络连接")
        return False

    def _setup_backend(self) -> bool:
        self.log.info("检查后端依赖...")
        venv_dir = self._root / ".venv"

        if not venv_dir.is_dir():
            self.log.info("创建 Python 虚拟环境 (.venv)...")
            cmd = (["py", "-3", "-m", "venv", str(venv_dir)] if self._python_exe == "py"
                   else [self._python_exe, "-m", "venv", str(venv_dir)])
            ok = _run_stream(cmd, cwd=self._root, on_line=lambda l: self.log.info(f"  [venv] {l}"),
                             log=self.log)
            if not ok or not self._vpy.is_file():
                self.log.err("虚拟环境创建失败")
                return False
            self.log.ok("虚拟环境已创建")
        else:
            self.log.ok(f"虚拟环境已存在")

        rc, _, _ = _run([str(self._vpy), "-c", "import uvicorn; import excelmanus"])
        if rc == 0:
            self.log.ok("后端依赖已安装，跳过 pip install")
            return True

        self.log.info("安装后端 Python 依赖 (pip install)，请稍候...")
        domestic = _is_domestic_network()
        mirror = ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"] if domestic else []
        ok = _run_stream([str(self._vpy), "-m", "pip", "install", "-e", str(self._root)] + mirror,
                         cwd=self._root, timeout=600,
                         on_line=lambda l: self.log.info(f"  [pip] {l}"),
                         log=self.log)
        if not ok and not domestic:
            self.log.warn("pip 失败，尝试清华镜像源...")
            ok = _run_stream([str(self._vpy), "-m", "pip", "install", "-e", str(self._root),
                              "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
                             cwd=self._root, timeout=600,
                             on_line=lambda l: self.log.info(f"  [pip] {l}"),
                             log=self.log)
        if not ok:
            self.log.err("后端依赖安装失败")
            return False
        self.log.ok("后端依赖就绪")
        return True

    def _setup_frontend(self) -> bool:
        web_dir = self._root / "web"
        if not web_dir.is_dir():
            self.log.err(f"未找到 web 目录: {web_dir}")
            return False

        nm = web_dir / "node_modules"
        if nm.is_dir() and (nm / ".package-lock.json").is_file():
            self.log.ok("前端依赖已存在")
            return self._build_frontend(web_dir)

        self.log.info("安装前端依赖 (npm install)，请稍候...")
        domestic = _is_domestic_network()
        npm_exe = "npm.cmd" if IS_WINDOWS else "npm"
        npm_cmd = [npm_exe, "install", "--prefer-offline"]
        if domestic:
            npm_cmd.append("--registry=https://registry.npmmirror.com")

        ok = _run_stream(npm_cmd, cwd=web_dir, timeout=300,
                         on_line=lambda l: self.log.info(f"  [npm] {l}"),
                         log=self.log)
        if not ok:
            self.log.warn("npm install 失败，尝试备用源...")
            fallback = [npm_exe, "install"]
            if not domestic:
                fallback.append("--registry=https://registry.npmmirror.com")
            ok = _run_stream(fallback, cwd=web_dir, timeout=300,
                             on_line=lambda l: self.log.info(f"  [npm] {l}"),
                             log=self.log)
        if not ok:
            self.log.err("npm install 失败")
            return False
        self.log.ok("前端依赖就绪")
        return self._build_frontend(web_dir)

    def _build_frontend(self, web_dir: Path) -> bool:
        if (web_dir / ".next" / "BUILD_ID").is_file():
            self.log.ok("前端已构建，跳过 build")
            return True
        self.log.info("构建前端 (npm run build)，请稍候...")
        env = {**os.environ,
               "BACKEND_INTERNAL_URL": f"http://127.0.0.1:{self._be_port}",
               "NEXT_PUBLIC_BACKEND_ORIGIN": f"http://localhost:{self._be_port}",
               "NODE_OPTIONS": "--max-old-space-size=4096",
               "NEXT_TELEMETRY_DISABLED": "1"}
        try:
            npm_exe = "npm.cmd" if IS_WINDOWS else "npm"
            p = subprocess.Popen([npm_exe, "run", "build"], cwd=str(web_dir),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, env=env, **_NOWIN)
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    self.log.info(f"  [build] {line}")
            p.wait(timeout=600)
            if p.returncode != 0:
                self.log.err("npm run build 失败")
                return False
            self.log.ok("前端构建完成")
            return True
        except Exception as e:
            self.log.err(f"npm run build 异常: {e}")
            return False

    # ── Services ──

    def _cleanup_stale_pids(self) -> None:
        if not self._pid_dir.is_dir():
            return
        for f in self._pid_dir.glob("*.pid"):
            try:
                pid = int(f.read_text().strip())
                if pid > 0:
                    self.log.warn(f"发现残留进程 PID={pid}，正在清理...")
                    _kill_process_tree(pid)
            except Exception:
                pass
        shutil.rmtree(self._pid_dir, ignore_errors=True)

    def _save_pid(self, pid: int, name: str) -> None:
        try:
            self._pid_dir.mkdir(exist_ok=True)
            (self._pid_dir / f"{name}.pid").write_text(str(pid))
        except Exception:
            pass

    def _start_services(self) -> None:
        self.log.hl("启动后端服务...")
        self._cleanup_stale_pids()
        _kill_port(self._be_port)
        _kill_port(self._fe_port)

        vpy = str(self._vpy) if self._vpy.is_file() else "python"
        env = {**os.environ, "PYTHONIOENCODING": "utf-8",
               "EXCELMANUS_CORS_ALLOW_ORIGINS": f"http://localhost:{self._fe_port},http://localhost:5173",
               "EXCELMANUS_AUTH_ENABLED": "false",
               "EXCELMANUS_EXTERNAL_SAFE_MODE": "false"}
        try:
            self._proc_be = subprocess.Popen(
                [vpy, "-m", "uvicorn", "excelmanus.api:app",
                 "--host", "0.0.0.0", "--port", self._be_port],
                cwd=str(self._root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, **_NOWIN)
            self._save_pid(self._proc_be.pid, "backend")
            self.log.ok(f"后端已启动 → http://localhost:{self._be_port}")
            threading.Thread(target=self._pipe_output, args=(self._proc_be, "后端"), daemon=True).start()
        except Exception as e:
            self.log.err(f"后端启动失败: {e}")
            return
        threading.Thread(target=self._wait_and_start_frontend, daemon=True).start()

    def _pipe_output(self, proc: subprocess.Popen, name: str) -> None:
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.log.info(line)
            proc.wait()
            if self.is_running:
                self.log.err(f"{name}服务异常退出！")
                self._on_crash(name)
        except Exception:
            pass

    def _on_crash(self, name: str) -> None:
        with self._lock:
            was = self._running
            self._running = False
        if was:
            self.log.warn("所有服务已停止，请检查日志排查问题后重新部署")
            if name == "后端" and self._proc_fe:
                _kill_process_tree(self._proc_fe.pid); self._proc_fe = None
            elif self._proc_be:
                _kill_process_tree(self._proc_be.pid); self._proc_be = None
        shutil.rmtree(self._pid_dir, ignore_errors=True)

    def _wait_and_start_frontend(self) -> None:
        import urllib.request
        self.log.info(f"等待后端就绪...")
        for attempt in range(30):
            time.sleep(2)
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self._be_port}/api/v1/health", timeout=3):
                    break
            except Exception:
                pass
            if attempt % 5 == 4:
                self.log.info(f"  后端尚未就绪，已等待 {(attempt+1)*2} 秒...")
        else:
            self.log.err("后端在 60 秒内未就绪")
            self._stop_proc(self._proc_be); self._proc_be = None
            return

        self.log.ok(f"后端已就绪")
        self.log.hl("启动前端服务 (生产模式)...")
        env = {**os.environ,
               "BACKEND_INTERNAL_URL": f"http://127.0.0.1:{self._be_port}",
               "NEXT_PUBLIC_BACKEND_ORIGIN": f"http://localhost:{self._be_port}"}
        try:
            self._proc_fe = subprocess.Popen(
                ["npm", "run", "start", "--", "--port", self._fe_port],
                cwd=str(self._root / "web"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, **_NOWIN)
            self._save_pid(self._proc_fe.pid, "frontend")
            threading.Thread(target=self._pipe_output, args=(self._proc_fe, "前端"), daemon=True).start()
        except Exception as e:
            self.log.err(f"前端启动失败: {e}")
            return

        self.log.info(f"等待前端就绪...")
        for fa in range(30):
            time.sleep(2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self._fe_port}/", timeout=3) as r:
                    if r.status < 500:
                        break
            except Exception:
                pass
            if fa % 5 == 4:
                self.log.info(f"  前端尚未就绪，已等待 {(fa+1)*2} 秒...")
        else:
            self.log.err("前端在 60 秒内未就绪")
            for proc in [self._proc_fe, self._proc_be]:
                self._stop_proc(proc)
            self._proc_fe = self._proc_be = None
            return

        self.log.ok(f"前端已就绪 → http://localhost:{self._fe_port}")
        with self._lock:
            self._running = True
        self._create_desktop_shortcut()

    def stop_services(self) -> None:
        self.log.warn("正在停止服务...")
        for proc in [self._proc_be, self._proc_fe]:
            self._stop_proc(proc)
        self._proc_be = self._proc_fe = None
        _kill_port(self._be_port)
        _kill_port(self._fe_port)
        shutil.rmtree(self._pid_dir, ignore_errors=True)
        with self._lock:
            self._running = self._deploying = False
        self.log.ok("所有服务已停止")

    def _stop_proc(self, proc: subprocess.Popen | None) -> None:
        if proc and proc.poll() is None:
            try:
                _kill_process_tree(proc.pid)
                proc.wait(timeout=5)
            except Exception:
                pass

    # ── Update ──

    def _ensure_github_remote(self) -> None:
        _run(["git", "remote", "add", "github", REPO_URL], cwd=self._root)
        _run(["git", "remote", "set-url", "github", REPO_URL], cwd=self._root)

    def _check_update_internal(self, fetch_timeout: int = 15) -> dict:
        shallow = self._root / ".git" / "shallow"
        depth = ["--depth=50"] if shallow.is_file() else []

        git_remote = "origin"
        rc, _, _ = _run(["git", "-C", str(self._root), "fetch", "origin"] + depth + ["--tags"],
                        timeout=fetch_timeout)
        if rc != 0:
            self.log.warn("origin fetch 超时，尝试 GitHub...")
            self._ensure_github_remote()
            rc, _, _ = _run(["git", "-C", str(self._root), "fetch", "github"] + depth + ["--tags"],
                            timeout=fetch_timeout)
            if rc != 0:
                return {"has_update": False, "timeout": True,
                        "current": _read_version_from_toml(self._root / "pyproject.toml")}
            git_remote = "github"

        current = _read_version_from_toml(self._root / "pyproject.toml")
        _, branch, _ = _run(["git", "-C", str(self._root), "rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch if branch and branch != "HEAD" else "main"
        _, cnt, _ = _run(["git", "-C", str(self._root), "rev-list", "--count",
                          f"HEAD..{git_remote}/{branch}"])
        behind = int(cnt) if cnt.isdigit() else 0
        latest = current
        if behind > 0:
            _, rt, _ = _run(["git", "-C", str(self._root), "show",
                             f"{git_remote}/{branch}:pyproject.toml"])
            if rt:
                latest = _parse_version_from_toml_content(rt) or current
        return {"has_update": behind > 0, "current": current, "latest": latest, "behind": behind}

    def check_update(self) -> str:
        try:
            self.log.info("检查更新...")
            return json.dumps(self._check_update_internal(15))
        except Exception as e:
            return json.dumps({"has_update": False, "error": str(e)})

    def check_update_quick(self) -> str:
        try:
            self.log.info("快速检查更新（8秒超时）...")
            return json.dumps(self._check_update_internal(8))
        except Exception as e:
            return json.dumps({"has_update": False, "error": str(e)})

    def apply_update(self) -> str:
        try:
            self.log.hl("═══ 开始更新 ═══")
            old_ver = _read_version_from_toml(self._root / "pyproject.toml")
            domestic = _is_domestic_network()
            if domestic:
                self.log.info("检测到国内网络，将优先使用镜像加速")

            # Backup
            ts = time.strftime("%Y%m%d_%H%M%S")
            bk = self._root / "backups" / f"backup_{old_ver}_{ts}"
            bk.mkdir(parents=True, exist_ok=True)
            if self._env_path.is_file():
                shutil.copy2(self._env_path, bk / ".env")
                self.log.ok("备份 .env")
            for d in ("users", "outputs", "uploads"):
                src = self._root / d
                if src.is_dir() and any(src.iterdir()):
                    try:
                        shutil.copytree(src, bk / d, dirs_exist_ok=True)
                        self.log.ok(f"备份 {d}/")
                    except Exception as e:
                        self.log.warn(f"备份 {d}/ 失败: {e}")
            home_db = Path.home() / ".excelmanus"
            if home_db.is_dir():
                dst = bk / ".excelmanus_home"
                dst.mkdir(exist_ok=True)
                for f in home_db.glob("*.db*"):
                    shutil.copy2(f, dst / f.name)
                self.log.ok("备份数据库")

            # Cleanup old backups (keep latest 2)
            backup_base = self._root / "backups"
            if backup_base.is_dir():
                old_backups = sorted(
                    (d for d in backup_base.iterdir()
                     if d.is_dir() and d.name.startswith("backup_")),
                    key=lambda d: d.stat().st_mtime, reverse=True)
                for old_bk in old_backups[2:]:
                    try:
                        shutil.rmtree(old_bk)
                        self.log.info(f"清理旧备份: {old_bk.name}")
                    except Exception:
                        pass

            self._install_complete_path.unlink(missing_ok=True)

            # Git pull
            self.log.info("拉取最新代码...")
            _, status, _ = _run(["git", "status", "--porcelain"], cwd=self._root)
            has_stash = False
            if status.strip():
                rc, _, _ = _run(["git", "stash", "--include-untracked"], cwd=self._root)
                has_stash = rc == 0
            _, branch, _ = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self._root)
            branch = branch if branch and branch != "HEAD" else "main"

            remote = "origin"
            rc, _, err = _run(["git", "pull", "origin", branch, "--ff-only"],
                              cwd=self._root, timeout=60)
            if rc != 0:
                self.log.warn("origin pull 失败，尝试 GitHub...")
                self._ensure_github_remote()
                rc2, _, _ = _run(["git", "fetch", "github", branch], cwd=self._root, timeout=120)
                if rc2 == 0:
                    remote = "github"
                    rc, _, err = _run(["git", "merge", f"github/{branch}", "--ff-only"],
                                      cwd=self._root)
                if rc != 0:
                    self.log.err("fast-forward 合并失败，本地有未推送的提交")
                    self.log.warn("请手动执行 git pull --rebase 或 git merge 解决冲突")
                    if has_stash:
                        _run(["git", "stash", "pop"], cwd=self._root)
                    return json.dumps({"success": False,
                                       "error": "本地有未推送的提交，无法自动更新。请手动执行 git pull"})
            if has_stash:
                rc_pop, _, _ = _run(["git", "stash", "pop"], cwd=self._root)
                if rc_pop != 0:
                    self.log.warn("git stash pop 失败，本地修改保留在 stash 中，请手动执行 git stash pop")
            self.log.ok("代码已更新")

            # Reinstall backend deps (with mirror fallback)
            self.log.info("更新后端依赖...")
            vpy = str(self._vpy) if self._vpy.is_file() else "python"
            pip_cmd = [vpy, "-m", "pip", "install", "-e", str(self._root), "--quiet"]
            if domestic:
                pip_cmd.extend(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
            ok = _run_stream(pip_cmd, cwd=self._root, timeout=300,
                             on_line=lambda l: self.log.info(f"  [pip] {l}"),
                             log=self.log)
            if not ok and not domestic:
                self.log.warn("pip install 失败，尝试清华镜像源...")
                pip_cmd_mirror = [vpy, "-m", "pip", "install", "-e", str(self._root),
                                  "--quiet", "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
                _run_stream(pip_cmd_mirror, cwd=self._root, timeout=300,
                            on_line=lambda l: self.log.info(f"  [pip] {l}"),
                            log=self.log)
            self.log.ok("后端依赖已更新")

            # Reinstall frontend deps & rebuild (with mirror fallback)
            self.log.info("重新构建前端...")
            web_dir = self._root / "web"
            if web_dir.is_dir():
                npm_cmd = ["npm", "install"]
                if domestic:
                    npm_cmd.append("--registry=https://registry.npmmirror.com")
                ok = _run_stream(npm_cmd, cwd=web_dir, timeout=300,
                                 on_line=lambda l: self.log.info(f"  [npm] {l}"),
                                 log=self.log)
                if not ok and not domestic:
                    self.log.warn("npm install 失败，尝试淘宝镜像源...")
                    _run_stream(["npm", "install", "--registry=https://registry.npmmirror.com"],
                                cwd=web_dir, timeout=300,
                                on_line=lambda l: self.log.info(f"  [npm] {l}"),
                                log=self.log)
                shutil.rmtree(web_dir / ".next", ignore_errors=True)
                self._build_frontend(web_dir)
            self.log.ok("前端已重新构建")

            new_ver = _read_version_from_toml(self._root / "pyproject.toml")
            self._write_install_complete()
            self.log.hl(f"更新成功！{old_ver} → {new_ver}")
            return json.dumps({"success": True, "old_version": old_ver, "new_version": new_ver})
        except Exception as e:
            self.log.err(f"更新失败: {e}")
            return json.dumps({"success": False, "error": str(e)})

    # ── Shortcut ──

    def _create_desktop_shortcut(self) -> None:
        if not IS_WINDOWS:
            return
        try:
            desktop = Path.home() / "Desktop"
            if not desktop.is_dir():
                return
            lnk = desktop / "ExcelManus.lnk"
            if lnk.is_file():
                return
            target = self._root / "deploy" / "start.bat"
            if not target.is_file():
                return
            _create_lnk(lnk, target, self._root)
            if lnk.is_file():
                self.log.ok(f"桌面快捷方式已创建: {lnk}")
        except Exception as e:
            self.log.warn(f"创建桌面快捷方式失败（非致命）: {e}")

    def create_shortcut(self) -> str:
        if not IS_WINDOWS:
            return json.dumps({"error": "仅支持 Windows"})
        try:
            desktop = Path.home() / "Desktop"
            if not desktop.is_dir():
                return json.dumps({"error": "未找到桌面目录"})
            lnk = desktop / "ExcelManus.lnk"
            target = self._root / "deploy" / "start.bat"
            exe = self._root / "ExcelManus.exe"
            t = target if target.is_file() else exe if exe.is_file() else None
            if not t:
                return json.dumps({"error": "未找到启动脚本"})
            _create_lnk(lnk, t, self._root)
            if lnk.is_file():
                self.log.ok(f"桌面快捷方式已创建: {lnk}")
                return json.dumps({"path": str(lnk)})
            return json.dumps({"error": "创建快捷方式失败"})
        except Exception as e:
            return json.dumps({"error": str(e)})


def _create_lnk(lnk: Path, target: Path, workdir: Path) -> None:
    ps = (f"$ws=New-Object -ComObject WScript.Shell;"
          f"$sc=$ws.CreateShortcut('{lnk}');"
          f"$sc.TargetPath='cmd.exe';"
          f"$sc.Arguments='/c \"\"{target}\"\"';"
          f"$sc.WorkingDirectory='{workdir}';"
          f"$sc.Description='ExcelManus';"
          f"$sc.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=10, **_NOWIN)


# ═══════════════════════════════════════════════════════════
#  WebServer — HTTP API (stdlib http.server)
# ═══════════════════════════════════════════════════════════

_html_cache: str | None = None
_engine_ref: Engine | None = None


def _get_icon_candidates() -> list[Path]:
    d = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    return [d / "icon.ico", d / "deploy" / "icon.ico", d.parent / "deploy" / "icon.ico",
            d / "web" / "public" / "favicon.ico", d.parent / "web" / "public" / "favicon.ico"]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, body: str):
        self._respond(200, "application/json", body)

    def _respond(self, code, ct, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def do_GET(self):
        global _html_cache
        parsed = urlparse(self.path)
        p = parsed.path
        e = _engine_ref
        if p == "/":
            if _html_cache is None:
                _html_cache = _load_html()
            self._respond(200, "text/html; charset=utf-8", _html_cache)
        elif p == "/api/config":
            self._json(e.get_config_json())
        elif p == "/api/status":
            self._json(e.get_status_json())
        elif p == "/api/logs":
            since = int(parse_qs(parsed.query).get("since", ["0"])[0])
            self._json(e.get_logs_json(since))
        elif p == "/favicon.ico":
            for fp in _get_icon_candidates():
                if fp.is_file():
                    self._respond(200, "image/x-icon", fp.read_bytes())
                    return
            self.send_response(204); self.end_headers()
        else:
            self._respond(404, "text/plain", "Not Found")

    def do_POST(self):
        p = urlparse(self.path).path
        e = _engine_ref
        routes = {
            "/api/deploy":            lambda: (e.start_deploy(), '{"ok":true}')[1],
            "/api/quick-start":       lambda: (e.quick_start(), '{"ok":true}')[1],
            "/api/check-env":         lambda: (e.check_env(), '{"ok":true}')[1],
            "/api/stop":              lambda: (e.stop_services(), '{"ok":true}')[1],
            "/api/force-deploy":      lambda: (e.start_force_deploy(), '{"ok":true}')[1],
            "/api/create-shortcut":   e.create_shortcut,
            "/api/check-update-quick": e.check_update_quick,
            "/api/update-check":      e.check_update,
            "/api/update-apply":      e.apply_update,
        }
        if p in routes:
            self._json(routes[p]())
        elif p == "/api/verify-path":
            d = self._body()
            self._json(e.verify_custom_path(d.get("tool", ""), d.get("path", "")))
        else:
            self._respond(404, "text/plain", "Not Found")


class WebServer:
    def __init__(self, engine: Engine):
        global _engine_ref
        _engine_ref = engine
        self._engine = engine
        self._server: ThreadingHTTPServer | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}/"

    def start(self) -> bool:
        for port in SETUP_PORTS:
            try:
                self._server = ThreadingHTTPServer(("localhost", port), _Handler)
                self.port = port
                threading.Thread(target=self._server.serve_forever, daemon=True).start()
                self._engine.log.hl(f"部署工具 UI: http://localhost:{port}")
                return True
            except OSError:
                continue
        return False

    def stop(self):
        if self._server:
            self._server.shutdown()


# ═══════════════════════════════════════════════════════════
#  App-mode Browser & System Tray
# ═══════════════════════════════════════════════════════════

def _launch_app_mode(url: str) -> None:
    if IS_WINDOWS:
        for base in [os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")]:
            if not base:
                continue
            for browser in [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                            Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]:
                if browser.is_file():
                    try:
                        subprocess.Popen([str(browser), f"--app={url}",
                                          "--window-size=1100,780", "--disable-extensions"],
                                         **_NOWIN)
                        return
                    except Exception:
                        continue
    webbrowser.open(url)


def _generate_brand_icon() -> "Image.Image":
    from PIL import Image, ImageDraw, ImageFilter
    SZ = 256
    img = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Rounded-square background with brand gradient ──
    pad, r = 16, 52
    bg = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    bg_draw.rounded_rectangle(
        [pad, pad, SZ - pad, SZ - pad], radius=r, fill=(33, 115, 70))
    # Vertical gradient overlay: top lighter → bottom darker
    grad = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    for y in range(pad, SZ - pad):
        t = (y - pad) / (SZ - 2 * pad)
        cr = int(51 * (1 - t) + 26 * t)
        cg = int(168 * (1 - t) + 138 * t)
        cb = int(103 * (1 - t) + 80 * t)
        ImageDraw.Draw(grad).line([(pad, y), (SZ - pad, y)],
                                  fill=(cr, cg, cb, 255))
    bg = Image.alpha_composite(bg, grad)
    # Subtle highlight arc at top
    hi = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    hi_draw = ImageDraw.Draw(hi)
    hi_draw.ellipse([pad + 20, pad - 40, SZ - pad - 20, pad + 90],
                    fill=(255, 255, 255, 38))
    bg = Image.alpha_composite(bg, hi)
    img = Image.alpha_composite(img, bg)

    # ── "E" letterform (Excel brand mark) ──
    draw = ImageDraw.Draw(img)
    lw = 22  # stroke width
    ex, ey = 72, 60  # top-left of "E"
    ew, eh = 112, 136  # width, height of "E"
    white = (255, 255, 255, 240)
    # Vertical bar
    draw.rounded_rectangle([ex, ey, ex + lw, ey + eh], radius=8, fill=white)
    # Top horizontal
    draw.rounded_rectangle([ex, ey, ex + ew, ey + lw], radius=8, fill=white)
    # Middle horizontal (slightly shorter)
    mid_y = ey + eh // 2 - lw // 2
    draw.rounded_rectangle([ex, mid_y, ex + ew - 16, mid_y + lw],
                           radius=8, fill=white)
    # Bottom horizontal
    draw.rounded_rectangle([ex, ey + eh - lw, ex + ew, ey + eh],
                           radius=8, fill=white)

    # ── Small diamond accent (bottom-right, matches frontend IconDiamond) ──
    dx, dy, ds = 190, 160, 24
    draw.polygon([(dx, dy - ds), (dx + ds, dy), (dx, dy + ds), (dx - ds, dy)],
                 fill=(51, 168, 103, 200))

    # ── Drop shadow behind the rounded-square ──
    shadow = Image.new("RGBA", (SZ, SZ), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)
    s_draw.rounded_rectangle([pad + 4, pad + 6, SZ - pad + 4, SZ - pad + 6],
                             radius=r, fill=(0, 0, 0, 50))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=8))
    final = Image.alpha_composite(shadow, img)

    return final.resize((64, 64), Image.LANCZOS)


def _create_tray(engine: Engine, server: WebServer):
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        engine.log.warn("pystray/Pillow 未安装，跳过系统托盘图标")
        return None

    # ── Load icon: prefer bundled .ico, fallback to brand-generated ──
    icon_image = None
    for p in _get_icon_candidates():
        if p.is_file() and p.suffix == ".ico":
            try:
                icon_image = Image.open(str(p))
                icon_image = icon_image.resize((64, 64), Image.LANCZOS)
                break
            except Exception:
                continue
    if icon_image is None:
        icon_image = _generate_brand_icon()

    ver = engine.get_version()

    # ── Dynamic status helpers ──
    def _status_text(_) -> str:
        if engine.is_running:
            return f"● 服务运行中  :{engine._fe_port}"
        elif engine._progress >= 100:
            return "○ 部署完成（服务已停止）"
        elif engine._progress > 0:
            return f"◐ 部署中…  {engine._progress}%"
        return "○ 等待部署"

    def _tooltip() -> str:
        base = f"ExcelManus v{ver}"
        if engine.is_running:
            return f"{base} — 运行中 (:{engine._fe_port})"
        return f"{base} — 部署工具"

    # ── Actions ──
    def on_open(_):
        if engine.is_running:
            _launch_app_mode(f"http://localhost:{engine._fe_port}")
        else:
            _launch_app_mode(server.url)

    def on_panel(_):
        webbrowser.open(server.url)

    def on_restart(_):
        if engine.is_running:
            engine.stop_services()
        engine.quick_start()

    def on_stop(_):
        if engine.is_running:
            engine.stop_services()

    def on_exit(icon):
        if engine.is_running:
            engine.stop_services()
        server.stop()
        icon.stop()

    # ── Build menu ──
    menu = pystray.Menu(
        pystray.MenuItem(
            f"ExcelManus v{ver}", None, enabled=False),
        pystray.MenuItem(_status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "打开 ExcelManus", on_open, default=True,
            visible=lambda _: engine.is_running),
        pystray.MenuItem(
            "部署管理面板", on_panel),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "重启服务", on_restart,
            visible=lambda _: engine.is_running),
        pystray.MenuItem(
            "停止服务", on_stop,
            visible=lambda _: engine.is_running),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )

    tray = pystray.Icon("ExcelManus", icon_image, _tooltip(), menu)

    # ── Background thread: keep tooltip fresh ──
    def _tooltip_updater():
        import time as _t
        while tray.visible:
            try:
                tray.title = _tooltip()
            except Exception:
                pass
            _t.sleep(3)

    t = threading.Thread(target=_tooltip_updater, daemon=True)
    t.start()

    return tray


# ═══════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════

def main():
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, True, "ExcelManus_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:
            ctypes.windll.user32.MessageBoxW(
                0, "部署工具已在运行中。\n请检查系统托盘图标。", "ExcelManus", 0x40)
            return

    engine = Engine()
    server = WebServer(engine)

    if not server.start():
        msg = "无法启动本地服务器，端口可能被占用。"
        if IS_WINDOWS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "ExcelManus", 0x10)
        else:
            print(msg, file=sys.stderr)
        return

    _launch_app_mode(server.url)

    tray = _create_tray(engine, server)
    if tray:
        tray.run()
    else:
        print(f"\nExcelManus 部署工具已启动: {server.url}")
        print("按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if engine.is_running:
                engine.stop_services()
            server.stop()


# ═══════════════════════════════════════════════════════════
#  Fallback HTML (minimal functional UI when Vite build unavailable)
# ═══════════════════════════════════════════════════════════

MINIMAL_HTML = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ExcelManus Setup</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;background:#fafafa;color:#1a1a1a;display:flex;flex-direction:column;min-height:100vh}
.hdr{background:#fff;border-bottom:1px solid #e5e5e5;padding:0 24px;height:52px;display:flex;align-items:center;gap:12px}
.hdr h1{font-size:16px;font-weight:700;color:#217346}
.hdr span{font-size:11px;color:#999}
.wrap{max-width:540px;width:100%;margin:24px auto;padding:0 16px}
.card{background:#fff;border:1px solid #e5e5e5;border-radius:12px;padding:20px;margin-bottom:16px}
.card h2{font-size:13px;color:#666;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px}
.env-item{display:flex;align-items:center;padding:10px 0;border-bottom:1px solid #f5f5f5}
.env-item:last-child{border-bottom:none}
.env-icon{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;margin-right:12px;background:#f5f5f5}
.env-icon.ok{background:#e8f5e9;color:#217346}.env-icon.fail{background:#fbe9e7;color:#d32f2f}.env-icon.wait{background:#fff8e1;color:#f9a825}
.env-name{font-size:14px;font-weight:600}.env-detail{font-size:12px;color:#999;margin-top:2px}
.env-detail a{color:#0078d4;text-decoration:none}
.btn{padding:10px 20px;border-radius:8px;font-size:14px;font-weight:600;border:none;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.btn:disabled{opacity:.4;cursor:not-allowed}
.b1{background:#217346;color:#fff}.b1:hover:not(:disabled){background:#1a5c38}
.b2{background:#d32f2f;color:#fff}.b3{background:#fff;color:#217346;border:1.5px solid #d4d4d4}
.btn-row{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
.pbar{height:5px;background:#e5e5e5;border-radius:3px;overflow:hidden;margin:12px 0}
.pfill{height:100%;width:0%;background:linear-gradient(90deg,#217346,#33a867);border-radius:3px;transition:width .5s}
.log{max-height:180px;overflow-y:auto;background:#f5f5f5;padding:8px 12px;border-radius:8px;font-family:monospace;font-size:11px;line-height:1.6;display:none;margin-top:8px}
.log.show{display:block}
.log .ok{color:#217346}.log .err{color:#d32f2f}.log .warn{color:#f9a825}.log .hl{color:#0078d4}
.success{text-align:center;padding:24px}.success .icon{font-size:48px;margin-bottom:12px}
.success .title{font-size:20px;font-weight:700;color:#217346;margin-bottom:4px}
.success .sub{font-size:13px;color:#999;margin-bottom:20px}
.step-panel{display:none}.step-panel.show{display:block}
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #e5e5e5;border-top-color:#f9a825;border-radius:50%;animation:spin .7s linear infinite}
.opt-row{display:flex;align-items:center;gap:8px;padding:10px 0;font-size:13px}
.opt-row label{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.opt-row input[type=checkbox]{width:16px;height:16px;accent-color:#217346;cursor:pointer}
.opt-row .opt-hint{font-size:11px;color:#999}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.45);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:#fff;border-radius:16px;padding:28px;max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,.2);text-align:center}
.modal .m-icon{font-size:40px;margin-bottom:12px}
.modal .m-title{font-size:17px;font-weight:700;color:#1a1a1a;margin-bottom:8px}
.modal .m-desc{font-size:13px;color:#666;line-height:1.6;margin-bottom:6px}
.modal .m-ver{background:#f0f9f4;border:1px solid #d4edda;border-radius:8px;padding:10px;margin:12px 0;font-size:13px;color:#217346}
.modal .m-ver b{font-weight:700}
.modal .m-btns{display:flex;gap:8px;margin-top:16px;justify-content:center}
.modal .m-btns .btn{min-width:100px;justify-content:center}
.modal .m-updating{display:none;margin-top:12px}
.modal .m-updating .spinner{margin-right:6px}
</style></head><body>
<div class="hdr"><h1>ExcelManus</h1><span>Setup</span></div>
<div class="wrap">
  <div class="step-panel show" id="p1">
    <div class="card"><h2>环境检测</h2><div id="env-list"></div></div>
    <div class="card">
      <h2>更新设置</h2>
      <div class="opt-row">
        <label><input type="checkbox" id="chkAutoUpdate" onchange="toggleAutoUpdate(this.checked)"> 启用自动更新检查</label>
      </div>
      <div class="opt-row opt-hint">启用后，每次启动部署工具时将自动检查远程仓库是否有新版本</div>
      <div id="repoStatus" style="display:none;padding:8px 0">
        <div id="repoStatusText" style="font-size:12px;color:#999"></div>
      </div>
    </div>
    <div class="btn-row">
      <button class="btn b3" id="btnRecheck" onclick="doCheck()" style="display:none">重新检测</button>
      <button class="btn b2" id="btnForce" onclick="doForce()" style="display:none">强行部署</button>
      <button class="btn b1" id="btnNext" onclick="goStep2()" disabled>开始部署 →</button>
    </div>
  </div>
  <div class="step-panel" id="p2">
    <div class="card">
      <div id="preDeploy" style="text-align:center;padding:16px">
        <div style="font-size:36px;margin-bottom:8px">🚀</div>
        <div style="font-size:16px;font-weight:700;margin-bottom:16px">一切就绪，准备部署！</div>
        <button class="btn b1" style="width:100%;justify-content:center;padding:12px" onclick="doDeploy()">开始部署</button>
      </div>
      <div id="deploying" style="display:none">
        <div id="dStage" style="font-size:14px;font-weight:700;margin-bottom:4px">正在准备...</div>
        <div class="pbar"><div class="pfill" id="pf"></div></div>
        <div style="font-size:12px;color:#0078d4;cursor:pointer" onclick="toggleLog()">📋 日志</div>
        <div class="log" id="lc"></div>
      </div>
      <div id="successView" style="display:none">
        <div class="success">
          <div class="icon">🎉</div>
          <div class="title">部署成功！</div>
          <div class="sub">服务已启动</div>
          <button class="btn b1" style="width:100%;justify-content:center;padding:12px" onclick="location.href='http://localhost:'+fePort">打开 ExcelManus</button>
          <div style="margin-top:12px;display:flex;gap:8px;justify-content:center">
            <button class="btn b3" onclick="doShortcut()">创建快捷方式</button>
            <button class="btn b2" onclick="doStop()">停止服务</button>
          </div>
        </div>
      </div>
    </div>
    <div class="btn-row" id="s2back"><button class="btn b3" onclick="goStep1()">← 上一步</button></div>
  </div>
</div>
<div class="modal-overlay" id="updateModal">
  <div class="modal">
    <div class="m-icon">🔄</div>
    <div class="m-title">发现新版本</div>
    <div class="m-desc">检测到远程仓库有更新，建议升级到最新版本以获得最新功能和修复。</div>
    <div class="m-ver" id="updateVerInfo"></div>
    <div class="m-btns" id="updateBtns">
      <button class="btn b3" onclick="closeUpdateModal()">稍后再说</button>
      <button class="btn b1" onclick="doApplyUpdate()">立即更新</button>
    </div>
    <div class="m-updating" id="updatingStatus">
      <div style="display:flex;align-items:center;justify-content:center;font-size:13px;color:#217346">
        <div class="spinner"></div> 正在更新，请稍候...
      </div>
    </div>
    <div id="updateResult" style="display:none;margin-top:12px;font-size:13px"></div>
  </div>
</div>
<script>
var ENV=[{id:'python',name:'Python 3.x',dl:'https://www.python.org/downloads/'},
  {id:'node',name:'Node.js',dl:'https://nodejs.org/'},
  {id:'git',name:'Git',dl:'https://git-scm.com/download/win'}];
var logIdx=0,deploying=false,done=false,fePort='3000',autoUpdate=true,updatePromptShown=false;
function init(){
  buildEnv();
  fetch('/api/config').then(r=>r.json()).then(d=>{
    if(d.fePort)fePort=d.fePort;
    if(typeof d.autoUpdate==='boolean'){autoUpdate=d.autoUpdate;document.getElementById('chkAutoUpdate').checked=autoUpdate}
    if(d.updateChecked&&d.updateInfo&&d.updateInfo.has_update){showUpdatePrompt(d.updateInfo)}
    else if(!d.updateChecked){pollUpdateCheck()}
    if(d.quickStart){deploying=true;fetch('/api/quick-start',{method:'POST'})}
    else doCheck()
  }).catch(()=>doCheck());
  setInterval(pollLogs,600);setInterval(pollSt,900)
}
function pollUpdateCheck(){
  var iv=setInterval(function(){
    fetch('/api/config').then(r=>r.json()).then(d=>{
      if(typeof d.autoUpdate==='boolean'){autoUpdate=d.autoUpdate;document.getElementById('chkAutoUpdate').checked=autoUpdate}
      if(d.updateChecked){
        clearInterval(iv);
        showRepoStatus(d.updateInfo);
        if(d.updateInfo&&d.updateInfo.has_update&&!updatePromptShown){showUpdatePrompt(d.updateInfo)}
      }
    })
  },2000)
}
function showRepoStatus(info){
  var el=document.getElementById('repoStatus'),txt=document.getElementById('repoStatusText');
  if(!info){el.style.display='none';return}
  el.style.display='';
  if(info.has_update){txt.innerHTML='<span style="color:#f9a825">⚠ 有新版本可用: '+info.current+' → '+info.latest+' (落后 '+info.behind+' 个提交)</span>'}
  else if(info.no_git){txt.innerHTML='<span style="color:#999">非 Git 仓库，无法检查更新</span>'}
  else if(info.error){txt.innerHTML='<span style="color:#999">检查更新失败: '+info.error+'</span>'}
  else if(info.timeout){txt.innerHTML='<span style="color:#999">检查更新超时</span>'}
  else{txt.innerHTML='<span style="color:#217346">✓ 已是最新版本 ('+info.current+')</span>'}
}
function showUpdatePrompt(info){
  if(!info||!info.has_update||updatePromptShown)return;
  updatePromptShown=true;
  document.getElementById('updateVerInfo').innerHTML='当前版本: <b>'+info.current+'</b> → 最新版本: <b>'+info.latest+'</b><br><span style="font-size:12px;color:#666">落后 '+info.behind+' 个提交</span>';
  document.getElementById('updateModal').className='modal-overlay show';
  document.getElementById('updateBtns').style.display='flex';
  document.getElementById('updatingStatus').style.display='none';
  document.getElementById('updateResult').style.display='none'
}
function closeUpdateModal(){document.getElementById('updateModal').className='modal-overlay'}
function doApplyUpdate(){
  document.getElementById('updateBtns').style.display='none';
  document.getElementById('updatingStatus').style.display='block';
  document.getElementById('updateResult').style.display='none';
  fetch('/api/update-apply',{method:'POST'}).then(r=>r.json()).then(d=>{
    document.getElementById('updatingStatus').style.display='none';
    var res=document.getElementById('updateResult');res.style.display='block';
    if(d.success){res.innerHTML='<div style="color:#217346;font-weight:700">✓ 更新成功！'+d.old_version+' → '+d.new_version+'</div><div style="margin-top:8px"><button class="btn b1" onclick="closeUpdateModal()">继续部署</button></div>'}
    else{res.innerHTML='<div style="color:#d32f2f;font-weight:700">✗ 更新失败</div><div style="color:#666;margin-top:4px">'+(d.error||'未知错误')+'</div><div style="margin-top:8px"><button class="btn b3" onclick="closeUpdateModal()">关闭</button></div>'}
  }).catch(e=>{
    document.getElementById('updatingStatus').style.display='none';
    var res=document.getElementById('updateResult');res.style.display='block';
    res.innerHTML='<div style="color:#d32f2f">更新请求失败: '+e+'</div><div style="margin-top:8px"><button class="btn b3" onclick="closeUpdateModal()">关闭</button></div>'
  })
}
function toggleAutoUpdate(checked){
  autoUpdate=checked;
  fetch('/api/set-auto-update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:checked})})
}
function buildEnv(){var h='';ENV.forEach(e=>{h+='<div class="env-item"><div class="env-icon wait" id="ei_'+e.id+'"><div class="spinner"></div></div><div><div class="env-name">'+e.name+'</div><div class="env-detail" id="ed_'+e.id+'">检测中...</div></div></div>'});document.getElementById('env-list').innerHTML=h}
function doCheck(){buildEnv();document.getElementById('btnRecheck').style.display='none';document.getElementById('btnNext').disabled=true;fetch('/api/check-env',{method:'POST'})}
function updateEnv(checks,details){var allOk=true,anyFail=false;ENV.forEach(e=>{var s=checks[e.id]||0,el=document.getElementById('ei_'+e.id),dl=document.getElementById('ed_'+e.id);if(s===1){el.className='env-icon ok';el.innerHTML='✓';dl.textContent=details[e.id]||'就绪'}else if(s===2){el.className='env-icon fail';el.innerHTML='✗';dl.innerHTML='未找到 — <a href="'+e.dl+'" target="_blank">下载</a>';allOk=false;anyFail=true}else if(s===3){el.className='env-icon wait';el.innerHTML='<div class="spinner"></div>';dl.textContent='检测中...'}else allOk=false});var d=checks.python&&checks.node&&checks.git&&checks.python!==3&&checks.node!==3&&checks.git!==3;if(d){document.getElementById('btnRecheck').style.display='';if(allOk)document.getElementById('btnNext').disabled=false}document.getElementById('btnForce').style.display=(anyFail&&d)?'':'none'}
function goStep1(){document.getElementById('p1').className='step-panel show';document.getElementById('p2').className='step-panel'}
function goStep2(){document.getElementById('p1').className='step-panel';document.getElementById('p2').className='step-panel show'}
function doDeploy(){document.getElementById('preDeploy').style.display='none';document.getElementById('deploying').style.display='';document.getElementById('s2back').style.display='none';deploying=true;fetch('/api/deploy',{method:'POST'})}
function doForce(){if(!confirm('强行部署可能失败，确定继续？'))return;goStep2();document.getElementById('preDeploy').style.display='none';document.getElementById('deploying').style.display='';document.getElementById('s2back').style.display='none';deploying=true;fetch('/api/force-deploy',{method:'POST'})}
function doStop(){fetch('/api/stop',{method:'POST'}).then(()=>{deploying=false;done=false;document.getElementById('preDeploy').style.display='';document.getElementById('deploying').style.display='none';document.getElementById('successView').style.display='none';document.getElementById('s2back').style.display=''})}
function doShortcut(){fetch('/api/create-shortcut',{method:'POST'}).then(r=>r.json()).then(d=>{alert(d.path?'快捷方式已创建: '+d.path:'创建失败: '+(d.error||'未知错误'))})}
var STAGES={0:'正在准备...',14:'下载源码...',28:'下载源码...',42:'安装后端依赖...',57:'安装后端依赖...',71:'安装前端...',85:'启动服务...',100:'部署完成！'};
function closestStage(p){var b='';for(var k in STAGES)if(parseInt(k)<=p)b=STAGES[k];return b||'部署中...'}
function pollLogs(){fetch('/api/logs?since='+logIdx).then(r=>r.json()).then(d=>{var el=document.getElementById('lc');d.logs.forEach(l=>{var div=document.createElement('div');div.className=l.level;div.textContent=l.text;el.appendChild(div);logIdx=l.idx+1});if(d.logs.length)el.scrollTop=el.scrollHeight})}
function pollSt(){fetch('/api/status').then(r=>r.json()).then(d=>{updateEnv(d.checks,d.details||{});if(deploying){document.getElementById('pf').style.width=d.progress+'%';document.getElementById('dStage').textContent=closestStage(d.progress);if(d.running&&!done){done=true;document.getElementById('deploying').style.display='none';document.getElementById('successView').style.display=''}}})}
function toggleLog(){var el=document.getElementById('lc');el.className=el.className.indexOf('show')>=0?'log':'log show';el.scrollTop=el.scrollHeight}
init();
</script></body></html>"""


if __name__ == "__main__":
    main()
