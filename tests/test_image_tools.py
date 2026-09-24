"""image_tools 单元测试。"""
from __future__ import annotations
import base64
import json
import tempfile
from pathlib import Path
import pytest
_MINIMAL_PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

@pytest.fixture(autouse=True)
def _isolate_attachment_store(tmp_path, monkeypatch):
    monkeypatch.setenv('EXCELMANUS_HOME', str(tmp_path))
    from excelmanus.attachments.store import reset_attachment_store
    reset_attachment_store()
    yield
    reset_attachment_store()

class TestReadImage:

    def test_read_png_file(self, tmp_path: Path) -> None:
        """读取 PNG 文件返回正确元数据。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        png_data = base64.b64decode(_MINIMAL_PNG_B64)
        img_path = tmp_path / 'test.png'
        img_path.write_bytes(png_data)
        init_guard(str(tmp_path))
        out = read_image(file_path=str(img_path))
        assert out.success
        assert out.value['status'] == 'ok'
        assert out.value['mime_type'] == 'image/png'
        assert out.value['size_bytes'] > 0
        assert out.value['attachment_id'].startswith('sha256:')
        assert '__tool_result_image__' not in out.model_text
        assert out.value['attachment_id'] in out.model_text
        assert '尺寸=' in out.model_text
        assert out.ui_meta.image and out.ui_meta.image.get('attachment')

    def test_read_nonexistent_file(self, tmp_path: Path) -> None:
        """读取不存在的文件返回错误。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        init_guard(str(tmp_path))
        out = read_image(file_path=str(tmp_path / 'nope.png'))
        assert not out.success
        assert out.value['status'] == 'error'

    def test_read_unsupported_format(self, tmp_path: Path) -> None:
        """不支持的格式返回错误。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        txt = tmp_path / 'test.txt'
        txt.write_text('not an image')
        init_guard(str(tmp_path))
        out = read_image(file_path=str(txt))
        assert not out.success
        assert out.value['status'] == 'error'

    def test_read_image_too_large(self, tmp_path: Path) -> None:
        """超大文件返回错误。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        big = tmp_path / 'big.png'
        big.write_bytes(b'x' * 20000001)
        init_guard(str(tmp_path))
        out = read_image(file_path=str(big))
        assert not out.success
        assert '超限' in out.error.message or 'size' in out.error.message.lower()

    def test_image_injection_structure(self, tmp_path: Path) -> None:
        """__tool_result_image__ 结构正确。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        png_data = base64.b64decode(_MINIMAL_PNG_B64)
        img_path = tmp_path / 'test.png'
        img_path.write_bytes(png_data)
        init_guard(str(tmp_path))
        out = read_image(file_path=str(img_path))
        injection = out.ui_meta.image
        assert injection['mime_type'] == 'image/png'
        assert injection['detail'] == 'auto'
        assert injection['attachment']['attachmentId'].startswith('sha256:')
        assert 'base64' not in injection
        assert '__tool_result_image__' not in out.model_text

    def test_get_tools_returns_read_image(self) -> None:
        """get_tools 返回 read_image 工具定义。"""
        from excelmanus.tools.image_tools import get_tools
        tools = get_tools()
        names = [t.name for t in tools]
        assert 'read_image' in names

    def test_read_by_attachment_id_from_history(self, tmp_path: Path) -> None:
        from excelmanus.tools.context import ToolCallContext, bind_call, current_call, reset_call
        from excelmanus.tools.image_tools import init_guard, read_image
        png_data = base64.b64decode(_MINIMAL_PNG_B64)
        img_path = tmp_path / 'hist.png'
        img_path.write_bytes(png_data)
        init_guard(str(tmp_path))
        first = read_image(file_path=str(img_path))
        assert first.success
        attach_id = first.value['attachment_id']
        current = current_call()
        assert current is not None
        token = bind_call(ToolCallContext(binding=current.binding, durable_attachment_ids=frozenset({attach_id})))
        try:
            out = read_image(attachment_id=attach_id)
        finally:
            reset_call(token)
        assert out.success
        assert out.value['attachment_id'] == attach_id

    def test_attachment_id_without_history_denied(self, tmp_path: Path) -> None:
        from excelmanus.tools.image_tools import init_guard, read_image
        png_data = base64.b64decode(_MINIMAL_PNG_B64)
        img_path = tmp_path / 'hist.png'
        img_path.write_bytes(png_data)
        init_guard(str(tmp_path))
        first = read_image(file_path=str(img_path))
        out = read_image(attachment_id=first.value['attachment_id'])
        assert not out.success
        assert out.error.code == 'PERMISSION_DENIED'
        assert out.value['failure_class'] == 'permission_denied'

    def test_rejects_outside_workspace_path(self, tmp_path: Path) -> None:
        """workspace 外图片路径应被拒绝。"""
        from excelmanus.tools.image_tools import read_image, init_guard
        outside_dir = Path(tempfile.mkdtemp())
        outside_img = outside_dir / 'outside.png'
        outside_img.write_bytes(base64.b64decode(_MINIMAL_PNG_B64))
        init_guard(str(tmp_path))
        out = read_image(file_path=str(outside_img))
        assert not out.success
        assert '路径' in out.error.message

class TestUiMetaImageInjection:
    """ToolDispatcher ui_meta.image 注入通道测试。"""

    def _make_dispatcher(self):
        """创建最小化 mock ToolDispatcher。"""
        from unittest.mock import MagicMock
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        engine = MagicMock()
        engine.memory = MagicMock()
        engine.is_vision_capable = True
        engine._database = None
        engine.config = MagicMock()
        engine.config.code_policy_enabled = False
        engine.state = MagicMock()
        engine.approval = MagicMock()
        engine.approval.is_audit_only_tool = MagicMock(return_value=False)
        engine.approval.is_high_risk_tool = MagicMock(return_value=False)
        engine.full_access_enabled = False
        dispatcher = ToolDispatcher.__new__(ToolDispatcher)
        dispatcher._engine = engine
        dispatcher._deferred_image_injections = []
        dispatcher._tool_call_store = None
        dispatcher._handlers = []
        return (dispatcher, engine)

    def test_inject_image_from_ui_meta(self) -> None:
        """ui_meta.image 应延迟注入图片通道。"""
        from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta
        dispatcher, engine = self._make_dispatcher()
        b64 = _MINIMAL_PNG_B64.replace('\n', '').strip()
        tr = ToolResult(success=True, model_text='图片已加载', ui_meta=ToolUiMeta(image={'base64': b64, 'mime_type': 'image/png', 'detail': 'auto'}))
        dispatcher._apply_ui_meta_effects(tr)
        engine.memory.add_user_message.assert_not_called()
        assert len(dispatcher._deferred_image_injections) == 1
        dispatcher.flush_deferred_images()
        engine.memory.add_user_message.assert_called_once()
        content = engine.memory.add_user_message.call_args.args[0]
        assert content[0]['type'] == 'image'
        assert content[0]['attachment']['attachmentId'].startswith('sha256:')
        kwargs = engine.memory.add_user_message.call_args.kwargs
        assert kwargs['hidden'] is True
        assert kwargs['prompt_kind'] == 'image_observation'

    def test_image_burst_is_one_hidden_observation_message(self) -> None:
        """同一批图片不应各自制造一个 user turn。"""
        from excelmanus.engine_core.tool_result import ToolResult, ToolUiMeta
        dispatcher, engine = self._make_dispatcher()
        b64 = _MINIMAL_PNG_B64.replace('\n', '').strip()
        for _ in range(3):
            dispatcher._apply_ui_meta_effects(ToolResult(success=True, model_text='图片已加载', ui_meta=ToolUiMeta(image={'base64': b64, 'mime_type': 'image/png', 'detail': 'auto'})))
        dispatcher.flush_deferred_images()
        engine.memory.add_user_message.assert_called_once()
        content = engine.memory.add_user_message.call_args.args[0]
        assert len(content) == 1

    def test_legacy_json_magic_field_is_lifted_at_dispatcher(self) -> None:
        """消费边界把未迁移工具的 JSON 魔法字段提升到 ui_meta 并注入。"""
        dispatcher, engine = self._make_dispatcher()
        b64 = _MINIMAL_PNG_B64.replace('\n', '').strip()
        raw = json.dumps({'status': 'ok', '__tool_result_image__': {'base64': b64, 'mime_type': 'image/png'}})
        tr = dispatcher._coerce_tool_result(raw)
        dispatcher._apply_ui_meta_effects(tr)
        assert tr.ui_meta.image is not None
        assert tr.ui_meta.image['base64'] == b64
        assert '__tool_result_image__' not in tr.model_text
        assert len(dispatcher._deferred_image_injections) == 1
        engine.memory.add_user_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_call_registry_tool_injects_before_truncate(self) -> None:
        """read_image 结果应先注入 __tool_result_image__ 再截断，避免截断破坏 JSON。"""
        from unittest.mock import MagicMock
        from excelmanus.engine_core.tool_dispatcher import ToolDispatcher
        payload = json.dumps({'status': 'ok', 'hint': '图片已加载', '__tool_result_image__': {'base64': 'A' * 5000, 'mime_type': 'image/png', 'detail': 'auto'}}, ensure_ascii=False)
        tool_def = MagicMock()
        tool_def.truncate_result.side_effect = lambda s: s[:100]
        registry = MagicMock()
        registry.call_tool.return_value = payload
        registry.get_tool.return_value = tool_def
        engine = MagicMock()
        engine._registry = registry
        engine.registry = registry
        engine.memory = MagicMock()
        engine._persistent_memory = None
        engine._database = None
        engine.config = MagicMock()
        engine.config.code_policy_enabled = False
        engine.state = MagicMock()
        engine.approval = MagicMock()
        engine.approval.is_audit_only_tool = MagicMock(return_value=False)
        engine.approval.is_high_risk_tool = MagicMock(return_value=False)
        engine.full_access_enabled = False
        dispatcher = ToolDispatcher(engine)
        engine.is_vision_capable = True
        out = await dispatcher.call_registry_tool(tool_name='read_image', arguments={'file_path': 'x.png'}, tool_scope=None)
        from excelmanus.engine_core.tool_result import ToolResult
        assert isinstance(out, ToolResult)
        assert out.ui_meta.image is not None
        assert out.ui_meta.image['base64'] == 'A' * 5000
        assert '__tool_result_image__' not in out.model_text
        assert len(out.model_text) <= 100
        engine.memory.add_user_message.assert_not_called()
        assert len(dispatcher._deferred_image_injections) == 1
_BASIC_SPEC = {'version': '2', 'name': 'test', 'sheets': [{'name': 'Sheet1', 'dimensions': {'rows': 2, 'cols': 2}, 'cells': [{'address': 'A1', 'value': 'Name', 'value_type': 'string', 'style_id': 'header', 'confidence': 1.0}, {'address': 'B1', 'value': 'Age', 'value_type': 'string', 'style_id': 'header', 'confidence': 1.0}, {'address': 'A2', 'value': 'Alice', 'value_type': 'string', 'confidence': 1.0}, {'address': 'B2', 'value': 30, 'value_type': 'number', 'confidence': 1.0}], 'styles': {'header': {'font': {'bold': True, 'size': 12, 'color': '#FFFFFF'}, 'fill': {'type': 'solid', 'color': '#4472C4'}}}, 'column_widths': [15, 10]}], 'uncertainties': []}

def _compile_to_xlsx(spec: dict, dest: Path) -> dict:
    from tests.workbook_support import create_document_bytes
    data, summary = create_document_bytes(spec)
    dest.write_bytes(data)
    return summary

class TestCompileWorkbookSpec:

    def test_basic_compile(self, tmp_path: Path) -> None:
        output_path = tmp_path / 'output.xlsx'
        summary = _compile_to_xlsx(_BASIC_SPEC, output_path)
        assert output_path.exists()
        assert summary['applied'].count('write') >= 4
        from openpyxl import load_workbook
        wb = load_workbook(str(output_path))
        ws = wb['Sheet1']
        assert ws['A1'].value == 'Name'
        assert ws['B2'].value == 30
        assert ws['A1'].font.bold is True

    def test_invalid_json_raises(self) -> None:
        from tests.workbook_support import create_document_bytes
        with pytest.raises(ValueError):
            create_document_bytes('{not json')

    def test_merged_cells(self, tmp_path: Path) -> None:
        spec = {**_BASIC_SPEC, 'sheets': [{'name': 'Sheet1', 'dimensions': {'rows': 2, 'cols': 2}, 'cells': [{'address': 'A1', 'value': 'Title', 'value_type': 'string', 'confidence': 1.0}, {'address': 'A2', 'value': 'Alice', 'value_type': 'string', 'confidence': 1.0}, {'address': 'B2', 'value': 30, 'value_type': 'number', 'confidence': 1.0}], 'merged_ranges': [{'range': 'A1:B1', 'confidence': 0.95}], 'styles': {}, 'column_widths': [15, 10]}]}
        output_path = tmp_path / 'output.xlsx'
        summary = _compile_to_xlsx(spec, output_path)
        assert summary['applied'].count('merge') == 1
