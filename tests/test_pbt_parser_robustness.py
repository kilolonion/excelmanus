"""属性测试：解析器健壮性。

# Feature: v3-post-refactor-cleanup, Property 3: 引号字符串解析正确
# Feature: v3-post-refactor-cleanup, Property 4: 不支持的 frontmatter 语法抛出异常
# Feature: v3-post-refactor-cleanup, Property 5: Frontmatter round-trip

使用 hypothesis 验证引号解析、非法语法拒绝、format→parse 往返一致性。

**Validates: Requirements 5.1, 5.3, 5.5**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from excelmanus.skillpacks.loader import SkillpackLoader, SkillpackValidationError


# ── 辅助策略 ──────────────────────────────────────────────

# 生成不含引号字符的字符串（用于 Property 3）
# 排除单引号、双引号，同时排除空字符串
_no_quote_chars = st.text(
    alphabet=st.characters(
        blacklist_characters="'\"",
        blacklist_categories=("Cs",),  # 排除代理字符
    ),
    min_size=1,
    max_size=50,
)

# 合法的 frontmatter key：非空字母数字下划线，不含冒号
_fm_key = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)

# 合法的标量值策略（str / int / bool）
# 字符串值需要避免被 _parse_scalar 误解析为 bool/int/引号字符串/列表
_safe_str_value = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        blacklist_characters="'\":|>{}\n\r\x0b\x0c\x1c\x1d\x1e\x85[]#",
        blacklist_categories=("Cs", "Z"),  # 排除代理字符和分隔符（含换行类字符）
    ),
    min_size=1,
    max_size=30,
).filter(
    lambda s: s.lower() not in ("true", "false")  # 排除 bool 字面量
    and not s.lstrip("-").isdigit()  # 排除纯整数
    and not s.startswith("-")  # 排除以 - 开头（列表项语法）
    and ":" not in s  # 排除含冒号
    and s.strip() == s  # 排除首尾空白
    and len(s.strip()) > 0  # 排除空白字符串
)

_int_value = st.integers(min_value=-9999, max_value=9999)
_bool_value = st.booleans()

# 标量值：str / int / bool（不含 float，因为 _parse_scalar 不解析 float）
_scalar_value = st.one_of(_safe_str_value, _int_value, _bool_value)

# 列表值：列表中的元素也是标量
_list_value = st.lists(_scalar_value, min_size=0, max_size=5)

# frontmatter 字典值：标量或标量列表
_fm_value = st.one_of(_scalar_value, _list_value)

# 完整的 frontmatter 字典
_fm_dict = st.dictionaries(
    keys=_fm_key,
    values=_fm_value,
    min_size=1,
    max_size=8,
)


# ---------------------------------------------------------------------------
# Property 3：引号字符串解析正确
# Feature: v3-post-refactor-cleanup, Property 3: 引号字符串解析正确
# **Validates: Requirements 5.1**
# ---------------------------------------------------------------------------


@given(s=_no_quote_chars)
@settings(max_examples=100)
def test_property_3_double_quoted_string_parsed_correctly(s: str) -> None:
    """Property 3（双引号）：对于任意不含引号字符的字符串 s，
    _parse_scalar('"' + s + '"') 应返回 s。

    **Validates: Requirements 5.1**
    """
    result = SkillpackLoader._parse_scalar(f'"{s}"')
    assert result == s, (
        f"双引号解析失败：输入 '\"{ s }\"'，期望 {s!r}，实际 {result!r}"
    )


@given(s=_no_quote_chars)
@settings(max_examples=100)
def test_property_3_single_quoted_string_parsed_correctly(s: str) -> None:
    """Property 3（单引号）：对于任意不含引号字符的字符串 s，
    _parse_scalar("'" + s + "'") 应返回 s。

    **Validates: Requirements 5.1**
    """
    result = SkillpackLoader._parse_scalar(f"'{s}'")
    assert result == s, (
        f"单引号解析失败：输入 \"'{ s }'\"，期望 {s!r}，实际 {result!r}"
    )


# ---------------------------------------------------------------------------
# Property 4：不支持的 frontmatter 语法抛出异常
# Feature: v3-post-refactor-cleanup, Property 4: 不支持的 frontmatter 语法抛出异常
# **Validates: Requirements 5.3**
# ---------------------------------------------------------------------------

# 生成不支持的语法标记前缀
_unsupported_prefix = st.sampled_from(["|", ">", "{"])

# 生成后缀内容（非空）
_suffix_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N"),
        blacklist_categories=("Cs",),
    ),
    min_size=1,
    max_size=30,
)


@given(
    key=_fm_key,
    prefix=_unsupported_prefix,
    suffix=_suffix_text,
)
@settings(max_examples=100)
def test_property_4_unsupported_syntax_raises_validation_error(
    key: str,
    prefix: str,
    suffix: str,
) -> None:
    """Property 4：对于包含不支持语法标记（|、>、{）的 frontmatter 文本，
    _parse_frontmatter() 应抛出 SkillpackValidationError。

    **Validates: Requirements 5.3**
    """
    # 构造包含不支持语法的 frontmatter 文本
    frontmatter_text = f"{key}: {prefix}{suffix}"

    with pytest.raises(SkillpackValidationError):
        SkillpackLoader._parse_frontmatter(frontmatter_text)


# ---------------------------------------------------------------------------
# Property 5：Frontmatter round-trip
# Feature: v3-post-refactor-cleanup, Property 5: Frontmatter round-trip
# **Validates: Requirements 5.5**
# ---------------------------------------------------------------------------


def _normalize_value(v):
    """将值规范化以便比较 round-trip 结果。

    _format_frontmatter 输出的字符串经 _parse_scalar 解析后，
    某些类型会发生预期的转换（如 float → str，因为解析器不支持 float）。
    """
    if isinstance(v, list):
        return [_normalize_value(item) for item in v]
    return v


@given(data=_fm_dict)
@settings(max_examples=100)
def test_property_5_frontmatter_round_trip(data: dict) -> None:
    """Property 5：对于任意合法的 frontmatter 字典，
    _parse_frontmatter(_format_frontmatter(d)) 应产生与 d 等价的字典。

    **Validates: Requirements 5.5**
    """
    # 格式化为 frontmatter 文本
    formatted = SkillpackLoader._format_frontmatter(data)

    # 解析回字典
    parsed = SkillpackLoader._parse_frontmatter(formatted)

    # 规范化后比较
    expected = {k: _normalize_value(v) for k, v in data.items()}
    actual = {k: _normalize_value(v) for k, v in parsed.items()}

    assert actual == expected, (
        f"Round-trip 不一致：\n"
        f"  原始: {data!r}\n"
        f"  格式化: {formatted!r}\n"
        f"  解析: {parsed!r}"
    )
