"""CLI 包 — 极简风格的命令行交互界面。"""


def main() -> None:
    """CLI 入口函数（延迟导入，避免循环依赖）。"""
    _missing = []
    try:
        import rich  # noqa: F401
    except ImportError:
        _missing.append("rich")
    try:
        import prompt_toolkit  # noqa: F401
    except ImportError:
        _missing.append("prompt_toolkit")
    if _missing:
        import sys
        print(
            f"错误：CLI 模式缺少依赖 {', '.join(_missing)}。\n"
            "请使用以下命令安装：\n"
            "  pip install excelmanus[cli]",
            file=sys.stderr,
        )
        sys.exit(1)

    from excelmanus.cli.main import main as _main

    _main()


__all__ = ["main"]
