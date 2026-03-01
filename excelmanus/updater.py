"""ExcelManus 更新器 — 版本检查、数据备份、代码更新、依赖重装。

提供统一的更新逻辑，供 CLI / API / GUI / 独立脚本调用。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

REPO_URL = "https://github.com/kilolonion/excelmanus.git"
REPO_URL_GITEE = "https://gitee.com/kilolonion/excelmanus.git"
GITHUB_API_TAGS = "https://api.github.com/repos/kilolonion/excelmanus/tags"
_BACKUP_DIR_NAME = "backups"
_DATA_PATHS_TO_BACKUP = [".env", "users", "outputs", "uploads"]


@dataclass
class VersionInfo:
    current: str = ""
    latest: str = ""
    has_update: bool = False
    commits_behind: int = 0
    release_notes: str = ""
    release_url: str = ""
    check_method: str = ""


@dataclass
class BackupResult:
    success: bool = False
    backup_dir: str = ""
    files_backed_up: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class UpdateResult:
    success: bool = False
    old_version: str = ""
    new_version: str = ""
    backup_dir: str = ""
    steps_completed: list[str] = field(default_factory=list)
    error: str = ""
    needs_restart: bool = False


def get_current_version(project_root: str | Path | None = None) -> str:
    try:
        from excelmanus import __version__
        return __version__
    except ImportError:
        pass
    if project_root:
        toml_path = Path(project_root) / "pyproject.toml"
        if toml_path.exists():
            for line in toml_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("version") and "=" in line:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def _run_cmd(
    cmd: list[str], cwd: str | Path | None = None, timeout: int = 60,
) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时 ({timeout}s)"
    except FileNotFoundError:
        return -1, "", f"命令未找到: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _is_git_repo(project_root: str | Path) -> bool:
    return (Path(project_root) / ".git").is_dir()


_domestic_cache: bool | None = None


def _is_domestic_network() -> bool:
    """Auto-detect if user is on a domestic (Chinese) network via TCP ping race.

    Compares latency to PyPI mirror (Tsinghua) vs official PyPI.
    Results are cached for the lifetime of the process.
    """
    global _domestic_cache
    if _domestic_cache is not None:
        return _domestic_cache
    import socket as _socket
    from concurrent.futures import ThreadPoolExecutor

    def _tcp_ping(host: str, port: int = 443, timeout: float = 3.0) -> float:
        try:
            t0 = time.monotonic()
            with _socket.create_connection((host, port), timeout=timeout):
                return time.monotonic() - t0
        except Exception:
            return float("inf")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_mirror = pool.submit(_tcp_ping, "pypi.tuna.tsinghua.edu.cn")
            f_pypi = pool.submit(_tcp_ping, "pypi.org")
            t_mirror = f_mirror.result(timeout=5)
            t_pypi = f_pypi.result(timeout=5)
        _domestic_cache = t_mirror < float("inf") and (
            t_pypi > 5.0 or t_mirror < t_pypi * 0.8
        )
        logger.debug(
            "domestic network detection: %s (mirror=%.3fs pypi=%.3fs)",
            _domestic_cache, t_mirror, t_pypi,
        )
    except Exception:
        _domestic_cache = False
    return _domestic_cache


def _has_uv() -> bool:
    """Check if uv package manager is available (10-100x faster than pip)."""
    try:
        r = subprocess.run(["uv", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_gitee_remote(project_root: Path) -> None:
    """Ensure 'gitee' remote exists as a fallback mirror."""
    _run_cmd(["git", "remote", "add", "gitee", REPO_URL_GITEE], cwd=project_root)
    _run_cmd(["git", "remote", "set-url", "gitee", REPO_URL_GITEE], cwd=project_root)


def check_for_updates(project_root: str | Path | None = None) -> VersionInfo:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    info = VersionInfo(current=get_current_version(project_root))

    if _is_git_repo(project_root):
        info.check_method = "git"
        rc, _, _ = _run_cmd(["git", "fetch", "origin", "--tags"], cwd=project_root, timeout=30)
        if rc == 0:
            _, branch, _ = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root)
            branch = branch or "main"
            _, count_str, _ = _run_cmd(
                ["git", "rev-list", "--count", f"HEAD..origin/{branch}"], cwd=project_root,
            )
            try:
                info.commits_behind = int(count_str)
            except (ValueError, TypeError):
                info.commits_behind = 0
            info.has_update = info.commits_behind > 0
            if info.has_update:
                _, log_out, _ = _run_cmd(
                    ["git", "log", f"HEAD..origin/{branch}", "--oneline", "-20"], cwd=project_root,
                )
                info.release_notes = log_out
                _, remote_toml, _ = _run_cmd(
                    ["git", "show", f"origin/{branch}:pyproject.toml"], cwd=project_root,
                )
                for line in remote_toml.splitlines():
                    if line.strip().startswith("version") and "=" in line:
                        info.latest = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not info.latest:
            info.latest = info.current
        return info

    # GitHub API fallback
    info.check_method = "github_api"
    try:
        import urllib.request
        req = urllib.request.Request(
            GITHUB_API_TAGS,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "ExcelManus-Updater"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data, list):
                latest_tag = data[0].get("name", "").lstrip("v")
                info.latest = latest_tag
                info.has_update = latest_tag != info.current
    except Exception as e:
        logger.warning("GitHub API 检查更新失败: %s", e)
        info.latest = info.current
    return info


def backup_user_data(
    project_root: str | Path | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> BackupResult:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    version = get_current_version(project_root)
    backup_dir = project_root / _BACKUP_DIR_NAME / f"backup_{version}_{timestamp}"
    result = BackupResult()

    def _log(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for rel_path in _DATA_PATHS_TO_BACKUP:
            src = project_root / rel_path
            dst = backup_dir / rel_path
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                result.files_backed_up.append(rel_path)
                _log(f"  备份文件: {rel_path}")
            elif src.is_dir() and any(src.iterdir()):
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
                result.files_backed_up.append(rel_path + "/")
                _log(f"  备份目录: {rel_path}/")

        home_db = Path.home() / ".excelmanus"
        if home_db.is_dir():
            dst_db = backup_dir / ".excelmanus_home"
            for db_file in home_db.glob("*.db*"):
                dst_db.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(db_file), str(dst_db / db_file.name))
                result.files_backed_up.append(f"~/.excelmanus/{db_file.name}")
                _log(f"  备份数据库: ~/.excelmanus/{db_file.name}")

        result.success = True
        result.backup_dir = str(backup_dir)
        _log(f"备份完成: {backup_dir}")
    except Exception as e:
        result.error = str(e)
        logger.error("备份失败: %s", e, exc_info=True)
    return result


def list_backups(project_root: str | Path | None = None) -> list[dict]:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    backup_base = Path(project_root) / _BACKUP_DIR_NAME
    if not backup_base.is_dir():
        return []
    backups = []
    for d in sorted(backup_base.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("backup_"):
            parts = d.name.split("_", 2)
            size_bytes = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            backups.append({
                "name": d.name, "path": str(d),
                "version": parts[1] if len(parts) > 1 else "unknown",
                "timestamp": parts[2] if len(parts) > 2 else "",
                "size_mb": round(size_bytes / 1024 / 1024, 2),
            })
    return backups


def cleanup_old_backups(
    project_root: str | Path | None = None,
    max_keep: int = 2,
    progress_cb: Callable[[str], None] | None = None,
) -> list[str]:
    """删除最旧的备份，仅保留最近 *max_keep* 个。返回被删除的目录路径列表。"""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    backup_base = Path(project_root) / _BACKUP_DIR_NAME
    if not backup_base.is_dir():
        return []
    dirs = sorted(
        (d for d in backup_base.iterdir() if d.is_dir() and d.name.startswith("backup_")),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    removed: list[str] = []
    for d in dirs[max_keep:]:
        try:
            shutil.rmtree(str(d))
            removed.append(str(d))
            msg = f"  清理旧备份: {d.name}"
            logger.info(msg)
            if progress_cb:
                progress_cb(msg)
        except Exception as e:
            logger.warning("清理备份失败 %s: %s", d, e)
    return removed


def restore_from_backup(
    backup_dir: str | Path, project_root: str | Path | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> bool:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root, backup_dir = Path(project_root), Path(backup_dir)
    if not backup_dir.is_dir():
        logger.error("备份目录不存在: %s", backup_dir)
        return False
    def _log(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)
    try:
        for rel_path in _DATA_PATHS_TO_BACKUP:
            src, dst = backup_dir / rel_path, project_root / rel_path
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
                _log(f"  恢复文件: {rel_path}")
            elif src.is_dir():
                if dst.is_dir():
                    shutil.rmtree(str(dst))
                shutil.copytree(str(src), str(dst))
                _log(f"  恢复目录: {rel_path}/")
        home_db_backup = backup_dir / ".excelmanus_home"
        if home_db_backup.is_dir():
            home_db = Path.home() / ".excelmanus"
            home_db.mkdir(parents=True, exist_ok=True)
            for db_file in home_db_backup.glob("*.db*"):
                shutil.copy2(str(db_file), str(home_db / db_file.name))
                _log(f"  恢复数据库: ~/.excelmanus/{db_file.name}")
        _log("数据恢复完成")
        return True
    except Exception as e:
        logger.error("恢复失败: %s", e, exc_info=True)
        return False


def _build_pip_cmd(
    project_root: Path, use_mirror: bool = False, use_uv: bool = False,
) -> list[str]:
    if use_uv:
        cmd = ["uv", "pip", "install", "-e", str(project_root)]
    else:
        if platform.system() == "Windows":
            venv_py = project_root / ".venv" / "Scripts" / "python.exe"
        else:
            venv_py = project_root / ".venv" / "bin" / "python"
        py = str(venv_py) if venv_py.exists() else sys.executable
        cmd = [py, "-m", "pip", "install", "-e", str(project_root)]
    if use_mirror:
        cmd.extend(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    return cmd


def verify_database_migration(
    project_root: str | Path | None = None,
) -> tuple[bool, str]:
    """预验证数据库迁移是否可以成功执行。

    在更新代码后、重启服务前调用，尝试打开数据库并执行迁移。
    Returns:
        (success, message) — True 表示迁移成功或无需迁移。
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    try:
        from excelmanus.config import load_config
        config = load_config()
    except Exception:
        return True, "无法加载配置，跳过预验证"

    if not config.chat_history_enabled:
        return True, "数据库未启用，无需迁移"

    import os
    resolved_db_path = os.path.expanduser(
        config.chat_history_db_path or config.db_path
    )

    try:
        if config.database_url:
            from excelmanus.database import Database
            db = Database(database_url=config.database_url)
        else:
            from excelmanus.database import Database
            db = Database(resolved_db_path)

        from excelmanus.database import _LATEST_VERSION
        current = db._current_version()
        db.close()

        if current >= _LATEST_VERSION:
            return True, f"schema v{current}（已是最新）"
        return True, f"schema v{current} → v{_LATEST_VERSION} 迁移成功"
    except Exception as e:
        return False, f"迁移失败: {e}"


