"""Capability discovery must agree with scope, actual contracts and request timing."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from openpyxl import Workbook

from excelmanus.tools.registry import ToolRegistry
from excelmanus.tools.introspection_tools import register_introspection_tools


def registry_at(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_builtin_tools(str(root))
    register_introspection_tools(registry)
    return registry


def query(registry, kind, text):
    return registry.call_tool("introspect_capability", {"query_type": kind, "query": text}).model_text


def test_discovery_uses_calling_registry_after_other_session_binds(tmp_path):
    writer = registry_at(tmp_path)
    reader = registry_at(tmp_path)
    reader.bind_catalog(mode="read")
    assert "apply_spreadsheet_changes" in query(writer, "can_i_do", "写入")
    assert "apply_spreadsheet_changes" not in query(reader, "can_i_do", "写入")
    fork = writer.fork()
    fork.bind_catalog(mode="read")
    assert "不可用" in query(fork, "tool_detail", "apply_spreadsheet_changes")


def test_language_search_is_navigation_not_a_guarantee(tmp_path):
    registry=registry_at(tmp_path)
    text=query(registry,"can_i_do","已有表添加自动筛选")
    assert "候选工具" in text
    assert "具体输入和引擎支持见合同" in text
    unknown=query(registry,"can_i_do","xyz_qzx_123")
    assert "unknown" in unknown and "不表示能力不存在" in unknown


def test_tool_detail_keeps_schema_and_can_select_nested_field(tmp_path):
    registry = registry_at(tmp_path)
    result = query(registry, "tool_detail", "apply_spreadsheet_changes.operations.kind")
    assert '"write"' in result and '"delete_rows"' in result
    assert "结果已截断" not in result


def test_tool_detail_workbook_spec_nested_styles_returns_contract(tmp_path):
    registry = registry_at(tmp_path)
    result = query(registry, "tool_detail", "apply_spreadsheet_changes.workbook_spec.sheets.styles")
    assert "字段不存在" not in result
    assert "border" in result or "font" in result
    assert "不要继续 introspect" not in result
    assert "以系统规格段为准" not in result
    missing = query(registry, "tool_detail", "apply_spreadsheet_changes.workbook_spec.styles")
    assert "字段不存在" in missing
    assert "sheets" in missing
    border = query(registry, "tool_detail", "apply_spreadsheet_changes.workbook_spec.sheets.styles.border")
    assert "字段不存在" not in border
    assert "top" in border or "style" in border


def test_discovered_workbook_spec_compiles_via_edit(tmp_path):
    from excelmanus.tools.context import bind_workspace
    from excelmanus.tools.workbook_tools import apply_spreadsheet_changes, init_guard

    bind_workspace(str(tmp_path))
    init_guard(str(tmp_path))
    registry = registry_at(tmp_path)
    sheets = query(registry, "tool_detail", "apply_spreadsheet_changes.workbook_spec.sheets")
    assert "value_blocks" in sheets
    result = apply_spreadsheet_changes(
        file_path="预算.xlsx",
        workbook_spec={
            "sheets": [{
                "name": "月度",
                "dimensions": {"rows": 3, "cols": 2},
                "value_blocks": [{"start": "A1", "values": [["项目", "金额"], ["租金", 1200]]}],
                "styles": {"title": {"font": {"bold": True}, "border": {"style": "thin"}}},
                "style_regions": [{"range": "A1:B1", "style_id": "title"}],
                "conditional_formats": [{
                    "type": "cell_value",
                    "range": "B2:B2",
                    "operator": "greater_than",
                    "value": 0,
                    "fill_color": "#FF0000",
                }],
            }],
            "uncertainties": [],
        },
    )
    assert result.success, result.model_text
    assert (tmp_path / "预算.xlsx").is_file()
    join = query(registry, "tool_detail", "analyze_spreadsheet.join")
    assert "left_on" in join or "on" in join


def make_engine(tmp_path):
    from excelmanus.agent.session import AgentEngine
    from excelmanus.config import ExcelManusConfig
    registry = ToolRegistry()
    registry.register_builtin_tools(str(tmp_path))
    return AgentEngine(ExcelManusConfig(api_key="test", base_url="https://invalid.example/v1", model="test", workspace_root=str(tmp_path)), registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{"command": "find ."}, {"command": "ls"}, {"command": "echo test", "timeout_seconds": 0}, {"command": "echo test", "unknown_parameter": True}])
async def test_invalid_shell_never_creates_pending_approval(tmp_path, monkeypatch, arguments):
    from excelmanus.tools import shell_tools
    engine = make_engine(tmp_path)
    engine._full_access_enabled = False
    monkeypatch.setattr(shell_tools.shutil, "which", lambda command: None if command == "ls" else "present")
    events = []
    call = SimpleNamespace(id="shell-failure", function=SimpleNamespace(name="run_shell", arguments=json.dumps(arguments)))
    result = await engine._tool_runtime.execute(call, None, events.append, 1)
    assert not result.success and not result.pending_approval
    assert engine.approval.pending is None
    assert not any(event.event_type.value == "pending_approval" for event in events)


def test_prompt_reload_reaches_existing_session_and_invalid_prompt_stops_request(tmp_path):
    import shutil
    from excelmanus.prompt.load import PromptComposer
    from excelmanus.prompt.envelope import assemble_envelope
    engine = make_engine(tmp_path)
    source = Path(__file__).parents[1] / "excelmanus" / "prompts"
    dest = tmp_path / "prompts"
    shutil.copytree(source, dest)
    engine._prompt_composer = PromptComposer(dest)
    engine._prompt_composer.load_all()
    engine.memory.add_user_message("介绍能力")
    first, error = assemble_envelope(engine)
    assert error is None
    path = dest / "core" / "10_core_principles.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n更新能力说明。", encoding="utf-8")
    second, error = assemble_envelope(engine)
    assert error is None
    assert second.messages[0] == first.messages[0]
    assert "更新能力说明" in str(second.messages[-1])
    path.write_text("broken frontmatter", encoding="utf-8")
    third, error = assemble_envelope(engine)
    assert third is None and "提示词加载不完整" in error


def test_selection_preserves_location_errors_and_version(tmp_path):
    from excelmanus.mentions import MentionParser, MentionResolver
    from excelmanus.engine_utils import build_mention_context_block
    from excelmanus.security import FileAccessGuard
    wb = Workbook()
    wb.active.title = "My Sheet"
    wb.active["B3"] = 95
    wb.save(tmp_path / "book.xlsx")
    resolver = MentionResolver(str(tmp_path), FileAccessGuard(str(tmp_path)))
    parsed = MentionParser.parse("@file:book.xlsx['My Sheet'!B3] 我选中了什么")
    assert "B3" in parsed.display_text
    valid = resolver._resolve_file(parsed.mentions[0])
    assert valid.error is None and "95" in valid.context_block
    assert valid.content_version in build_mention_context_block([valid])
    bad = resolver._resolve_file(MentionParser.parse("@file:book.xlsx[Sheet1!B3]").mentions[0])
    assert bad.error_code == "SHEET_NOT_FOUND"
    assert bad.error_fields["available_sheets"] == ["My Sheet"]
    stale = resolver._resolve_file(MentionParser.parse("@file:book.xlsx['My Sheet'!B3]@sha256:abc").mentions[0])
    assert stale.error_code == "STALE_READ"


def test_repeated_selection_belongs_to_each_user_message(tmp_path):
    from excelmanus.mentions.parser import Mention, ResolvedMention
    from excelmanus.prompt.envelope import flush_dynamic_contexts
    engine = make_engine(tmp_path)
    selected = ResolvedMention(Mention("file", "book.xlsx", "@file:book.xlsx[B3]", 0, 20, "B3"), "95")
    for _ in range(2):
        engine.memory.add_user_message("选中了什么")
        engine._mention_contexts = [selected]
        assert flush_dynamic_contexts(engine)
        assert not flush_dynamic_contexts(engine)
    assert sum("<mention_context>" in str(m.get("content", "")) for m in engine.memory.messages) == 2


def test_skill_catalog_reappears_after_compaction_and_clears_after_uninstall(tmp_path):
    from excelmanus.prompt.skill_catalog import attach_skill_catalog
    engine = make_engine(tmp_path)
    packs = {"sample": SimpleNamespace(description="示例技能", disable_model_invocation=False)}
    engine._skill_router = SimpleNamespace(_loader=SimpleNamespace(get_skillpacks=lambda: packs))
    assert "sample" in attach_skill_catalog(engine)
    assert not attach_skill_catalog(engine)
    engine.memory.clear()  # 模拟压缩已移除目录正文，但引擎 fingerprint 保留。
    assert "sample" in attach_skill_catalog(engine)
    packs.clear()
    assert "当前技能目录为空" in attach_skill_catalog(engine)
    assert not attach_skill_catalog(engine)


def test_versions_and_formula_values_survive_model_projection(tmp_path):
    from excelmanus.tools.workbook_tools import observe_spreadsheet, apply_spreadsheet_changes, manage_spreadsheet_versions
    from excelmanus.tools.context import bind_workspace
    from tests.workbook_support import model_projection
    registry_at(tmp_path); bind_workspace(tmp_path)
    wb=Workbook(); wb.active.title="销售"; wb.active.append(["销量","总额"]); wb.active.append([3,"=A2*10"]); wb.save(tmp_path/"book.xlsx"); wb.close()
    read=observe_spreadsheet(file_path="book.xlsx",mode="range",sheet="销售",range="A2:B2",facets=["data"])
    assert "=A2*10" in model_projection(read,tmp_path)
    result=apply_spreadsheet_changes(file_path="book.xlsx",expected_version=read.value["content_version"],operations=[{"kind":"write","sheet":"销售","start_cell":"A2","values":[[3]]}])
    assert result.success
    assert result.value["observation"]["cell_checks"][0]["cell"]=="A2"
    assert result.value["observation"]["visual_observed"] is False
    assert "cell_checks" in model_projection(result,tmp_path)
    versions=manage_spreadsheet_versions(file_path="book.xlsx",action="list")
    assert versions.value["revisions"]
    assert versions.value["revisions"][-1]["revision_id"] in model_projection(versions,tmp_path)
