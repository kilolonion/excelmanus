"""写入契约交叉测试：快照同源、回滚前置版本、沙盒锁/连续保存、路径唯一键。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook, load_workbook

from excelmanus.security.sandbox_hook import generate_wrapper_script
from excelmanus.workbook_commit import (
    content_version_of_file,
    lock_path_for,
    remember_content_version,
    seed_seen_versions,
)


def _sha(path: Path) -> str:
    return content_version_of_file(path) or ""


def _patches(*cells: dict) -> list[dict]:
    by_sheet: dict[str, list[dict]] = {}
    for cell in cells:
        by_sheet.setdefault(str(cell.get("sheet") or "Sheet"), []).append({k: v for k, v in cell.items() if k != "sheet"})
    return [{"kind": "cells.patch", "sheet": sheet, "cells": values} for sheet, values in by_sheet.items()]


def _run_sandbox(
    workspace: Path,
    script: str,
    *,
    env_override: dict[str, str] | None = None,
    timeout: float = 8,
) -> subprocess.CompletedProcess:
    path = workspace / "script.py"
    path.write_text(script, encoding="utf-8")
    wrapper = generate_wrapper_script("GREEN", str(workspace))
    wrapper_path = workspace / "_wrapper.py"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, str(wrapper_path), str(path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


@pytest.mark.asyncio
async def test_snapshot_bound_bytes_not_later_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """解析用的 bytes 被替换后，快照仍返回旧数据 + 旧版本；用该版本写入应 409。"""
    from excelmanus import api as api_module
    from excelmanus import api_app_state
    import excelmanus.api_routes_files as files_mod

    seed = Workbook()
    seed.active["A1"] = "seen"
    seed.save(tmp_path / "book.xlsx")
    seed.close()
    seen_ver = _sha(tmp_path / "book.xlsx")

    real_load = load_workbook

    def _load(src, **kwargs):
        wb = real_load(src, **kwargs)
        outsider = Workbook()
        outsider.active["A1"] = "external"
        outsider.save(tmp_path / "book.xlsx")
        outsider.close()
        return wb

    monkeypatch.setattr("openpyxl.load_workbook", _load)

    cfg = type("C", (), {"workspace_root": str(tmp_path)})()
    api_app_state.set_config(cfg)
    api_app_state.set_session_manager(None)
    monkeypatch.setattr(files_mod, "_resolve_workspace_root", lambda _req, session_id=None, **kwargs: str(tmp_path))
    monkeypatch.setattr(files_mod, "_resolve_excel_path", lambda *a, **k: str(tmp_path / "book.xlsx"))

    req = MagicMock()
    req.query_params = {"path":"book.xlsx", "range":"A1:Z20", "facets":"data,geometry"}
    req.headers = {}
    req.app.state.auth_enabled = False
    from excelmanus.api_routes_files import get_workbook_observation
    snap = await get_workbook_observation(req)
    assert snap.status_code == 200
    body = json.loads(snap.body)
    assert body["content_version"] == seen_ver
    assert "external" not in json.dumps(body, ensure_ascii=False)

    write_req = api_module.WorkbookChangesRequest(
        path="book.xlsx",
        operations=_patches({"cell": "A1", "value": "from-ui"}),
        expected_version=body["content_version"],
    )
    raw = MagicMock()
    raw.app.state.auth_enabled = False
    monkeypatch.setattr(files_mod, "_resolve_excel_path", lambda *a, **k: str(tmp_path / "book.xlsx"))
    resp = await api_module.apply_workbook_changes(write_req, raw)
    assert resp.status_code == 409
    wb = load_workbook(tmp_path / "book.xlsx")
    assert wb.active["A1"].value == "external"
    wb.close()


def test_sandbox_double_save_does_not_replace_user_xlsx(tmp_path: Path) -> None:
    """工作区 xlsx 直写被 wrapper 硬拒绝，用户文件保持原值。"""
    xlsx = tmp_path / "book.xlsx"
    wb = Workbook()
    wb.active["A1"] = "v0"
    wb.save(xlsx)
    wb.close()
    seen = _sha(xlsx)
    code = (
        "from openpyxl import load_workbook\n"
        f"wb = load_workbook(r'{xlsx}')\n"
        "wb.active['A1'] = 'v1'\n"
        f"wb.save(r'{xlsx}')\n"
        "wb.active['A1'] = 'v2'\n"
        f"wb.save(r'{xlsx}')\n"
        "print('ok')\n"
    )
    result = _run_sandbox(
        tmp_path,
        code,
        env_override={"EXCELMANUS_EXPECTED_VERSIONS": json.dumps({"book.xlsx": seen})},
    )
    assert result.returncode != 0
    assert "PermissionError" in (result.stderr or "")
    assert "ok" not in result.stdout
    wb = load_workbook(xlsx)
    assert wb.active["A1"].value == "v0"
    wb.close()


def test_sandbox_same_basename_pending_does_not_touch_other_dir(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    for folder, value in ((left, "L"), (right, "R")):
        wb = Workbook()
        wb.active["A1"] = value
        wb.save(folder / "book.xlsx")
        wb.close()
    mapping = {
        "left/book.xlsx": _sha(left / "book.xlsx"),
        "right/book.xlsx": _sha(right / "book.xlsx"),
    }
    code = (
        "from openpyxl import load_workbook\n"
        f"wb = load_workbook(r'{left / 'book.xlsx'}')\n"
        "wb.active['A1'] = 'L2'\n"
        f"wb.save(r'{left / 'book.xlsx'}')\n"
        "print('ok')\n"
    )
    result = _run_sandbox(
        tmp_path,
        code,
        env_override={"EXCELMANUS_EXPECTED_VERSIONS": json.dumps(mapping)},
    )
    assert result.returncode != 0
    assert "PermissionError" in (result.stderr or "")
    wb = load_workbook(left / "book.xlsx")
    assert wb.active["A1"].value == "L"
    wb.close()
    wb = load_workbook(right / "book.xlsx")
    assert wb.active["A1"].value == "R"
    wb.close()


def test_sandbox_waits_for_host_em_lock(tmp_path: Path) -> None:
    """工作区 xlsx 直写在锁前即被拒：持锁状态下仍立即 PermissionError。"""
    xlsx = tmp_path / "book.xlsx"
    wb = Workbook()
    wb.active["A1"] = "v0"
    wb.save(xlsx)
    wb.close()
    lock_path = lock_path_for(xlsx)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    released = threading.Event()

    def _hold() -> None:
        released.wait(4)

    holder = threading.Thread(target=_hold)
    holder.start()
    code = (
        "from openpyxl import load_workbook\n"
        f"wb = load_workbook(r'{xlsx}')\n"
        "wb.active['A1'] = 'sandboxed'\n"
        f"wb.save(r'{xlsx}')\n"
        "print('saved')\n"
    )
    try:
        result = _run_sandbox(tmp_path, code, timeout=8)
        assert result.returncode != 0
        assert "PermissionError" in (result.stderr or "")
    finally:
        released.set()
        if os.name != "nt":
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
        holder.join(timeout=2)
    assert load_workbook(xlsx).active["A1"].value == "v0"


def test_paged_read_expected_version_detects_mid_pagination_drift(tmp_path: Path) -> None:
    """S08：窗口/分页读取带 expected_version 时，中途改盘返回 STALE_SNAPSHOT，
    错误内含当前版本；不带版本则逐页重开快照（版本随页变，调用方需自查）。"""
    from excelmanus.security import FileAccessGuard
    from excelmanus.tools._guard_ctx import set_guard
    from excelmanus.tools.context import bind_workspace
    from excelmanus.tools.workbook_tools import init_guard, observe_spreadsheet

    workspace = str(tmp_path)
    set_guard(FileAccessGuard(workspace))
    init_guard(workspace)
    bind_workspace(tmp_path)
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["项目", "金额"])
        for i in range(9):
            ws.append([f"P{i}", i])
        wb.save(tmp_path / "book.xlsx")
        wb.close()

        page1 = observe_spreadsheet(
            file_path="book.xlsx", mode="range", range="A1:B5",
        )
        assert page1.success, page1.model_text
        v1 = (page1.value or {}).get("content_version")
        assert v1 and str(v1).startswith("sha256:")

        outsider = load_workbook(tmp_path / "book.xlsx")
        outsider.active["B5"] = 9999
        outsider.save(tmp_path / "book.xlsx")
        outsider.close()

        page2 = observe_spreadsheet(
            file_path="book.xlsx", mode="range", range="A6:B10",
            expected_version=v1,
        )
        assert page2.success is False
        assert page2.error is not None and page2.error.code == "STALE_SNAPSHOT"
        fields = getattr(page2.error, "fields", None) or {}
        assert fields.get("content_version") and fields["content_version"] != v1

        # 重新核对后用新版本继续读 → 成功且数据是新页
        page2b = observe_spreadsheet(
            file_path="book.xlsx", mode="range", range="A6:B10",
            expected_version=fields["content_version"],
        )
        assert page2b.success, page2b.model_text
    finally:
        seed_seen_versions({})


def test_remember_does_not_alias_basename() -> None:
    from excelmanus.workbook_commit import peek_seen_content_version

    seed_seen_versions({})
    remember_content_version("left/book.xlsx", "sha256:left")
    remember_content_version("right/book.xlsx", "sha256:right")
    assert peek_seen_content_version("left/book.xlsx") == "sha256:left"
    assert peek_seen_content_version("right/book.xlsx") == "sha256:right"
    assert peek_seen_content_version("book.xlsx") is None
    seed_seen_versions({})


def test_remember_tool_versions_ignores_basename_file_field() -> None:
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
    from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta
    from excelmanus.workbook_commit import peek_seen_content_version

    recorded: dict[str, str] = {}

    class _State:
        file_content_versions = recorded

        def remember_file_version(self, path: str, version: str) -> None:
            recorded[path] = version

    class _Engine:
        state = _State()

    seed_seen_versions({})
    dispatcher = ToolDispatcher(_Engine())
    dispatcher._remember_tool_versions(
        ToolResult(
            success=True,
            model_text="ok",
            value={
                "file": "book.xlsx",
                "file_path": "left/book.xlsx",
                "content_version": "sha256:x",
            },
            ui_meta=ToolUiMeta(files=["left/book.xlsx"], content_version="sha256:x"),
        )
    )
    assert peek_seen_content_version("left/book.xlsx") == "sha256:x"
    assert peek_seen_content_version("book.xlsx") is None
    assert recorded.get("left/book.xlsx") == "sha256:x"
    assert "book.xlsx" not in recorded
    seed_seen_versions({})


@pytest.mark.asyncio
async def test_execute_subcall_uses_parent_event_and_unique_ids() -> None:
    from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
    from excelmanus.engine_core.tool_result import ToolResult

    events: list[str] = []

    def on_event(event) -> None:
        events.append(getattr(event, "tool_call_id", None) or getattr(event, "call_id", "") or "")

    class _Engine:
        sandbox_env = None
        file_access_guard = None
        state = type("S", (), {"file_content_versions": {}, "remember_file_version": lambda *a, **k: None})()
        config = type("C", (), {"workspace_root": "."})()
        registry = None

        def emit(self, cb, event) -> None:
            if cb:
                cb(event)

    dispatcher = ToolDispatcher(_Engine())

    async def _fake_execute(tc, tool_scope, on_event, iteration, *args, **kwargs):
        if on_event:
            on_event(type("E", (), {"tool_call_id": tc.id, "parent_call_id": getattr(tc, "parent_call_id", None)})())
        return ToolResult(success=True, model_text="ok", value={"id": tc.id})

    dispatcher.execute = _fake_execute  # type: ignore[method-assign]
    dispatcher._current_on_event = on_event

    first = await dispatcher.execute_subcall(
        tool_name="read_excel",
        arguments={"file_path": "a.xlsx"},
        root_call_id="run_1",
        on_event=on_event,
    )
    second = await dispatcher.execute_subcall(
        tool_name="read_excel",
        arguments={"file_path": "a.xlsx"},
        root_call_id="run_1",
        on_event=on_event,
    )
    assert first.value["id"] != second.value["id"]
    assert first.value["id"].startswith("run_1:read_excel:")
    assert second.value["id"].startswith("run_1:read_excel:")
    assert events[0] != events[1]


def test_sandbox_stat_family_sees_pending_writes(tmp_path: Path) -> None:
    """R24 复现：写完 getsize/exists/is_file 必须看到 pending 副本而非 FileNotFoundError。"""
    code = (
        "import os, pathlib\n"
        "out = 'outputs/probe.bin'\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "with open(out, 'wb') as f:\n"
        "    f.write(b'x' * 123)\n"
        "assert os.path.getsize(out) == 123, 'getsize 看不到 pending'\n"
        "assert os.path.exists(out)\n"
        "assert os.path.isfile(out)\n"
        "p = pathlib.Path(out)\n"
        "assert p.exists() and p.is_file()\n"
        "assert p.stat().st_size == 123\n"
        "assert os.path.getmtime(out) > 0\n"
        "print('pending-read-ok')\n"
    )
    result = _run_sandbox(tmp_path, code, timeout=8)
    assert result.returncode == 0, result.stderr
    assert "pending-read-ok" in result.stdout


def test_sandbox_stat_family_unaffected_for_real_files(tmp_path: Path) -> None:
    """未写入的真实文件走真实 stat；工作区外路径不受 pending 投影影响。"""
    # 沙盒子进程继承 conftest 的 cwd=<tmp>/cwd，相对路径按其解析。
    (tmp_path / "cwd" / "real.txt").write_text("real", encoding="utf-8")
    code = (
        "import os, tempfile\n"
        "assert os.path.getsize('real.txt') == 4\n"
        "assert not os.path.exists('nope.bin')\n"
        "tmpf = os.path.join(tempfile.gettempdir(), 'em_probe_outside.txt')\n"
        "with open(tmpf, 'w') as f:\n"
        "    f.write('t')\n"
        "assert os.path.exists(tmpf)\n"
        "os.remove(tmpf)\n"
        "print('real-stat-ok')\n"
    )
    result = _run_sandbox(tmp_path, code, timeout=8)
    assert result.returncode == 0, result.stderr
    assert "real-stat-ok" in result.stdout


def test_sandbox_listdir_scandir_see_pending_writes(tmp_path: Path) -> None:
    """列目录应把 pending 写入投影回 logical 名；虚拟中间目录也可列。"""
    code = (
        "import os\n"
        "os.makedirs('outputs', exist_ok=True)\n"
        "with open('outputs/a.txt', 'w') as f:\n"
        "    f.write('a')\n"
        "with open('outputs/nested/b.txt', 'w') as f:\n"
        "    f.write('b')\n"
        "with open('virtdir/c.txt', 'w') as f:\n"
        "    f.write('c')\n"
        "names = os.listdir('outputs')\n"
        "assert 'a.txt' in names, names\n"
        "assert 'nested' in names, names\n"
        "assert not any(n.endswith('.em-lock') for n in names)\n"
        "entries = {e.name: e for e in os.scandir('outputs')}\n"
        "assert entries['a.txt'].is_file()\n"
        "assert entries['nested'].is_dir()\n"
        "assert entries['a.txt'].stat().st_size == 1\n"
        "assert os.listdir('virtdir') == ['c.txt']\n"
        "assert os.path.isdir('virtdir') and os.path.exists('virtdir')\n"
        "assert os.listdir('outputs/nested') == ['b.txt']\n"
        "print('listdir-ok')\n"
    )
    result = _run_sandbox(tmp_path, code, timeout=8)
    assert result.returncode == 0, result.stderr
    assert "listdir-ok" in result.stdout


def test_sandbox_listdir_hides_em_lock_artifacts(tmp_path: Path) -> None:
    """R24 复现：host 侧写路径产生 <file>.em-lock 姊妹锁文件（真实文件，
    不经 pending），listdir/scandir 对模型隐藏，但 exists 单路径仍可探。"""
    outdir = tmp_path / "cwd" / "outputs"
    outdir.mkdir(parents=True)
    (outdir / "a.xlsx").write_bytes(b"pk")
    (outdir / "a.xlsx.em-lock").write_bytes(b"")
    code = (
        "import os\n"
        "assert os.path.exists('outputs/a.xlsx.em-lock')\n"
        "names = os.listdir('outputs')\n"
        "assert 'a.xlsx' in names, names\n"
        "assert not any(n.endswith('.em-lock') for n in names), names\n"
        "with os.scandir('outputs') as it:\n"
        "    snames = [e.name for e in it]\n"
        "assert 'a.xlsx' in snames\n"
        "assert not any(n.endswith('.em-lock') for n in snames), snames\n"
        "print('emlock-hidden-ok')\n"
    )
    result = _run_sandbox(tmp_path, code, timeout=8)
    assert result.returncode == 0, result.stderr
    assert "emlock-hidden-ok" in result.stdout


def test_sandbox_listdir_pending_isolated_across_runs(tmp_path: Path) -> None:
    """其它 run 的 pending 不并入列目录；访问被拒。"""
    foreign = tmp_path / ".excelmanus" / "pending" / "other_run_9"
    foreign.mkdir(parents=True)
    (foreign / "deadbeef_a.txt").write_text("x", encoding="utf-8")
    tree_root = str(tmp_path / ".excelmanus" / "pending").replace("\\", "/")
    foreign_abs = str(foreign).replace("\\", "/")
    code = (
        "import os\n"
        f"names = os.listdir({tree_root!r})\n"
        "assert 'other_run_9' not in names, names\n"
        "try:\n"
        f"    os.listdir({foreign_abs!r})\n"
        "except PermissionError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('foreign pending 未隔离')\n"
        "try:\n"
        f"    os.stat({(foreign_abs + '/deadbeef_a.txt')!r})\n"
        "except PermissionError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('foreign pending stat 未隔离')\n"
        "print('isolation-ok')\n"
    )
    result = _run_sandbox(tmp_path, code, timeout=8)
    assert result.returncode == 0, result.stderr
    assert "isolation-ok" in result.stdout
