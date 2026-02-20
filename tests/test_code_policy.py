"""CodePolicyEngine 静态分析单元测试。"""
from __future__ import annotations

import pytest

from excelmanus.security.code_policy import CodePolicyEngine, CodeRiskTier


class TestGreenTier:
    """纯计算 + 安全 IO 代码应归类为 GREEN。"""

    @pytest.mark.parametrize("code", [
        "import pandas as pd\ndf = pd.read_excel('data.xlsx')\nprint(df.head())",
        "import numpy as np\nx = np.array([1,2,3])\nprint(x.mean())",
        "from pathlib import Path\ndata = Path('out.csv').read_text()\nprint(len(data))",
        "import json, csv, re, math, datetime\nprint('ok')",
        "x = 1 + 2\nprint(x)",
        "# just a comment",
        "",
    ])
    def test_safe_code_is_green(self, code: str) -> None:
        result = CodePolicyEngine().analyze(code)
        assert result.tier == CodeRiskTier.GREEN


class TestYellowTier:
    """含网络但无危险操作的代码应归类为 YELLOW。"""

    @pytest.mark.parametrize("code", [
        "import requests\nr = requests.get('https://example.com')\nprint(r.status_code)",
        "from urllib.request import urlopen\ndata = urlopen('https://example.com').read()",
        "import httpx\nclient = httpx.Client()",
        "import socket\ns = socket.socket()",
    ])
    def test_network_code_is_yellow(self, code: str) -> None:
        result = CodePolicyEngine().analyze(code)
        assert result.tier == CodeRiskTier.YELLOW


class TestRedTier:
    """含危险操作的代码应归类为 RED。"""

    @pytest.mark.parametrize("code", [
        "import subprocess\nsubprocess.run(['ls'])",
        "import os\nos.system('rm -rf /')",
        "exec('print(1)')",
        "eval('__import__(\"os\").system(\"ls\")')",
        "import ctypes",
        "import signal",
        "compile('print(1)', '<string>', 'exec')",
        "__import__('subprocess')",
        "import importlib\nimportlib.import_module('subprocess')",
    ])
    def test_dangerous_code_is_red(self, code: str) -> None:
        result = CodePolicyEngine().analyze(code)
        assert result.tier == CodeRiskTier.RED

    def test_syntax_error_is_red(self) -> None:
        result = CodePolicyEngine().analyze("def f(\n")
        assert result.tier == CodeRiskTier.RED

    def test_obfuscation_base64_exec(self) -> None:
        code = "import base64\nexec(base64.b64decode('cHJpbnQoMSk='))"
        result = CodePolicyEngine().analyze(code)
        assert result.tier == CodeRiskTier.RED


class TestCapabilities:
    """检测到的能力标签应正确反映。"""

    def test_pandas_has_safe_compute(self) -> None:
        result = CodePolicyEngine().analyze("import pandas")
        assert "SAFE_COMPUTE" in result.capabilities

    def test_requests_has_network(self) -> None:
        result = CodePolicyEngine().analyze("import requests")
        assert "NETWORK" in result.capabilities

    def test_subprocess_has_subprocess(self) -> None:
        result = CodePolicyEngine().analyze("import subprocess")
        assert "SUBPROCESS" in result.capabilities


class TestExtraModules:
    """用户自定义白名单/黑名单。"""

    def test_extra_safe_module(self) -> None:
        engine = CodePolicyEngine(extra_safe_modules=("my_custom_lib",))
        result = engine.analyze("import my_custom_lib")
        assert result.tier == CodeRiskTier.GREEN

    def test_extra_blocked_module(self) -> None:
        engine = CodePolicyEngine(extra_blocked_modules=("pandas",))
        result = engine.analyze("import pandas")
        assert result.tier == CodeRiskTier.RED
