"""公共辅助函数：供 run_code sandbox 和内部模块共享。

本模块设计为轻量级、无副作用，可在 sandbox 环境中安全 import。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def smart_read_excel(
    path: str,
    sheet: str | None = None,
    max_rows: int | None = None,
    max_header_scan: int = 10,
    unnamed_threshold: float = 0.3,
) -> tuple[pd.DataFrame, int]:
    """智能读取 Excel，自动跳过合并标题行找到真实表头。

    当 ``pd.read_excel(header=0)`` 产生大量 ``Unnamed:*`` 列名时，
    自动向下扫描最多 ``max_header_scan`` 行，选择 Unnamed 比例
    低于 ``unnamed_threshold`` 的行作为表头。

    Args:
        path: Excel 文件路径。
        sheet: 工作表名称，默认第一个。
        max_rows: 最大读取数据行数（不含表头），默认全部。
        max_header_scan: 最多扫描的候选表头行数，默认 10。
        unnamed_threshold: Unnamed 列名占比阈值，低于此值即认为
            找到了合理表头，默认 0.3（30%）。

    Returns:
        ``(DataFrame, effective_header_row)`` 元组。
        ``effective_header_row`` 是实际使用的表头行号（0-indexed）。
    """
    read_kwargs: dict[str, Any] = {"io": path}
    if sheet is not None:
        read_kwargs["sheet_name"] = sheet
    if max_rows is not None:
        read_kwargs["nrows"] = max_rows

    for header in range(0, max_header_scan):
        try:
            df = pd.read_excel(**read_kwargs, header=header)
        except Exception:
            break
        if df.empty and header > 0:
            break
        unnamed = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
        if unnamed / max(len(df.columns), 1) < unnamed_threshold:
            return df, header

    # 回退：使用默认 header=0
    df = pd.read_excel(**read_kwargs, header=0)
    return df, 0


def mask_pii(text: str) -> str:
    """对文本中的常见 PII 做脱敏处理。

    覆盖：中国大陆手机号、身份证号、银行卡号、邮箱地址。
    可在 ``run_code`` 脚本中直接调用，用于输出前脱敏。

    Example::

        from excelmanus.utils import mask_pii
        print(mask_pii("联系人: 张三 13812345678"))
        # → 联系人: 张三 138****5678
    """
    import re

    # 身份证（18位）— 必须先于银行卡
    value = re.sub(
        r"(?<!\d)\d{17}[\dXx](?!\d)",
        lambda m: m.group(0)[:6] + "********" + m.group(0)[14:],
        text,
    )
    # 银行卡（16-19位）
    value = re.sub(
        r"(?<!\d)\d{16,19}(?!\d)",
        lambda m: m.group(0)[:4] + " **** **** " + m.group(0)[-4:],
        value,
    )
    # 手机号（11位）
    value = re.sub(
        r"(?<!\d)1[3-9]\d{9}(?!\d)",
        lambda m: m.group(0)[:3] + "****" + m.group(0)[7:],
        value,
    )
    # 邮箱
    value = re.sub(
        r"(?<![\w.@])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w@])",
        lambda m: _mask_email(m.group(0)),
        value,
    )
    return value


def _mask_email(email: str) -> str:
    local, domain = email.rsplit("@", 1)
    masked = (local[0] + "***") if len(local) > 1 else (local + "***")
    return f"{masked}@{domain}"
