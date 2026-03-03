"""跨文件策略提示词 + 路由匹配测试。

覆盖：
- multi_file 词法标签检测（中/英文）
- candidate_file_paths >= 2 自动标签
- PromptComposer 策略注入 / 不误注入
- 策略文件格式与内容校验
"""

from __future__ import annotations

from pathlib import Path

import pytest

from excelmanus.skillpacks.router import SkillRouter


# ── 1. 词法标签检测：应触发 multi_file ──


@pytest.mark.parametrize(
    "message",
    [
        "把 orders.xlsx 的数据合并到 summary.xlsx",
        "跨文件合并两个表格",
        "多文件汇总",
        "多个文件的数据整合到一起",
        "两个文件的客户ID匹配",
        "从订单文件导入到汇总文件",
        "从 A.xlsx 导入到 B.xlsx",
        "文件间的合并",
        "文件的对比",
        "合并这几个文件",
        "cross-file merge",
        "multi-file operation",
        "merge files together",
        "combine files into one",
        "multiple files analysis",
        "from sales.xlsx to report.xlsx",
    ],
)
def test_multi_file_tag_detected(message: str) -> None:
    """跨文件相关消息应触发 multi_file 标签。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "multi_file" in tags, f"Expected 'multi_file' in tags for: {message!r}, got {tags}"


# ── 2. 词法标签检测：不应触发 multi_file ──


@pytest.mark.parametrize(
    "message",
    [
        "读取 data.xlsx 的内容",
        "修改 Sheet1 的格式",
        "从 Sheet1 查找数据填入 Sheet2",
        "创建一个新的工作表",
        "帮我分析这个文件的数据",
        "format the header row",
        "read the excel file",
        "merge cells A1:B2",
        "合并单元格",
    ],
)
def test_multi_file_tag_not_false_positive(message: str) -> None:
    """非跨文件消息不应触发 multi_file 标签。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "multi_file" not in tags, f"Unexpected 'multi_file' in tags for: {message!r}, got {tags}"


# ── 3. 策略注入集成测试 ──


def test_cross_file_strategy_injected_when_multi_file_tag() -> None:
    """当 task_tags 包含 multi_file 时，PromptComposer 应注入跨文件合并策略。"""
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
    if not prompts_dir.is_dir():
        pytest.skip("prompts/ 目录不存在")

    composer = PromptComposer(prompts_dir)
    composer.load_all()
    ctx = PromptContext(
        chat_mode="write",
        write_hint="may_write",
        task_tags=["multi_file"],
    )
    text = composer.compose_strategies_text(ctx)
    assert "跨文件合并与匹配策略" in text
    assert "run_code" in text
    assert "键列不一致处理" in text


def test_cross_file_strategy_not_injected_without_tag() -> None:
    """无 multi_file tag 时不应注入跨文件合并策略。"""
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
    if not prompts_dir.is_dir():
        pytest.skip("prompts/ 目录不存在")

    composer = PromptComposer(prompts_dir)
    composer.load_all()
    ctx = PromptContext(
        chat_mode="write",
        write_hint="may_write",
        task_tags=[],
    )
    text = composer.compose_strategies_text(ctx)
    assert "跨文件合并与匹配策略" not in text


def test_cross_file_strategy_not_injected_in_read_mode() -> None:
    """read 模式下即使有 multi_file tag 也不应注入（conditions 要求 chat_mode=write）。"""
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
    if not prompts_dir.is_dir():
        pytest.skip("prompts/ 目录不存在")

    composer = PromptComposer(prompts_dir)
    composer.load_all()
    ctx = PromptContext(
        chat_mode="read",
        write_hint="read_only",
        task_tags=["multi_file"],
    )
    text = composer.compose_strategies_text(ctx)
    assert "跨文件合并与匹配策略" not in text


# ── 4. 策略文件格式校验 ──


def test_cross_file_merge_md_parseable() -> None:
    """cross_file_merge.md 应可正确解析 frontmatter。"""
    from excelmanus.prompt_composer import parse_prompt_file

    prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
    strat_file = prompts_dir / "strategies" / "cross_file_merge.md"
    if not strat_file.exists():
        pytest.skip("cross_file_merge.md 不存在")

    seg = parse_prompt_file(strat_file)
    assert seg.name == "cross_file_merge"
    assert seg.layer == "strategy"
    assert seg.priority == 48
    assert seg.max_tokens == 500
    assert seg.conditions.get("chat_mode") == "write"
    assert "multi_file" in seg.conditions.get("task_tags", [])