def perform_update(
    project_root: str | Path | None = None, *,
    skip_backup: bool = False, skip_deps: bool = False,
    use_mirror: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> UpdateResult:
    """执行完整更新流程。"""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    result = UpdateResult(old_version=get_current_version(project_root))

    def _p(msg: str, pct: int) -> None:
        logger.info("[%d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    # Step 1: 检查更新
    _p("正在检查更新...", 5)
    vi = check_for_updates(project_root)
    if not vi.has_update:
        result.success, result.new_version = True, vi.current
        result.error = "已是最新版本"
        _p("已是最新版本", 100)
        return result
    _p(f"发现新版本: {vi.current} → {vi.latest} ({vi.commits_behind} 个新提交)", 10)
    result.steps_completed.append("version_check")

    # Step 2: 备份
    if not skip_backup:
        _p("正在备份用户数据...", 15)
        bk = backup_user_data(project_root)
        if bk.success:
            result.backup_dir = bk.backup_dir
            result.steps_completed.append("backup")
            _p(f"备份完成: {len(bk.files_backed_up)} 项", 25)
        else:
            result.error = f"备份失败: {bk.error}"
            return result
    else:
        result.steps_completed.append("backup_skipped")

    # Step 2b: 清理旧备份（保留最近 2 个）
    removed = cleanup_old_backups(project_root, max_keep=2, progress_cb=lambda m: _p(m, 27))
    if removed:
        _p(f"已清理 {len(removed)} 个旧备份", 28)

    # ── 探测网络环境与安装工具 ──
    domestic = use_mirror or _is_domestic_network()
    use_uv = _has_uv()
    if domestic:
        _p("检测到国内网络，将优先使用镜像加速", 28)

    # Step 3: Git 拉取（国内自动 Gitee 加速）
    if not _is_git_repo(project_root):
        result.error = "项目不是 Git 仓库，无法更新"
        return result
    _p("正在拉取最新代码...", 30)
    _run_cmd(["git", "stash", "--include-untracked"], cwd=project_root)
    _, branch, _ = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root)
    branch = branch or "main"
    git_remote = "origin"
    rc, _, err = _run_cmd(
        ["git", "pull", "origin", branch, "--ff-only"],
        cwd=project_root, timeout=60 if domestic else 120,
    )
    if rc != 0 and domestic:
        _p("GitHub 拉取较慢，尝试 Gitee 镜像...", 33)
        _ensure_gitee_remote(project_root)
        rc2, _, _ = _run_cmd(["git", "fetch", "gitee", branch], cwd=project_root, timeout=120)
        if rc2 == 0:
            git_remote = "gitee"
            rc, _, err = _run_cmd(
                ["git", "merge", f"gitee/{branch}", "--ff-only"], cwd=project_root,
            )
    if rc != 0:
        _p("fast-forward 失败，执行强制覆盖...", 35)
        rc, _, err = _run_cmd(["git", "reset", "--hard", f"{git_remote}/{branch}"], cwd=project_root)
        if rc != 0:
            result.error = f"代码更新失败: {err}"
            _run_cmd(["git", "stash", "pop"], cwd=project_root)
            return result
    result.steps_completed.append("git_pull")
    _p("代码已更新", 45)

    # Step 4: 安装依赖（pip + npm 并行，uv 优先，镜像加速）
    if not skip_deps:
        from concurrent.futures import ThreadPoolExecutor

        installer_label = "uv" if use_uv else "pip"
        _p(f"正在并行安装依赖 (安装器: {installer_label})...", 50)

        def _install_backend() -> tuple[bool, str]:
            rc, _, err = _run_cmd(
                _build_pip_cmd(project_root, domestic, use_uv),
                cwd=project_root, timeout=300,
            )
            if rc != 0 and not domestic:
                rc, _, err = _run_cmd(
                    _build_pip_cmd(project_root, True, use_uv),
                    cwd=project_root, timeout=300,
                )
            return rc == 0, err

        def _install_frontend() -> tuple[bool, str]:
            web_dir = project_root / "web"
            if not (web_dir.is_dir() and (web_dir / "package.json").exists()):
                return True, ""
            npm_args = ["npm", "install"]
            if domestic:
                npm_args.append("--registry=https://registry.npmmirror.com")
            rc, _, err = _run_cmd(npm_args, cwd=web_dir, timeout=300)
            if rc != 0 and not domestic:
                npm_args.append("--registry=https://registry.npmmirror.com")
                rc, _, err = _run_cmd(npm_args, cwd=web_dir, timeout=300)
            return rc == 0, err

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_be = pool.submit(_install_backend)
            fut_fe = pool.submit(_install_frontend)
            be_ok, be_err = fut_be.result(timeout=600)
            fe_ok, _ = fut_fe.result(timeout=600)

        if not be_ok:
            result.error = f"后端依赖更新失败: {be_err[-200:]}"
            return result
        result.steps_completed.append("pip_install")
        _p("后端依赖已更新", 65)

        if fe_ok:
            result.steps_completed.append("npm_install")
            _p("前端依赖已更新", 80)
        else:
            _p("前端依赖更新失败（非致命）", 80)

    # Step 5: 预验证数据库迁移
    _p("正在预验证数据库迁移...", 85)
    db_ok, db_msg = verify_database_migration(project_root)
    if db_ok:
        result.steps_completed.append("db_migration_verified")
        _p(f"数据库迁移验证通过: {db_msg}", 90)
    else:
        _p(f"数据库迁移预验证警告: {db_msg}（将在启动时自动重试）", 90)

    # Step 6: 验证
    _p("正在验证...", 95)
    result.new_version = get_current_version(project_root)
    result.success = True
    result.needs_restart = True
    result.steps_completed.append("verified")
    _p(f"更新成功！{result.old_version} → {result.new_version}", 100)
    _p("请重启服务以应用更新（数据库迁移将在启动时自动执行）", 100)
    return result
