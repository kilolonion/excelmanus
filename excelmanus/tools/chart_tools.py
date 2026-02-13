"""图表工具：支持柱状图、折线图、饼图、散点图和雷达图。"""

from __future__ import annotations

import json
from functools import lru_cache
import warnings
from typing import Any

import matplotlib
matplotlib.use("Agg")  # 非交互式后端，必须在导入 pyplot 之前设置

from matplotlib import font_manager
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from excelmanus.logger import get_logger
from excelmanus.security import FileAccessGuard
from excelmanus.tools.registry import ToolDef

logger = get_logger("tools.chart")

# ── Skill 元数据 ──────────────────────────────────────────

SKILL_NAME = "chart"
SKILL_DESCRIPTION = "可视化工具集：支持柱状图、折线图、饼图、散点图和雷达图"

# ── 模块级 FileAccessGuard（延迟初始化） ─────────────────

_guard: FileAccessGuard | None = None

SUPPORTED_CHART_TYPES = ("bar", "line", "pie", "scatter", "radar")
CJK_FONT_CANDIDATES = (
    "Noto Sans CJK SC",
    "Microsoft YaHei",
    "PingFang SC",
    "WenQuanYi Zen Hei",
    "Arial Unicode MS",
    "STHeiti",
    "Songti SC",
    "SimHei",
)


def _get_guard() -> FileAccessGuard:
    """获取或创建 FileAccessGuard 单例。"""
    global _guard
    if _guard is None:
        _guard = FileAccessGuard(".")
    return _guard


def init_guard(workspace_root: str) -> None:
    """初始化文件访问守卫（供外部配置调用）。

    Args:
        workspace_root: 工作目录根路径。
    """
    global _guard
    _guard = FileAccessGuard(workspace_root)


@lru_cache(maxsize=1)
def _select_cjk_font() -> str | None:
    """选择一个可用的中文字体，避免中文字符渲染警告。"""
    available = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in CJK_FONT_CANDIDATES:
        if font_name in available:
            return font_name
    return None


def _contains_cjk(text: str) -> bool:
    """判断文本是否包含 CJK 统一表意文字（常见中文字符范围）。"""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _configure_plot_fonts(labels: pd.Series, title: str) -> None:
    """根据数据内容配置字体，优先选择可用中文字体。"""
    sample_texts = [title]
    sample_texts.extend(str(v) for v in labels.head(20).tolist())
    needs_cjk = any(_contains_cjk(text) for text in sample_texts if text)

    if needs_cjk:
        cjk_font = _select_cjk_font()
        if cjk_font:
            plt.rcParams["font.sans-serif"] = [cjk_font, "DejaVu Sans"]
        else:
            plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
            logger.warning("未检测到可用中文字体，图表中文可能显示异常。")
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]

    plt.rcParams["axes.unicode_minus"] = False


# ── 工具函数 ──────────────────────────────────────────────


