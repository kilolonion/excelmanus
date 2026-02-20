#!/usr/bin/env python3
"""SSH Tunnel Manager — Tkinter GUI + macOS 菜单栏托盘"""

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CONFIG_PATH = Path.home() / ".ssh_tunnel_manager.json"
LOG_PATH = Path.home() / ".ssh_tunnel_manager.log"

DEFAULT_CONFIG = {
    "host": "43.163.195.153",
    "port": 22,
    "user": "root",
    "key_path": "~/Downloads/dasd.pem",
    "forwards": ["8080", "18789", "18792"],
}

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logger = logging.getLogger("ssh_tunnel")
logger.setLevel(logging.DEBUG)

# 文件 handler
_fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


class GUILogHandler(logging.Handler):
    """把日志写入 Tkinter ScrolledText 控件"""

    def __init__(self, text_widget: scrolledtext.ScrolledText):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        level = record.levelname
        # 跨线程安全写入
        self.text_widget.after(0, self._append, msg, level)

    def _append(self, msg: str, level: str = ""):
        self.text_widget.configure(state=tk.NORMAL)
        tag = level if level in ("INFO", "WARNING", "ERROR", "DEBUG") else None
        if tag:
            self.text_widget.insert(tk.END, msg + "\n", tag)
        else:
            self.text_widget.insert(tk.END, msg + "\n")
        self.text_widget.see(tk.END)
        self.text_widget.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# 配置持久化
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
            logger.info("已加载配置: %s", CONFIG_PATH)
            return data
        except Exception as exc:
            logger.warning("配置文件解析失败，使用默认值: %s", exc)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
        logger.info("配置已保存: %s", CONFIG_PATH)
    except Exception as exc:
        logger.error("保存配置失败: %s", exc)


# ---------------------------------------------------------------------------
# 端口占用检测与清理
# ---------------------------------------------------------------------------

