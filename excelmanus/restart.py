"""通用跨平台服务重启模块。

提供可复用的进程自重启能力，供 auth 切换、配置热更新等场景使用。

用法::

    # 异步（BackgroundTask / async 函数）
    from excelmanus.restart import schedule_restart
    await schedule_restart()

    # 同步（CLI / 脚本）
    from excelmanus.restart import schedule_restart_sync
    schedule_restart_sync()
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

logger = logging.getLogger(__name__)

# 临时脚本和日志的文件名
_HELPER_SCRIPT_NAME = "_excelmanus_restart.py"
_RESTART_LOG_NAME = "excelmanus-restart.log"


def _build_helper_script(port: int, entry: str) -> str:
    """生成临时 Python 重启脚本的源码（纯标准库，跨平台）。"""
    python_path = sys.executable
    cwd = os.getcwd()
    log_path = os.path.join(tempfile.gettempdir(), _RESTART_LOG_NAME)

    return textwrap.dedent(f"""\
        import socket, subprocess, time, os, sys

        PORT     = {port!r}
        PYTHON   = {python_path!r}
        CWD      = {cwd!r}
        ENTRY    = {entry!r}
        LOG_PATH = {log_path!r}

        def _log(msg):
            try:
                with open(LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"[{{time.strftime('%Y-%m-%d %H:%M:%S')}}] {{msg}}\\n")
            except Exception:
                pass

        def _port_free(p):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.settimeout(0.5)
                s.connect(("127.0.0.1", p))
                return False
            except Exception:
                return True
            finally:
                s.close()

        _log(f"重启脚本启动，等待端口 {{PORT}} 释放...")

        # 等待旧进程释放端口（最多 30 秒）
        for i in range(60):
            if _port_free(PORT):
                _log(f"端口 {{PORT}} 已释放 (第 {{i}} 次检测)")
                break
            time.sleep(0.5)
        else:
            _log(f"端口 {{PORT}} 等待超时，继续尝试启动")

        time.sleep(0.5)

        # 启动新服务进程
        cmd = [PYTHON, "-c", ENTRY]
        _log(f"启动命令: {{cmd}}")
        _log(f"工作目录: {{CWD}}")
        try:
            subprocess.Popen(cmd, cwd=CWD)
            _log("新服务进程已启动")
        except Exception as e:
            _log(f"启动失败: {{e}}")
            sys.exit(1)
    """)


def _do_restart(port: int, entry: str) -> None:
    """核心重启逻辑：生成临时脚本 → Popen 启动 → os._exit(0)。

    此函数应在独立线程中调用，因为它会调用 os._exit(0)。
    """
    script_path = os.path.join(tempfile.gettempdir(), _HELPER_SCRIPT_NAME)
    script_content = _build_helper_script(port, entry)

    # 写入临时脚本
    try:
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)
        logger.info("重启脚本已写入: %s", script_path)
    except Exception:
        logger.exception("写入重启脚本失败")
        return

    # 启动脱离父进程的辅助进程
    try:
        popen_kwargs: dict = {
            "args": [sys.executable, script_path],
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL

        subprocess.Popen(**popen_kwargs)
        logger.info("重启辅助进程已启动")
    except Exception:
        logger.exception("启动重启辅助进程失败")
        return

    # 等待辅助进程稳定后退出当前进程
    logger.info("1 秒后退出当前进程...")
    time.sleep(1)
    os._exit(0)


async def schedule_restart(
    port: int = 8000,
    entry: str = "from excelmanus.api import main; main()",
    delay: float = 1.0,
) -> None:
    """异步触发服务重启。适用于 BackgroundTask / async 函数。

    Args:
        port: 服务监听端口，辅助脚本会等待该端口释放后再启动新进程。
        entry: 新进程的 Python 入口代码。
        delay: 触发重启前的等待秒数（确保 HTTP 响应已发送完毕）。
    """
    await asyncio.sleep(delay)
    t = threading.Thread(target=_do_restart, args=(port, entry), daemon=False)
    t.start()


def schedule_restart_sync(
    port: int = 8000,
    entry: str = "from excelmanus.api import main; main()",
) -> None:
    """同步触发服务重启。适用于 CLI / 脚本场景。

    注意：此函数不会返回 —— 内部调用 os._exit(0)。

    Args:
        port: 服务监听端口。
        entry: 新进程的 Python 入口代码。
    """
    _do_restart(port, entry)
