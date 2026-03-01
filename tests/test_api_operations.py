"""操作历史时间线 API 端点测试。

覆盖：
- GET  /api/v1/sessions/{session_id}/operations
- GET  /api/v1/sessions/{session_id}/operations/{approval_id}
- POST /api/v1/sessions/{session_id}/operations/{approval_id}/undo
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import importlib.util
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from excelmanus.approval import AppliedApprovalRecord, FileChangeRecord
from excelmanus.config import ExcelManusConfig

# excelmanus/api.py 被 excelmanus/api/ 包遮蔽，直接从文件加载
_api_py = Path(__file__).resolve().parent.parent / "excelmanus" / "api.py"
_spec = importlib.util.spec_from_file_location("excelmanus._api_module", str(_api_py))
assert _spec and _spec.loader
api_module = importlib.util.module_from_spec(_spec)
sys.modules["excelmanus._api_module"] = api_module
_spec.loader.exec_module(api_module)
app = api_module.app


# ── 辅助 ─────────────────────────────────────────────────


def _test_config(tmp_path: Path, **overrides) -> ExcelManusConfig:
    defaults = dict(
        api_key="test-key",
        base_url="https://test.example.com/v1",
        model="test-model",
        session_ttl_seconds=60,
        max_sessions=5,
        workspace_root=str(tmp_path),
    )
    defaults.update(overrides)
    return ExcelManusConfig(**defaults)


def _make_record(
    approval_id: str = "apv_test_001",
    tool_name: str = "write_cells",
    session_id: str = "sess-1",
    undoable: bool = True,
    execution_status: str = "success",
    session_turn: int | None = 1,
) -> AppliedApprovalRecord:
    return AppliedApprovalRecord(
        approval_id=approval_id,
        tool_name=tool_name,
        arguments={"fileAbsolutePath": "/tmp/test.xlsx", "sheetName": "Sheet1"},
        tool_scope=["write_cells"],
        created_at_utc="2026-03-01T10:00:00+00:00",
        applied_at_utc="2026-03-01T10:00:01+00:00",
        undoable=undoable,
        manifest_file="outputs/audits/apv_test_001/manifest.json",
        audit_dir="outputs/audits/apv_test_001",
        result_preview="写入成功",
        execution_status=execution_status,
        changes=[
            FileChangeRecord(
                path="test.xlsx",
                before_exists=True,
                after_exists=True,
                before_hash="aaa",
                after_hash="bbb",
                before_size=1000,
                after_size=1200,
                is_binary=True,
            ),
        ],
        binary_snapshots={},
        session_turn=session_turn,
        session_id=session_id,
    )


def _make_engine_mock(
    records: list[AppliedApprovalRecord] | None = None,
    config_workspace: str = "/tmp",
) -> MagicMock:
    """构造一个带 _approval 的 mock engine。"""
    engine = MagicMock()
    approval = MagicMock()

    _records = records or []

    def _list_applied(*, limit=50, session_id=None, undoable_only=False):
        result = _records
        if session_id:
            result = [r for r in result if r.session_id == session_id]
        if undoable_only:
            result = [r for r in result if r.undoable]
        return result[:limit]

    def _get_applied(aid):
        for r in _records:
            if r.approval_id == aid:
                return r
        return None

    approval.list_applied = MagicMock(side_effect=_list_applied)
    approval.get_applied = MagicMock(side_effect=_get_applied)
    approval.undo = MagicMock(return_value="已回滚 `apv_test_001`：恢复 1 个文件。")
    engine._approval = approval
    engine._config = MagicMock()
    engine._config.workspace_root = config_workspace
    return engine


@pytest.fixture
def tmp_workspace(tmp_path: Path):
    return tmp_path


@pytest.fixture
def api_state(tmp_workspace):
    """注入 API 全局状态。"""
    config = _test_config(tmp_workspace)
    manager = MagicMock()

    old_config = api_module._config
    old_manager = api_module._session_manager

    api_module._config = config
    api_module._session_manager = manager

    yield {"config": config, "manager": manager, "workspace": tmp_workspace}

    api_module._config = old_config
    api_module._session_manager = old_manager


@pytest.fixture
def client(api_state):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


# ── GET /api/v1/sessions/{session_id}/operations ────────


class TestListOperations:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_engine(self, client, api_state):
        """无活跃引擎时返回空列表。"""
        api_state["manager"].get_engine.return_value = None
        # 需要 _has_session_access 通过
        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.get("/api/v1/sessions/sess-1/operations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["operations"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    @pytest.mark.asyncio
    async def test_returns_operations_list(self, client, api_state):
        """有记录时返回操作列表。"""
        records = [_make_record(), _make_record(approval_id="apv_test_002", session_turn=2)]
        engine = _make_engine_mock(records)
        api_state["manager"].get_engine.return_value = engine

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.get("/api/v1/sessions/sess-1/operations")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["operations"]) == 2
        op = data["operations"][0]
        assert op["approval_id"] == "apv_test_001"
        assert op["tool_name"] == "write_cells"
        assert op["execution_status"] == "success"
        assert op["undoable"] is True
        assert len(op["changes"]) == 1
        assert op["changes"][0]["change_type"] == "modified"
        assert op["changes"][0]["before_size"] == 1000
        assert op["changes"][0]["after_size"] == 1200

    @pytest.mark.asyncio
    async def test_session_not_found(self, client, api_state):
        """会话不存在返回 404。"""
        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=False)):
            resp = await client.get("/api/v1/sessions/nonexistent/operations")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_pagination(self, client, api_state):
        """分页参数正常工作。"""
        records = [_make_record(approval_id=f"apv_{i}", session_turn=i) for i in range(5)]
        engine = _make_engine_mock(records)
        api_state["manager"].get_engine.return_value = engine

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.get("/api/v1/sessions/sess-1/operations?limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["operations"]) == 2
        assert data["has_more"] is True


# ── GET /api/v1/sessions/{sid}/operations/{approval_id} ──


class TestGetOperationDetail:
    @pytest.mark.asyncio
    async def test_returns_detail(self, client, api_state, tmp_workspace):
        """返回操作详情（含 patch 内容）。"""
        rec = _make_record()
        rec.patch_file = "outputs/audits/apv_test_001/changes.patch"
        # 创建 patch 文件
        patch_dir = tmp_workspace / "outputs" / "audits" / "apv_test_001"
        patch_dir.mkdir(parents=True, exist_ok=True)
        (patch_dir / "changes.patch").write_text("--- a/test.xlsx\n+++ b/test.xlsx\n")

        engine = _make_engine_mock([rec], config_workspace=str(tmp_workspace))
        api_state["manager"].get_engine.return_value = engine

        with (
            patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)),
            patch.object(api_module, "_is_external_safe_mode", return_value=False),
        ):
            resp = await client.get("/api/v1/sessions/sess-1/operations/apv_test_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["approval_id"] == "apv_test_001"
        assert data["tool_name"] == "write_cells"
        assert "arguments" in data
        assert data["patch_content"] is not None
        assert "---" in data["patch_content"]

    @pytest.mark.asyncio
    async def test_not_found(self, client, api_state):
        """操作不存在返回 404。"""
        engine = _make_engine_mock([])
        api_state["manager"].get_engine.return_value = engine

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.get("/api/v1/sessions/sess-1/operations/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_session(self, client, api_state):
        """操作不属于当前会话返回 404。"""
        rec = _make_record(session_id="other-session")
        engine = _make_engine_mock([rec])
        api_state["manager"].get_engine.return_value = engine

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.get("/api/v1/sessions/sess-1/operations/apv_test_001")
        assert resp.status_code == 404


# ── POST /api/v1/sessions/{sid}/operations/{aid}/undo ────


class TestUndoOperation:
    @pytest.mark.asyncio
    async def test_undo_success(self, client, api_state):
        """成功回滚操作。"""
        rec = _make_record()
        engine = _make_engine_mock([rec])
        api_state["manager"].get_or_restore_engine = AsyncMock(return_value=engine)

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.post("/api/v1/sessions/sess-1/operations/apv_test_001/undo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "已回滚" in data["message"]

    @pytest.mark.asyncio
    async def test_undo_not_found(self, client, api_state):
        """回滚不存在的操作返回 404。"""
        engine = _make_engine_mock([])
        api_state["manager"].get_or_restore_engine = AsyncMock(return_value=engine)

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.post("/api/v1/sessions/sess-1/operations/nonexistent/undo")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_undo_wrong_session(self, client, api_state):
        """回滚不属于当前会话的操作返回 404。"""
        rec = _make_record(session_id="other-session")
        engine = _make_engine_mock([rec])
        api_state["manager"].get_or_restore_engine = AsyncMock(return_value=engine)

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.post("/api/v1/sessions/sess-1/operations/apv_test_001/undo")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_undo_not_undoable(self, client, api_state):
        """不可回滚的操作返回 error 状态。"""
        rec = _make_record(undoable=False)
        engine = _make_engine_mock([rec])
        engine._approval.undo.return_value = "记录 `apv_test_001` 不支持自动回滚（工具：write_cells）。"
        api_state["manager"].get_or_restore_engine = AsyncMock(return_value=engine)

        with patch.object(api_module, "_has_session_access", new=AsyncMock(return_value=True)):
            resp = await client.post("/api/v1/sessions/sess-1/operations/apv_test_001/undo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"


# ── 辅助函数 _change_type 测试 ───────────────────────────


class TestChangeTypeHelper:
    def test_added(self):
        assert api_module._change_type(False, True) == "added"

    def test_deleted(self):
        assert api_module._change_type(True, False) == "deleted"

    def test_modified(self):
        assert api_module._change_type(True, True) == "modified"

    def test_both_false_is_modified(self):
        assert api_module._change_type(False, False) == "modified"
