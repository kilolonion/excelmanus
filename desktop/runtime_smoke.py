"""Offline validation inside the frozen backend, not the build venv."""
from pathlib import Path
import tempfile


def check_runtime() -> None:
    import importlib
    import os
    import sys
    if getattr(sys, "frozen", False):
        import playwright
        driver = Path(playwright.__file__).parent / "driver"
        assert not (driver / "node").exists() and not (driver / "node.exe").exists()
        assert Path(os.environ["PLAYWRIGHT_NODEJS_PATH"]).is_file()
        browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        assert browser_root.is_dir() and list(browser_root.glob("chromium_headless_shell-*"))
        assert not list(browser_root.glob("chromium-*")), "Headed Chromium must not be duplicated"
    for module in ("mcp.client.stdio", "mcp.client.streamable_http", "xlrd", "pyxlsb", "xlsxwriter", "docx", "openpyxl"):
        importlib.import_module(module)
    from excelmanus.security.cipher import TokenCipher
    cipher = TokenCipher()
    encrypted = cipher.encrypt("desktop-smoke-secret")
    assert cipher.decrypt(encrypted) == "desktop-smoke-secret"
    from excelmanus.tools.code_tools import init_guard, run_code
    from excelmanus.tools.workbook_tools import init_guard as init_workbook_guard, apply_spreadsheet_changes
    from openpyxl import load_workbook
    with tempfile.TemporaryDirectory(prefix="excelmanus-smoke-") as directory:
        init_guard(directory)
        init_workbook_guard(directory)
        created = apply_spreadsheet_changes(file_path="验收.xlsx", create=True,
            operations=[{"kind": "write", "sheet": "数据", "start_cell": "A1", "values": [["金额"], [20], [22]]}])
        assert created.success, created
        result = run_code(code='''import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
frame = pd.read_excel('验收.xlsx', sheet_name='数据')
assert int(frame['金额'].sum()) == 42
plt.plot([1, 2], [20, 22])
plt.savefig('验收.png')
plt.close('all')
print('desktop-runtime-ok')
''', sandbox_tier="GREEN", timeout_seconds=60)
        assert result.success, result
        assert 'desktop-runtime-ok' in str(result.value), result
        workbook = load_workbook(Path(directory) / '验收.xlsx')
        assert workbook['数据']['A2'].value == 20
        workbook.close()
        from excelmanus.tools.workbook_tools import preview_spreadsheet
        preview = preview_spreadsheet(file_path="验收.xlsx", sheet="数据", range="A1:A3", expected_version=created.value["content_version"])
        assert preview.success, preview.model_text
        assert preview.value["source_pixel_size"]["width"] > 0
        # Keep non-ASCII interchange covered under Windows' legacy code pages.
        unicode_book = apply_spreadsheet_changes(file_path="字符验收.xlsx", create=True,
            operations=[{"kind": "write", "sheet": "数据", "start_cell": "A1", "values": [["中文 € 😀"], [42]]}])
        assert unicode_book.success, unicode_book
        unicode_preview = preview_spreadsheet(file_path="字符验收.xlsx", sheet="数据", range="A1:A2")
        assert unicode_preview.success, unicode_preview.model_text
    print("FROZEN_RUNTIME_OK", flush=True)
