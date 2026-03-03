"""完整会话导出与恢复测试：EMX v2.0 格式。

覆盖：
- export_emx v2.0 字段序列化
- parse_emx v1/v2 兼容性
- workspace 文件收集 / 恢复
- SessionManager.export_full_session / import_full_session 集成
- API 端点 export / import
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from excelmanus.session_export import (
    EMX_FORMAT_ID,
    EMXImportError,
    collect_workspace_files,
    export_emx,
    parse_emx,
    restore_workspace_files,
)


# ── export_emx v2.0 ─────────────────────────────────────────


class TestExportEmxV2:
    """export_emx 函数 v2.0 扩展字段测试。"""

    def _base_meta(self) -> dict:
        return {"id": "s1", "title": "测试会话", "created_at": "2024-01-01T00:00:00Z", "updated_at": "2024-01-01T01:00:00Z"}

    def _base_messages(self) -> list[dict]:
        return [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    def test_basic_v2_fields(self):
        """v2 基础字段存在且格式正确。"""
        data = export_emx(
            self._base_meta(),
            self._base_messages(),
            session_state={"session_turn": 3, "affected_files": ["a.xlsx"]},
            task_list={"plan_file_path": None, "tasks": []},
            memories=[{"category": "general", "content": "记住这个", "source": "", "created_at": ""}],
            config_snapshot={"model": "gpt-4", "chat_mode": "write", "full_access_enabled": False},
        )
        assert data["format"] == EMX_FORMAT_ID
        assert data["version"] == "2.0.0"
        assert data["session"]["id"] == "s1"
        assert len(data["messages"]) == 2
        assert data["session_state"]["session_turn"] == 3
        assert data["task_list"]["tasks"] == []
        assert len(data["memories"]) == 1
        assert data["config_snapshot"]["model"] == "gpt-4"

    def test_v2_fields_optional(self):
        """不传 v2 字段时不出现在输出中。"""
        data = export_emx(self._base_meta(), self._base_messages())
        assert "session_state" not in data
        assert "task_list" not in data
        assert "memories" not in data
        assert "config_snapshot" not in data
        assert "workspace_files" not in data

    def test_workspace_files_field(self):
        """workspace_files 字段正确序列化。"""
        files = [{"path": "data.xlsx", "content_b64": base64.b64encode(b"fake").decode(), "size": 4}]
        data = export_emx(self._base_meta(), self._base_messages(), workspace_files=files)
        assert data["workspace_files"][0]["path"] == "data.xlsx"
        assert data["workspace_files"][0]["size"] == 4


# ── parse_emx v1/v2 兼容 ─────────────────────────────────────


class TestParseEmxCompat:
    """parse_emx 兼容 v1 和 v2 格式。"""

    def _v1_data(self) -> dict:
        return {
            "format": EMX_FORMAT_ID,
            "version": "1.0.0",
            "session": {"id": "s1", "title": "旧会话"},
            "messages": [{"role": "user", "content": "hi"}],
        }

    def _v2_data(self) -> dict:
        return {
            "format": EMX_FORMAT_ID,
            "version": "2.0.0",
            "session": {"id": "s2", "title": "新会话"},
            "messages": [{"role": "user", "content": "hi"}],
            "session_state": {"session_turn": 5},
            "task_list": {"plan_file_path": None, "tasks": []},
            "memories": [{"category": "general", "content": "test"}],
            "config_snapshot": {"model": "gpt-4", "chat_mode": "write", "full_access_enabled": True},
            "workspace_files": [{"path": "a.xlsx", "content_b64": "AAAA", "size": 3}],
        }

    def test_parse_v1_backward_compat(self):
        """v1 格式正常解析，v2 字段返回 None。"""
        parsed = parse_emx(self._v1_data())
        assert parsed["session_meta"]["id"] == "s1"
        assert len(parsed["messages"]) == 1
        assert parsed["session_state"] is None
        assert parsed["task_list"] is None
        assert parsed["memories"] is None
        assert parsed["config_snapshot"] is None
        assert parsed["workspace_files"] is None

    def test_parse_v2_full(self):
        """v2 格式完整解析。"""
        parsed = parse_emx(self._v2_data())
        assert parsed["session_meta"]["id"] == "s2"
        assert parsed["session_state"]["session_turn"] == 5
        assert len(parsed["task_list"]["tasks"]) == 0
        assert len(parsed["memories"]) == 1
        assert parsed["config_snapshot"]["full_access_enabled"] is True
        assert len(parsed["workspace_files"]) == 1

    def test_parse_rejects_bad_format(self):
        """不支持的 format 抛异常。"""
        with pytest.raises(EMXImportError, match="不支持的格式"):
            parse_emx({"format": "wrong", "version": "2.0.0", "session": {}, "messages": []})

    def test_parse_rejects_bad_version(self):
        """不支持的版本抛异常。"""
        with pytest.raises(EMXImportError, match="不支持的版本"):
            parse_emx({"format": EMX_FORMAT_ID, "version": "3.0.0", "session": {}, "messages": []})

    def test_parse_rejects_bad_session_state_type(self):
        """session_state 非 dict 时抛异常。"""
        data = self._v2_data()
        data["session_state"] = "bad"
        with pytest.raises(EMXImportError, match="session_state 必须是 dict"):
            parse_emx(data)

    def test_parse_rejects_bad_task_list_type(self):
        """task_list 非 dict 时抛异常。"""
        data = self._v2_data()
        data["task_list"] = [1, 2]
        with pytest.raises(EMXImportError, match="task_list 必须是 dict"):
            parse_emx(data)

    def test_parse_rejects_bad_memories_type(self):
        """memories 非 list 时抛异常。"""
        data = self._v2_data()
        data["memories"] = "bad"
        with pytest.raises(EMXImportError, match="memories 必须是 list"):
            parse_emx(data)

    def test_parse_rejects_bad_workspace_files_type(self):
        """workspace_files 非 list 时抛异常。"""
        data = self._v2_data()
        data["workspace_files"] = {}
        with pytest.raises(EMXImportError, match="workspace_files 必须是 list"):
            parse_emx(data)

    def test_parse_message_validation(self):
        """消息列表中缺 role 字段抛异常。"""
        data = self._v1_data()
        data["messages"].append({"content": "no role"})
        with pytest.raises(EMXImportError, match="缺少 role 字段"):
            parse_emx(data)


# ── workspace 文件收集 / 恢复 ─────────────────────────────────


class TestWorkspaceFiles:
    """collect_workspace_files 和 restore_workspace_files 测试。"""

    def test_collect_all_files(self, tmp_path: Path):
        """收集工作区所有文件。"""
        (tmp_path / "data.xlsx").write_bytes(b"excel_data")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "report.csv").write_bytes(b"csv_data")

        files = collect_workspace_files(str(tmp_path))
        paths = {f["path"] for f in files}
        assert "data.xlsx" in paths
        assert "subdir/report.csv" in paths
        for f in files:
            assert "content_b64" in f
            assert f["size"] > 0

    def test_collect_affected_only(self, tmp_path: Path):
        """仅收集 affected_only 指定的文件。"""
        (tmp_path / "a.xlsx").write_bytes(b"a")
        (tmp_path / "b.xlsx").write_bytes(b"b")
        (tmp_path / "c.xlsx").write_bytes(b"c")

        files = collect_workspace_files(str(tmp_path), affected_only=["a.xlsx", "c.xlsx"])
        paths = {f["path"] for f in files}
        assert paths == {"a.xlsx", "c.xlsx"}

    def test_collect_excludes_git(self, tmp_path: Path):
        """.git 目录被排除。"""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_bytes(b"data")
        (tmp_path / "real.txt").write_bytes(b"data")

        files = collect_workspace_files(str(tmp_path))
        paths = {f["path"] for f in files}
        assert "real.txt" in paths
        assert ".git/config" not in paths

    def test_collect_excludes_pycache(self, tmp_path: Path):
        """__pycache__ 目录被排除。"""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mod.cpython-311.pyc").write_bytes(b"data")
        (tmp_path / "real.py").write_bytes(b"data")

        files = collect_workspace_files(str(tmp_path))
        paths = {f["path"] for f in files}
        assert "real.py" in paths
        assert "__pycache__/mod.cpython-311.pyc" not in paths

    def test_collect_excludes_extensions(self, tmp_path: Path):
        """排除特定扩展名文件。"""
        (tmp_path / "mod.pyc").write_bytes(b"data")
        (tmp_path / "lib.dll").write_bytes(b"data")
        (tmp_path / "real.txt").write_bytes(b"data")

        files = collect_workspace_files(str(tmp_path))
        paths = {f["path"] for f in files}
        assert "real.txt" in paths
        assert "mod.pyc" not in paths
        assert "lib.dll" not in paths

    def test_collect_skips_empty_files(self, tmp_path: Path):
        """空文件被跳过。"""
        (tmp_path / "empty.txt").write_bytes(b"")
        (tmp_path / "nonempty.txt").write_bytes(b"data")

        files = collect_workspace_files(str(tmp_path))
        paths = {f["path"] for f in files}
        assert "nonempty.txt" in paths
        assert "empty.txt" not in paths

    def test_collect_nonexistent_dir(self):
        """不存在的工作区返回空。"""
        files = collect_workspace_files("/nonexistent/path/xyz123")
        assert files == []

    def test_restore_files(self, tmp_path: Path):
        """恢复文件到磁盘。"""
        content = b"restored_data"
        files = [
            {"path": "data.xlsx", "content_b64": base64.b64encode(content).decode(), "size": len(content)},
            {"path": "sub/report.csv", "content_b64": base64.b64encode(b"csv").decode(), "size": 3},
        ]
        restored, skipped = restore_workspace_files(str(tmp_path), files)
        assert restored == 2
        assert skipped == 0
        assert (tmp_path / "data.xlsx").read_bytes() == content
        assert (tmp_path / "sub" / "report.csv").read_bytes() == b"csv"

    def test_restore_path_traversal_blocked(self, tmp_path: Path):
        """路径穿越被阻止。"""
        files = [
            {"path": "../../../etc/passwd", "content_b64": base64.b64encode(b"evil").decode(), "size": 4},
        ]
        restored, skipped = restore_workspace_files(str(tmp_path), files)
        assert restored == 0
        assert skipped == 1

    def test_restore_skips_missing_fields(self, tmp_path: Path):
        """缺少必要字段的条目被跳过。"""
        files = [
            {"path": "", "content_b64": "AAAA", "size": 3},
            {"path": "ok.txt", "content_b64": "", "size": 0},
        ]
        restored, skipped = restore_workspace_files(str(tmp_path), files)
        assert restored == 0
        assert skipped == 2

    def test_roundtrip_collect_restore(self, tmp_path: Path):
        """收集 → 恢复 → 内容一致。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "data.xlsx").write_bytes(b"excel_content_here")

        files = collect_workspace_files(str(src))
        assert len(files) == 1

        dst = tmp_path / "dst"
        restored, skipped = restore_workspace_files(str(dst), files)
        assert restored == 1
        assert (dst / "data.xlsx").read_bytes() == b"excel_content_here"


