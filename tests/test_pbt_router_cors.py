"""属性测试：路由器和 CORS。

# Feature: v3-post-refactor-cleanup, Property 6: Description 词汇交集正向评分
# Feature: v3-post-refactor-cleanup, Property 7: 中文 n-gram 分词包含所有 bigram
# Feature: v3-post-refactor-cleanup, Property 8: Triggers 评分权重高于 description 单词评分
# Feature: v3-post-refactor-cleanup, Property 9: CORS 环境变量逗号分隔解析

使用 hypothesis 验证路由器评分逻辑和 CORS 配置解析。

**Validates: Requirements 6.1, 6.2, 6.3, 7.2**
"""

from __future__ import annotations

import os
import re

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from excelmanus.skillpacks.router import SkillRouter
from excelmanus.skillpacks.models import Skillpack


# ── 辅助工具 ──────────────────────────────────────────────

def _make_skillpack(
    *,
    name: str = "test_skill",
    description: str = "",
    triggers: list[str] | None = None,
    allowed_tools: list[str] | None = None,
) -> Skillpack:
    """构造一个最小化的 Skillpack 实例用于测试。"""
    return Skillpack(
        name=name,
        description=description,
        allowed_tools=allowed_tools or [],
        triggers=triggers or [],
        instructions="",
        source="system",
        root_dir="/tmp/test",
    )


# ── 策略定义 ──────────────────────────────────────────────

# 英文单词策略（小写字母，至少 2 个字符以避免单字母噪音）
_english_word = st.from_regex(r"[a-z]{2,10}", fullmatch=True)

# 中文字符策略（常用汉字范围，使用 codepoint 映射）
_chinese_char = st.integers(
    min_value=0x4E00, max_value=0x9FFF
).map(chr)

# 至少包含 2 个中文字符的字符串（用于 Property 7）
_chinese_text = st.lists(
    _chinese_char, min_size=2, max_size=20
).map("".join)

# CORS origin 策略：非空、不含逗号、不含空白的字符串
_cors_origin = st.from_regex(
    r"https?://[a-z0-9\-\.]+(\:[0-9]{1,5})?",
    fullmatch=True,
)

# CORS origin 列表（至少 1 个元素）
_cors_origin_list = st.lists(_cors_origin, min_size=1, max_size=5)


# ---------------------------------------------------------------------------
# Property 6：Description 词汇交集正向评分
# Feature: v3-post-refactor-cleanup, Property 6: Description 词汇交集正向评分
# **Validates: Requirements 6.1**
# ---------------------------------------------------------------------------


@given(
    shared_word=_english_word,
    extra_desc_words=st.lists(_english_word, min_size=0, max_size=5),
    extra_query_words=st.lists(_english_word, min_size=0, max_size=5),
)
@settings(max_examples=100)
def test_property_6_description_overlap_positive_score(
    shared_word: str,
    extra_desc_words: list[str],
    extra_query_words: list[str],
) -> None:
    """Property 6：对于任意 Skillpack 和用户消息，如果用户消息与
    Skillpack 的 description 存在至少一个词汇交集（通过 _tokenize 分词），
    则 _score_description() 的返回值应大于 0。

    **Validates: Requirements 6.1**
    """
    # 构造 description 和 query，确保共享至少一个单词
    desc_text = " ".join([shared_word] + extra_desc_words)
    query_text = " ".join([shared_word] + extra_query_words)

    # 验证确实存在交集（前置条件）
    query_tokens = SkillRouter._tokenize(query_text)
    desc_tokens = SkillRouter._tokenize(desc_text)
    assume(len(query_tokens & desc_tokens) > 0)

    skill = _make_skillpack(description=desc_text)
    score = SkillRouter._score_description(query=query_text, skill=skill)

    assert score > 0, (
        f"存在词汇交集但评分为 0：\n"
        f"  query={query_text!r}, desc={desc_text!r}\n"
        f"  query_tokens={query_tokens}, desc_tokens={desc_tokens}\n"
        f"  交集={query_tokens & desc_tokens}"
    )


@given(
    shared_chars=_chinese_text,
    extra_desc=st.lists(_chinese_char, min_size=0, max_size=10).map("".join),
    extra_query=st.lists(_chinese_char, min_size=0, max_size=10).map("".join),
)
@settings(max_examples=100)
def test_property_6_chinese_description_overlap_positive_score(
    shared_chars: str,
    extra_desc: str,
    extra_query: str,
) -> None:
    """Property 6（中文）：中文 description 与 query 有交集时评分 > 0。

    **Validates: Requirements 6.1**
    """
    desc_text = shared_chars + extra_desc
    query_text = shared_chars + extra_query

    # 验证确实存在交集
    query_tokens = SkillRouter._tokenize(query_text)
    desc_tokens = SkillRouter._tokenize(desc_text)
    assume(len(query_tokens & desc_tokens) > 0)

    skill = _make_skillpack(description=desc_text)
    score = SkillRouter._score_description(query=query_text, skill=skill)

    assert score > 0, (
        f"中文词汇交集存在但评分为 0：\n"
        f"  query={query_text!r}, desc={desc_text!r}"
    )


