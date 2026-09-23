"""Deterministic numeric parsing; Chinese magnitude and percent apply per cell."""
from decimal import Decimal, InvalidOperation
import math
import numbers
import re
import unicodedata

_NUMBER = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(万亿|亿|万|千)?(元|份|个|台|件|套)?(%)?$")
_SCALE = {None: 1, "千": 1000, "万": 10000, "亿": 100000000, "万亿": 1000000000000}


def parse_number(value):
    if value is None or isinstance(value, bool):
        return float("nan")
    if isinstance(value, numbers.Number):
        return float(value)
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text or text.startswith("="):
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = re.sub(r"^(?:人民币|RMB|CNY|[¥￥$€£])\s*", "", text, flags=re.I)
    if "," in text:
        if not re.match(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:[^\d,].*)?$", text):
            return float("nan")
        text = text.replace(",", "")
    match = _NUMBER.fullmatch(text)
    if not match:
        return float("nan")
    try:
        amount = Decimal(match[1]) * _SCALE[match[2]]
        if match[4]:
            amount /= 100
        result = float(-amount if negative else amount)
        return result if math.isfinite(result) else float("nan")
    except InvalidOperation:
        return float("nan")