# ── EMX v2.0 完整 roundtrip 测试 ─────────────────────────────


class TestEmxV2Roundtrip:
    """export_emx → parse_emx 往返一致性。"""

    def test_roundtrip_with_all_fields(self, tmp_path: Path):
        """所有字段完整往返。"""
        meta = {"id": "rt1", "title": "往返测试", "created_at": "2024-06-01T00:00:00Z", "updated_at": "2024-06-01T01:00:00Z"}
        messages = [
            {"role": "user", "content": "处理 Excel"},
            {"role": "assistant", "content": "好的", "tool_calls": []},
            {"role": "tool", "content": "done", "name": "write_excel", "tool_call_id": "tc1"},
        ]
        state = {"session_turn": 5, "last_iteration_count": 2, "affected_files": ["a.xlsx"]}
        tasks = {"plan_file_path": "/tmp/plan.md", "tasks": [{"title": "task1", "items": []}]}
        mems = [
            {"category": "file_pattern", "content": "用户喜欢 CSV", "source": "extract", "created_at": "2024-06-01T00:00:00Z"},
        ]
        config = {"model": "claude-3.5-sonnet", "chat_mode": "write", "full_access_enabled": True}

        (tmp_path / "a.xlsx").write_bytes(b"xlsx_content")
        ws_files = collect_workspace_files(str(tmp_path))

        exported = export_emx(
            meta, messages,
            excel_diffs=[{"tool_call_id": "tc1", "file_path": "a.xlsx", "sheet": "Sheet1", "affected_range": "A1:B2", "changes": []}],
            affected_files=["a.xlsx"],
            session_state=state,
            task_list=tasks,
            memories=mems,
            config_snapshot=config,
            workspace_files=ws_files,
        )

        # roundtrip via JSON serialization
        json_str = json.dumps(exported, ensure_ascii=False)
        reimported = json.loads(json_str)
        parsed = parse_emx(reimported)

        assert parsed["session_meta"]["id"] == "rt1"
        assert len(parsed["messages"]) == 3
        assert parsed["session_state"]["session_turn"] == 5
        assert parsed["task_list"]["plan_file_path"] == "/tmp/plan.md"
        assert parsed["memories"][0]["category"] == "file_pattern"
        assert parsed["config_snapshot"]["model"] == "claude-3.5-sonnet"
        assert len(parsed["workspace_files"]) == 1

    def test_roundtrip_v1_compat(self):
        """v1 格式 roundtrip — v2 字段全为 None。"""
        exported = {
            "format": EMX_FORMAT_ID,
            "version": "1.0.0",
            "session": {"id": "v1s", "title": "v1"},
            "messages": [{"role": "user", "content": "hi"}],
            "excel_diffs": [],
            "affected_files": [],
        }
        parsed = parse_emx(exported)
        assert parsed["session_state"] is None
        assert parsed["workspace_files"] is None


