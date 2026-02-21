# excelmanus/cv_analyzer.py
"""CV 像素分析：用 Pillow 从表格图片中提取精确的样式信息。

提供三个核心能力：
1. detect_grid_lines — 检测表格网格线位置
2. sample_cell_colors — 采样每个单元格的背景色
3. compute_column_widths — 从像素距离计算 Excel 列宽
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)


def detect_grid_lines(
    raw: bytes,
    *,
    edge_threshold: int = 30,
    min_line_ratio: float = 0.5,
) -> dict[str, Any]:
    """检测图片中的水平和垂直表格线。

    Returns:
        {"h_lines": [y1, y2, ...], "v_lines": [x1, x2, ...],
         "width": int, "height": int}
    """
    from PIL import Image, ImageFilter

    img = Image.open(BytesIO(raw)).convert("L")
    w, h = img.size

    # 边缘检测
    edges = img.filter(ImageFilter.FIND_EDGES)
    pixels = edges.load()

    # 检测水平线：每行中边缘像素占比超过阈值
    h_lines: list[int] = []
    for y in range(h):
        edge_count = sum(
            1 for x in range(w) if pixels[x, y] > edge_threshold
        )
        if edge_count / w > min_line_ratio:
            h_lines.append(y)

    # 检测垂直线：每列中边缘像素占比超过阈值
    v_lines: list[int] = []
    for x in range(w):
        edge_count = sum(
            1 for y in range(h) if pixels[x, y] > edge_threshold
        )
        if edge_count / h > min_line_ratio:
            v_lines.append(x)

    # 合并相邻线（±2px 内的视为同一条线）
    h_lines = _merge_nearby(h_lines, tolerance=2)
    v_lines = _merge_nearby(v_lines, tolerance=2)

    return {
        "h_lines": h_lines,
        "v_lines": v_lines,
        "width": w,
        "height": h,
    }


def sample_cell_colors(
    raw: bytes,
    grid: dict[str, Any],
    *,
    sample_margin: int = 5,
) -> list[list[str]]:
    """基于网格线位置，采样每个单元格中心区域的背景色。

    Returns:
        2D list[row][col] of hex color strings like "#4472C4"
    """
    from PIL import Image

    img = Image.open(BytesIO(raw)).convert("RGB")
    h_lines = grid["h_lines"]
    v_lines = grid["v_lines"]

    rows = len(h_lines) - 1
    cols = len(v_lines) - 1
    colors: list[list[str]] = []

    for r in range(rows):
        row_colors: list[str] = []
        y_top = h_lines[r] + sample_margin
        y_bot = h_lines[r + 1] - sample_margin
        y_mid = (y_top + y_bot) // 2

        for c in range(cols):
            x_left = v_lines[c] + sample_margin
            x_right = v_lines[c + 1] - sample_margin
            x_mid = (x_left + x_right) // 2

            # 采样中心 5x5 区域取平均
            samples = []
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    px = max(0, min(img.width - 1, x_mid + dx))
                    py = max(0, min(img.height - 1, y_mid + dy))
                    samples.append(img.getpixel((px, py)))

            avg_r = sum(s[0] for s in samples) // len(samples)
            avg_g = sum(s[1] for s in samples) // len(samples)
            avg_b = sum(s[2] for s in samples) // len(samples)
            hex_color = f"#{avg_r:02X}{avg_g:02X}{avg_b:02X}"
            row_colors.append(hex_color)

        colors.append(row_colors)

    return colors


def compute_column_widths(
    grid: dict[str, Any],
    *,
    base_char_width: float = 7.0,
) -> list[float]:
    """从垂直线像素距离计算 Excel 字符单位列宽。

    Args:
        grid: detect_grid_lines 的返回值
        base_char_width: 1 个 Excel 字符对应的像素数（默认 7.0）
    """
    v_lines = grid["v_lines"]
    widths: list[float] = []
    for i in range(len(v_lines) - 1):
        px_width = v_lines[i + 1] - v_lines[i]
        char_width = round(px_width / base_char_width, 1)
        widths.append(max(char_width, 2.0))  # 最小 2 字符
    return widths


def compute_row_heights(
    grid: dict[str, Any],
    *,
    base_row_height_px: float = 1.33,
) -> dict[str, float]:
    """从水平线像素距离计算 Excel 行高（磅）。

    Args:
        grid: detect_grid_lines 的返回值
        base_row_height_px: 1 磅对应的像素数（默认 1.33）
    """
    h_lines = grid["h_lines"]
    heights: dict[str, float] = {}
    for i in range(len(h_lines) - 1):
        px_height = h_lines[i + 1] - h_lines[i]
        pt_height = round(px_height / base_row_height_px, 1)
        heights[str(i + 1)] = max(pt_height, 12.0)  # 最小 12 磅
    return heights


def _merge_nearby(values: list[int], tolerance: int = 2) -> list[int]:
    """合并相邻值（±tolerance 内取平均）。"""
    if not values:
        return []
    merged: list[int] = []
    group: list[int] = [values[0]]
    for v in values[1:]:
        if v - group[-1] <= tolerance:
            group.append(v)
        else:
            merged.append(sum(group) // len(group))
            group = [v]
    merged.append(sum(group) // len(group))
    return merged
