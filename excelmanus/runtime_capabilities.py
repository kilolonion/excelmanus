"""Cheap host facts shared by execution and model-facing capability disclosure.

Finding an executable is not proof that a particular runner can execute it.
Do not spawn probes, install dependencies, or infer authorization here.
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


def office_executable() -> str | None:
    system = platform.system()
    # On Windows .com is the CLI launcher: unlike the GUI launcher it forwards
    # console output and waits for the headless invocation to complete.
    names = ("soffice.com", "soffice", "libreoffice") if system == "Windows" else ("soffice", "libreoffice")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    # GUI installs often do not add their executable to PATH.
    candidates: list[Path] = []
    if system == "Darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidates.append(root / "LibreOffice.app/Contents/MacOS/soffice")
    elif system == "Windows":
        for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(key)
            if root:
                for name in ("soffice.com", "soffice.exe"):
                    candidates.append(Path(root) / "LibreOffice/program" / name)
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def host_capabilities() -> dict:
    office = office_executable()
    pdf = shutil.which("pdftoppm")
    enabled = os.environ.get("EXCELMANUS_FORMULA_RECALC", "auto").strip().lower() not in {"0", "false", "off", "never"}
    return {
        "scope": "host",
        "platform": platform.system(),
        "soffice": {"status": "installed" if office else "unavailable", "executable": office,
                    "reason": "found_executable" if office else "executable_not_found"},
        "pdftoppm": {"status": "installed" if pdf else "unavailable", "executable": pdf,
                     "reason": "found_executable" if pdf else "executable_not_found"},
        # Discovery cannot establish that LO will launch, fit in the runtime's
        # resources, or successfully calculate this particular workbook.
        "formula_recalculation": "disabled" if not enabled else "engine_detected" if office else "unavailable",
    }


def environment_text(*, full_access: bool = False) -> str:
    from excelmanus.execution_isolation import docker_enabled

    facts = host_capabilities()
    parts = [f"宿主运行环境：{facts['platform']}。"]
    for name in ("soffice", "pdftoppm"):
        item = facts[name]
        if item["executable"]:
            parts.append(f"{name} 已安装：{item['executable']}。")
        else:
            parts.append(f"{name} 在宿主不可用（未找到可执行文件）；宿主环境未变时无需重复试探。")
    recalc_labels = {
        "disabled": "已由配置禁用",
        "engine_detected": "已发现引擎，执行结果以工具返回为准",
        "unavailable": "无可执行引擎",
    }
    parts.append(f"宿主公式重算：{recalc_labels[facts['formula_recalculation']]}。安装状态不代表当前 runner 的执行权限或成功结果。")
    if docker_enabled():
        parts.append("run_code 配置为 Docker；以上仅为宿主探测，容器内工具尚未探测，宿主路径不保证在容器内存在。容器受配置的 CPU、内存和进程数限额约束。")
    elif facts["platform"] == "Windows":
        parts.append("run_code 使用本机子进程；Windows 不启用 Unix RLIMIT 资源限制，仍有执行超时。")
    else:
        parts.append("run_code 使用本机子进程；会按平台支持情况尝试设置 CPU、地址空间、文件句柄和进程数限制，仍有执行超时。")
    parts.append(
        "run_code 当前允许子进程。"
        if full_access else
        "run_code 的 GREEN/YELLOW 禁止启动子进程；RED 仅在执行策略允许时可用。"
    )
    return "".join(parts)
