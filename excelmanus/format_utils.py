"""数字格式推断工具——pipeline 与 image_tools 共享。"""

from __future__ import annotations

import re as _re


def infer_number_format(display_text: str) -> str | None:
    """从 display_text 推断 Excel number_format。

    示例:
        "12.50"    → "#,##0.00"
        "85%"      → "0%"
        "12.5%"    → "0.0%"
        "$1,200"   → "$#,##0"
        "¥1,200.00" → "¥#,##0.00"
        "1,200"    → "#,##0"
    """
    text = display_text.strip()
    if not text:
        return None

    # 百分数
    pct_match = _re.match(r'^-?[\d,]+(\.\d+)?%$', text)
    if pct_match:
        decimals = len(pct_match.group(1)[1:]) if pct_match.group(1) else 0
        return f"0.{'0' * decimals}%" if decimals else "0%"

    # 货币前缀
    currency_prefix = ""
    for sym in ("$", "¥", "€", "£", "₩"):
        if text.startswith(sym):
            currency_prefix = sym
            text = text[len(sym):].strip()
            break

    # 负号
    text = text.lstrip("-").strip()

    # 千分位 + 小数
    num_match = _re.match(r'^[\d,]+(\.\d+)?$', text)
    if num_match:
        has_comma = "," in text
        decimal_part = num_match.group(1)
        decimals = len(decimal_part[1:]) if decimal_part else 0

        if has_comma and decimals > 0:
            fmt = f"#,##0.{'0' * decimals}"
        elif has_comma:
            fmt = "#,##0"
        elif decimals > 0:
            fmt = f"0.{'0' * decimals}"
        else:
            return None  # 纯整数不需要特殊格式

        return f"{currency_prefix}{fmt}" if currency_prefix else fmt

    # 仅货币前缀 + 纯数字（无千分位）
    if currency_prefix:
        return f"{currency_prefix}#,##0"

    return None
