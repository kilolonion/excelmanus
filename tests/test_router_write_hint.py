"""write_hint 正则扩充 + 小模型分类器提示词优化 测试。

覆盖需求 2.1–2.4, 3.1–3.3。
"""

from __future__ import annotations

import inspect

import pytest

from excelmanus.skillpacks.router import SkillRouter, _MAY_WRITE_HINT_RE, _CHITCHAT_RE


# ── 1. 新增英文写入意图词匹配 ──

@pytest.mark.parametrize(
    "message",
    [
        "fill column D with values",
        "match invoice numbers from Sheet2",
        "solve the equation in cell B3",
        "compute the total revenue",
        "populate the missing fields",
        "replace all occurrences of X",
        "insert a new row at the top",
        "sort the data by date",
        "merge cells A1:B2",
        "fix the formula errors",
        "address the missing values",
        "apply conditional formatting",
        "create a summary table",
        "make a pivot table",
        "generate a report",
        "calculate the average",
        "assign categories to each row",
        "Please set the value of A1 to 100",
        "put the result in column C",
        "add a new column for totals",
    ],
)
def test_new_english_write_words_match_may_write(message: str) -> None:
    """新增英文写入意图词应返回 may_write。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "may_write", f"Expected 'may_write' for: {message!r}, got {result!r}"


# ── 2. 纯分析消息不误判 ──

@pytest.mark.parametrize(
    "message",
    [
        "analyze the data and check for errors",
        "read the contents of Sheet1",
        "scan the file for duplicates",
        "inspect the column headers",
        "list all sheet names",
        "read and preview the first 10 rows",
    ],
)
def test_pure_analysis_messages_return_read_only(message: str) -> None:
    """纯分析/只读消息应返回 read_only，不被误判为 may_write。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "read_only", f"Expected 'read_only' for: {message!r}, got {result!r}"


# ── 3. "dataset" 不因 "set" 子串误判 ──

def test_dataset_not_false_positive() -> None:
    """'load the dataset' 不应因 \\bset\\b 匹配为 may_write。"""
    message = "load the dataset"
    match = _MAY_WRITE_HINT_RE.search(message)
    assert match is None, (
        f"'dataset' should NOT trigger _MAY_WRITE_HINT_RE, "
        f"but matched: {match.group()!r}"
    )


def test_offset_not_false_positive() -> None:
    """'offset' 不应因 \\bset\\b 匹配。"""
    message = "use the offset value"
    match = _MAY_WRITE_HINT_RE.search(message)
    assert match is None


# ── 4. 中文关键词不受影响 ──

@pytest.mark.parametrize(
    "message",
    [
        "请创建一个新的工作表",
        "修改A1单元格的值",
        "删除第三行",
        "填充D列数据",
        "格式化表头",
        "生成图表",
        "排序数据",
        "合并单元格",
    ],
)
def test_chinese_keywords_still_work(message: str) -> None:
    """已有中文关键词应继续正常匹配 may_write。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "may_write", f"Expected 'may_write' for: {message!r}, got {result!r}"


# ── 5. #82-38 完整消息返回 may_write ──

def test_issue_82_38_full_message() -> None:
    """模拟 #82-38 类型的完整消息应返回 may_write。"""
    message = (
        "Please fill column D by matching invoice numbers "
        "from Sheet2 in file.xlsx"
    )
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "may_write"


# ── 6. 提示词包含新规则文本 ──

def test_llm_prompt_contains_expanded_may_write_examples() -> None:
    """_classify_task_llm 的提示词应包含扩充后的 may_write 示例。"""
    source = inspect.getsource(SkillRouter._classify_task_llm)
    # 新增的英文动词应出现在提示词中
    for keyword in ["fill", "match", "solve", "compute", "calculate", "generate"]:
        assert keyword in source, f"Prompt should contain '{keyword}'"


def test_llm_prompt_contains_file_path_rule() -> None:
    """提示词应包含文件路径+数据变更描述优先 may_write 的规则。"""
    source = inspect.getsource(SkillRouter._classify_task_llm)
    assert "文件路径" in source
    assert "数据变更" in source
    assert "优先 may_write" in source


def test_llm_prompt_no_longer_defaults_read_only() -> None:
    """提示词不应再包含'不确定时优先 read_only'。"""
    source = inspect.getsource(SkillRouter._classify_task_llm)
    assert "不确定时优先 read_only" not in source


def test_llm_prompt_strict_read_only_condition() -> None:
    """提示词应包含严格的 read_only 判定条件。"""
    source = inspect.getsource(SkillRouter._classify_task_llm)
    assert "仅当完全确定不涉及写入时才判定 read_only" in source


# ── 7. Chitchat 短路检测 ──

@pytest.mark.parametrize(
    "message",
    [
        "你好",
        "您好！",
        "hi",
        "Hello!",
        "hey",
        "嗨",
        "哈喽",
        "早上好",
        "下午好",
        "晚上好",
        "good morning",
        "Good Afternoon",
        "在吗",
        "在不在",
        "谢谢",
        "thanks",
        "Thank you!",
        "好的",
        "ok",
        "okay",
        "  你好  ",
        "你好！！",
    ],
)
def test_chitchat_regex_matches_greetings(message: str) -> None:
    """纯问候/闲聊消息应被 _CHITCHAT_RE 匹配。"""
    assert _CHITCHAT_RE.match(message.strip()), f"Expected match for: {message!r}"