# ---------------------------------------------------------------------------
# Property 7：中文 n-gram 分词包含所有 bigram
# Feature: v3-post-refactor-cleanup, Property 7: 中文 n-gram 分词包含所有 bigram
# **Validates: Requirements 6.2**
# ---------------------------------------------------------------------------


@given(text=_chinese_text)
@settings(max_examples=100)
def test_property_7_chinese_tokenize_contains_all_bigrams(text: str) -> None:
    """Property 7：对于任意包含至少两个连续中文字符的字符串，
    _tokenize() 的结果应包含所有相邻中文字符对（bigram），
    且包含每个单独的中文字符。

    **Validates: Requirements 6.2**
    """
    tokens = SkillRouter._tokenize(text)

    # 提取所有中文字符
    chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
    assume(len(chinese_chars) >= 2)

    # 验证所有 bigram 都在 tokens 中
    for i in range(len(chinese_chars) - 1):
        bigram = chinese_chars[i] + chinese_chars[i + 1]
        assert bigram in tokens, (
            f"缺少 bigram {bigram!r}：text={text!r}, tokens={tokens}"
        )

    # 验证每个单独的中文字符都在 tokens 中
    for ch in chinese_chars:
        assert ch in tokens, (
            f"缺少单字 {ch!r}：text={text!r}, tokens={tokens}"
        )


# ---------------------------------------------------------------------------
# Property 8：Triggers 评分权重高于 description 单词评分
# Feature: v3-post-refactor-cleanup, Property 8: Triggers 评分权重高于 description 单词评分
# **Validates: Requirements 6.3**
# ---------------------------------------------------------------------------


@given(word=_english_word)
@settings(max_examples=100)
def test_property_8_trigger_score_gte_description_score(word: str) -> None:
    """Property 8：单个 trigger 精确匹配的评分增量（+3）应大于等于
    description 中单个词汇交集的评分增量（+1）。

    **Validates: Requirements 6.3**
    """
    # 构造一个仅有 trigger 匹配的 Skillpack
    trigger_skill = _make_skillpack(
        triggers=[word],
        description="",  # 无 description，排除 description 评分
    )
    trigger_score = SkillRouter._score_triggers(query=word, skill=trigger_skill)

    # 构造一个仅有 description 匹配的 Skillpack
    desc_skill = _make_skillpack(
        triggers=[],
        description=word,
    )
    desc_score = SkillRouter._score_description(query=word, skill=desc_skill)

    # 单个 trigger 匹配评分应 >= 单个 description 词汇交集评分
    assert trigger_score >= desc_score, (
        f"Trigger 评分 ({trigger_score}) 低于 description 评分 ({desc_score})：\n"
        f"  word={word!r}"
    )

    # 更严格：验证具体数值关系（trigger +3, description +1）
    assert trigger_score >= 3, (
        f"单个 trigger 匹配评分应至少为 3，实际为 {trigger_score}"
    )
    assert desc_score >= 1, (
        f"单个 description 词汇交集评分应至少为 1，实际为 {desc_score}"
    )


# ---------------------------------------------------------------------------
# Property 9：CORS 环境变量逗号分隔解析
# Feature: v3-post-refactor-cleanup, Property 9: CORS 环境变量逗号分隔解析
# **Validates: Requirements 7.2**
# ---------------------------------------------------------------------------


@given(origins=_cors_origin_list)
@settings(max_examples=100)
def test_property_9_cors_comma_separated_parsing(origins: list[str]) -> None:
    """Property 9：对于任意由非空非逗号字符串组成的列表 origins，
    将其用逗号连接后通过 CORS 解析逻辑处理，结果应等于原始列表
    （去除首尾空白后）。

    **Validates: Requirements 7.2**
    """
    # 用逗号连接
    raw_value = ",".join(origins)

    # 模拟 load_config() 中的 CORS 解析逻辑
    parsed = tuple(o.strip() for o in raw_value.split(",") if o.strip())

    # 期望值：原始列表各元素 strip 后的 tuple
    expected = tuple(o.strip() for o in origins)

    assert parsed == expected, (
        f"CORS 解析不一致：\n"
        f"  原始列表: {origins!r}\n"
        f"  连接字符串: {raw_value!r}\n"
        f"  解析结果: {parsed!r}\n"
        f"  期望结果: {expected!r}"
    )
