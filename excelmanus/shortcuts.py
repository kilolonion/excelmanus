"""跨平台桌面快捷方式管理。

支持 Windows (.lnk)、macOS (.command)、Linux (.desktop) 三种平台。
快捷方式独立于应用目录，更换安装位置后可自动更新指向。
"""
from __future__ import annotations

import logging
import os
import platform
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_APP_NAME = "ExcelManus"


def _get_desktop_path() -> Path | None:
    """获取当前用户桌面路径。"""
    system = platform.system()
    if system == "Windows":
        # 优先使用 USERPROFILE
        home = os.environ.get("USERPROFILE") or Path.home()
        desktop = Path(home) / "Desktop"
        if desktop.is_dir():
            return desktop
        # 中文 Windows
        desktop_cn = Path(home) / "桌面"
        if desktop_cn.is_dir():
            return desktop_cn
    elif system == "Darwin":
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            return desktop
    else:  # Linux
        # XDG
        xdg = os.environ.get("XDG_DESKTOP_DIR")
        if xdg:
            p = Path(xdg)
            if p.is_dir():
                return p
        desktop = Path.home() / "Desktop"
        if desktop.is_dir():
            return desktop
    return None


def _find_start_script(project_root: Path) -> Path | None:
    """在项目目录中寻找最佳启动脚本。"""
    system = platform.system()
    candidates: list[str]
    if system == "Windows":
        candidates = [
            "deploy/start.bat",
            "deploy/start.ps1",
            "ExcelManusSetup.exe",
        ]
    elif system == "Darwin":
        candidates = [
            "deploy/start.sh",
        ]
    else:
        candidates = [
            "deploy/start.sh",
        ]
    for c in candidates:
        p = project_root / c
        if p.is_file():
            return p
    return None


# ── Windows ──────────────────────────────────────────────


def _create_windows_shortcut(
    project_root: Path,
    desktop: Path,
    name: str = _APP_NAME,
) -> Path | None:
    """创建 Windows .lnk 快捷方式（使用 COM 或 PowerShell fallback）。"""
    target = _find_start_script(project_root)
    if target is None:
        logger.warning("未找到启动脚本，无法创建快捷方式")
        return None

    lnk_path = desktop / f"{name}.lnk"

    # 方法1: PowerShell（不需要额外依赖）
    ps_script = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$sc = $ws.CreateShortcut("{lnk_path}"); '
    )

    suffix = target.suffix.lower()
    if suffix == ".bat":
        ps_script += (
            f'$sc.TargetPath = "cmd.exe"; '
            f'$sc.Arguments = "/c `"{target}`""; '
        )
    elif suffix == ".ps1":
        ps_script += (
            f'$sc.TargetPath = "powershell.exe"; '
            f'$sc.Arguments = "-ExecutionPolicy Bypass -File `"{target}`""; '
        )
    elif suffix == ".exe":
        ps_script += f'$sc.TargetPath = "{target}"; '
    else:
        ps_script += f'$sc.TargetPath = "{target}"; '

    ps_script += (
        f'$sc.WorkingDirectory = "{project_root}"; '
        f'$sc.Description = "启动 {name}"; '
        f'$sc.Save()'
    )

    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and lnk_path.exists():
            logger.info("桌面快捷方式已创建: %s", lnk_path)
            return lnk_path
        logger.warning("PowerShell 创建快捷方式失败: %s", result.stderr)
    except Exception as e:
        logger.warning("创建 Windows 快捷方式失败: %s", e)
    return None


# ── macOS ────────────────────────────────────────────────