@pytest.mark.parametrize(
    "message",
    [
        "你好，帮我读取 data.xlsx",
        "hello, please format the table",
        "hi 帮我创建图表",
        "读取销售明细前10行",
        "请修改A1的值",
        "帮我分析数据",
        "你好你好你好",
    ],
)
def test_chitchat_regex_does_not_match_task_messages(message: str) -> None:
    """包含任务内容的消息不应被 _CHITCHAT_RE 匹配。"""
    assert not _CHITCHAT_RE.match(message.strip()), f"Should NOT match: {message!r}"


# ── 8. P2 回归：纯分析中文消息应返回 read_only ──

@pytest.mark.parametrize(
    "message",
    [
        "筛选出所有必须参加的班干部并统计每个班有多少人",
        "找出哪个班自愿报名的人数最多",
        "帮我汇总各部门的销售数据",
        "按月份排名各产品销量",
        "分析各地区营收占比",
        "预览前20行数据",
        "检查是否有重复的学号",
        "对比两个工作表的差异",
    ],
)
def test_pure_analysis_chinese_returns_read_only(message: str) -> None:
    """P2 回归：纯分析/筛选/统计/找出中文消息应返回 read_only。

    conversation_20260221T135637 中"筛选+统计+找出"被误判为 may_write。
    """
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "read_only", f"Expected 'read_only' for: {message!r}, got {result!r}"


@pytest.mark.parametrize(
    "message",
    [
        "筛选出所有必须参加的班干部并排序",
        "格式化表头后统计数据",
        "创建一个汇总表",
    ],
)
def test_mixed_write_and_read_returns_may_write(message: str) -> None:
    """同时包含写入和只读关键词时，may_write 应优先。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "may_write", f"Expected 'may_write' for: {message!r}, got {result!r}"


# ── 9. 身份/元问题 chitchat 短路 ──

@pytest.mark.parametrize(
    "message",
    [
        "你是谁",
        "你是什么",
        "你叫什么",
        "介绍一下自己",
        "你能做什么",
        "你会什么",
        "你有什么功能",
        "who are you",
        "what are you",
        "what can you do",
        "introduce yourself",
        "what is your name",
        "怎么用",
        "如何使用",
        "help",
        "帮助",
        "使用说明",
        "who are you?",
        "你是谁？",
    ],
)
def test_identity_faq_matches_chitchat(message: str) -> None:
    """身份/FAQ/元问题应被 _CHITCHAT_RE 匹配，走短路 read_only。"""
    assert _CHITCHAT_RE.match(message.strip()), f"Expected chitchat match for: {message!r}"


# ── 10. 短消息无关键词 → None（交由 LLM 分类） ──

@pytest.mark.parametrize(
    "message",
    [
        "干嘛呢",
        "test",
        "嗯",
        "?",
        "啥意思",
        "能干啥",
    ],
)
def test_short_no_keyword_returns_none(message: str) -> None:
    """短消息无读/写关键词时，词法返回 None，交由 LLM 分类。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result is None, f"Expected None for short msg: {message!r}, got {result!r}"


# ── 11. 短写入命令不被兜底误伤 ──

@pytest.mark.parametrize(
    "message",
    [
        "排序",
        "合并",
        "删除",
        "创建",
        "sort",
        "merge",
        "fix",
        "create",
    ],
)
def test_short_write_commands_not_affected_by_fallback(message: str) -> None:
    """短写入命令应被 _MAY_WRITE_HINT_RE 优先匹配，不受兜底规则影响。"""
    result = SkillRouter._classify_write_hint_lexical(message)
    assert result == "may_write", f"Expected 'may_write' for: {message!r}, got {result!r}"


# ── image_replica 词法标签 + 策略注入 ──


@pytest.mark.parametrize(
    "message",
    [
        "请帮我复刻这张图片里的表格",
        "照着这张图做一个Excel表格",
        "按照图片还原表格",
        "replicate this table from the screenshot",
        "create excel from image",
        "照着做",
        "图片复刻",
        "做成一样的",
        "仿照这个表格做一个",
        "截图还原成Excel",
        "photo to excel spreadsheet",
    ],
)
def test_image_replica_tag_detected(message: str) -> None:
    """图片复刻相关消息应触发 image_replica 标签。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "image_replica" in tags, f"Expected 'image_replica' in tags for: {message!r}, got {tags}"


@pytest.mark.parametrize(
    "message",
    [
        "请帮我分析一下数据",
        "写入A列数据",
        "创建一个图表",
        "format the header row",
        "read the excel file",
    ],
)
def test_image_replica_tag_not_false_positive(message: str) -> None:
    """非图片复刻消息不应触发 image_replica 标签。"""
    tags = SkillRouter._classify_task_tags_lexical(message)
    assert "image_replica" not in tags, f"Unexpected 'image_replica' in tags for: {message!r}, got {tags}"


def test_image_replica_strategy_injected_when_tag_present() -> None:
    """当 task_tags 包含 image_replica 时，PromptComposer 应注入复刻策略。"""
    from pathlib import Path
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    composer = PromptComposer(Path("excelmanus/prompts"))
    composer.load_all()
    ctx = PromptContext(
        chat_mode="write",
        write_hint="may_write",
        task_tags=["image_replica"],
    )
    text = composer.compose_strategies_text(ctx)
    assert "图片表格复刻策略" in text
    assert "extract_table_spec" in text
    assert "rebuild_excel_from_spec" in text


def test_image_replica_strategy_not_injected_without_tag() -> None:
    """无 image_replica tag 时不应注入复刻策略。"""
    from pathlib import Path
    from excelmanus.prompt_composer import PromptComposer, PromptContext

    composer = PromptComposer(Path("excelmanus/prompts"))
    composer.load_all()
    ctx = PromptContext(
        chat_mode="write",
        write_hint="may_write",
        task_tags=[],
    )
    text = composer.compose_strategies_text(ctx)
    assert "图片表格复刻策略" not in text
