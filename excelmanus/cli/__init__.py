"""CLI 包 — 基于 Claude Code 风格的命令行交互界面。"""


def main() -> None:
    """CLI 入口函数（延迟导入，避免循环依赖）。"""
    from excelmanus.cli.main import main as _main

    _main()


__all__ = ["main"]
