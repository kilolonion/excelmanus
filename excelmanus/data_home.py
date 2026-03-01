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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 路径常量 ──────────────────────────────────────────────

_EXCELMANUS_HOME = Path.home() / ".excelmanus"
_CONFIG_ENV_NAME = "config.env"
_INSTALLATIONS_NAME = "installations.json"
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
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "installations" in data:
            return data["installations"]
    except Exception:
        pass
    return []


def _save_installations(installations: list[dict[str, Any]]) -> None:
    """保存安装注册表。"""
    ensure_config_home()
    path = get_installations_path()
    path.write_text(
        json.dumps(
            {"installations": installations},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def register_installation(
    project_root: str | Path,
    version: str = "",
) -> dict[str, Any]:
    """注册当前安装到 ``~/.excelmanus/installations.json``。

    如果相同路径已注册，更新版本和时间。返回注册条目。
    """
    project_root = str(Path(project_root).expanduser().resolve())

    if not version:
        try:
            import excelmanus
            version = excelmanus.__version__
        except Exception:
            version = "unknown"

    installations = _load_installations()

    # 查找是否已注册
    entry: dict[str, Any] | None = None
    for inst in installations:
        if inst.get("path") == project_root:
            entry = inst
            break

    now = datetime.now(timezone.utc).isoformat()
    if entry is not None:
        entry["version"] = version
        entry["last_seen"] = now
        entry["platform"] = platform.system()
    else:
        entry = {
            "path": project_root,
            "version": version,
            "installed_at": now,
            "last_seen": now,
            "platform": platform.system(),
            "shortcut": "",
        }
        installations.append(entry)

    _save_installations(installations)
    logger.info("安装已注册: %s (v%s)", project_root, version)
    return entry


def discover_old_installations() -> list[dict[str, Any]]:
    """返回所有已注册的安装记录（按 last_seen 倒序）。"""
    installations = _load_installations()
    installations.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
    return installations


def get_current_installation(project_root: str | Path) -> dict[str, Any] | None:
    """查找指定路径的安装记录。"""
    project_root = str(Path(project_root).expanduser().resolve())
    for inst in _load_installations():
        if inst.get("path") == project_root:
            return inst
    return None


# ── 数据迁移 ──────────────────────────────────────────────


def has_project_local_data(project_root: str | Path) -> bool:
    """检测项目目录内是否存在本地数据（users/uploads/outputs/.env）。"""
    root = Path(project_root).expanduser().resolve()
    for name in ("users", "uploads", "outputs"):
        d = root / name
        if d.is_dir() and any(d.iterdir()):
            return True
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
