---
trigger: always_on
---

# 运行环境

- 操作系统：macOS (Apple Silicon / arm64)
- Shell：zsh
- Python：3.12（通过 uv 管理，虚拟环境位于 `.venv/`）
- 包管理器：uv（路径 `~/.local/bin/uv`）
- 工作区根目录：`/Users/jiangwenxuan/Desktop/excelagent`

## 注意事项

- 执行 Python 或 pytest 命令时，使用 `.venv` 中的解释器（虚拟环境已激活，名称 `excelmanus`）。
- 安装依赖使用 `uv pip install` 而非裸 `pip`。
- 路径分隔符为 `/`，脚本中避免使用 Windows 风格路径。
- Homebrew 可用于安装系统级工具。
