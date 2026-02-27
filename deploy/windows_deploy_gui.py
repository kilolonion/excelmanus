#!/usr/bin/env python3
"""
ExcelManus Windows 一键部署工具
美观的图形化界面，傻瓜式一键部署。
仅依赖 Python 标准库 tkinter，无需额外安装。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import (
    BOTH,
    BOTTOM,
    CENTER,
    DISABLED,
    END,
    FLAT,
    GROOVE,
    HORIZONTAL,
    LEFT,
    NONE,
    NORMAL,
    RIGHT,
    SUNKEN,
    TOP,
    VERTICAL,
    W,
    X,
    Y,
    BooleanVar,
    Frame,
    IntVar,
    Label,
    StringVar,
    Text,
    Tk,
    Toplevel,
    messagebox,
)
from tkinter import font as tkfont
from tkinter import ttk

# ═══════════════════════════════════════════════════════════════
#  常量
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
VERSION = "1.0.0"
APP_TITLE = "ExcelManus 一键部署工具"

# 颜色主题 - 现代深色
COLORS = {
    "bg_dark": "#0f0f23",
    "bg_main": "#1a1a2e",
    "bg_card": "#16213e",
    "bg_card_hover": "#1a2744",
    "bg_input": "#0f3460",
    "bg_input_focus": "#1a4a7a",
    "accent": "#00d4aa",
    "accent_hover": "#00f5c4",
    "accent_dim": "#007a63",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "success": "#2ecc71",
    "text_primary": "#e8e8e8",
    "text_secondary": "#8899aa",
    "text_dim": "#556677",
    "border": "#2a3a5c",
    "border_light": "#3a4a6c",
    "progress_bg": "#1e2d4a",
    "btn_primary": "#00d4aa",
    "btn_primary_fg": "#0f0f23",
    "btn_secondary": "#2a3a5c",
    "btn_danger": "#e74c3c",
    "btn_danger_fg": "#ffffff",
    "status_running": "#00d4aa",
    "status_stopped": "#e74c3c",
    "status_pending": "#f39c12",
    "log_bg": "#0a0a1a",
    "log_info": "#00d4aa",
    "log_warn": "#f39c12",
    "log_error": "#e74c3c",
    "log_text": "#c0c8d8",
}

# ═══════════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════════


def find_python() -> str | None:
    """查找可用的 Python 解释器。"""
    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    venv_python2 = PROJECT_ROOT / ".venv" / "bin" / "python.exe"
    if venv_python2.exists():
        return str(venv_python2)
    if shutil.which("python"):
        return "python"
    if shutil.which("python3"):
        return "python3"
    return None


def find_node() -> str | None:
    """查找 Node.js。"""
    if shutil.which("node"):
        return "node"
    return None


def find_npm() -> str | None:
    """查找 npm。"""
    if shutil.which("npm"):
        return "npm"
    return None


def find_git() -> str | None:
    """查找 git。"""
    if shutil.which("git"):
        return "git"
    return None


def get_version(cmd: list[str]) -> str:
    """获取命令版本号。"""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return result.stdout.strip().split("\n")[0]
    except Exception:
        return "未知"


def load_env_file(path: Path) -> dict[str, str]:
    """加载 .env 文件为字典。"""
    env = {}
    if not path.exists():
        return env
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    return env


def save_env_file(path: Path, env: dict[str, str]):
    """保存字典为 .env 文件。"""
    lines = ["# ExcelManus Configuration\n"]
    for key, val in env.items():
        lines.append(f"{key}={val}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ═══════════════════════════════════════════════════════════════
#  自定义控件
# ═══════════════════════════════════════════════════════════════


class ModernEntry(Frame):
    """带样式的输入框。"""

    def __init__(self, master, label_text="", placeholder="", show="", textvariable=None, **kw):
        super().__init__(master, bg=COLORS["bg_card"])
        self.placeholder = placeholder
        self.show_char = show

        if label_text:
            lbl = Label(
                self,
                text=label_text,
                bg=COLORS["bg_card"],
                fg=COLORS["text_secondary"],
                font=("Microsoft YaHei UI", 9),
                anchor=W,
            )
            lbl.pack(fill=X, pady=(0, 4))

        self.entry_frame = Frame(self, bg=COLORS["bg_input"], padx=2, pady=2)
        self.entry_frame.pack(fill=X)

        self.var = textvariable or StringVar()
        self.entry = ttk.Entry(
            self.entry_frame,
            textvariable=self.var,
            font=("Consolas", 10),
            show=show,
        )
        self.entry.pack(fill=X, padx=8, pady=6)

        if placeholder and not self.var.get():
            self.entry.insert(0, placeholder)
            self.entry.configure(foreground=COLORS["text_dim"])
            self.entry.bind("<FocusIn>", self._on_focus_in)
            self.entry.bind("<FocusOut>", self._on_focus_out)
            self._is_placeholder = True
        else:
            self._is_placeholder = False

    def _on_focus_in(self, _):
        if self._is_placeholder:
            self.entry.delete(0, END)
            self.entry.configure(foreground=COLORS["text_primary"], show=self.show_char)
            self._is_placeholder = False

    def _on_focus_out(self, _):
        if not self.var.get():
            self.entry.insert(0, self.placeholder)
            self.entry.configure(foreground=COLORS["text_dim"], show="")
            self._is_placeholder = True

    def get(self):
        if self._is_placeholder:
            return ""
        return self.var.get()

    def set(self, value):
        self._is_placeholder = False
        self.var.set(value)
        self.entry.configure(foreground=COLORS["text_primary"], show=self.show_char)


class StatusDot(Frame):
    """状态指示灯。"""

    def __init__(self, master, text="", status="pending", **kw):
        super().__init__(master, bg=COLORS["bg_card"])
        self.canvas = Label(self, text="●", font=("Segoe UI", 12), bg=COLORS["bg_card"])
        self.canvas.pack(side=LEFT, padx=(0, 8))
        self.label = Label(
            self,
            text=text,
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
            font=("Microsoft YaHei UI", 10),
            anchor=W,
        )
        self.label.pack(side=LEFT, fill=X, expand=True)
        self.set_status(status)

    def set_status(self, status: str):
        color_map = {
            "running": COLORS["status_running"],
            "success": COLORS["success"],
            "stopped": COLORS["status_stopped"],
            "error": COLORS["error"],
            "pending": COLORS["status_pending"],
            "checking": COLORS["text_secondary"],
        }
        self.canvas.configure(fg=color_map.get(status, COLORS["text_dim"]))

    def set_text(self, text: str):
        self.label.configure(text=text)


class ModernButton(Label):
    """现代风格按钮。"""

    def __init__(self, master, text="", command=None, style="primary", width=None, **kw):
        self.command = command
        self.style = style
        self._disabled = False

        styles = {
            "primary": {
                "bg": COLORS["btn_primary"],
                "fg": COLORS["btn_primary_fg"],
                "hover_bg": COLORS["accent_hover"],
            },
            "secondary": {
                "bg": COLORS["btn_secondary"],
                "fg": COLORS["text_primary"],
                "hover_bg": COLORS["border_light"],
            },
            "danger": {
                "bg": COLORS["btn_danger"],
                "fg": COLORS["btn_danger_fg"],
                "hover_bg": "#c0392b",
            },
        }
        s = styles.get(style, styles["primary"])
        self._normal_bg = s["bg"]
        self._hover_bg = s["hover_bg"]
        self._normal_fg = s["fg"]

        super().__init__(
            master,
            text=text,
            bg=s["bg"],
            fg=s["fg"],
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=24,
            pady=10,
            cursor="hand2",
            **kw,
        )

        if width:
            self.configure(width=width)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _):
        if not self._disabled:
            self.configure(bg=self._hover_bg)

    def _on_leave(self, _):
        if not self._disabled:
            self.configure(bg=self._normal_bg)

    def _on_click(self, _):
        if not self._disabled and self.command:
            self.command()

    def set_disabled(self, disabled: bool):
        self._disabled = disabled
        if disabled:
            self.configure(bg=COLORS["text_dim"], fg=COLORS["bg_dark"], cursor="")
        else:
            self.configure(bg=self._normal_bg, fg=self._normal_fg, cursor="hand2")

    def set_text(self, text: str):
        self.configure(text=text)


# ═══════════════════════════════════════════════════════════════
#  主应用
# ═══════════════════════════════════════════════════════════════


class ExcelManusDeployer(Tk):
    """ExcelManus 一键部署工具主窗口。"""

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.configure(bg=COLORS["bg_dark"])
        self.minsize(960, 700)

        # 居中显示
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w, win_h = 1040, 760
        x = (screen_w - win_w) // 2
        y = (screen_h - win_h) // 2
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # 设置图标（如果存在）
        try:
            ico_path = PROJECT_ROOT / "web" / "public" / "favicon.ico"
            if ico_path.exists():
                self.iconbitmap(str(ico_path))
        except Exception:
            pass

        # 状态变量
        self.backend_process = None
        self.frontend_process = None
        self.backend_port = IntVar(value=8000)
        self.frontend_port = IntVar(value=3000)
        self.api_key_var = StringVar()
        self.base_url_var = StringVar()
        self.model_var = StringVar()
        self.auto_open_browser = BooleanVar(value=True)

        # 加载已有配置
        self._load_existing_config()

        # 配置 ttk 样式
        self._setup_styles()

        # 构建 UI
        self._build_ui()

        # 启动环境检查
        self.after(500, self._check_environment)

        # 关闭处理
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_existing_config(self):
        """加载已有的 .env 配置。"""
        env_path = PROJECT_ROOT / ".env"
        env = load_env_file(env_path)
        if env.get("EXCELMANUS_API_KEY"):
            self.api_key_var.set(env["EXCELMANUS_API_KEY"])
        if env.get("EXCELMANUS_BASE_URL"):
            self.base_url_var.set(env["EXCELMANUS_BASE_URL"])
        if env.get("EXCELMANUS_MODEL"):
            self.model_var.set(env["EXCELMANUS_MODEL"])

    def _setup_styles(self):
        """配置 ttk 样式。"""
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            "Dark.TFrame",
            background=COLORS["bg_main"],
        )
        style.configure(
            "Card.TFrame",
            background=COLORS["bg_card"],
        )
        style.configure(
            "TEntry",
            fieldbackground=COLORS["bg_input"],
            foreground=COLORS["text_primary"],
            insertcolor=COLORS["accent"],
            borderwidth=0,
        )
        style.map(
            "TEntry",
            fieldbackground=[("focus", COLORS["bg_input_focus"])],
        )
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=COLORS["progress_bg"],
            background=COLORS["accent"],
            thickness=6,
        )
        style.configure(
            "TCheckbutton",
            background=COLORS["bg_card"],
            foreground=COLORS["text_primary"],
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", COLORS["bg_card"])],
        )
        style.configure(
            "TNotebook",
            background=COLORS["bg_dark"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=COLORS["bg_card"],
            foreground=COLORS["text_secondary"],
            padding=[16, 8],
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["bg_main"])],
            foreground=[("selected", COLORS["accent"])],
        )

    def _build_ui(self):
        """构建主界面。"""
        # ── 顶部标题栏 ──
        header = Frame(self, bg=COLORS["bg_dark"], height=80)
        header.pack(fill=X, padx=0, pady=0)
        header.pack_propagate(False)

        header_inner = Frame(header, bg=COLORS["bg_dark"])
        header_inner.pack(fill=BOTH, expand=True, padx=30, pady=12)

        # Logo + 标题
        title_frame = Frame(header_inner, bg=COLORS["bg_dark"])
        title_frame.pack(side=LEFT)

        logo_label = Label(
            title_frame,
            text="📊",
            font=("Segoe UI Emoji", 24),
            bg=COLORS["bg_dark"],
            fg=COLORS["accent"],
        )
        logo_label.pack(side=LEFT, padx=(0, 12))

        title_text = Frame(title_frame, bg=COLORS["bg_dark"])
        title_text.pack(side=LEFT)

        Label(
            title_text,
            text="ExcelManus",
            font=("Segoe UI", 18, "bold"),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_primary"],
        ).pack(anchor=W)

        Label(
            title_text,
            text="智能 Excel 代理框架 · 一键部署工具",
            font=("Microsoft YaHei UI", 9),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_secondary"],
        ).pack(anchor=W)

        # 版本号
        Label(
            header_inner,
            text=f"v{VERSION}",
            font=("Consolas", 9),
            bg=COLORS["bg_dark"],
            fg=COLORS["text_dim"],
        ).pack(side=RIGHT, pady=(8, 0))

        # ── 分隔线 ──
        Frame(self, bg=COLORS["accent"], height=2).pack(fill=X)

        # ── 主体区域 ──
        main = Frame(self, bg=COLORS["bg_dark"])
        main.pack(fill=BOTH, expand=True, padx=24, pady=16)

        # 左右布局
        left_panel = Frame(main, bg=COLORS["bg_dark"], width=460)
        left_panel.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 12))

        right_panel = Frame(main, bg=COLORS["bg_dark"], width=460)
        right_panel.pack(side=RIGHT, fill=BOTH, expand=True, padx=(12, 0))

        # ── 左侧：环境检查 + 配置 ──
        self._build_env_check_card(left_panel)
        self._build_config_card(left_panel)

        # ── 右侧：控制台 + 操作按钮 ──
        self._build_action_card(right_panel)
        self._build_log_card(right_panel)

    def _build_env_check_card(self, parent):
        """环境检查卡片。"""
        card = Frame(parent, bg=COLORS["bg_card"], padx=20, pady=16)
        card.pack(fill=X, pady=(0, 12))

        # 标题
        title_row = Frame(card, bg=COLORS["bg_card"])
        title_row.pack(fill=X, pady=(0, 12))

        Label(
            title_row,
            text="🔍  环境检查",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
        ).pack(side=LEFT)

        self.env_status_label = Label(
            title_row,
            text="检查中...",
            font=("Microsoft YaHei UI", 9),
            bg=COLORS["bg_card"],
            fg=COLORS["status_pending"],
        )
        self.env_status_label.pack(side=RIGHT)

        # 检查项
        self.python_status = StatusDot(card, text="Python  ·  检查中...", status="checking")
        self.python_status.pack(fill=X, pady=2)

        self.node_status = StatusDot(card, text="Node.js  ·  检查中...", status="checking")
        self.node_status.pack(fill=X, pady=2)

        self.npm_status = StatusDot(card, text="npm  ·  检查中...", status="checking")
        self.npm_status.pack(fill=X, pady=2)

        self.git_status = StatusDot(card, text="Git  ·  检查中...", status="checking")
        self.git_status.pack(fill=X, pady=2)

        self.deps_status = StatusDot(card, text="项目依赖  ·  等待检查", status="checking")
        self.deps_status.pack(fill=X, pady=2)

    def _build_config_card(self, parent):
        """配置卡片。"""
        card = Frame(parent, bg=COLORS["bg_card"], padx=20, pady=16)
        card.pack(fill=BOTH, expand=True, pady=(0, 0))

        Label(
            card,
            text="⚙️  LLM 配置",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
        ).pack(anchor=W, pady=(0, 12))

        # API Key
        self.api_key_entry = ModernEntry(
            card,
            label_text="API Key",
            placeholder="sk-xxx...",
            show="●",
            textvariable=self.api_key_var,
        )
        self.api_key_entry.pack(fill=X, pady=(0, 8))

        # Base URL
        self.base_url_entry = ModernEntry(
            card,
            label_text="Base URL",
            placeholder="https://api.openai.com/v1",
            textvariable=self.base_url_var,
        )
        self.base_url_entry.pack(fill=X, pady=(0, 8))

        # Model
        self.model_entry = ModernEntry(
            card,
            label_text="模型名称",
            placeholder="gpt-4o",
            textvariable=self.model_var,
        )
        self.model_entry.pack(fill=X, pady=(0, 12))

        # 端口配置
        port_frame = Frame(card, bg=COLORS["bg_card"])
        port_frame.pack(fill=X, pady=(0, 8))

        Label(
            port_frame,
            text="端口设置",
            bg=COLORS["bg_card"],
            fg=COLORS["text_secondary"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor=W, pady=(0, 4))

        port_inputs = Frame(port_frame, bg=COLORS["bg_card"])
        port_inputs.pack(fill=X)

        # 后端端口
        bp_frame = Frame(port_inputs, bg=COLORS["bg_card"])
        bp_frame.pack(side=LEFT, fill=X, expand=True, padx=(0, 8))
        Label(
            bp_frame,
            text="后端",
            bg=COLORS["bg_card"],
            fg=COLORS["text_dim"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor=W)
        bp_entry_frame = Frame(bp_frame, bg=COLORS["bg_input"], padx=2, pady=2)
        bp_entry_frame.pack(fill=X)
        ttk.Entry(bp_entry_frame, textvariable=self.backend_port, font=("Consolas", 10), width=8).pack(
            padx=8, pady=4
        )

        # 前端端口
        fp_frame = Frame(port_inputs, bg=COLORS["bg_card"])
        fp_frame.pack(side=LEFT, fill=X, expand=True, padx=(8, 0))
        Label(
            fp_frame,
            text="前端",
            bg=COLORS["bg_card"],
            fg=COLORS["text_dim"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor=W)
        fp_entry_frame = Frame(fp_frame, bg=COLORS["bg_input"], padx=2, pady=2)
        fp_entry_frame.pack(fill=X)
        ttk.Entry(fp_entry_frame, textvariable=self.frontend_port, font=("Consolas", 10), width=8).pack(
            padx=8, pady=4
        )

        # 自动打开浏览器
        ttk.Checkbutton(
            card,
            text="启动后自动打开浏览器",
            variable=self.auto_open_browser,
            style="TCheckbutton",
        ).pack(anchor=W, pady=(8, 0))

    def _build_action_card(self, parent):
        """操作按钮区域。"""
        card = Frame(parent, bg=COLORS["bg_card"], padx=20, pady=16)
        card.pack(fill=X, pady=(0, 12))

        Label(
            card,
            text="🚀  服务控制",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
        ).pack(anchor=W, pady=(0, 12))

        # 服务状态
        status_row = Frame(card, bg=COLORS["bg_card"])
        status_row.pack(fill=X, pady=(0, 12))

        self.backend_svc_status = StatusDot(card, text="后端服务  ·  未启动", status="stopped")
        self.backend_svc_status.pack(fill=X, pady=2)

        self.frontend_svc_status = StatusDot(card, text="前端服务  ·  未启动", status="stopped")
        self.frontend_svc_status.pack(fill=X, pady=2)

        # 按钮组
        btn_frame = Frame(card, bg=COLORS["bg_card"])
        btn_frame.pack(fill=X, pady=(12, 0))

        self.deploy_btn = ModernButton(
            btn_frame,
            text="▶  一键启动",
            command=self._on_deploy,
            style="primary",
        )
        self.deploy_btn.pack(side=LEFT, padx=(0, 8))

        self.stop_btn = ModernButton(
            btn_frame,
            text="■  停止服务",
            command=self._on_stop,
            style="danger",
        )
        self.stop_btn.pack(side=LEFT, padx=(0, 8))
        self.stop_btn.set_disabled(True)

        self.open_btn = ModernButton(
            btn_frame,
            text="🌐  打开网页",
            command=self._open_browser,
            style="secondary",
        )
        self.open_btn.pack(side=RIGHT)
        self.open_btn.set_disabled(True)

        # 进度条
        self.progress = ttk.Progressbar(
            card,
            style="Accent.Horizontal.TProgressbar",
            mode="indeterminate",
            length=200,
        )
        self.progress.pack(fill=X, pady=(12, 0))

    def _build_log_card(self, parent):
        """日志输出区域。"""
        card = Frame(parent, bg=COLORS["bg_card"], padx=20, pady=16)
        card.pack(fill=BOTH, expand=True)

        title_row = Frame(card, bg=COLORS["bg_card"])
        title_row.pack(fill=X, pady=(0, 8))

        Label(
            title_row,
            text="📋  运行日志",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=COLORS["bg_card"],
            fg=COLORS["text_primary"],
        ).pack(side=LEFT)

        # 清除日志按钮
        clear_label = Label(
            title_row,
            text="清除",
            font=("Microsoft YaHei UI", 9),
            bg=COLORS["bg_card"],
            fg=COLORS["text_dim"],
            cursor="hand2",
        )
        clear_label.pack(side=RIGHT)
        clear_label.bind("<Button-1>", lambda e: self._clear_log())
        clear_label.bind("<Enter>", lambda e: clear_label.configure(fg=COLORS["accent"]))
        clear_label.bind("<Leave>", lambda e: clear_label.configure(fg=COLORS["text_dim"]))

        # 日志文本框
        log_frame = Frame(card, bg=COLORS["log_bg"], padx=1, pady=1)
        log_frame.pack(fill=BOTH, expand=True)

        self.log_text = Text(
            log_frame,
            bg=COLORS["log_bg"],
            fg=COLORS["log_text"],
            font=("Consolas", 9),
            wrap="word",
            borderwidth=0,
            highlightthickness=0,
            insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent_dim"],
            padx=12,
            pady=8,
            state=DISABLED,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient=VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text.pack(fill=BOTH, expand=True)

        # 配置日志标签颜色
        self.log_text.tag_configure("info", foreground=COLORS["log_info"])
        self.log_text.tag_configure("warn", foreground=COLORS["log_warn"])
        self.log_text.tag_configure("error", foreground=COLORS["log_error"])
        self.log_text.tag_configure("dim", foreground=COLORS["text_dim"])
        self.log_text.tag_configure("success", foreground=COLORS["success"])
        self.log_text.tag_configure("normal", foreground=COLORS["log_text"])

    # ═══════════════════════════════════════════════════════════════
    #  日志方法
    # ═══════════════════════════════════════════════════════════════

    def log(self, message: str, tag: str = "normal"):
        """向日志区域追加消息。"""
        timestamp = time.strftime("%H:%M:%S")
        prefix_map = {
            "info": "[OK]",
            "warn": "[!!]",
            "error": "[XX]",
            "success": "[✓]",
            "dim": "[..]",
        }
        prefix = prefix_map.get(tag, "[--]")

        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, f"  {timestamp}  {prefix} {message}\n", tag)
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)

    def _clear_log(self):
        self.log_text.configure(state=NORMAL)
        self.log_text.delete("1.0", END)
        self.log_text.configure(state=DISABLED)

    # ═══════════════════════════════════════════════════════════════
    #  环境检查
    # ═══════════════════════════════════════════════════════════════

    def _check_environment(self):
        """在后台线程中检查环境。"""
        thread = threading.Thread(target=self._do_check_environment, daemon=True)
        thread.start()

    def _do_check_environment(self):
        """执行环境检查。"""
        all_ok = True
        self.log("开始环境检查...", "dim")

        # Python
        python = find_python()
        if python:
            ver = get_version([python, "--version"])
            self.after(0, lambda: self.python_status.set_text(f"Python  ·  {ver}"))
            self.after(0, lambda: self.python_status.set_status("success"))
            self.log(f"Python: {ver} ({python})", "info")
        else:
            self.after(0, lambda: self.python_status.set_text("Python  ·  ❌ 未安装"))
            self.after(0, lambda: self.python_status.set_status("error"))
            self.log("Python 未安装！请从 https://www.python.org/ 下载安装", "error")
            all_ok = False

        # Node.js
        node = find_node()
        if node:
            ver = get_version(["node", "--version"])
            self.after(0, lambda: self.node_status.set_text(f"Node.js  ·  {ver}"))
            self.after(0, lambda: self.node_status.set_status("success"))
            self.log(f"Node.js: {ver}", "info")
        else:
            self.after(0, lambda: self.node_status.set_text("Node.js  ·  ❌ 未安装"))
            self.after(0, lambda: self.node_status.set_status("error"))
            self.log("Node.js 未安装！请从 https://nodejs.org/ 下载安装", "error")
            all_ok = False

        # npm
        npm = find_npm()
        if npm:
            ver = get_version(["npm", "--version"])
            self.after(0, lambda: self.npm_status.set_text(f"npm  ·  v{ver}"))
            self.after(0, lambda: self.npm_status.set_status("success"))
            self.log(f"npm: v{ver}", "info")
        else:
            self.after(0, lambda: self.npm_status.set_text("npm  ·  ❌ 未安装"))
            self.after(0, lambda: self.npm_status.set_status("error"))
            all_ok = False

        # Git
        git = find_git()
        if git:
            ver = get_version(["git", "--version"])
            self.after(0, lambda: self.git_status.set_text(f"Git  ·  {ver}"))
            self.after(0, lambda: self.git_status.set_status("success"))
            self.log(f"Git: {ver}", "info")
        else:
            self.after(0, lambda: self.git_status.set_text("Git  ·  ⚠ 未安装 (可选)"))
            self.after(0, lambda: self.git_status.set_status("pending"))
            self.log("Git 未安装（可选，不影响本地部署）", "warn")

        # 项目依赖
        self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  检查中..."))
        venv_exists = (PROJECT_ROOT / ".venv").exists()
        node_modules_exists = (PROJECT_ROOT / "web" / "node_modules").exists()

        if venv_exists and node_modules_exists:
            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  ✅ 已安装"))
            self.after(0, lambda: self.deps_status.set_status("success"))
            self.log("项目依赖已安装", "info")
        elif venv_exists:
            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  ⚠ 前端依赖未安装"))
            self.after(0, lambda: self.deps_status.set_status("pending"))
            self.log("前端依赖未安装，启动时将自动安装", "warn")
        elif node_modules_exists:
            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  ⚠ 后端依赖未安装"))
            self.after(0, lambda: self.deps_status.set_status("pending"))
            self.log("后端依赖未安装，启动时将自动安装", "warn")
        else:
            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  ⚠ 未安装（首次启动将自动安装）"))
            self.after(0, lambda: self.deps_status.set_status("pending"))
            self.log("项目依赖未安装，首次启动将自动安装（可能需要几分钟）", "warn")

        # 总结
        if all_ok:
            self.after(
                0,
                lambda: self.env_status_label.configure(text="✅ 环境就绪", fg=COLORS["success"]),
            )
            self.log("环境检查完成，一切就绪！", "success")
        else:
            self.after(
                0,
                lambda: self.env_status_label.configure(text="❌ 缺少依赖", fg=COLORS["error"]),
            )
            self.log("环境检查完成，存在缺失项，请先安装", "error")

    # ═══════════════════════════════════════════════════════════════
    #  部署操作
    # ═══════════════════════════════════════════════════════════════

    def _save_config(self):
        """保存当前配置到 .env 文件。"""
        env_path = PROJECT_ROOT / ".env"
        existing = load_env_file(env_path)

        api_key = self.api_key_var.get().strip()
        base_url = self.base_url_var.get().strip()
        model = self.model_var.get().strip()

        if api_key:
            existing["EXCELMANUS_API_KEY"] = api_key
        if base_url:
            existing["EXCELMANUS_BASE_URL"] = base_url
        if model:
            existing["EXCELMANUS_MODEL"] = model

        save_env_file(env_path, existing)
        self.log("配置已保存到 .env", "info")

    def _on_deploy(self):
        """一键部署。"""
        # 保存配置
        self._save_config()

        # 检查 Python
        python = find_python()
        if not python:
            messagebox.showerror("错误", "未找到 Python，请先安装 Python 3.10+")
            return

        # 禁用按钮
        self.deploy_btn.set_disabled(True)
        self.deploy_btn.set_text("⏳  部署中...")
        self.stop_btn.set_disabled(False)
        self.progress.start(15)

        # 后台线程执行部署
        thread = threading.Thread(target=self._do_deploy, daemon=True)
        thread.start()

    def _do_deploy(self):
        """执行部署流程。"""
        try:
            python = find_python()
            bp = self.backend_port.get()
            fp = self.frontend_port.get()

            # ── Step 1: 创建虚拟环境 ──
            venv_path = PROJECT_ROOT / ".venv"
            if not venv_path.exists():
                self.log("正在创建 Python 虚拟环境...", "dim")
                self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  创建虚拟环境..."))
                self.after(0, lambda: self.deps_status.set_status("pending"))

                result = subprocess.run(
                    [sys.executable, "-m", "venv", str(venv_path)],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    self.log(f"创建虚拟环境失败: {result.stderr}", "error")
                    self._deploy_failed()
                    return
                self.log("虚拟环境已创建", "success")
                python = str(venv_path / "Scripts" / "python.exe")

            # ── Step 2: 安装后端依赖 ──
            self.log("检查后端依赖...", "dim")
            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  检查后端依赖..."))

            check = subprocess.run(
                [python, "-c", "import fastapi; import uvicorn; import rich"],
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if check.returncode != 0:
                self.log("正在安装后端依赖（首次可能需要几分钟）...", "warn")
                self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  安装后端依赖..."))

                pip_cmd = [
                    python, "-m", "pip", "install", "-e",
                    f"{PROJECT_ROOT}[all]",
                    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
                    "--trusted-host", "pypi.tuna.tsinghua.edu.cn",
                ]
                result = subprocess.run(
                    pip_cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    self.log("清华镜像安装失败，尝试默认源...", "warn")
                    pip_cmd = [python, "-m", "pip", "install", "-e", f"{PROJECT_ROOT}[all]"]
                    result = subprocess.run(
                        pip_cmd,
                        capture_output=True,
                        text=True,
                        cwd=str(PROJECT_ROOT),
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if result.returncode != 0:
                        self.log(f"后端依赖安装失败: {result.stderr[-500:]}", "error")
                        self._deploy_failed()
                        return
                self.log("后端依赖安装完成", "success")
            else:
                self.log("后端依赖已就绪", "info")

            # ── Step 3: 安装前端依赖 ──
            node_modules = PROJECT_ROOT / "web" / "node_modules"
            if not node_modules.exists():
                self.log("正在安装前端依赖...", "dim")
                self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  安装前端依赖..."))

                npm = find_npm()
                if not npm:
                    self.log("npm 未安装，跳过前端", "error")
                    self._deploy_failed()
                    return

                result = subprocess.run(
                    ["npm", "install"],
                    capture_output=True,
                    text=True,
                    cwd=str(PROJECT_ROOT / "web"),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    shell=True,
                )
                if result.returncode != 0:
                    self.log(f"前端依赖安装失败: {result.stderr[-500:]}", "error")
                    self._deploy_failed()
                    return
                self.log("前端依赖安装完成", "success")
            else:
                self.log("前端依赖已就绪", "info")

            self.after(0, lambda: self.deps_status.set_text("项目依赖  ·  ✅ 已安装"))
            self.after(0, lambda: self.deps_status.set_status("success"))

            # ── Step 4: 清理占用端口 ──
            self._kill_port(bp)
            self._kill_port(fp)

            # ── Step 5: 启动后端 ──
            self.log(f"启动 FastAPI 后端 [0.0.0.0:{bp}]...", "dim")
            self.after(0, lambda: self.backend_svc_status.set_text("后端服务  ·  启动中..."))
            self.after(0, lambda: self.backend_svc_status.set_status("pending"))

            env = os.environ.copy()
            env_file = PROJECT_ROOT / ".env"
            if env_file.exists():
                for k, v in load_env_file(env_file).items():
                    env[k] = v

            self.backend_process = subprocess.Popen(
                [
                    python, "-c",
                    f"import uvicorn; uvicorn.run('excelmanus.api:app', host='0.0.0.0', port={bp}, log_level='info')",
                ],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            # 启动后端日志读取线程
            threading.Thread(
                target=self._read_process_output,
                args=(self.backend_process, "后端"),
                daemon=True,
            ).start()

            # 等待后端就绪
            backend_ready = False
            for i in range(60):
                if self.backend_process.poll() is not None:
                    self.log("后端进程异常退出", "error")
                    self._deploy_failed()
                    return
                try:
                    import urllib.request
                    req = urllib.request.urlopen(f"http://localhost:{bp}/api/v1/health", timeout=2)
                    if req.status == 200:
                        backend_ready = True
                        break
                except Exception:
                    pass
                time.sleep(1)

            if backend_ready:
                self.log(f"后端已就绪 → http://localhost:{bp}", "success")
                self.after(0, lambda: self.backend_svc_status.set_text(f"后端服务  ·  运行中 :{bp}"))
                self.after(0, lambda: self.backend_svc_status.set_status("running"))
            else:
                self.log("后端启动超时（60s），但仍在尝试...", "warn")
                self.after(0, lambda: self.backend_svc_status.set_text(f"后端服务  ·  启动中（超时）"))
                self.after(0, lambda: self.backend_svc_status.set_status("pending"))

            # ── Step 6: 启动前端 ──
            self.log(f"启动 Next.js 前端 [dev] [端口 {fp}]...", "dim")
            self.after(0, lambda: self.frontend_svc_status.set_text("前端服务  ·  启动中..."))
            self.after(0, lambda: self.frontend_svc_status.set_status("pending"))

            self.frontend_process = subprocess.Popen(
                ["npm", "run", "dev", "--", "-p", str(fp)],
                cwd=str(PROJECT_ROOT / "web"),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                shell=True,
            )

            # 启动前端日志读取线程
            threading.Thread(
                target=self._read_process_output,
                args=(self.frontend_process, "前端"),
                daemon=True,
            ).start()

            time.sleep(5)

            self.log(f"前端已启动 → http://localhost:{fp}", "success")
            self.after(0, lambda: self.frontend_svc_status.set_text(f"前端服务  ·  运行中 :{fp}"))
            self.after(0, lambda: self.frontend_svc_status.set_status("running"))

            # ── 完成 ──
            self.after(0, self.progress.stop)
            self.after(0, lambda: self.deploy_btn.set_text("✅  已启动"))
            self.after(0, lambda: self.open_btn.set_disabled(False))

            self.log("", "dim")
            self.log("═" * 44, "info")
            self.log("   ExcelManus 部署成功！", "success")
            self.log(f"   前端: http://localhost:{fp}", "info")
            self.log(f"   后端: http://localhost:{bp}", "info")
            self.log("═" * 44, "info")

            # 自动打开浏览器
            if self.auto_open_browser.get():
                time.sleep(2)
                webbrowser.open(f"http://localhost:{fp}")

        except Exception as e:
            self.log(f"部署异常: {e}", "error")
            self._deploy_failed()

    def _read_process_output(self, process, label):
        """读取子进程输出并写入日志。"""
        try:
            for line in iter(process.stdout.readline, b""):
                try:
                    text = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    text = str(line).rstrip()
                if text:
                    # 限制日志行长度
                    if len(text) > 200:
                        text = text[:200] + "..."
                    self.after(0, lambda t=text, l=label: self.log(f"[{l}] {t}", "dim"))
        except Exception:
            pass

    def _deploy_failed(self):
        """部署失败处理。"""
        self.after(0, self.progress.stop)
        self.after(0, lambda: self.deploy_btn.set_disabled(False))
        self.after(0, lambda: self.deploy_btn.set_text("▶  一键启动"))
        self.log("部署失败，请检查上方错误信息", "error")

    def _kill_port(self, port: int):
        """杀死占用指定端口的进程。"""
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.split("\n"):
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid and pid != "0":
                        self.log(f"端口 {port} 被占用 (PID {pid})，正在清理...", "warn")
                        subprocess.run(
                            ["taskkill", "/PID", pid, "/F"],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  停止服务
    # ═══════════════════════════════════════════════════════════════

    def _on_stop(self):
        """停止所有服务。"""
        self.log("正在停止服务...", "dim")

        if self.backend_process:
            try:
                self.backend_process.terminate()
                self.backend_process.wait(timeout=5)
            except Exception:
                try:
                    self.backend_process.kill()
                except Exception:
                    pass
            self.backend_process = None

        if self.frontend_process:
            try:
                self.frontend_process.terminate()
                self.frontend_process.wait(timeout=5)
            except Exception:
                try:
                    self.frontend_process.kill()
                except Exception:
                    pass
            self.frontend_process = None

        # 清理端口
        self._kill_port(self.backend_port.get())
        self._kill_port(self.frontend_port.get())

        self.backend_svc_status.set_text("后端服务  ·  已停止")
        self.backend_svc_status.set_status("stopped")
        self.frontend_svc_status.set_text("前端服务  ·  已停止")
        self.frontend_svc_status.set_status("stopped")

        self.deploy_btn.set_disabled(False)
        self.deploy_btn.set_text("▶  一键启动")
        self.stop_btn.set_disabled(True)
        self.open_btn.set_disabled(True)
        self.progress.stop()

        self.log("所有服务已停止", "info")

    # ═══════════════════════════════════════════════════════════════
    #  辅助操作
    # ═══════════════════════════════════════════════════════════════

    def _open_browser(self):
        """打开浏览器。"""
        fp = self.frontend_port.get()
        webbrowser.open(f"http://localhost:{fp}")

    def _on_close(self):
        """关闭窗口前的清理。"""
        if self.backend_process or self.frontend_process:
            if messagebox.askyesno("退出确认", "服务正在运行，退出将停止所有服务。\n确定要退出吗？"):
                self._on_stop()
                self.destroy()
        else:
            self.destroy()


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Windows DPI 适配
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = ExcelManusDeployer()
    app.mainloop()
