from __future__ import annotations

import builtins
import importlib
import sys
from unittest.mock import patch


def test_api_module_imports_without_orjson() -> None:
    """API 模块在缺少 orjson 时仍应可导入并回退到标准 JSON。"""
    sys.modules.pop("excelmanus.api", None)
    sys.modules.pop("orjson", None)

    real_import = builtins.__import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "orjson":
            raise ModuleNotFoundError("No module named 'orjson'")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=_fake_import):
        module = importlib.import_module("excelmanus.api")

    response = module.CustomJSONResponse(content={"message": "中文", "count": 1})
    payload = response.body.decode("utf-8")

    assert '"message":"中文"' in payload
    assert "\\u4e2d\\u6587" not in payload
