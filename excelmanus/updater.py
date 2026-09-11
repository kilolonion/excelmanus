"""ExcelManus 更新器 — 版本检查、数据备份、代码更新、依赖重装、远程部署。

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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

REPO_URL = "https://gitee.com/kilolonion/excelmanus.git"
REPO_URL_GITHUB = "https://github.com/kilolonion/excelmanus.git"
GITEE_API_TAGS = "https://gitee.com/api/v5/repos/kilolonion/excelmanus/tags?per_page=100"
GITHUB_API_TAGS = "https://api.github.com/repos/kilolonion/excelmanus/tags?per_page=100"
_BACKUP_DIR_NAME = "backups"

# ── 版本检查 TTL 缓存 ──────────────────────────────────
_version_check_cache: VersionInfo | None = None
_version_check_cache_time: float = 0.0
_VERSION_CHECK_TTL = 300.0  # 5 分钟 TTL
_FAILED_CHECK_TTL = 60.0   # 失败时仅缓存 60 秒，便于快速重试

# ── perform_update 互斥锁，防止并发更新 ──────────────────
_update_lock = threading.Lock()


def _invalidate_version_cache() -> None:
    """清除版本检查 TTL 缓存，使下次 check_for_updates 重新请求。"""
    global _version_check_cache, _version_check_cache_time
    _version_check_cache = None
    _version_check_cache_time = 0.0


def _parse_version_tuple(v: str) -> tuple[int, ...]:
    """将版本字符串解析为可比较的整数元组，用于 semver 比较。

    例如 '1.6.10' → (1, 6, 10)，无法解析时回退到 (0,)。
    """
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except (ValueError, AttributeError):
        return (0,)


@dataclass
class VersionInfo:
    current: str = ""
    latest: str = ""
    has_update: bool = False
    commits_behind: int = 0
    release_notes: str = ""
    release_url: str = ""
    check_method: str = ""
    check_failed: bool = False


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


def _read_version_from_disk(project_root: Path) -> str:
    """从磁盘文件直接读取版本号，绕过 Python 模块缓存。

    优先读 pyproject.toml（唯一版本源），
    回退到 excelmanus/__init__.py 中的 __version__。
    """
    toml_path = project_root / "pyproject.toml"
    if toml_path.is_file():
        try:
            for line in toml_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    init_py = project_root / "excelmanus" / "__init__.py"
    if init_py.is_file():
        try:
            for line in init_py.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("__version__"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return "unknown"


def get_current_version(project_root: str | Path | None = None) -> str:
    """获取当前版本号，始终从磁盘读取以避免 Python 模块缓存问题。"""
    if project_root:
        v = _read_version_from_disk(Path(project_root))
        if v != "unknown":
            return v
    # 回退: 尝试默认项目根目录
    default_root = Path(__file__).resolve().parent.parent
    v = _read_version_from_disk(default_root)
    if v != "unknown":
        return v
    # 最终回退: import（会被缓存，但聊胜于无）
    try:
        from excelmanus import __version__
        return __version__
    except ImportError:
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
_domestic_cache_time: float = 0.0
_DOMESTIC_CACHE_TTL = 1800.0  # 30 分钟 TTL


def _is_domestic_network() -> bool:
    """Auto-detect if user is on a domestic (Chinese) network via TCP ping race.

    Compares latency to PyPI mirror (Tsinghua) vs official PyPI.
    Results are cached with 30-minute TTL.
    """
    global _domestic_cache, _domestic_cache_time
    if (
        _domestic_cache is not None
        and (time.monotonic() - _domestic_cache_time) < _DOMESTIC_CACHE_TTL
    ):
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
        _domestic_cache_time = time.monotonic()
        logger.debug(
            "domestic network detection: %s (mirror=%.3fs pypi=%.3fs)",
            _domestic_cache, t_mirror, t_pypi,
        )
    except Exception:
        _domestic_cache = False
        _domestic_cache_time = time.monotonic()
    return _domestic_cache


def _has_uv() -> bool:
    """Check if uv package manager is available (10-100x faster than pip)."""
    try:
        r = subprocess.run(["uv", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_github_remote(project_root: Path) -> None:
    """Ensure 'github' remote exists as a fallback mirror."""
    _run_cmd(["git", "remote", "add", "github", REPO_URL_GITHUB], cwd=project_root)
    _run_cmd(["git", "remote", "set-url", "github", REPO_URL_GITHUB], cwd=project_root)


def check_for_updates(
    project_root: str | Path | None = None, *, force: bool = False,
) -> VersionInfo:
    """检查是否有可用更新。

    结果带 TTL 缓存（默认 5 分钟），避免频繁网络请求。
    设置 *force=True* 跳过缓存强制刷新。
    """
    global _version_check_cache, _version_check_cache_time

    if (
        not force
        and _version_check_cache is not None
        and (time.monotonic() - _version_check_cache_time) < _VERSION_CHECK_TTL
    ):
        return _version_check_cache

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    info = VersionInfo(current=get_current_version(project_root))

    if _is_git_repo(project_root):
        info.check_method = "git"
        rc, _, _ = _run_cmd(["git", "fetch", "origin", "--tags"], cwd=project_root, timeout=30)
        # origin 失败时尝试 GitHub 备用源
        git_remote = "origin"
        if rc != 0:
            _ensure_github_remote(project_root)
            rc, _, _ = _run_cmd(["git", "fetch", "github", "--tags"], cwd=project_root, timeout=30)
            if rc == 0:
                git_remote = "github"
        if rc != 0:
            # 两个源都 fetch 失败：标记失败 + 短 TTL 缓存
            info.check_failed = True
            info.latest = info.current
            _version_check_cache = info
            _version_check_cache_time = time.monotonic() - (_VERSION_CHECK_TTL - _FAILED_CHECK_TTL)
            return info
        # fetch 成功，比较版本
        _, branch, _ = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=project_root)
        branch = branch or "main"
        _, count_str, _ = _run_cmd(
            ["git", "rev-list", "--count", f"HEAD..{git_remote}/{branch}"], cwd=project_root,
        )
        try:
            info.commits_behind = int(count_str)
        except (ValueError, TypeError):
            info.commits_behind = 0
        info.has_update = info.commits_behind > 0
        if info.has_update:
            _, log_out, _ = _run_cmd(
                ["git", "log", f"HEAD..{git_remote}/{branch}", "--oneline", "-20"], cwd=project_root,
            )
            info.release_notes = log_out
            _, remote_toml, _ = _run_cmd(
                ["git", "show", f"{git_remote}/{branch}:pyproject.toml"], cwd=project_root,
            )
            for line in remote_toml.splitlines():
                if line.strip().startswith("version") and "=" in line:
                    info.latest = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not info.latest:
            info.latest = info.current
        # 更新 TTL 缓存（git 路径）
        _version_check_cache = info
        _version_check_cache_time = time.monotonic()
        return info

    # Gitee API 优先，GitHub API 备用
    import urllib.request

    def _fetch_latest_tag(api_url: str, headers: dict) -> str | None:
        """从 API 获取所有 tag 并返回版本号最大的那个。

        遍历全部 tag 做 semver 比较，而非假设 data[0] 是最新的
        （API 按创建时间排序，不是按版本号排序）。
        """
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not data or not isinstance(data, list):
                    return None
                best_ver: str = ""
                best_tuple: tuple[int, ...] = (0,)
                for tag_obj in data:
                    raw = tag_obj.get("name", "").lstrip("v")
                    if not raw:
                        continue
                    vt = _parse_version_tuple(raw)
                    if vt > best_tuple:
                        best_tuple = vt
                        best_ver = raw
                return best_ver or None
        except Exception:
            return None

    # 1) 先尝试 Gitee
    info.check_method = "gitee_api"
    latest_tag = _fetch_latest_tag(
        GITEE_API_TAGS, {"User-Agent": "ExcelManus-Updater"},
    )
    # 2) Gitee 失败则回退 GitHub
    if not latest_tag:
        info.check_method = "github_api"
        latest_tag = _fetch_latest_tag(
            GITHUB_API_TAGS,
            {"Accept": "application/vnd.github.v3+json", "User-Agent": "ExcelManus-Updater"},
        )
    if latest_tag:
        info.latest = latest_tag
        info.has_update = (
            _parse_version_tuple(latest_tag)
            > _parse_version_tuple(info.current)
        )
    else:
        logger.warning("Gitee/GitHub API 检查更新均失败")
        info.latest = info.current
        info.check_failed = True

    # 更新 TTL 缓存（失败时用短 TTL）
    _version_check_cache = info
    if info.check_failed:
        _version_check_cache_time = time.monotonic() - (_VERSION_CHECK_TTL - _FAILED_CHECK_TTL)
    else:
        _version_check_cache_time = time.monotonic()
    return info


def _backup_sqlite_database(src: Path, dst: Path) -> None:
    """Consistent SQLite copy via backup() + WAL checkpoint.

    Non-SQLite ``*.db`` files fall back to a byte copy so tests and stray
    files still round-trip.
    """
    import sqlite3

    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    header = b""
    try:
        with src.open("rb") as fh:
            header = fh.read(16)
    except OSError:
        header = b""
    if not header.startswith(b"SQLite format 3"):
        shutil.copy2(str(src), str(dst))
        return
    src_conn = sqlite3.connect(str(src), timeout=30.0)
    try:
        try:
            src_conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error:
            logger.debug("wal checkpoint skipped for %s", src, exc_info=True)
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _atomic_restore_file(src: Path, dst: Path) -> None:
    """Write to a sibling temp file, then replace the destination."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + "._restore_tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_dir():
            shutil.rmtree(str(tmp))
        else:
            tmp.unlink()
    shutil.copy2(str(src), str(tmp))
    os.replace(str(tmp), str(dst))