# ── 5. multi_file 与 cross_sheet 可共存 ──


def test_multi_file_and_cross_sheet_coexist() -> None:
    """消息同时涉及跨文件和 VLOOKUP 时，两个标签应共存。"""
    message = "从 orders.xlsx 用 VLOOKUP 匹配填充到 report.xlsx"
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "multi_file" in tags, f"Expected 'multi_file' in tags, got {tags}"
    assert "cross_sheet" in tags, f"Expected 'cross_sheet' in tags, got {tags}"


# ── 6. 正则误触发保护——单文件操作不触发 multi_file ──


@pytest.mark.parametrize(
    "message",
    [
        "这个文件合并单元格",
        "帮我合并单元格A1到B2",
        "文件格式化",
        "打开文件",
        "保存文件",
        "文件已损坏",
        "导入数据到Sheet1",
    ],
)
def test_single_file_ops_no_multi_file_tag(message: str) -> None:
    """单文件操作不应误触发 multi_file 标签。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "multi_file" not in tags, f"Unexpected 'multi_file' in tags for: {message!r}, got {tags}"


# ── 7. _WIDE_TAGS 包含 multi_file（防止 simple_read 误加） ──


def test_multi_file_in_wide_tags() -> None:
    """multi_file 应出现在 _WIDE_TAGS 中，防止 read_only 模式下误加 simple_read。"""
    import inspect
    source = inspect.getsource(SkillRouter.route)
    assert "multi_file" in source, "route() 方法中应引用 multi_file"
    # 验证 _WIDE_TAGS 包含 multi_file
    assert '"multi_file"' in source or "'multi_file'" in source


# ── 8. LLM 分类器提示词包含 multi_file ──


def test_llm_prompt_contains_multi_file_tag() -> None:
    """_classify_task_llm 的提示词应包含 multi_file 标签描述。"""
    import inspect
    source = inspect.getsource(SkillRouter._classify_task_llm)
    assert "multi_file" in source, "LLM 分类器提示词应包含 multi_file"


# ── 9. 文件间的合并 vs 文件合并 精确性 ──


@pytest.mark.parametrize(
    "message",
    [
        "文件间合并",
        "文件间的合并",
        "文件间对比",
        "文件间的匹配",
        "合并两个文件",
    ],
)
def test_file_relationship_phrases_detected(message: str) -> None:
    """文件间/的 + 操作词应正确触发。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "multi_file" in tags, f"Expected 'multi_file' for: {message!r}, got {tags}"


# ── 10. Playbook reflector 支持 multi_file 分类 ──


def test_playbook_reflector_valid_categories_include_multi_file() -> None:
    """反思器的有效分类应包含 multi_file。"""
    from excelmanus.playbook.reflector import _VALID_CATEGORIES
    assert "multi_file" in _VALID_CATEGORIES


def test_playbook_reflector_prompt_mentions_multi_file() -> None:
    """反思器 LLM 提示词应包含 multi_file 分类。"""
    from excelmanus.playbook.reflector import _REFLECTOR_SYSTEM_PROMPT
    assert "multi_file" in _REFLECTOR_SYSTEM_PROMPT


# ── 11. Window perception 放宽生命周期阈值包含 multi_file ──


def test_window_perception_relax_lifecycle_includes_multi_file() -> None:
    """窗口感知的放宽生命周期标签应包含 multi_file。"""
    from excelmanus.window_perception.advisor import _RELAX_LIFECYCLE_TAGS
    assert "multi_file" in _RELAX_LIFECYCLE_TAGS


# ── 12. 策略内容完整性校验 ──


def test_strategy_references_discover_file_relationships() -> None:
    """策略应引用 discover_file_relationships 工具。"""
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    prompts_dir = Path(__file__).resolve().parent.parent / "excelmanus" / "prompts"
    if not prompts_dir.is_dir():
        pytest.skip("prompts/ 目录不存在")

    composer = PromptComposer(prompts_dir)
    composer.load_all()
    ctx = PromptContext(
        chat_mode="write",
        write_hint="may_write",
        task_tags=["multi_file"],
    )
    text = composer.compose_strategies_text(ctx)
    assert "discover_file_relationships" in text
    assert "示例流程" in text
    assert "finish_task" in text