def create_chart(
    file_path: str,
    chart_type: str,
    x_column: str,
    y_column: str,
    output_path: str,
    title: str | None = None,
    sheet_name: str | None = None,
) -> str:
    """从 Excel 数据生成图表并保存为图片文件。

    Args:
        file_path: 数据源 Excel 文件路径。
        chart_type: 图表类型，支持 bar/line/pie/scatter/radar。
        x_column: X 轴（或标签）列名。
        y_column: Y 轴（或数值）列名。
        output_path: 输出图片文件路径（如 output.png）。
        title: 图表标题，默认自动生成。
        sheet_name: 工作表名称，默认读取第一个。

    Returns:
        JSON 格式的操作结果。
    """
    if chart_type not in SUPPORTED_CHART_TYPES:
        return json.dumps(
            {"status": "error", "message": f"不支持的图表类型 '{chart_type}'，支持: {list(SUPPORTED_CHART_TYPES)}"},
            ensure_ascii=False,
        )

    guard = _get_guard()
    safe_input = guard.resolve_and_validate(file_path)
    safe_output = guard.resolve_and_validate(output_path)

    # 读取数据
    kwargs: dict[str, Any] = {"io": safe_input}
    if sheet_name is not None:
        kwargs["sheet_name"] = sheet_name

    df = pd.read_excel(**kwargs)

    # 校验列名
    for col_name, col_label in [(x_column, "x_column"), (y_column, "y_column")]:
        if col_name not in df.columns:
            return json.dumps(
                {"status": "error", "message": f"{col_label} '{col_name}' 不存在，可用列: {list(df.columns)}"},
                ensure_ascii=False,
            )

    plot_df = df[[x_column, y_column]].dropna()
    if plot_df.empty:
        return json.dumps(
            {"status": "error", "message": f"列 '{x_column}' 与 '{y_column}' 没有可绘图的数据"},
            ensure_ascii=False,
        )

    if chart_type == "radar" and len(plot_df) < 3:
        return json.dumps(
            {"status": "error", "message": "雷达图至少需要 3 条有效数据"},
            ensure_ascii=False,
        )

    x_data = plot_df[x_column]
    y_data = plot_df[y_column]
    chart_title = title or f"{y_column} by {x_column}"

    _configure_plot_fonts(x_data, chart_title)

    fig, ax = plt.subplots(figsize=(10, 6))

    try:
        # 部分环境缺少完整 CJK 字体时，抑制 matplotlib 的缺字 warning，避免污染日志。
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                category=UserWarning,
                message=r"Glyph .* missing from font\(s\) .*",
            )

            if chart_type == "bar":
                ax.bar(x_data.astype(str), y_data)
                ax.set_xlabel(x_column)
                ax.set_ylabel(y_column)
                plt.xticks(rotation=45, ha="right")

            elif chart_type == "line":
                ax.plot(x_data, y_data, marker="o")
                ax.set_xlabel(x_column)
                ax.set_ylabel(y_column)

            elif chart_type == "pie":
                ax.pie(y_data, labels=x_data.astype(str), autopct="%1.1f%%")
                ax.set_aspect("equal")

            elif chart_type == "scatter":
                ax.scatter(x_data, y_data)
                ax.set_xlabel(x_column)
                ax.set_ylabel(y_column)

            elif chart_type == "radar":
                # 雷达图需要特殊处理
                plt.close(fig)
                fig, ax = _draw_radar(x_data, y_data, chart_title)

            ax.set_title(chart_title)
            fig.tight_layout()
            fig.savefig(safe_output, dpi=150, bbox_inches="tight")

    finally:
        plt.close(fig)

    logger.info("已生成 %s 图表 -> %s", chart_type, safe_output.name)

    return json.dumps(
        {"status": "success", "chart_type": chart_type, "output_file": str(safe_output.name)},
        ensure_ascii=False,
    )


def _draw_radar(
    labels: pd.Series, values: pd.Series, title: str
) -> tuple[plt.Figure, plt.Axes]:
    """绘制雷达图。

    Args:
        labels: 各维度标签。
        values: 各维度数值。
        title: 图表标题。

    Returns:
        (fig, ax) 元组。
    """
    categories = labels.astype(str).tolist()
    vals = values.tolist()
    n = len(categories)
    if n == 0:
        raise ValueError("雷达图数据为空")
    if n < 3:
        raise ValueError("雷达图至少需要 3 个维度")

    # 计算角度（均匀分布）
    angles = [i / n * 2 * np.pi for i in range(n)]
    # 闭合多边形
    vals_closed = vals + [vals[0]]
    angles_closed = angles + [angles[0]]

    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw={"projection": "polar"})
    ax.plot(angles_closed, vals_closed, "o-", linewidth=2)
    ax.fill(angles_closed, vals_closed, alpha=0.25)
    ax.set_xticks(angles)
    ax.set_xticklabels(categories)
    ax.set_title(title, y=1.08)

    return fig, ax


# ── get_tools() 导出 ──────────────────────────────────────


def get_tools() -> list[ToolDef]:
    """返回可视化 Skill 的所有工具定义。"""
    return [
        ToolDef(
            name="create_chart",
            description="从 Excel 数据生成图表（柱状图、折线图、饼图、散点图、雷达图）并保存为图片。调用前先用 read_excel 确认数据范围、列名和数据类型。",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "数据源 Excel 文件路径",
                    },
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "scatter", "radar"],
                        "description": "图表类型",
                    },
                    "x_column": {
                        "type": "string",
                        "description": "X 轴（或标签）列名",
                    },
                    "y_column": {
                        "type": "string",
                        "description": "Y 轴（或数值）列名",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "输出图片文件路径（如 chart.png）",
                    },
                    "title": {
                        "type": "string",
                        "description": "图表标题，默认自动生成",
                    },
                    "sheet_name": {
                        "type": "string",
                        "description": "工作表名称，默认读取第一个",
                    },
                },
                "required": ["file_path", "chart_type", "x_column", "y_column", "output_path"],
                "additionalProperties": False,
            },
            func=create_chart,
        ),
    ]
