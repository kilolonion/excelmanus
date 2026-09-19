"""Upgrade subprocess decoding: UTF-8 git/npm output must not hang on Windows."""

from __future__ import annotations

import sys

from excelmanus.updater import _run_cmd


def test_run_cmd_decodes_utf8_output_and_stderr() -> None:
    script = (
        "import sys; "
        "sys.stdout.buffer.write(bytes([228,184,173,230,150,135])); "
        "sys.stderr.buffer.write(bytes([233,148,153,232,175,175]))"
    )
    rc, out, err = _run_cmd([sys.executable, "-c", script], timeout=15)
    assert rc == 0
    assert out == "中文"
    assert err == "错误"