def check_port_in_use(port: int) -> bool:
    """检测本地端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_pid_on_port(port: int) -> list[tuple[int, str]]:
    """用 lsof 查找占用指定端口的进程，返回 [(pid, command), ...]"""
    results = []
    try:
        out = subprocess.check_output(
            ["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-n", "-P"],
            stderr=subprocess.DEVNULL, text=True,
        )
        for line in out.strip().splitlines()[1:]:  # 跳过表头
            parts = line.split()
            if len(parts) >= 2:
                pid = int(parts[1])
                cmd = parts[0]
                results.append((pid, cmd))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # 去重
    return list(dict.fromkeys(results))


def kill_pids(pids: list[int]):
    """终止指定 PID 列表"""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("已发送 SIGTERM 到 PID %d", pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            logger.warning("无权限终止 PID %d", pid)


def check_and_clear_ports(forwards: list[str]) -> tuple[bool, str]:
    """
    检查转发端口是否被占用。
    返回 (all_clear, detail_message)。
    如果有占用，detail_message 包含占用详情。
    """
    occupied: dict[int, list[tuple[int, str]]] = {}
    for fwd in forwards:
        port = int(fwd.strip())
        if check_port_in_use(port):
            procs = find_pid_on_port(port)
            occupied[port] = procs

    if not occupied:
        return True, ""

    lines = []
    for port, procs in occupied.items():
        if procs:
            proc_desc = ", ".join(f"{cmd}(PID {pid})" for pid, cmd in procs)
            lines.append(f"  端口 {port}: {proc_desc}")
        else:
            lines.append(f"  端口 {port}: 被占用（无法识别进程）")

    detail = "以下端口已被占用:\n" + "\n".join(lines)
    return False, detail


# ---------------------------------------------------------------------------
# SSH 隧道管理
# ---------------------------------------------------------------------------

class TunnelProcess:
    """封装 ssh 子进程"""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self._monitor_thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, host: str, port: int, user: str, key_path: str,
              forwards: list[str], on_exit: callable = None):
        if self.alive:
            logger.warning("隧道已在运行，请先断开")
            return False

        key_full = os.path.expanduser(key_path)
        if not os.path.isfile(key_full):
            logger.error("私钥文件不存在: %s", key_full)
            return False

        cmd = ["ssh", "-N", "-o", "StrictHostKeyChecking=no",
               "-o", "ServerAliveInterval=30",
               "-o", "ServerAliveCountMax=3",
               "-o", "ExitOnForwardFailure=yes",
               "-i", key_full, "-p", str(port)]
        for fwd in forwards:
            fwd = fwd.strip()
            if fwd:
                cmd += ["-L", f"{fwd}:localhost:{fwd}"]
        cmd.append(f"{user}@{host}")

        logger.info("启动隧道: %s", " ".join(cmd))
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except FileNotFoundError:
            logger.error("找不到 ssh 命令，请确认已安装 OpenSSH")
            return False
        except Exception as exc:
            logger.error("启动 ssh 进程失败: %s", exc)
            return False

        logger.info("SSH 进程已启动 (PID %d)", self.proc.pid)

        # 后台监控线程：读 stderr 并检测退出
        def _monitor():
            assert self.proc and self.proc.stderr
            for line in self.proc.stderr:
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.warning("ssh stderr: %s", text)
            retcode = self.proc.wait()
            if retcode != 0:
                logger.error("SSH 进程退出，返回码 %d", retcode)
            else:
                logger.info("SSH 进程正常退出")
            if on_exit:
                on_exit()

        self._monitor_thread = threading.Thread(target=_monitor, daemon=True)
        self._monitor_thread.start()
        return True

    def stop(self):
        if not self.alive:
            logger.info("隧道未运行")
            return
        logger.info("正在终止 SSH 进程 (PID %d) ...", self.proc.pid)
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
            logger.info("SSH 进程已终止")
        except subprocess.TimeoutExpired:
            logger.warning("进程未响应 SIGTERM，发送 SIGKILL")
            self.proc.kill()
            self.proc.wait()
            logger.info("SSH 进程已强制终止")
        self.proc = None


# ---------------------------------------------------------------------------
# 托盘图标（pystray）
# ---------------------------------------------------------------------------

def _create_tray_image():
    """生成一个简单的绿色圆形图标"""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(34, 197, 94))  # 绿色
    return img


def _create_tray_image_disconnected():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=(239, 68, 68))  # 红色
    return img


class TrayManager:
    """macOS 菜单栏托盘"""

    def __init__(self, on_show: callable, on_quit: callable,
                 on_disconnect: callable):
        self.on_show = on_show
        self.on_quit = on_quit
        self.on_disconnect = on_disconnect
        self.icon = None
        self._connected = False

    def start(self, connected: bool = False):
        import pystray
        self._connected = connected
        img = _create_tray_image() if connected else _create_tray_image_disconnected()
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", lambda: self.on_show()),
            pystray.MenuItem("断开连接", lambda: self.on_disconnect()),
            pystray.MenuItem("退出", lambda: self.on_quit()),
        )
        self.icon = pystray.Icon("ssh_tunnel", img, "SSH Tunnel Manager", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        logger.info("托盘图标已启动")

    def update_status(self, connected: bool):
        if self.icon is None:
            return
        self._connected = connected
        self.icon.icon = (
            _create_tray_image() if connected else _create_tray_image_disconnected()
        )

    def stop(self):
        if self.icon:
            self.icon.stop()
            self.icon = None


# ---------------------------------------------------------------------------
# GUI 主窗口
# ---------------------------------------------------------------------------

class App:
    def __init__(self):
        self.tunnel = TunnelProcess()
        self.tray: TrayManager | None = None
        self.cfg = load_config()

        # --- 主窗口 ---
        self.root = tk.Tk()
        self.root.title("SSH Tunnel Manager")
        self.root.geometry("580x560")
        self.root.minsize(500, 480)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # macOS 原生风格
        style = ttk.Style()
        style.theme_use("aqua" if sys.platform == "darwin" else "clam")

        self._build_ui()
        self._setup_gui_logger()
        self._load_fields()

        logger.info("应用启动")

    # ---- UI 构建 ----

    def _build_ui(self):
        # 顶部表单区
        frm = ttk.LabelFrame(self.root, text="连接配置", padding=(16, 8))
        frm.pack(fill=tk.X, padx=14, pady=(10, 4))

        # 统一列宽
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="服务器 IP:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self.ent_host = ttk.Entry(frm, width=32)
        self.ent_host.grid(row=row, column=1, sticky=tk.W, pady=4)

        row += 1
        ttk.Label(frm, text="SSH 端口:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self.ent_port = ttk.Entry(frm, width=8)
        self.ent_port.grid(row=row, column=1, sticky=tk.W, pady=4)

        row += 1
        ttk.Label(frm, text="用户名:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        self.ent_user = ttk.Entry(frm, width=20)
        self.ent_user.grid(row=row, column=1, sticky=tk.W, pady=4)

        row += 1
        ttk.Label(frm, text="私钥路径:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        key_frm = ttk.Frame(frm)
        key_frm.grid(row=row, column=1, sticky=tk.W, pady=4)
        self.ent_key = ttk.Entry(key_frm, width=28)
        self.ent_key.pack(side=tk.LEFT)
        ttk.Button(key_frm, text="浏览…", command=self._browse_key).pack(side=tk.LEFT, padx=(6, 0))

        row += 1
        ttk.Label(frm, text="转发端口:").grid(row=row, column=0, sticky=tk.E, padx=(0, 8), pady=4)
        fwd_frm = ttk.Frame(frm)
        fwd_frm.grid(row=row, column=1, sticky=tk.W, pady=4)
        self.ent_forwards = ttk.Entry(fwd_frm, width=28)
        self.ent_forwards.pack(side=tk.LEFT)
        ttk.Label(fwd_frm, text="逗号分隔", foreground="gray").pack(side=tk.LEFT, padx=(6, 0))

        # 按钮 + 状态行
        ctrl_frm = ttk.Frame(self.root, padding=(14, 6))
        ctrl_frm.pack(fill=tk.X)

        self.btn_connect = ttk.Button(ctrl_frm, text="▶ 连接", command=self._on_connect)
        self.btn_connect.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_disconnect = ttk.Button(ctrl_frm, text="■ 断开", command=self._on_disconnect,
                                         state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.LEFT, padx=(0, 12))

        self.lbl_status = ttk.Label(ctrl_frm, text="● 未连接", foreground="gray",
                                    font=("SF Pro Text", 13))
        self.lbl_status.pack(side=tk.LEFT)

        # 日志面板
        log_frm = ttk.LabelFrame(self.root, text="日志", padding=(8, 4))
        log_frm.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 10))
        self.log_text = scrolledtext.ScrolledText(
            log_frm, height=14, state=tk.DISABLED,
            font=("Menlo", 10), wrap=tk.WORD,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
            relief=tk.FLAT, borderwidth=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _setup_gui_logger(self):
        gh = GUILogHandler(self.log_text)
        gh.setLevel(logging.DEBUG)
        gh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                          datefmt="%H:%M:%S"))
        logger.addHandler(gh)

        # 日志级别颜色标签
        self.log_text.tag_config("INFO", foreground="#6abf69")
        self.log_text.tag_config("WARNING", foreground="#e5c07b")
        self.log_text.tag_config("ERROR", foreground="#e06c75")
        self.log_text.tag_config("DEBUG", foreground="#808080")

    def _load_fields(self):
        self.ent_host.insert(0, self.cfg.get("host", ""))
        self.ent_port.insert(0, str(self.cfg.get("port", 22)))
        self.ent_user.insert(0, self.cfg.get("user", "root"))
        self.ent_key.insert(0, self.cfg.get("key_path", ""))
        self.ent_forwards.insert(0, ",".join(self.cfg.get("forwards", [])))

    def _read_fields(self) -> dict:
        return {
            "host": self.ent_host.get().strip(),
            "port": int(self.ent_port.get().strip() or "22"),
            "user": self.ent_user.get().strip() or "root",
            "key_path": self.ent_key.get().strip(),
            "forwards": [p.strip() for p in self.ent_forwards.get().split(",") if p.strip()],
        }

    def _browse_key(self):
        path = filedialog.askopenfilename(
            title="选择私钥文件",
            initialdir=str(Path.home() / "Downloads"),
            filetypes=[("PEM 文件", "*.pem"), ("所有文件", "*")],
        )
        if path:
            self.ent_key.delete(0, tk.END)
            self.ent_key.insert(0, path)

    # ---- 连接/断开 ----

    def _on_connect(self):
        cfg = self._read_fields()
        if not cfg["host"]:
            messagebox.showwarning("缺少参数", "请填写服务器 IP")
            return
        if not cfg["forwards"]:
            messagebox.showwarning("缺少参数", "请填写至少一个转发端口")
            return

        # 检测端口占用
        all_clear, detail = check_and_clear_ports(cfg["forwards"])
        if not all_clear:
            logger.warning("端口占用检测:\n%s", detail)
            answer = messagebox.askyesnocancel(
                "端口被占用",
                f"{detail}\n\n是否终止占用进程后继续连接？\n"
                "（是=终止并连接，否=忽略继续连接，取消=放弃）",
            )
            if answer is None:  # 取消
                return
            if answer:  # 是 → 杀进程
                pids_to_kill = set()
                for port_str in cfg["forwards"]:
                    port = int(port_str.strip())
                    for pid, _ in find_pid_on_port(port):
                        pids_to_kill.add(pid)
                if pids_to_kill:
                    kill_pids(list(pids_to_kill))
                    logger.info("等待端口释放…")
                    time.sleep(1)
                    # 二次检查
                    still_clear, detail2 = check_and_clear_ports(cfg["forwards"])
                    if not still_clear:
                        logger.error("部分端口仍被占用:\n%s", detail2)
                        messagebox.showerror("端口仍被占用", detail2)
                        return
                    logger.info("端口已释放")

        save_config(cfg)
        self._set_status("connecting")

        def _do():
            ok = self.tunnel.start(
                host=cfg["host"], port=cfg["port"], user=cfg["user"],
                key_path=cfg["key_path"], forwards=cfg["forwards"],
                on_exit=lambda: self.root.after(0, self._on_tunnel_exit),
            )
            self.root.after(0, self._set_status, "connected" if ok else "disconnected")

        threading.Thread(target=_do, daemon=True).start()

    def _on_disconnect(self):
        self._set_status("disconnected")

        def _do():
            self.tunnel.stop()

        threading.Thread(target=_do, daemon=True).start()

    def _on_tunnel_exit(self):
        """SSH 进程意外退出时回调"""
        self._set_status("disconnected")
        logger.warning("SSH 隧道已断开（进程退出）")

    def _set_status(self, state: str):
        if state == "connected":
            self.lbl_status.config(text="● 已连接", foreground="#22c55e")
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.NORMAL)
            if self.tray:
                self.tray.update_status(True)
        elif state == "connecting":
            self.lbl_status.config(text="● 连接中…", foreground="orange")
            self.btn_connect.config(state=tk.DISABLED)
            self.btn_disconnect.config(state=tk.DISABLED)
        else:
            self.lbl_status.config(text="● 未连接", foreground="gray")
            self.btn_connect.config(state=tk.NORMAL)
            self.btn_disconnect.config(state=tk.DISABLED)
            if self.tray:
                self.tray.update_status(False)

    # ---- 窗口关闭 → 托盘 ----

    def _on_close(self):
        if self.tunnel.alive:
            self.root.withdraw()  # 隐藏窗口
            if self.tray is None:
                self.tray = TrayManager(
                    on_show=self._show_window,
                    on_quit=self._quit_app,
                    on_disconnect=self._tray_disconnect,
                )
                self.tray.start(connected=True)
            logger.info("窗口已隐藏，隧道继续运行（菜单栏图标可管理）")
        else:
            self._quit_app()

    def _show_window(self):
        self.root.after(0, self.root.deiconify)

    def _tray_disconnect(self):
        self.root.after(0, self._on_disconnect)

    def _quit_app(self):
        logger.info("应用退出")
        if self.tray:
            self.tray.stop()

        def _do():
            self.tunnel.stop()
            self.root.after(0, self.root.destroy)

        threading.Thread(target=_do, daemon=True).start()

    # ---- 启动 ----

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = App()
    app.run()
