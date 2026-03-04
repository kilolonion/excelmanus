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
            "ExcelManus.exe",
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


# ── 浏览器书签快捷方式（server/docker 模式）────────────────


def _create_windows_web_shortcut(
    url: str, desktop: Path, name: str = _APP_NAME,
) -> Path | None:
    """创建 Windows .url 浏览器快捷方式。"""
    url_path = desktop / f"{name}.url"
    content = f"[InternetShortcut]\nURL={url}\n"
    try:
        url_path.write_text(content, encoding="utf-8")
        logger.info("浏览器快捷方式已创建: %s", url_path)
        return url_path
    except Exception as e:
        logger.warning("创建 Windows 浏览器快捷方式失败: %s", e)
        return None


def _create_macos_web_shortcut(
    url: str, desktop: Path, name: str = _APP_NAME,
) -> Path | None:
    """创建 macOS .webloc 浏览器快捷方式。"""
    webloc_path = desktop / f"{name}.webloc"
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
        ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '\t<key>URL</key>\n'
        f'\t<string>{url}</string>\n'
        '</dict>\n'
        '</plist>\n'
    )
    try:
        webloc_path.write_text(content, encoding="utf-8")
        logger.info("浏览器快捷方式已创建: %s", webloc_path)
        return webloc_path
    except Exception as e:
        logger.warning("创建 macOS 浏览器快捷方式失败: %s", e)
        return None


def _create_linux_web_shortcut(
    url: str, desktop: Path, name: str = _APP_NAME,
) -> Path | None:
    """创建 Linux .desktop 浏览器快捷方式（Type=Link）。"""
    desktop_file = desktop / f"{name}.desktop"
    content = f"""[Desktop Entry]
Version=1.0
Type=Link
Name={name}
Comment=打开 {name} 网页版
URL={url}
Icon=text-html
"""
    try:
        desktop_file.write_text(content, encoding="utf-8")
        desktop_file.chmod(desktop_file.stat().st_mode | stat.S_IXUSR)
        logger.info("浏览器快捷方式已创建: %s", desktop_file)
        return desktop_file
    except Exception as e:
        logger.warning("创建 Linux 浏览器快捷方式失败: %s", e)
        return None


def create_web_shortcut(
    url: str,
    name: str = _APP_NAME,
) -> str | None:
    """创建浏览器书签快捷方式（自动检测平台）。

    适用于前后端分离部署，快捷方式打开浏览器访问指定 URL。
    返回创建的快捷方式路径字符串，失败返回 None。
    """
    desktop = _get_desktop_path()
    if desktop is None:
        logger.warning("未找到桌面目录，无法创建浏览器快捷方式")
        return None

    system = platform.system()
    result: Path | None = None
    if system == "Windows":
        result = _create_windows_web_shortcut(url, desktop, name)
    elif system == "Darwin":
        result = _create_macos_web_shortcut(url, desktop, name)
    else:
        result = _create_linux_web_shortcut(url, desktop, name)

    return str(result) if result is not None else None


# ── 公共 API ─────────────────────────────────────────────


def create_desktop_shortcut(
    project_root: str | Path,
    name: str = _APP_NAME,
    *,
    deploy_mode: str = "standalone",
    site_url: str = "",
) -> str | None:
    """创建桌面快捷方式（自动检测平台）。

    - standalone 模式：创建启动脚本快捷方式（.lnk / .command / .desktop）
    - server/docker 模式：创建浏览器书签（.url / .webloc / Type=Link）

    返回创建的快捷方式路径字符串，失败返回 None。
    """
    # 服务器模式：创建浏览器书签
    if deploy_mode in ("server", "docker"):
        if not site_url:
            logger.warning("服务器模式需要 site_url 参数才能创建浏览器快捷方式")
            return None
        return create_web_shortcut(site_url, name)

    # 单机模式：创建启动脚本快捷方式
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
            from excelmanus.data_home import _load_installations, _save_installations
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


def remove_desktop_shortcut(name: str = _APP_NAME) -> bool:
    """删除桌面上的 ExcelManus 快捷方式（同时检查启动脚本和浏览器书签两种类型）。"""
    desktop = _get_desktop_path()
    if desktop is None:
        return False

    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        candidates = [desktop / f"{name}.lnk", desktop / f"{name}.url"]
    elif system == "Darwin":
        candidates = [desktop / f"{name}.command", desktop / f"{name}.webloc"]
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
    """返回当前桌面快捷方式状态信息（同时检测启动脚本和浏览器书签两种类型）。"""
    desktop = _get_desktop_path()
    if desktop is None:
        return {"exists": False, "desktop_path": None, "shortcut_path": None, "shortcut_type": None}

    system = platform.system()

    # 检查启动脚本快捷方式（standalone）
    if system == "Windows":
        app_path = desktop / f"{_APP_NAME}.lnk"
    elif system == "Darwin":
        app_path = desktop / f"{_APP_NAME}.command"
    else:
        app_path = desktop / f"{_APP_NAME}.desktop"

    if app_path.is_file():
        return {
            "exists": True,
            "desktop_path": str(desktop),
            "shortcut_path": str(app_path),
            "shortcut_type": "app",
            "platform": system,
        }

    # 检查浏览器书签快捷方式（server/docker）
    if system == "Windows":
        web_path = desktop / f"{_APP_NAME}.url"
    elif system == "Darwin":
        web_path = desktop / f"{_APP_NAME}.webloc"
    else:
        web_path = desktop / f"{_APP_NAME}.desktop"  # 同后缀，已检查过
        # Linux 的 .desktop 文件可能是 Type=Link，上面已返回
        web_path = None  # type: ignore[assignment]

    if web_path and web_path.is_file():
        return {
            "exists": True,
            "desktop_path": str(desktop),
            "shortcut_path": str(web_path),
            "shortcut_type": "web",
            "platform": system,
        }

    return {
        "exists": False,
        "desktop_path": str(desktop),
        "shortcut_path": None,
        "shortcut_type": None,
        "platform": system,
    }