def _create_macos_shortcut(
    project_root: Path,
    desktop: Path,
    name: str = _APP_NAME,
) -> Path | None:
    """创建 macOS .command 快捷方式脚本。"""
    target = _find_start_script(project_root)
    if target is None:
        logger.warning("未找到启动脚本，无法创建快捷方式")
        return None

    command_path = desktop / f"{name}.command"
    script_content = f"""#!/bin/bash
# {name} 启动快捷方式
# 自动生成 — 请勿手动编辑

cd "{project_root}"
exec bash "{target}" "$@"
"""
    try:
        command_path.write_text(script_content, encoding="utf-8")
        # 添加执行权限
        command_path.chmod(command_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        logger.info("桌面快捷方式已创建: %s", command_path)
        return command_path
    except Exception as e:
        logger.warning("创建 macOS 快捷方式失败: %s", e)
        return None


# ── Linux ────────────────────────────────────────────────


def _create_linux_shortcut(
    project_root: Path,
    desktop: Path,
    name: str = _APP_NAME,
) -> Path | None:
    """创建 Linux .desktop 快捷方式。"""
    target = _find_start_script(project_root)
    if target is None:
        logger.warning("未找到启动脚本，无法创建快捷方式")
        return None

    desktop_file = desktop / f"{name}.desktop"
    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={name}
Comment=启动 {name}
Exec=bash "{target}"
Path={project_root}
Terminal=true
Categories=Office;
"""
    try:
        desktop_file.write_text(content, encoding="utf-8")
        desktop_file.chmod(desktop_file.stat().st_mode | stat.S_IXUSR)
        logger.info("桌面快捷方式已创建: %s", desktop_file)
        return desktop_file
    except Exception as e:
        logger.warning("创建 Linux 快捷方式失败: %s", e)
        return None


# ── 公共 API ─────────────────────────────────────────────


def create_desktop_shortcut(
    project_root: str | Path,
    name: str = _APP_NAME,
) -> str | None:
    """创建桌面快捷方式（自动检测平台）。

    返回创建的快捷方式路径字符串，失败返回 None。
    """
    project_root = Path(project_root).expanduser().resolve()
    desktop = _get_desktop_path()
    if desktop is None:
        logger.warning("未找到桌面目录，无法创建快捷方式")
        return None

    system = platform.system()
    result: Path | None = None
    if system == "Windows":
        result = _create_windows_shortcut(project_root, desktop, name)
    elif system == "Darwin":
        result = _create_macos_shortcut(project_root, desktop, name)
    else:
        result = _create_linux_shortcut(project_root, desktop, name)

    if result is not None:
        # 更新 installations.json 中的 shortcut 字段
        try:
            from excelmanus.data_home import get_current_installation, _load_installations, _save_installations
            installations = _load_installations()
            project_str = str(project_root)
            for inst in installations:
                if inst.get("path") == project_str:
                    inst["shortcut"] = str(result)
                    break
            _save_installations(installations)
        except Exception:
            pass
        return str(result)
    return None


def update_desktop_shortcut(
    project_root: str | Path,
    name: str = _APP_NAME,
) -> str | None:
    """更新桌面快捷方式指向新的安装路径。

    先删除旧的，再创建新的。
    """
    remove_desktop_shortcut(name)
    return create_desktop_shortcut(project_root, name)


def remove_desktop_shortcut(name: str = _APP_NAME) -> bool:
    """删除桌面上的 ExcelManus 快捷方式。"""
    desktop = _get_desktop_path()
    if desktop is None:
        return False

    system = platform.system()
    if system == "Windows":
        candidates = [desktop / f"{name}.lnk"]
    elif system == "Darwin":
        candidates = [desktop / f"{name}.command"]
    else:
        candidates = [desktop / f"{name}.desktop"]

    removed = False
    for path in candidates:
        if path.is_file():
            try:
                path.unlink()
                logger.info("已删除桌面快捷方式: %s", path)
                removed = True
            except Exception as e:
                logger.warning("删除快捷方式失败: %s", e)
    return removed


def get_shortcut_info() -> dict[str, Any]:
    """返回当前桌面快捷方式状态信息。"""
    desktop = _get_desktop_path()
    if desktop is None:
        return {"exists": False, "desktop_path": None, "shortcut_path": None}

    system = platform.system()
    if system == "Windows":
        path = desktop / f"{_APP_NAME}.lnk"
    elif system == "Darwin":
        path = desktop / f"{_APP_NAME}.command"
    else:
        path = desktop / f"{_APP_NAME}.desktop"

    return {
        "exists": path.is_file(),
        "desktop_path": str(desktop),
        "shortcut_path": str(path) if path.is_file() else None,
        "platform": system,
    }
