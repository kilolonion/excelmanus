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

    cfg = type("C", (), {"workspace_root": str(tmp_path), "backup_enabled": False})()
    monkeypatch.setattr(api_app_state, "_config", cfg)
    monkeypatch.setattr(api_app_state, "_session_manager", None)
    monkeypatch.setattr(files_mod, "_resolve_workspace_root", lambda _req: str(tmp_path))
    monkeypatch.setattr(files_mod, "_resolve_excel_path", lambda *a, **k: str(tmp_path / "book.xlsx"))

    req = MagicMock()
    req.query_params = {"path": "book.xlsx", "all_sheets": "1", "max_rows": "20", "with_styles": "0"}
    req.app.state.auth_enabled = False
    snap = await api_module.get_excel_snapshot(req)
    assert snap.status_code == 200
    body = json.loads(snap.body)
    assert body["content_version"] == seen_ver
    assert "external" not in json.dumps(body, ensure_ascii=False)

    write_req = api_module.ExcelWriteRequest(
        path="book.xlsx",
        changes=[{"cell": "A1", "value": "from-ui"}],
        expected_version=body["content_version"],
    )
    raw = MagicMock()
    raw.app.state.auth_enabled = False
    monkeypatch.setattr(files_mod, "_resolve_excel_path", lambda *a, **k: str(tmp_path / "book.xlsx"))
    resp = await api_module.write_excel_cells(write_req, raw)
    assert resp.status_code == 409
    wb = load_workbook(tmp_path / "book.xlsx")
    assert wb.active["A1"].value == "external"
    wb.close()


def test_sandbox_double_save_updates_expected(tmp_path: Path) -> None:
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
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
    wb = load_workbook(xlsx)
    assert wb.active["A1"].value == "v2"
    wb.close()


def test_sandbox_same_basename_does_not_use_other_dir(tmp_path: Path) -> None:
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
    assert result.returncode == 0, result.stderr
    wb = load_workbook(left / "book.xlsx")
    assert wb.active["A1"].value == "L2"
    wb.close()
    wb = load_workbook(right / "book.xlsx")
    assert wb.active["A1"].value == "R"
    wb.close()


def test_sandbox_waits_for_host_em_lock(tmp_path: Path) -> None:
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
        with pytest.raises(subprocess.TimeoutExpired):
            _run_sandbox(tmp_path, code, timeout=1.2)
    finally:
        released.set()
        if os.name != "nt":
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
        holder.join(timeout=2)
    assert load_workbook(xlsx).active["A1"].value == "v0"


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
