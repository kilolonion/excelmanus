"""Cheap local checks before committing to downtime; no network or Git writes."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from excelmanus.updater import _run_cmd


def check_upgrade_environment(root: Path) -> str | None:
    if not shutil.which("git"):
        return "未找到 Git，请安装并加入服务进程的 PATH 后重试（当前服务未停止）"
    rc, branch, error = _run_cmd(["git", "symbolic-ref", "--short", "HEAD"], cwd=root)
    if rc != 0:
        return f"无法确定当前源码分支，请退出 detached HEAD 或修复 Git 权限后重试：{error}"
    rc, dirty, error = _run_cmd(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root)
    if rc != 0:
        return f"无法读取 Git 工作区，请检查仓库所有者和权限：{error}"
    if dirty:
        return "源码存在未提交修改，请先处理后再更新；当前服务和用户文件未改动"
    if not os.access(root, os.W_OK):
        return "程序目录不可写，请修复服务用户的目录权限后重试"
    web = root / "web"
    if (web / "package.json").is_file():
        if not shutil.which("node") or not shutil.which("npm"):
            return "未找到 Node.js 或 npm，请安装 Node.js 20.9+ 并加入服务进程的 PATH 后重试"
        rc, version, error = _run_cmd(["node", "--version"], cwd=root, timeout=10)
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.\d+", version)
        if rc != 0 or not match or tuple(map(int, match.groups())) < (20, 9):
            return f"网页构建需要 Node.js 20.9+，当前版本不可用：{version or error}"
        if not os.access(web, os.W_OK):
            return "网页目录不可写，请修复服务用户的目录权限后重试"
    return None
