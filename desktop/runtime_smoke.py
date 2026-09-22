"""Offline validation inside the frozen backend, not the build venv."""
from pathlib import Path
import tempfile


def check_runtime() -> None:
    import importlib
    for module in ("mcp.client.stdio", "mcp.client.streamable_http", "xlrd", "pyxlsb", "xlsxwriter", "docx", "openpyxl"):
        importlib.import_module(module)
    from excelmanus.security.cipher import TokenCipher
    cipher = TokenCipher()
    encrypted = cipher.encrypt("desktop-smoke-secret")
    assert cipher.decrypt(encrypted) == "desktop-smoke-secret"
    from excelmanus.tools.code_tools import init_guard, run_code
    from excelmanus.tools.intent_tools import edit_spreadsheet
    from openpyxl import load_workbook
    with tempfile.TemporaryDirectory(prefix="excelmanus-smoke-") as directory:
        init_guard(directory)
        created = edit_spreadsheet(file_path="验收.xlsx", create_workbook=True,
            operations=[{"kind": "write", "sheet": "数据", "start_cell": "A1", "values": [["金额"], [20], [22]]}])
        assert created.success, created
        result = run_code(code='''import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.linalg import solve
from sklearn.linear_model import LinearRegression
import plotly.graph_objects as go
frame = pd.read_excel('验收.xlsx', sheet_name='数据')
assert int(frame['金额'].sum()) == 42
assert solve([[2.]], [4.])[0] == 2.
assert LinearRegression().fit([[1.], [2.]], [2., 4.]).predict([[3.]])[0] > 5.9
plt.plot([1, 2], [20, 22])
plt.savefig('验收.png')
plt.close('all')
assert go.Figure(go.Bar(x=[1], y=[2])).to_json()
print('desktop-runtime-ok')
''', sandbox_tier="GREEN", timeout_seconds=60)
        assert result.success, result
        assert 'desktop-runtime-ok' in str(result.value), result
        workbook = load_workbook(Path(directory) / '验收.xlsx')
        assert workbook['数据']['A2'].value == 20
        workbook.close()
    print("FROZEN_RUNTIME_OK", flush=True)