# ── SessionManager 集成测试 ─────────────────────────────────


class TestSessionManagerExportImport:
    """SessionManager.export_full_session / import_full_session 集成测试。"""

    def _make_manager(self, tmp_path: Path) -> Any:
        """创建一个带 SQLite 后端的最小 SessionManager。"""
        from excelmanus.database import Database
        from excelmanus.chat_history import ChatHistoryStore

        db_path = str(tmp_path / "test.db")
        database = Database(db_path)
        ch = ChatHistoryStore(database)

        config = MagicMock()
        config.workspace_root = str(tmp_path / "workspace")
        config.data_root = ""
        config.memory_enabled = False
        config.chat_history_enabled = True

        registry = MagicMock()
        skill_router = None

        from excelmanus.session import SessionManager

        mgr = SessionManager(
            10, 3600,
            config=config,
            registry=registry,
            skill_router=skill_router,
            chat_history=ch,
            database=database,
        )
        return mgr, database, ch

    @pytest.mark.asyncio
    async def test_import_basic_v1(self, tmp_path: Path):
        """导入 v1 EMX 格式（仅消息）。"""
        mgr, db, ch = self._make_manager(tmp_path)
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "1.0.0",
            "session": {"id": "orig", "title": "原始会话"},
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        })
        result = await mgr.import_full_session(parsed)
        assert result["message_count"] == 2
        assert result["title"] == "原始会话"
        assert result["state_restored"] is False
        assert result["files_restored"] == 0

        # 验证消息已写入
        loaded = ch.load_messages(result["session_id"])
        assert len(loaded) == 2

    @pytest.mark.asyncio
    async def test_import_v2_with_checkpoint(self, tmp_path: Path):
        """导入 v2 EMX 格式，恢复 checkpoint。"""
        mgr, db, ch = self._make_manager(tmp_path)
        state = {"session_turn": 7, "last_iteration_count": 3, "affected_files": ["x.xlsx"]}
        tasks = {"plan_file_path": None, "tasks": []}
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "2.0.0",
            "session": {"id": "v2s", "title": "v2会话"},
            "messages": [{"role": "user", "content": "work"}],
            "session_state": state,
            "task_list": tasks,
        })
        result = await mgr.import_full_session(parsed)
        assert result["state_restored"] is True
        assert result["message_count"] == 1

        # 验证 checkpoint 已写入
        from excelmanus.stores.session_state_store import SessionStateStore
        store = SessionStateStore(db)
        cp = store.load_latest_checkpoint(result["session_id"])
        assert cp is not None
        assert cp["state_dict"]["session_turn"] == 7

    @pytest.mark.asyncio
    async def test_import_v2_with_memories(self, tmp_path: Path):
        """导入 v2 EMX 格式，恢复记忆。"""
        mgr, db, ch = self._make_manager(tmp_path)
        mems = [
            {"category": "general", "content": "用户偏好A", "source": "extract", "created_at": "2024-06-01T00:00:00+00:00"},
            {"category": "file_pattern", "content": "CSV格式", "source": "", "created_at": ""},
        ]
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "2.0.0",
            "session": {"id": "v2m", "title": "记忆会话"},
            "messages": [{"role": "user", "content": "hi"}],
            "memories": mems,
        })
        result = await mgr.import_full_session(parsed)
        assert result["memories_restored"] == 2

        # 验证记忆已写入
        from excelmanus.stores.memory_store import MemoryStore
        mem_store = MemoryStore(db)
        entries = mem_store.load_all()
        contents = {e.content for e in entries}
        assert "用户偏好A" in contents
        assert "CSV格式" in contents

    @pytest.mark.asyncio
    async def test_import_v2_with_workspace_files(self, tmp_path: Path):
        """导入 v2 EMX 格式，恢复工作区文件。"""
        mgr, db, ch = self._make_manager(tmp_path)
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)

        file_content = b"excel_data_here"
        files = [
            {"path": "report.xlsx", "content_b64": base64.b64encode(file_content).decode(), "size": len(file_content)},
        ]
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "2.0.0",
            "session": {"id": "v2f", "title": "文件会话"},
            "messages": [{"role": "user", "content": "hi"}],
            "workspace_files": files,
        })
        result = await mgr.import_full_session(parsed)
        assert result["files_restored"] == 1
        assert (ws_dir / "report.xlsx").read_bytes() == file_content

    @pytest.mark.asyncio
    async def test_import_v2_with_excel_previews(self, tmp_path: Path):
        """导入 v2 EMX 格式，恢复 excel_previews。"""
        mgr, db, ch = self._make_manager(tmp_path)
        previews = [
            {
                "tool_call_id": "tc_preview_1",
                "file_path": "sales.xlsx",
                "sheet": "Sheet1",
                "columns": ["A", "B", "C"],
                "rows": [["1", "Alice", "100"], ["2", "Bob", "200"]],
                "total_rows": 2,
                "truncated": False,
            },
        ]
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "2.0.0",
            "session": {"id": "v2p", "title": "预览会话"},
            "messages": [{"role": "user", "content": "show data"}],
            "excel_previews": previews,
        })
        result = await mgr.import_full_session(parsed)
        assert result["message_count"] == 1

        # 验证 preview 已写入
        loaded_previews = ch.load_excel_previews(result["session_id"])
        assert len(loaded_previews) == 1
        assert loaded_previews[0]["tool_call_id"] == "tc_preview_1"
        assert loaded_previews[0]["file_path"] == "sales.xlsx"
        assert loaded_previews[0]["columns"] == ["A", "B", "C"]
        assert len(loaded_previews[0]["rows"]) == 2

    @pytest.mark.asyncio
    async def test_import_no_chat_history_raises(self, tmp_path: Path):
        """聊天记录存储未启用时抛异常。"""
        from excelmanus.session import SessionManager

        config = MagicMock()
        config.workspace_root = str(tmp_path)
        mgr = SessionManager(
            10, 3600,
            config=config,
            registry=MagicMock(),
            skill_router=None,
            chat_history=None,
            database=None,
        )
        parsed = parse_emx({
            "format": EMX_FORMAT_ID,
            "version": "1.0.0",
            "session": {"id": "x", "title": "x"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        with pytest.raises(RuntimeError, match="聊天记录存储未启用"):
            await mgr.import_full_session(parsed)

    @pytest.mark.asyncio
    async def test_full_roundtrip_export_import(self, tmp_path: Path):
        """完整 roundtrip：创建会话 → 导出 → 导入 → 验证。"""
        mgr, db, ch = self._make_manager(tmp_path)
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)

        # 手动创建一个会话（模拟正常使用后的状态）
        original_sid = "orig-session-123"
        ch.create_session(original_sid, "测试往返")
        ch.save_turn_messages(original_sid, [
            {"role": "user", "content": "分析 data.xlsx"},
            {"role": "assistant", "content": "好的，我来分析"},
        ], turn_number=1)

        # 写入 checkpoint
        from excelmanus.stores.session_state_store import SessionStateStore
        ss = SessionStateStore(db)
        ss.save_checkpoint(
            session_id=original_sid,
            state_dict={"session_turn": 2, "affected_files": ["data.xlsx"]},
            task_list_dict={"plan_file_path": None, "tasks": []},
            turn_number=2,
        )

        # 写入工作区文件
        (ws_dir / "data.xlsx").write_bytes(b"original_excel_content")

        # 导出
        exported = await mgr.export_full_session(
            original_sid, include_workspace=True,
        )
        assert exported["format"] == EMX_FORMAT_ID
        assert exported["version"] == "2.0.0"
        assert len(exported["messages"]) == 2
        assert exported["session_state"]["session_turn"] == 2

        # 清空工作区
        (ws_dir / "data.xlsx").unlink()

        # 导入
        parsed = parse_emx(exported)
        result = await mgr.import_full_session(parsed)
        assert result["message_count"] == 2
        assert result["state_restored"] is True

        # 验证新会话的消息
        new_msgs = ch.load_messages(result["session_id"])
        assert len(new_msgs) == 2

        # 验证 checkpoint
        cp = ss.load_latest_checkpoint(result["session_id"])
        assert cp is not None
        assert cp["state_dict"]["session_turn"] == 2

        # 验证工作区文件恢复
        assert (ws_dir / "data.xlsx").read_bytes() == b"original_excel_content"
