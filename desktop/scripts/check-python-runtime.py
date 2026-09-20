"""Offline checks of features supported by the separately bundled interpreter."""
import json
import os
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory(prefix="excelmanus-runtime-") as directory:
    root = Path(directory)
    os.environ["MPLCONFIGDIR"] = str(root / "matplotlib")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    import seaborn as sns
    import yaml
    import xlrd
    import pyxlsb
    from docx import Document
    from oletools import olevba
    from PIL import Image
    from scipy.linalg import solve
    from sklearn.linear_model import LinearRegression
    import plotly.graph_objects as go

    frame = pd.DataFrame({"金额": [10, 20], "数量": [1, 2]})
    for engine in ("openpyxl", "xlsxwriter"):
        path = root / f"中文表格-{engine}.xlsx"
        frame.to_excel(path, index=False, engine=engine)
        pd.testing.assert_frame_equal(pd.read_excel(path), frame)
    document = Document()
    document.add_paragraph("中文文档")
    document.save(root / "文档.docx")
    assert Document(root / "文档.docx").paragraphs[0].text == "中文文档"
    np.testing.assert_allclose(solve([[2., 0.], [0., 2.]], [2., 4.]), [1., 2.])
    model = LinearRegression().fit([[1.], [2.], [3.]], [2., 4., 6.])
    np.testing.assert_allclose(model.predict([[4.]]), [8.])
    sns.lineplot(x=[1, 2, 3], y=[2, 4, 6])
    plt.savefig(root / "chart.png")
    plt.close("all")
    with Image.open(root / "chart.png") as image:
        assert image.width > 0
    assert json.loads(go.Figure(go.Bar(x=[1], y=[2])).to_json())["data"]
    assert yaml.safe_load("enabled: true")["enabled"] is True
    assert olevba.VBA_Parser and xlrd.open_workbook and pyxlsb.open_workbook

print("Bundled Python: XLSX read/write, DOCX, PNG, scientific analysis, Plotly and VBA imports OK")
