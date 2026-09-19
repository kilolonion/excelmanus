"""启动临界路径回归：导入不应拉起重 SDK，lifespan 不应等待 MCP。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_api_import_does_not_load_openai_or_mcp_sdk() -> None:
    """冷导入 excelmanus.api 不得加载 openai / MCP SDK。"""
    import os

    script = (
        "import sys\n"
        "import excelmanus.api  # noqa: F401\n"
        "loaded = set(sys.modules)\n"
        "heavy = [name for name in ('openai', 'mcp') if name in loaded]\n"
        "if heavy:\n"
        "    raise SystemExit('unexpected heavy imports: ' + ','.join(heavy))\n"
    )
    # 子进程必须能导入 fastapi/uvicorn 才能走到被测目标；依赖可能只通过
    # 父进程 site.addsitedir 可见（无 .venv 的裸解释器环境），补进 PYTHONPATH。
    env = os.environ.copy()
    try:
        import fastapi

        extra = str(Path(fastapi.__file__).resolve().parent.parent)
        env["PYTHONPATH"] = extra + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else extra
    except ImportError:
        pass
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_lifespan_yields_without_waiting_for_mcp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """lifespan 必须在 MCP initialize 完成前 yield，避免 health 被远程连接拖死。"""
    from excelmanus.config import ExcelManusConfig
    import excelmanus.api as api_module

    started = asyncio.Event()
    released = asyncio.Event()

    async def _slow_initialize(self, registry) -> None:  # noqa: ANN001
        started.set()
        await released.wait()

    monkeypatch.setattr(
        "excelmanus.mcp.manager.MCPManager.initialize",
        _slow_initialize,
    )

    config = ExcelManusConfig(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        workspace_root=str(tmp_path),
        cors_allow_origins=("http://a.example",),
    )
    local_app = api_module.create_app(config=config)

    async def _run() -> None:
        async with local_app.router.lifespan_context(local_app):
            await asyncio.wait_for(started.wait(), timeout=2)
            assert not released.is_set()

    try:
        await asyncio.wait_for(_run(), timeout=8)
    finally:
        released.set()
        api_module.set_draining(False)


def test_git_commit_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from excelmanus.api_routes_version import (
        _get_git_commit,
        reset_git_commit_cache,
    )

    calls = {"n": 0}

    def _fake_run(*_args, **_kwargs):
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout="abc1234\n")

    reset_git_commit_cache()
    monkeypatch.setattr("excelmanus.api_routes_version.subprocess.run", _fake_run)
    assert _get_git_commit(tmp_path) == "abc1234"
    assert _get_git_commit(tmp_path) == "abc1234"
    assert calls["n"] == 1
    reset_git_commit_cache()
