# tests/test_cv_analyzer.py
"""CV 像素分析模块测试。"""
from __future__ import annotations
from io import BytesIO
from PIL import Image, ImageDraw
import pytest


def _make_table_image(rows=3, cols=3, cell_w=80, cell_h=30,
                      bg_color=(255, 255, 255),
                      header_bg=(68, 114, 196),
                      border_color=(0, 0, 0)):
    """创建带边框线的简单表格图片。"""
    w = cols * cell_w + 1
    h = rows * cell_h + 1
    img = Image.new("RGB", (w, h), bg_color)
    draw = ImageDraw.Draw(img)
    # 画网格线
    for r in range(rows + 1):
        y = r * cell_h
        draw.line([(0, y), (w, y)], fill=border_color, width=1)
    for c in range(cols + 1):
        x = c * cell_w
        draw.line([(x, 0), (x, h)], fill=border_color, width=1)
    # 表头填充
    for c in range(cols):
        x0, y0 = c * cell_w + 2, 2
        x1, y1 = (c + 1) * cell_w - 1, cell_h - 1
        draw.rectangle([x0, y0, x1, y1], fill=header_bg)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestDetectGridLines:
    def test_detects_correct_row_count(self):
        from excelmanus.cv_analyzer import detect_grid_lines
        raw = _make_table_image(rows=4, cols=3)
        result = detect_grid_lines(raw)
        # 4 行 → 5 条水平线
        assert len(result["h_lines"]) == 5

    def test_detects_correct_col_count(self):
        from excelmanus.cv_analyzer import detect_grid_lines
        raw = _make_table_image(rows=3, cols=5)
        result = detect_grid_lines(raw)
        # 5 列 → 6 条垂直线
        assert len(result["v_lines"]) == 6


class TestSampleCellColors:
    def test_header_color_detected(self):
        from excelmanus.cv_analyzer import sample_cell_colors, detect_grid_lines
        raw = _make_table_image(
            rows=3, cols=3,
            header_bg=(68, 114, 196),
        )
        grid = detect_grid_lines(raw)
        colors = sample_cell_colors(raw, grid)
        # 第一行应检测到蓝色系
        header_color = colors[0][0]  # row 0, col 0
        r, g, b = int(header_color[1:3], 16), int(header_color[3:5], 16), int(header_color[5:7], 16)
        assert b > r  # 蓝色分量应大于红色


class TestComputeColumnWidths:
    def test_widths_proportional(self):
        from excelmanus.cv_analyzer import detect_grid_lines, compute_column_widths
        raw = _make_table_image(rows=2, cols=3, cell_w=80)
        grid = detect_grid_lines(raw)
        widths = compute_column_widths(grid, base_char_width=7.0)
        # 3 列等宽，每列约 80/7 ≈ 11.4 字符
        assert len(widths) == 3
        assert all(10 < w < 13 for w in widths)
