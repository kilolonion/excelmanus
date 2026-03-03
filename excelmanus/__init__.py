"""ExcelManus — 窗口感知层 + Tools + Skillpacks 架构。"""


def _read_version() -> str:
    """从 pyproject.toml（唯一版本源）读取版本号，失败时回退 importlib.metadata。"""
    from pathlib import Path

    toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if toml_path.is_file():
        try:
            for line in toml_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version") and "=" in stripped:
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    try:
        from importlib.metadata import version as _meta_version

        return _meta_version("excelmanus")
    except Exception:
        return "0.0.0"


__version__ = _read_version()
