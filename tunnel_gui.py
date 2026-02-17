#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, filedialog
import subprocess, os, signal, threading, json

CONFIG_FILE = os.path.expanduser("~/.tunnel_gui.json")
DEFAULTS = {
    "user": "root",
    "host": "43.163.195.153",
    "port": "22",
    "key": "~/Downloads/dasd.pem",
    "forwards": "18789, 18792",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return {**DEFAULTS, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULTS)

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

class TunnelApp:
    def __init__(self, root):
        self.root = root
        root.title("SSH Tunnel Manager")
        root.geometry("500x380")
        root.minsize(460, 340)

        style = ttk.Style()
        style.configure("Green.TLabel", foreground="green")
        style.configure("Red.TLabel", foreground="red")

        cfg = load_config()

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="SSH Tunnel Manager", font=("Helvetica", 16, "bold")).grid(row=0, column=0, columnspan=3, pady=(0, 12))

        # 用户名
        ttk.Label(frame, text="用户名").grid(row=1, column=0, sticky="w", pady=3)
        self.user_var = tk.StringVar(value=cfg["user"])
        ttk.Entry(frame, textvariable=self.user_var, width=12).grid(row=1, column=1, sticky="w", padx=6, pady=3)

        # 服务器
        ttk.Label(frame, text="服务器").grid(row=2, column=0, sticky="w", pady=3)
        self.host_var = tk.StringVar(value=cfg["host"])
        ttk.Entry(frame, textvariable=self.host_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=3)

        # SSH 端口
        ttk.Label(frame, text="SSH 端口").grid(row=3, column=0, sticky="w", pady=3)
        self.port_var = tk.StringVar(value=cfg["port"])
        ttk.Entry(frame, textvariable=self.port_var, width=8).grid(row=3, column=1, sticky="w", padx=6, pady=3)

        # 私钥
        ttk.Label(frame, text="私钥文件").grid(row=4, column=0, sticky="w", pady=3)
        self.key_var = tk.StringVar(value=cfg["key"])
        ttk.Entry(frame, textvariable=self.key_var).grid(row=4, column=1, sticky="ew", padx=6, pady=3)
        ttk.Button(frame, text="浏览", width=5, command=self.browse_key).grid(row=4, column=2, padx=(0, 2), pady=3)

        # 转发端口
        ttk.Label(frame, text="转发端口").grid(row=5, column=0, sticky="w", pady=3)
        self.fwd_var = tk.StringVar(value=cfg["forwards"])
        ttk.Entry(frame, textvariable=self.fwd_var).grid(row=5, column=1, columnspan=2, sticky="ew", padx=6, pady=3)
        ttk.Label(frame, text="多个用逗号分隔", foreground="gray").grid(row=6, column=1, sticky="w", padx=6)

        # 状态
        self.status_label = ttk.Label(frame, text="检测中...", font=("Helvetica", 13, "bold"))
        self.status_label.grid(row=7, column=0, columnspan=3, pady=(10, 4))

        # 按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=8, column=0, columnspan=3, pady=6)

        self.start_btn = ttk.Button(btn_frame, text="▶ 启动", command=self.start, width=10)
        self.start_btn.grid(row=0, column=0, padx=5)
        self.stop_btn = ttk.Button(btn_frame, text="■ 停止", command=self.stop, width=10)
        self.stop_btn.grid(row=0, column=1, padx=5)
        self.restart_btn = ttk.Button(btn_frame, text="↻ 重启", command=self.restart, width=10)
        self.restart_btn.grid(row=0, column=2, padx=5)

        # 消息
        self.msg_label = ttk.Label(frame, text="", font=("Helvetica", 10))
        self.msg_label.grid(row=9, column=0, columnspan=3, pady=(4, 0))

        self.refresh_status()

    def get_host_str(self):
        return f"{self.user_var.get().strip()}@{self.host_var.get().strip()}"

    def get_forward_ports(self):
        raw = self.fwd_var.get().strip()
        return [int(p.strip()) for p in raw.split(",") if p.strip().isdigit()]

    def browse_key(self):
        path = filedialog.askopenfilename(
            title="选择私钥文件",
            initialdir=os.path.expanduser("~/.ssh"),
            filetypes=[("PEM files", "*.pem"), ("All files", "*")]
        )
        if path:
            self.key_var.set(path)

    def save_current(self):
        save_config({
            "user": self.user_var.get().strip(),
            "host": self.host_var.get().strip(),
            "port": self.port_var.get().strip(),
            "key": self.key_var.get().strip(),
            "forwards": self.fwd_var.get().strip(),
        })

    def get_pids(self):
        host_str = self.get_host_str()
        try:
            out = subprocess.check_output(["pgrep", "-f", f"ssh -f -N.*{host_str}"], text=True)
            return [p.strip() for p in out.strip().split("\n") if p.strip()]
        except subprocess.CalledProcessError:
            return []

    def refresh_status(self):
        pids = self.get_pids()
        if pids:
            self.status_label.config(text="🟢 运行中", style="Green.TLabel")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        else:
            self.status_label.config(text="🔴 未运行", style="Red.TLabel")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    def show_msg(self, msg):
        self.msg_label.config(text=msg)
        self.msg_label.after(3000, lambda: self.msg_label.config(text=""))

    def start(self):
        self.save_current()
        def _run():
            ports = self.get_forward_ports()
            key_path = os.path.expanduser(self.key_var.get().strip())
            ssh_port = self.port_var.get().strip() or "22"
            port_args = []
            for p in ports:
                port_args += ["-L", f"{p}:127.0.0.1:{p}"]
            cmd = ["ssh", "-f", "-N", "-p", ssh_port]
            if key_path:
                cmd += ["-i", key_path]
            cmd += port_args + [
                "-o", "ServerAliveInterval=60",
                "-o", "ServerAliveCountMax=3",
                "-o", "ExitOnForwardFailure=yes",
                self.get_host_str()
            ]
            r = subprocess.run(cmd)
            if r.returncode == 0:
                self.show_msg(f"✅ 隧道已启动 (端口: {', '.join(map(str, ports))})")
            else:
                self.show_msg("❌ 启动失败，请检查配置")
            self.refresh_status()
        threading.Thread(target=_run, daemon=True).start()

    def stop(self):
        pids = self.get_pids()
        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
        self.show_msg(f"✅ 已关闭 {len(pids)} 个进程")
        self.refresh_status()

    def restart(self):
        self.stop()
        self.start()

if __name__ == "__main__":
    root = tk.Tk()
    TunnelApp(root)
    root.mainloop()