def backup_user_data(
    project_root: str | Path | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> BackupResult:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    version = get_current_version(project_root)

    from excelmanus.data_home import get_excelmanus_home

    home_root = get_excelmanus_home()
    backup_base = home_root / _BACKUP_DIR_NAME
    backup_dir = backup_base / f"backup_{version}_{timestamp}"
    result = BackupResult()

    def _log(msg: str) -> None:
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        home_dst = backup_dir / "excelmanus_home"
        home_dst.mkdir(parents=True, exist_ok=True)

        if home_root.is_dir():
            for db_file in sorted(home_root.glob("*.db")):
                if db_file.is_file():
                    _backup_sqlite_database(db_file, home_dst / db_file.name)
                    result.files_backed_up.append(db_file.name)
                    _log(f"  备份数据库: {db_file.name}")
            config_env = home_root / "config.env"
            if config_env.is_file():
                shutil.copy2(str(config_env), str(home_dst / "config.env"))
                result.files_backed_up.append("config.env")
                _log("  备份正式仓: config.env")
            for sub in ("data", "memory", "skillpacks"):
                src = home_root / sub
                if src.is_dir() and any(src.iterdir()):
                    shutil.copytree(str(src), str(home_dst / sub), dirs_exist_ok=True)
                    result.files_backed_up.append(f"{sub}/")
                    _log(f"  备份目录: {sub}/")

        env_file = project_root / ".env"
        if env_file.is_file():
            shutil.copy2(str(env_file), str(backup_dir / ".env"))
            result.files_backed_up.append(".env")
            _log("  备份文件: .env")

        result.success = True
        result.backup_dir = str(backup_dir)
        _log(f"备份完成: {backup_dir}")
    except Exception as e:
        result.error = str(e)
        logger.error("备份失败: %s", e, exc_info=True)
        if backup_dir.is_dir():
            shutil.rmtree(str(backup_dir), ignore_errors=True)
    return result


# 备份目录名格式: backup_{version}_{YYYYMMDD_HHMMSS}
_BACKUP_NAME_RE = None


def _get_backup_name_re():
    """Lazy-compile regex for backup directory name parsing."""
    global _BACKUP_NAME_RE
    if _BACKUP_NAME_RE is None:
        import re
        _BACKUP_NAME_RE = re.compile(
            r"^backup_(?P<version>[\d.]+[\w.-]*)_(?P<timestamp>\d{8}_\d{6})$"
        )
    return _BACKUP_NAME_RE


def _get_backup_bases(project_root: Path) -> list[Path]:
    """返回备份目录（优先 EXCELMANUS_HOME/backups，兼容旧的项目根 backups）。"""
    bases: list[Path] = []
    seen: set[Path] = set()
    try:
        from excelmanus.data_home import get_excelmanus_home
        home_backup = get_excelmanus_home() / _BACKUP_DIR_NAME
        if home_backup.is_dir():
            resolved = home_backup.resolve()
            bases.append(home_backup)
            seen.add(resolved)
    except Exception:
        pass
    proj_backup = project_root / _BACKUP_DIR_NAME
    if proj_backup.is_dir():
        resolved = proj_backup.resolve()
        if resolved not in seen:
            bases.append(proj_backup)
    return bases


def find_backup_dir(backup_name: str, project_root: str | Path | None = None) -> Path | None:
    """按备份目录名在所有备份根下查找。"""
    if not backup_name or backup_name.startswith(".") or "/" in backup_name or "\\" in backup_name or ".." in backup_name:
        return None
    if not backup_name.startswith("backup_"):
        return None
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    for base in _get_backup_bases(Path(project_root)):
        candidate = base / backup_name
        if candidate.is_dir():
            return candidate
    return None


def list_backups(project_root: str | Path | None = None) -> list[dict]:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    backup_bases = _get_backup_bases(Path(project_root))
    if not backup_bases:
        return []
    pattern = _get_backup_name_re()
    backups = []
    for backup_base in backup_bases:
      for d in sorted(backup_base.iterdir(), reverse=True):
        if not d.is_dir() or not d.name.startswith("backup_"):
            continue
        m = pattern.match(d.name)
        if m:
            version = m.group("version")
            timestamp = m.group("timestamp")
        else:
            # 回退：旧格式或不规则名称
            parts = d.name.split("_", 2)
            version = parts[1] if len(parts) > 1 else "unknown"
            timestamp = parts[2] if len(parts) > 2 else ""
        size_bytes = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        backups.append({
            "name": d.name, "path": str(d),
            "version": version,
            "timestamp": timestamp,
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
    backup_bases = _get_backup_bases(Path(project_root))
    if not backup_bases:
        return []
    # 合并所有备份目录中的备份，统一按时间排序
    all_dirs: list[Path] = []
    for backup_base in backup_bases:
        all_dirs.extend(
            d for d in backup_base.iterdir() if d.is_dir() and d.name.startswith("backup_")
        )
    dirs = sorted(all_dirs, key=lambda d: d.stat().st_mtime, reverse=True)
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

    def _restore_tree(src: Path, dst: Path) -> None:
        dst_tmp = dst.with_name(dst.name + "._restore_tmp")
        if dst_tmp.is_dir():
            shutil.rmtree(str(dst_tmp))
        shutil.copytree(str(src), str(dst_tmp))
        if dst.is_dir():
            shutil.rmtree(str(dst))
        dst_tmp.rename(dst)

    try:
        from excelmanus.data_home import get_excelmanus_home

        home_db = get_excelmanus_home()
        home_src = backup_dir / "excelmanus_home"
        if not home_src.is_dir():
            home_src = backup_dir / ".excelmanus_home"
        home_db.mkdir(parents=True, exist_ok=True)
        if home_src.is_dir():
            for db_file in home_src.glob("*.db*"):
                if not db_file.is_file():
                    continue
                _atomic_restore_file(db_file, home_db / db_file.name)
                _log(f"  恢复数据库: {db_file.name}")
            config_src = home_src / "config.env"
            if config_src.is_file():
                _atomic_restore_file(config_src, home_db / "config.env")
                _log("  恢复正式仓: config.env")
            for sub in ("data", "memory", "skillpacks"):
                src = home_src / sub
                if src.is_dir():
                    _restore_tree(src, home_db / sub)
                    _log(f"  恢复目录: {sub}/")

        env_src = backup_dir / ".env"
        if env_src.is_file():
            _atomic_restore_file(env_src, project_root / ".env")
            _log("  恢复文件: .env")

        # 兼容旧备份：项目根 users/outputs/uploads
        for rel_path in ("users", "outputs", "uploads"):
            src = backup_dir / rel_path
            dst = project_root / rel_path
            if src.is_dir():
                _restore_tree(src, dst)
                _log(f"  恢复目录: {rel_path}/")

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
    """预检数据库连接与 schema 版本，判断启动时是否需要自动迁移。

    在更新代码后、重启服务前调用。仅检查数据库可达性和当前 schema 版本，
    不实际执行迁移 SQL（迁移在服务启动时由 Database 构造函数自动完成）。
    Returns:
        (success, message) — True 表示数据库可达；False 表示连接失败。
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    try:
        from excelmanus.config import load_config
        config = load_config()
    except Exception:
        return True, "无法加载配置，跳过预验证"

    if not config.database_url and not (config.chat_history_db_path or config.db_path):
        return True, "数据库路径未配置，跳过预验证"

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
        return True, f"schema v{current} → v{_LATEST_VERSION} 待迁移（将在启动时自动执行）"
    except Exception as e:
        return False, f"迁移失败: {e}"


def perform_update(
    project_root: str | Path | None = None, *,
    skip_backup: bool = False, skip_deps: bool = False,
    use_mirror: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> UpdateResult:
    """在已停机的工作树上执行更新。服务仍在监听时请走 upgrade helper。"""
    if not _update_lock.acquire(blocking=False):
        return UpdateResult(error="另一个更新正在进行中，请等待完成后再试")
    try:
        if project_root is None:
            project_root = Path(__file__).resolve().parent.parent
        project_root = Path(project_root)
        from excelmanus.upgrade.helper import api_is_running
        from excelmanus.upgrade.apply import apply_on_stopped_tree

        if api_is_running():
            return UpdateResult(
                error="API 仍在运行。请使用设置页升级，或先停止服务再执行 CLI 更新。",
            )
        if not skip_backup:
            bk = backup_user_data(project_root)
            if not bk.success:
                return UpdateResult(error=f"备份失败: {bk.error}")
            cleanup_old_backups(project_root, max_keep=2)
        return apply_on_stopped_tree(
            project_root,
            skip_deps=skip_deps,
            use_mirror=use_mirror,
            progress_cb=progress_cb,
        )
    finally:
        _update_lock.release()



# ═══════════════════════════════════════════════════════════
# 远程部署：本地构建前端制品 → deploy.sh 推送到远程服务器
# ═══════════════════════════════════════════════════════════

_deploy_lock = threading.Lock()


@dataclass
class DeployResult:
    success: bool = False
    version: str = ""
    artifact_path: str = ""
    steps_completed: list[str] = field(default_factory=list)
    deploy_output: str = ""
    error: str = ""


@dataclass
class DeployConfig:
    """远程部署参数，对应 deploy.sh 的命令行选项。"""
    # 目标
    target: str = "full"           # full | backend | frontend
    # 前端制品
    skip_build: bool = False       # 跳过本地前端构建（使用已有制品）
    artifact_path: str = ""        # 已有制品路径（skip_build=True 时使用）
    # deploy.sh 选项
    from_local: bool = True        # 从本地 rsync（默认 True，否则远端 git pull）
    skip_deps: bool = False        # 跳过依赖安装
    force: bool = True             # 跳过确认提示
    verbose: bool = False          # 详细输出
    extra_args: list[str] = field(default_factory=list)  # 额外传递给 deploy.sh 的参数


def _find_deploy_script(project_root: Path) -> Path | None:
    """定位 deploy.sh 脚本。"""
    candidates = [
        project_root / "deploy" / "deploy.sh",
        project_root / "deploy.sh",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _check_deploy_env(project_root: Path) -> tuple[bool, str]:
    """检查部署环境：deploy.sh 存在、.env.deploy 配置、SSH 密钥等。"""
    script = _find_deploy_script(project_root)
    if not script:
        return False, "未找到 deploy/deploy.sh 部署脚本"

    env_deploy = script.parent / ".env.deploy"
    if not env_deploy.is_file():
        return False, (
            f"未找到部署配置文件: {env_deploy}\n"
            "请先创建 deploy/.env.deploy 并配置服务器信息。"
        )

    return True, f"部署脚本: {script}"


def build_frontend_artifact(
    project_root: str | Path | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
) -> DeployResult:
    """本地构建前端并打包为 standalone 制品 (tar.gz)。

    步骤:
      1. npm ci（安装依赖）
      2. npm run build（构建 Next.js standalone）
      3. tar -czf 打包 .next/standalone + .next/static + public
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    web_dir = project_root / "web"
    result = DeployResult(version=get_current_version(project_root))

    def _p(msg: str, pct: int) -> None:
        logger.info("[deploy-build %d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    if not (web_dir / "package.json").exists():
        result.error = f"前端项目不存在: {web_dir}"
        return result

    # Step 1: npm ci
    _p("安装前端依赖 (npm ci)...", 10)
    domestic = _is_domestic_network()
    npm_args = ["npm", "ci"]
    if domestic:
        npm_args.append("--registry=https://registry.npmmirror.com")
    rc, _, err = _run_cmd(npm_args, cwd=web_dir, timeout=300)
    if rc != 0:
        result.error = f"npm ci 失败: {err[-500:]}"
        return result
    result.steps_completed.append("npm_ci")

    # Step 2: npm run build
    _p("构建前端 (npm run build)...", 30)
    build_env = {**os.environ, "NODE_OPTIONS": "--max-old-space-size=8192"}
    try:
        r = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(web_dir), capture_output=True, text=True,
            timeout=600, env=build_env,
        )
        if r.returncode != 0:
            result.error = f"前端构建失败:\n{r.stderr[-1000:]}"
            return result
    except subprocess.TimeoutExpired:
        result.error = "前端构建超时（600s）"
        return result
    except Exception as e:
        result.error = f"前端构建异常: {e}"
        return result
    result.steps_completed.append("build")

    # Step 3: 验证构建产物
    standalone_dir = web_dir / ".next" / "standalone"
    static_dir = web_dir / ".next" / "static"
    public_dir = web_dir / "public"

    if not standalone_dir.is_dir():
        result.error = "构建产物缺失: .next/standalone 目录不存在"
        return result
    _p("构建完成，打包制品...", 70)

    # Step 4: 打包
    dist_dir = project_root / "web-dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    version = result.version
    artifact_name = f"frontend-standalone-v{version}.tar.gz"
    artifact_path = dist_dir / artifact_name

    # 删除旧制品
    if artifact_path.exists():
        artifact_path.unlink()

    # 构建 tar 参数列表
    tar_sources = [".next/standalone"]
    if static_dir.is_dir():
        tar_sources.append(".next/static")
    if public_dir.is_dir():
        tar_sources.append("public")

    rc, _, err = _run_cmd(
        ["tar", "-czf", str(artifact_path)] + tar_sources,
        cwd=web_dir, timeout=120,
    )
    if rc != 0:
        # Windows 可能没有 tar，尝试 Python 原生
        try:
            import tarfile
            with tarfile.open(str(artifact_path), "w:gz") as tf:
                for src in tar_sources:
                    src_path = web_dir / src
                    if src_path.exists():
                        tf.add(str(src_path), arcname=src)
        except Exception as e2:
            result.error = f"打包失败: tar={err}, python={e2}"
            return result
    result.steps_completed.append("package")

    size_mb = artifact_path.stat().st_size / 1024 / 1024
    result.artifact_path = str(artifact_path)
    result.success = True
    _p(f"制品打包完成: {artifact_name} ({size_mb:.1f} MB)", 100)
    return result


def perform_remote_deploy(
    project_root: str | Path | None = None, *,
    config: DeployConfig | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
) -> DeployResult:
    """执行远程部署完整流程。

    使用互斥锁防止并发部署。
    流程: 环境检查 → (可选)本地构建 → deploy.sh 执行部署。
    """
    if not _deploy_lock.acquire(blocking=False):
        return DeployResult(error="另一个部署正在进行中，请等待完成后再试")

    try:
        return _perform_remote_deploy_impl(
            project_root, config=config, progress_cb=progress_cb,
        )
    finally:
        _deploy_lock.release()


def _perform_remote_deploy_impl(
    project_root: str | Path | None = None, *,
    config: DeployConfig | None = None,
    progress_cb: Callable[[str, int], None] | None = None,
) -> DeployResult:
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)
    if config is None:
        config = DeployConfig()
    result = DeployResult(version=get_current_version(project_root))

    def _p(msg: str, pct: int) -> None:
        logger.info("[deploy %d%%] %s", pct, msg)
        if progress_cb:
            progress_cb(msg, pct)

    # Step 1: 环境检查
    _p("检查部署环境...", 5)
    ok, msg = _check_deploy_env(project_root)
    if not ok:
        result.error = msg
        return result
    result.steps_completed.append("env_check")
    _p(msg, 8)

    deploy_script = _find_deploy_script(project_root)
    if not deploy_script:
        result.error = "未找到 deploy.sh（环境检查通过但脚本丢失）"
        return result
    artifact_path = config.artifact_path

    # Step 2: 构建前端制品（如果需要部署前端且未跳过构建）
    need_frontend = config.target in ("full", "frontend")
    if need_frontend and not config.skip_build:
        _p("开始构建前端制品...", 10)
        build_result = build_frontend_artifact(
            project_root,
            progress_cb=lambda m, p: _p(m, 10 + int(p * 0.5)),
        )
        if not build_result.success:
            result.error = f"前端构建失败: {build_result.error}"
            result.steps_completed.extend(
                f"build:{s}" for s in build_result.steps_completed
            )
            return result
        artifact_path = build_result.artifact_path
        result.artifact_path = artifact_path
        result.steps_completed.append("frontend_build")
        _p(f"前端制品就绪: {Path(artifact_path).name}", 60)

    # Step 3: 执行 deploy.sh
    _p("执行远程部署...", 65)
    deploy_cmd = ["bash", str(deploy_script), "deploy"]

    # 模式
    if config.target == "backend":
        deploy_cmd.append("--backend-only")
    elif config.target == "frontend":
        deploy_cmd.append("--frontend-only")

    # 前端制品
    if artifact_path and need_frontend:
        deploy_cmd.extend(["--frontend-artifact", artifact_path])

    # 选项
    if config.from_local:
        deploy_cmd.append("--from-local")
    if config.skip_deps:
        deploy_cmd.append("--skip-deps")
    if config.force:
        deploy_cmd.append("--force")
    if config.verbose:
        deploy_cmd.append("--verbose")

    # 额外参数
    deploy_cmd.extend(config.extra_args)

    _p(f"执行: {' '.join(deploy_cmd[-6:])}", 70)

    try:
        r = subprocess.run(
            deploy_cmd,
            cwd=str(project_root),
            capture_output=True, text=True,
            timeout=900,  # 15 分钟超时
            env={**os.environ, "FORCE_COLOR": "0"},
        )
        result.deploy_output = r.stdout[-5000:] if r.stdout else ""
        if r.returncode != 0:
            result.error = (
                f"deploy.sh 退出码 {r.returncode}\n"
                f"{r.stderr[-2000:]}\n{r.stdout[-2000:]}"
            )
            result.steps_completed.append("deploy_failed")
            return result
    except subprocess.TimeoutExpired:
        result.error = "部署超时（900s）"
        return result
    except FileNotFoundError:
        result.error = "bash 未找到，请确保系统安装了 bash（Windows 上需要 Git Bash 或 WSL）"
        return result
    except Exception as e:
        result.error = f"部署异常: {e}"
        return result

    result.steps_completed.append("deploy")
    result.success = True
    _p("远程部署完成！", 100)
    return result


def _parse_env_deploy(project_root: Path) -> dict[str, str]:
    """解析 deploy/.env.deploy 返回键值对字典。"""
    env_file = project_root / "deploy" / ".env.deploy"
    result: dict[str, str] = {}
    if not env_file.is_file():
        return result
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return result


_REMOTE_LOCK_PATH = "/tmp/.excelmanus-deploy.lock"
_REMOTE_LOCK_TTL = 1800  # 30 分钟


def check_remote_deploy_lock(
    project_root: str | Path | None = None,
) -> dict:
    """检查远程服务器上的部署锁状态。

    返回:
        {
            "locked": bool,
            "holder_host": str,   # 持锁者主机名
            "holder_user": str,   # 持锁者用户名
            "holder_pid": str,    # 持锁进程 PID
            "locked_since": str,  # Unix 时间戳
            "elapsed_s": int,     # 已持续秒数
            "expired": bool,      # 是否已超过 TTL
            "error": str | None,  # SSH 连接失败等错误
        }
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    env = _parse_env_deploy(project_root)
    backend_host = env.get("BACKEND_HOST") or env.get("BACKEND_SERVER") or ""
    ssh_user = env.get("SSH_USER") or env.get("SERVER_USER") or ""
    ssh_key = env.get("SSH_KEY_PATH") or env.get("BACKEND_SSH_KEY_PATH") or ""
    if env.get("SSH_KEY_NAME"):
        ssh_key = str(project_root / env["SSH_KEY_NAME"])
    if env.get("BACKEND_SSH_KEY_NAME"):
        ssh_key = str(project_root / env["BACKEND_SSH_KEY_NAME"])
    ssh_port = env.get("SSH_PORT", "22")

    default = {
        "locked": False,
        "holder_host": "",
        "holder_user": "",
        "holder_pid": "",
        "locked_since": "",
        "elapsed_s": 0,
        "expired": False,
        "error": None,
    }

    if not backend_host or not ssh_user:
        default["error"] = "未配置远程服务器信息"
        return default

    # 构建 SSH 命令
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new"]
    if ssh_key:
        ssh_cmd.extend(["-i", ssh_key])
    if ssh_port and ssh_port != "22":
        ssh_cmd.extend(["-p", ssh_port])
    ssh_cmd.append(f"{ssh_user}@{backend_host}")
    ssh_cmd.append(f"cat '{_REMOTE_LOCK_PATH}' 2>/dev/null || echo ''")

    try:
        r = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=10, check=False)
        content = r.stdout.strip()
        if not content:
            return default

        # 解析: hostname:PID:timestamp:user
        parts = content.split(":", 3)
        if len(parts) < 3:
            return default

        r_host = parts[0]
        r_pid = parts[1]
        r_ts = parts[2]
        r_user = parts[3] if len(parts) > 3 else ""

        try:
            ts_int = int(r_ts)
        except (ValueError, TypeError):
            ts_int = 0

        elapsed = int(time.time()) - ts_int
        expired = elapsed >= _REMOTE_LOCK_TTL

        return {
            "locked": True,
            "holder_host": r_host,
            "holder_user": r_user,
            "holder_pid": r_pid,
            "locked_since": r_ts,
            "elapsed_s": max(0, elapsed),
            "expired": expired,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        default["error"] = "SSH 连接超时"
        return default
    except FileNotFoundError:
        default["error"] = "ssh 命令不可用"
        return default
    except Exception as e:
        default["error"] = str(e)[:200]
        return default


def get_deploy_status(
    project_root: str | Path | None = None,
) -> dict:
    """获取部署环境状态信息。"""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    script = _find_deploy_script(project_root)
    env_deploy = (project_root / "deploy" / ".env.deploy") if script else None

    # 解析 .env.deploy 中的关键配置
    env = _parse_env_deploy(project_root)
    servers: dict[str, str] = {}
    site_urls: list[str] = []
    for k, v in env.items():
        if k in ("BACKEND_HOST", "BACKEND_SERVER") and v:
            servers["backend"] = v
        elif k in ("FRONTEND_HOST", "FRONTEND_SERVER") and v:
            servers["frontend"] = v
        elif k in ("SITE_URL", "SITE_URLS") and v:
            site_urls = [u.strip() for u in v.split(",") if u.strip()]

    # 检查最近的制品
    dist_dir = project_root / "web-dist"
    artifacts: list[dict] = []
    if dist_dir.is_dir():
        for f in sorted(dist_dir.glob("frontend-standalone-*.tar.gz"), reverse=True):
            stat = f.stat()
            artifacts.append({
                "name": f.name,
                "path": str(f),
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

    # 部署历史
    history: list[str] = []
    history_file = project_root / "deploy" / ".deploy_history"
    if history_file.is_file():
        try:
            lines = history_file.read_text(encoding="utf-8").strip().splitlines()
            history = lines[-10:]  # 最近 10 条
        except Exception:
            pass

    # 本地锁信息
    local_lock_info: dict | None = None
    lock_file = project_root / "deploy" / ".deploy.lock"
    if lock_file.is_file():
        try:
            lines = lock_file.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                local_lock_info = {
                    "pid": lines[0] if len(lines) > 0 else "",
                    "started_at": lines[1] if len(lines) > 1 else "",
                }
        except Exception:
            pass

    return {
        "deploy_script_found": script is not None,
        "deploy_script_path": str(script) if script else None,
        "env_deploy_found": env_deploy is not None and env_deploy.is_file(),
        "servers": servers,
        "site_urls": site_urls,
        "version": get_current_version(project_root),
        "artifacts": artifacts,
        "recent_history": history,
        "is_deploying": _deploy_lock.locked(),
        "local_lock": local_lock_info,
    }


# ═══════════════════════════════════════════════════════════
# 结构化部署历史
# ═══════════════════════════════════════════════════════════


def get_deploy_history_structured(
    project_root: str | Path | None = None,
) -> list[dict]:
    """读取 .deploy_history.json 返回结构化部署历史列表。"""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    json_file = project_root / "deploy" / ".deploy_history.json"
    if not json_file.is_file():
        return []
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        logger.debug("读取结构化部署历史失败", exc_info=True)
    return []


# ═══════════════════════════════════════════════════════════
# 远程回滚
# ═══════════════════════════════════════════════════════════

_rollback_lock = threading.Lock()


def perform_remote_rollback(
    project_root: str | Path | None = None,
    *,
    target: str = "full",
    release_id: str = "",
    commit: str = "",
    skip_deps: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> dict:
    """执行远程回滚。通过调用 deploy.sh rollback-to 实现。"""
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    if not _rollback_lock.acquire(blocking=False):
        return {"success": False, "error": "另一个回滚正在进行中"}

    try:
        return _perform_remote_rollback_impl(
            project_root,
            target=target,
            release_id=release_id,
            commit=commit,
            skip_deps=skip_deps,
            progress_cb=progress_cb,
        )
    finally:
        _rollback_lock.release()


def _perform_remote_rollback_impl(
    project_root: Path,
    *,
    target: str,
    release_id: str,
    commit: str,
    skip_deps: bool,
    progress_cb: Callable[[str, int], None] | None,
) -> dict:
    def _p(msg: str, pct: int) -> None:
        if progress_cb:
            progress_cb(msg, pct)

    script = _find_deploy_script(project_root)
    if not script:
        return {"success": False, "error": "未找到 deploy.sh"}

    # 构建命令
    if release_id or commit:
        cmd = ["bash", str(script), "rollback-to", "--force"]
        if release_id:
            cmd += ["--release", release_id]
        elif commit:
            cmd += ["--commit", commit]
    else:
        cmd = ["bash", str(script), "rollback", "--force"]

    if target == "backend":
        cmd.append("--backend-only")
    elif target == "frontend":
        cmd.append("--frontend-only")

    if skip_deps:
        cmd.append("--skip-deps")

    _p("正在执行回滚...", 10)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        _p("回滚命令执行完成", 90)

        if proc.returncode == 0:
            _p("回滚成功", 100)
            return {
                "success": True,
                "output": output[-2000:],
                "target": target,
                "release_id": release_id,
                "commit": commit,
            }
        else:
            _p(f"回滚失败 (exit {proc.returncode})", 100)
            return {
                "success": False,
                "error": f"deploy.sh 退出码 {proc.returncode}",
                "output": output[-2000:],
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "回滚超时（300s）"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}



def get_deploy_log(
    project_root: str | Path | None = None,
    release_id: str = "",
) -> str:
    """读取指定 release_id 的部署日志。

    日志文件约定: deploy/.deploy_logs/{release_id}.log
    回退: deploy/.deploy_history 中对应行（纯文本历史）。
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    project_root = Path(project_root)

    if not release_id:
        return ""

    # 安全检查：release_id 不能包含路径穿越字符
    if "/" in release_id or "\\" in release_id or ".." in release_id:
        return ""

    # 优先查找独立日志文件
    log_file = project_root / "deploy" / ".deploy_logs" / f"{release_id}.log"
    if log_file.is_file():
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            return content[-10000:]  # 限制返回大小
        except Exception:
            pass

    # 回退：从 .deploy_history 纯文本中查找匹配行
    history_file = project_root / "deploy" / ".deploy_history"
    if history_file.is_file():
        try:
            for line in history_file.read_text(encoding="utf-8").splitlines():
                if release_id in line:
                    return line
        except Exception:
            pass

    return ""
