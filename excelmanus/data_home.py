"""集中式数据路径管理 — 跨版本数据发现与迁移。

所有用户数据集中到 ``~/.excelmanus/`` 下，使得不同安装目录
（例如从 GitHub 下载的新版本）能自动找到并复用已有数据。

目录结构::

    ~/.excelmanus/
    ├── excelmanus.db          # 主数据库（已有）
    ├── config.env             # 集中配置（API Key 等）
    ├── installations.json     # 安装注册表
    ├── data/                  # 集中数据根
    │   ├── users/             # 多用户工作区
    │   ├── uploads/           # 上传文件
    │   └── outputs/           # 输出文件
    ├── memory/                # 持久记忆（已有）
    └── skillpacks/            # 用户技能包（已有）
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


@contextmanager
def _file_lock(lock_path: str | Path, timeout: float = 5.0) -> Iterator[None]:
    """跨平台文件锁（标准库实现，无需第三方依赖）。

    在 Windows 上使用 msvcrt.locking，在 Unix 上使用 fcntl.flock。
    超时后放弃加锁并发出警告（不阻塞业务流程）。
    """
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fp = None
    locked = False
    try:
        fp = open(lock_path, "w", encoding="utf-8")  # noqa: SIM115
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except (OSError, IOError):
                time.sleep(0.1)
        if not locked:
            logger.warning("文件锁获取超时 (%ss): %s — 继续执行（可能存在并发风险）", timeout, lock_path)
        yield
    finally:
        if fp is not None:
            if locked:
                try:
                    if sys.platform == "win32":
                        import msvcrt
                        msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                except (OSError, IOError):
                    pass
            fp.close()


def _normalize_path(p: str) -> str:
    """规范化路径字符串，Windows 下统一为小写以适配大小写不敏感的文件系统。"""
    resolved = str(Path(p).expanduser().resolve())
    if sys.platform == "win32":
        return resolved.lower()
    return resolved


# ── 路径常量 ──────────────────────────────────────────────

_EXCELMANUS_HOME = Path.home() / ".excelmanus"
_CONFIG_ENV_NAME = "config.env"
_INSTALLATIONS_NAME = "installations.json"
_INSTALLATIONS_LOCK = ".installations.lock"
_DATA_DIR_NAME = "data"


# ── 路径获取 ──────────────────────────────────────────────


def get_config_home() -> Path:
    """返回 ExcelManus 全局配置目录: ``~/.excelmanus``。"""
    return _EXCELMANUS_HOME


def get_data_home() -> Path:
    """返回集中数据根目录。

    优先使用环境变量 ``EXCELMANUS_DATA_ROOT``，
    默认 ``~/.excelmanus/data``。
    """
    env = os.environ.get("EXCELMANUS_DATA_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return _EXCELMANUS_HOME / _DATA_DIR_NAME


def get_config_env_path() -> Path:
    """返回集中配置文件路径: ``~/.excelmanus/config.env``。"""
    return _EXCELMANUS_HOME / _CONFIG_ENV_NAME


def get_installations_path() -> Path:
    """返回安装注册表路径: ``~/.excelmanus/installations.json``。"""
    return _EXCELMANUS_HOME / _INSTALLATIONS_NAME


# ── 目录初始化 ────────────────────────────────────────────


def ensure_data_dirs() -> Path:
    """确保集中数据目录结构存在，返回 data_home 路径。"""
    data_home = get_data_home()
    for sub in ("users", "uploads", "outputs"):
        (data_home / sub).mkdir(parents=True, exist_ok=True)
    return data_home


def ensure_config_home() -> Path:
    """确保 ``~/.excelmanus`` 目录存在。"""
    _EXCELMANUS_HOME.mkdir(parents=True, exist_ok=True)
    return _EXCELMANUS_HOME


# ── 集中配置加载 ──────────────────────────────────────────


def load_centralized_config() -> dict[str, str]:
    """加载 ``~/.excelmanus/config.env`` 中的键值对。

    返回 dict（不修改 os.environ），调用者决定如何合并。
    支持 ``KEY=VALUE`` 格式，忽略注释和空行。
    """
    config_path = get_config_env_path()
    if not config_path.is_file():
        return {}
    result: dict[str, str] = {}
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去除引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key:
                result[key] = value
    except Exception as e:
        logger.warning("加载集中配置失败: %s", e)
    return result


def inject_centralized_config() -> int:
    """将集中配置注入 ``os.environ``，不覆盖已有值。

    返回注入的变量数量。
    """
    config = load_centralized_config()
    count = 0
    for key, value in config.items():
        if key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def migrate_project_env(project_root: str | Path) -> bool:
    """将项目目录内的 ``.env`` 复制到集中配置位置。

    仅在集中配置不存在时执行。返回是否执行了迁移。
    """
    project_root = Path(project_root).expanduser().resolve()
    project_env = project_root / ".env"
    config_env = get_config_env_path()

    if not project_env.is_file():
        return False
    if config_env.is_file():
        # 已有集中配置，不覆盖
        return False

    ensure_config_home()
    try:
        shutil.copy2(str(project_env), str(config_env))
        logger.info("已将项目配置迁移到集中位置: %s → %s", project_env, config_env)
        return True
    except Exception as e:
        logger.warning("配置迁移失败: %s", e)
        return False


# ── 安装注册表 ────────────────────────────────────────────


def _load_installations() -> list[dict[str, Any]]:
    """加载安装注册表。"""
    path = get_installations_path()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "installations" in data:
            return data["installations"]
    except Exception as e:
        logger.warning("加载安装注册表失败（文件可能损坏）: %s", e)
    return []


def _save_installations(installations: list[dict[str, Any]]) -> None:
    """保存安装注册表（原子写入：先写临时文件再重命名）。"""
    ensure_config_home()
    path = get_installations_path()
    content = json.dumps(
        {"installations": installations},
        indent=2,
        ensure_ascii=False,
    )
    # 原子写入：写入同目录临时文件，然后 rename（同文件系统内是原子操作）
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".installations_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            # os.replace 在所有平台上都支持原子覆盖（包括 Windows）
            os.replace(tmp_path, str(path))
        except BaseException:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        logger.error("保存安装注册表失败: %s", e)
        raise


def register_installation(
    project_root: str | Path,
    version: str = "",
) -> dict[str, Any]:
    """注册当前安装到 ``~/.excelmanus/installations.json``。

    如果相同路径已注册，更新版本和时间。返回注册条目。
    使用文件锁保护 load→modify→save 的原子性。
    """
    project_root_str = str(Path(project_root).expanduser().resolve())
    project_root_norm = _normalize_path(project_root_str)

    if not version:
        try:
            import excelmanus
            version = excelmanus.__version__
        except Exception:
            version = "unknown"

    lock_path = _EXCELMANUS_HOME / _INSTALLATIONS_LOCK
    with _file_lock(lock_path):
        installations = _load_installations()

        # 查找是否已注册（Windows 下大小写不敏感匹配）
        entry: dict[str, Any] | None = None
        for inst in installations:
            if _normalize_path(inst.get("path", "")) == project_root_norm:
                entry = inst
                break

        now = datetime.now(timezone.utc).isoformat()
        if entry is not None:
            entry["version"] = version
            entry["last_seen"] = now
            entry["platform"] = platform.system()
        else:
            entry = {
                "path": project_root_str,
                "version": version,
                "installed_at": now,
                "last_seen": now,
                "platform": platform.system(),
                "shortcut": "",
            }
            installations.append(entry)

        _save_installations(installations)

    logger.info("安装已注册: %s (v%s)", project_root_str, version)
    return entry


def discover_old_installations() -> list[dict[str, Any]]:
    """返回所有已注册的安装记录（按 last_seen 倒序）。"""
    installations = _load_installations()
    installations.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
    return installations


def get_current_installation(project_root: str | Path) -> dict[str, Any] | None:
    """查找指定路径的安装记录。"""
    norm = _normalize_path(str(project_root))
    for inst in _load_installations():
        if _normalize_path(inst.get("path", "")) == norm:
            return inst
    return None


# ── 主动扫描发现 ────────────────────────────────────────


def _read_version_from_dir(directory: Path) -> str | None:
    """尝试从目录中读取 ExcelManus 版本号。

    检测方式（按优先级）：
    1. ``excelmanus/__init__.py`` 中的 ``__version__``
    2. ``pyproject.toml`` 中的 ``version = "..."``（需 name 包含 excelmanus）
    """
    # 方式 1: __init__.py
    init_py = directory / "excelmanus" / "__init__.py"
    if init_py.is_file():
        try:
            for line in init_py.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("__version__"):
                    # __version__ = "1.6.1"
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().strip("\"'")
        except Exception:
            pass

    # 方式 2: pyproject.toml
    toml_path = directory / "pyproject.toml"
    if toml_path.is_file():
        try:
            content = toml_path.read_text(encoding="utf-8")
            # 确认是 excelmanus 项目
            if "excelmanus" not in content.lower():
                return None
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    return stripped.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass

    return None


def scan_for_installations(
    extra_dirs: list[str | Path] | None = None,
    *,
    skip_desktop_scan: bool = False,
) -> list[dict[str, Any]]:
    """主动扫描磁盘上的 ExcelManus 安装目录。

    扫描策略：
    1. 当前安装的同级目录（兄弟目录）
    2. 桌面目录（skip_desktop_scan=True 时跳过）
    3. 用户主目录下的常见开发目录（skip_desktop_scan=True 时跳过）
    4. 调用者指定的额外目录

    Args:
        skip_desktop_scan: 服务器/Docker 模式下设为 True，跳过桌面、下载、
            开发目录的扫描（无 GUI 环境下这些目录通常不存在或无意义）。

    找到的安装会自动注册到 ``installations.json``。
    返回所有发现的安装记录。
    """
    # 收集候选扫描根目录
    scan_roots: list[Path] = []

    # 当前安装的父目录（扫描兄弟目录）
    current_project = Path(__file__).resolve().parent.parent
    if current_project.parent.is_dir():
        scan_roots.append(current_project.parent)

    if not skip_desktop_scan:
        # 桌面
        home = Path.home()
        for desktop_name in ("Desktop", "桌面"):
            desktop = home / desktop_name
            if desktop.is_dir():
                scan_roots.append(desktop)

        # 下载目录
        for dl_name in ("Downloads", "下载"):
            dl_dir = home / dl_name
            if dl_dir.is_dir():
                scan_roots.append(dl_dir)

        # 常见开发目录
        for dev_name in ("Projects", "projects", "dev", "Dev", "Code", "code", "workspace"):
            dev_dir = home / dev_name
            if dev_dir.is_dir():
                scan_roots.append(dev_dir)

    # 额外指定目录
    if extra_dirs:
        for d in extra_dirs:
            p = Path(d).expanduser().resolve()
            if p.is_dir():
                scan_roots.append(p)

    # 去重
    seen_roots: set[str] = set()
    unique_roots: list[Path] = []
    for r in scan_roots:
        norm = _normalize_path(str(r))
        if norm not in seen_roots:
            seen_roots.add(norm)
            unique_roots.append(r)

    # 扫描：只扫描 1 层深度的子目录
    found: list[dict[str, Any]] = []
    already_registered = {
        _normalize_path(inst.get("path", ""))
        for inst in _load_installations()
    }

    for root in unique_roots:
        try:
            candidates = [root] + [
                d for d in root.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ]
        except PermissionError:
            continue

        for candidate in candidates:
            norm = _normalize_path(str(candidate))
            if norm in already_registered:
                continue

            version = _read_version_from_dir(candidate)
            if version is not None:
                entry = register_installation(candidate, version)
                found.append(entry)
                already_registered.add(norm)
                logger.info("扫描发现 ExcelManus 安装: %s (v%s)", candidate, version)

    return found


_scan_done = False


def scan_once(*, skip_desktop_scan: bool = False) -> list[dict[str, Any]]:
    """首次调用时执行一次主动扫描，后续调用直接返回空列表。

    适合在应用启动时调用，避免每次请求都扫描。

    Args:
        skip_desktop_scan: 传递给 scan_for_installations()，服务器模式下跳过桌面扫描。
    """
    global _scan_done
    if _scan_done:
        return []
    _scan_done = True
    try:
        return scan_for_installations(skip_desktop_scan=skip_desktop_scan)
    except Exception as e:
        logger.debug("主动扫描失败（非致命）: %s", e)
        return []


# ── 数据迁移 ──────────────────────────────────────────────


def has_project_local_data(project_root: str | Path) -> bool:
    """检测项目目录内是否存在本地数据（users/uploads/outputs/.env）。"""
    root = Path(project_root).expanduser().resolve()
    for name in ("users", "uploads", "outputs"):
        d = root / name
        try:
            if d.is_dir() and any(d.iterdir()):
                return True
        except PermissionError:
            logger.debug("无权限读取目录: %s", d)
            continue
    if (root / ".env").is_file():
        return True
    return False


def migrate_data_from_project(
    project_root: str | Path,
    *,
    force: bool = False,
) -> dict[str, int]:
    """从旧的项目内数据迁移到集中数据目录。

    - ``.env`` → ``~/.excelmanus/config.env``
    - ``users/`` → ``~/.excelmanus/data/users/``
    - ``uploads/`` → ``~/.excelmanus/data/uploads/``
    - ``outputs/`` → ``~/.excelmanus/data/outputs/``

    只复制不删除源文件。返回 ``{迁移类别: 文件数}``。
    """
    root = Path(project_root).expanduser().resolve()
    data_home = ensure_data_dirs()
    stats: dict[str, int] = {}

    # 迁移 .env
    if not force and get_config_env_path().is_file():
        pass  # 已有集中配置
    else:
        if migrate_project_env(root):
            stats["config"] = 1

    # 迁移目录
    for name in ("users", "uploads", "outputs"):
        src = root / name
        if not src.is_dir():
            continue
        dst = data_home / name
        count = 0
        for item in src.rglob("*"):
            if item.is_file():
                rel = item.relative_to(src)
                target = dst / rel
                if target.exists() and not force:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(item), str(target))
                count += 1
        if count:
            stats[name] = count
            logger.info("迁移 %s/: %d 个文件 → %s", name, count, dst)

    return stats


def is_data_centralized() -> bool:
    """检测集中数据目录是否已初始化（非空）。"""
    data_home = get_data_home()
    if not data_home.is_dir():
        return False
    for sub in ("users", "uploads", "outputs"):
        d = data_home / sub
        if d.is_dir() and any(d.iterdir()):
            return True
    return False
