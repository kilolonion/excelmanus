"""带类型标记的 JSON 编解码助手。

datetime/date/time 等原生 JSON 无法表达的值编码为
``{"$em_type": <类型名>, "v": <ISO 字符串>}`` 标记；其余常见非 JSON
类型尽力转换（Decimal→float、set→有序 list、numpy→tolist/item）。

Code Mode SDK 桥（``code_mode._SDK_PREAMBLE`` 内的 ``_em_json_default``）
与宿主侧持久化（审批 manifest、审批 DB）共用同一标记约定：
写入端用 ``em_json_default``，读回端用 ``revive_typed_args`` 还原真类型。
两处实现须保持一致。
"""

from __future__ import annotations

import datetime as _dt
import decimal as _dec
from typing import Any

_TYPE_TAG = "$em_type"
_TYPE_VALUE = "v"


def em_json_default(o: Any) -> Any:
    """``json.dumps`` 的 ``default`` 回调：按 ``$em_type`` 约定编码非 JSON 值。"""
    if isinstance(o, _dt.datetime):
        if o != o:  # NaT
            return None
        return {_TYPE_TAG: "datetime", _TYPE_VALUE: o.isoformat()}
    if isinstance(o, (_dt.date, _dt.time)):
        return {_TYPE_TAG: type(o).__name__, _TYPE_VALUE: o.isoformat()}
    if isinstance(o, _dec.Decimal):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=repr)
    tolist = getattr(o, "tolist", None)
    if callable(tolist):
        return tolist()
    item = getattr(o, "item", None)
    if callable(item):
        return item()
    return str(o)


def revive_typed_args(value: Any) -> Any:
    """还原 ``em_json_default`` 写出的 ``{"$em_type", "v"}`` 类型标记。"""
    if isinstance(value, list):
        return [revive_typed_args(item) for item in value]
    if isinstance(value, dict):
        tag = value.get(_TYPE_TAG)
        raw = value.get(_TYPE_VALUE)
        if tag in ("datetime", "date", "time") and isinstance(raw, str):
            try:
                if tag == "datetime":
                    return _dt.datetime.fromisoformat(raw)
                if tag == "date":
                    return _dt.date.fromisoformat(raw)
                return _dt.time.fromisoformat(raw)
            except ValueError:
                return None
        return {key: revive_typed_args(item) for key, item in value.items()}
    return value


__all__ = ["em_json_default", "revive_typed_args"]
