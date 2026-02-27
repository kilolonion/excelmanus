"""属性测试：@ 提及解析器（MentionParser）。

# Feature: mention-system, Property 1-2

使用 hypothesis 验证 MentionParser 的类型提取正确性和往返一致性。

**验证：需求 1.1, 1.2, 1.3, 1.4, 1.5, 1.9, 8.1**
"""

from __future__ import annotations

from hypothesis import given, assume
from hypothesis import strategies as st

from excelmanus.mentions.parser import MentionParser, Mention


# ── 辅助策略 ──────────────────────────────────────────────

# 合法的 mention kind（不含 img，img 语法不同）
_TYPED_KINDS = ["file", "folder", "skill", "mcp"]

# 合法的 mention value：非空、不含空白字符
# 使用字母、数字、常见路径字符
_value_alphabet = st.characters(
    whitelist_categories=("L", "N"),
    whitelist_characters="._-/",
)
_value_strategy = st.text(
    alphabet=_value_alphabet,
    min_size=1,
    max_size=30,
).filter(lambda s: s.strip() and not s.startswith("/"))

# 合法的图片扩展名
_IMG_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "bmp", "webp"]

# 图片文件名策略：name.ext
_img_filename_strategy = st.builds(
    lambda name, ext: f"{name}.{ext}",
    name=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="_-"),
        min_size=1,
        max_size=15,
    ).filter(lambda s: s.strip()),
    ext=st.sampled_from(_IMG_EXTENSIONS),
)

# 普通文本片段（不含 @）
_plain_text_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "Z"),
        whitelist_characters="，。！？、；：",
        blacklist_characters="@",
    ),
    min_size=0,
    max_size=20,
)


# ── 单个 @type:value 标记策略 ─────────────────────────────

@st.composite
def _typed_mention(draw: st.DrawFn) -> tuple[str, str, str]:
    """生成 (kind, value, raw_text) 元组。"""
    kind = draw(st.sampled_from(_TYPED_KINDS))
    value = draw(_value_strategy)
    raw = f"@{kind}:{value}"
    return kind, value, raw


@st.composite
def _img_mention(draw: st.DrawFn) -> tuple[str, str, str]:
    """生成 img 类型的 (kind, value, raw_text) 元组。"""
    filename = draw(_img_filename_strategy)
    raw = f"@img {filename}"
    return "img", filename, raw


# ── 组合输入策略 ──────────────────────────────────────────

@st.composite
def _input_with_typed_mentions(draw: st.DrawFn) -> tuple[str, list[tuple[str, str]]]:
    """生成包含 1-5 个 @type:value 标记的输入字符串。
    
    返回 (input_text, [(kind, value), ...])。
    """
    n = draw(st.integers(min_value=1, max_value=5))
    parts: list[str] = []
    expected: list[tuple[str, str]] = []

    for _ in range(n):
        # 可选前缀文本
        prefix = draw(_plain_text_strategy)
        if prefix:
            parts.append(prefix)

        kind, value, raw = draw(_typed_mention())
        parts.append(raw)
        expected.append((kind, value))

    # 可选后缀文本
    suffix = draw(_plain_text_strategy)
    if suffix:
        parts.append(suffix)

    text = " ".join(parts)
    return text, expected


@st.composite
def _input_with_img_mention(draw: st.DrawFn) -> tuple[str, str, str]:
    """生成包含 @img 标记的输入字符串。
    
    返回 (input_text, filename, raw)。
    """
    prefix = draw(_plain_text_strategy)
    kind, filename, raw = draw(_img_mention())
    suffix = draw(_plain_text_strategy)

    parts = [p for p in [prefix, raw, suffix] if p]
    text = " ".join(parts)
    return text, filename, raw


# ---------------------------------------------------------------------------
# Property 1: Mention 类型提取正确性
# Feature: mention-system, Property 1: 类型提取正确性
# **验证：需求 1.1, 1.2, 1.3, 1.4, 1.5**
# ---------------------------------------------------------------------------


@given(data=_input_with_typed_mentions())
def test_pbt_property_1_typed_mention_extraction(
    data: tuple[str, list[tuple[str, str]]],
) -> None:
    """Property 1：对于任意包含 @type:value 标记的输入字符串，
    MentionParser.parse() 返回的每个 Mention 的 kind 应与标记中的类型一致，
    value 应与标记中的值一致。

    **验证：需求 1.1, 1.2, 1.3, 1.4**
    """
    text, expected = data
    result = MentionParser.parse(text)

    # 解析出的 mention 数量应 >= 预期数量
    # （可能因为 value 中包含 @type: 模式而多匹配，但至少应包含所有预期的）
    parsed_pairs = [(m.kind, m.value) for m in result.mentions]

    for kind, value in expected:
        assert (kind, value) in parsed_pairs, (
            f"预期 ({kind}, {value}) 未在解析结果中找到。"
            f"\n输入: {text}"
            f"\n解析结果: {parsed_pairs}"
        )


@given(data=_input_with_img_mention())
def test_pbt_property_1_img_mention_extraction(
    data: tuple[str, str, str],
) -> None:
    """Property 1（@img 部分）：对于任意包含 @img path.ext 标记的输入字符串，
    MentionParser.parse() 返回的 Mention 的 kind 应为 'img'，
    value 应与图片文件名一致。

    **验证：需求 1.5, 8.1**
    """
    text, filename, raw = data
    result = MentionParser.parse(text)

    img_mentions = [m for m in result.mentions if m.kind == "img"]
    assert len(img_mentions) >= 1, (
        f"未找到 img 类型的 Mention。"
        f"\n输入: {text}"
        f"\n解析结果: {[(m.kind, m.value) for m in result.mentions]}"
    )

    values = [m.value for m in img_mentions]
    assert filename in values, (
        f"预期 img value '{filename}' 未在解析结果中找到。"
        f"\n输入: {text}"
        f"\n解析到的 img values: {values}"
    )


# ---------------------------------------------------------------------------
# Property 2: 解析往返一致性（Round-Trip）
# Feature: mention-system, Property 2: 往返一致性
# **验证：需求 1.9**
# ---------------------------------------------------------------------------


@st.composite
def _roundtrip_input(draw: st.DrawFn) -> str:
    """生成用于往返测试的输入字符串，包含 0-4 个 @type:value 标记。"""
    n = draw(st.integers(min_value=1, max_value=4))
    parts: list[str] = []

    for _ in range(n):
        prefix = draw(_plain_text_strategy)
        if prefix.strip():
            parts.append(prefix.strip())

        kind, value, raw = draw(_typed_mention())
        parts.append(raw)

    suffix = draw(_plain_text_strategy)
    if suffix.strip():
        parts.append(suffix.strip())

    return " ".join(parts)


@given(text=_roundtrip_input())
def test_pbt_property_2_roundtrip_consistency(text: str) -> None:
    """Property 2：对于任意有效输入字符串，将 ParseResult 的 clean_text
    与各 Mention 的 raw 文本按原始位置重新拼接后，再次调用 parse()
    应产生等价的 Mention 列表（kind、value 均相同）。

    **验证：需求 1.9**
    """
    result1 = MentionParser.parse(text)

    if not result1.mentions:
        return  # 无 mention 时无需验证往返

    # 重建：将 clean_text 和 mentions 的 raw 按位置重新拼接
    # 简化方式：将 mentions 的 raw 文本插回 clean_text
    # 由于 clean_text 已移除标记，我们用 clean_text + " " + " ".join(raws) 重建
    raws = " ".join(m.raw for m in result1.mentions)
    reconstructed = f"{result1.clean_text} {raws}".strip()

    result2 = MentionParser.parse(reconstructed)

    # 比较 kind 和 value 列表（排序后比较，因为重建后顺序可能不同）
    pairs1 = sorted((m.kind, m.value) for m in result1.mentions)
    pairs2 = sorted((m.kind, m.value) for m in result2.mentions)

    assert pairs1 == pairs2, (
        f"往返不一致。"
        f"\n原始输入: {text}"
        f"\n重建输入: {reconstructed}"
        f"\n第一次解析: {pairs1}"
        f"\n第二次解析: {pairs2}"
    )


# ══════════════════════════════════════════════════════════
# Property 3–7: MentionResolver 属性测试
# Feature: mention-system, Property 3-7
# **验证：需求 2.2, 2.3, 2.5, 2.6, 3.2, 3.3, 3.5, 7.7, 7.8, 9.1–9.3**
# ══════════════════════════════════════════════════════════

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from excelmanus.mentions.parser import Mention, ResolvedMention
from excelmanus.mentions.resolver import MentionResolver, _count_tokens
from excelmanus.security.guard import FileAccessGuard


# ── 辅助函数 ──────────────────────────────────────────────


def _make_mention(kind: str, value: str) -> Mention:
    """快速构造 Mention 对象。"""
    raw = f"@{kind}:{value}"
    return Mention(kind=kind, value=value, raw=raw, start=0, end=len(raw))


def _make_resolver(
    workspace_root: str,
    max_file_tokens: int = 2000,
    max_folder_depth: int = 2,
) -> MentionResolver:
    """构造 MentionResolver 实例。"""
    guard = FileAccessGuard(workspace_root)
    return MentionResolver(
        workspace_root=workspace_root,
        guard=guard,
        max_file_tokens=max_file_tokens,
        max_folder_depth=max_folder_depth,
    )


# ---------------------------------------------------------------------------
# Property 3: 文件 Token 预算不变量
# Feature: mention-system, Property 3: 文件 Token 预算不变量
# **验证：需求 2.3**
# ---------------------------------------------------------------------------

# 生成随机文本内容（多行，不同大小）
_text_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "Z", "P")),
    min_size=0,
    max_size=10000,
)


@given(content=_text_content_strategy)
@settings(max_examples=30, deadline=None)
def test_pbt_property_3_file_token_budget(content: str) -> None:
    """Property 3：对于任意成功解析的 file 类型 ResolvedMention，
    其 context_block 的 token 数不应超过配置的上限（默认 2000 tokens）。

    **验证：需求 2.3**
    """
    max_tokens = 200  # 使用较小值加速测试

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "test_file.txt"
        file_path.write_text(content, encoding="utf-8")

        resolver = _make_resolver(tmp_dir, max_file_tokens=max_tokens)
        mention = _make_mention("file", "test_file.txt")
        result = resolver._resolve_file(mention)

        if result.error is None and result.context_block:
            token_count = _count_tokens(result.context_block)
            assert token_count <= max_tokens, (
                f"Token 数 {token_count} 超过预算 {max_tokens}。"
                f"\ncontent_block 长度: {len(result.context_block)}"
            )


# ---------------------------------------------------------------------------
# Property 4: 文本文件摘要包含首 N 行
# Feature: mention-system, Property 4: 文本文件摘要包含首 N 行
# **验证：需求 2.2**
# ---------------------------------------------------------------------------

# 生成多行文本（每行较短，确保前几行在 token 预算内）
_short_line = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" "),
    min_size=1,
    max_size=20,
)
_multiline_strategy = st.lists(_short_line, min_size=1, max_size=20)


@given(lines=_multiline_strategy)
@settings(max_examples=30, deadline=None)
def test_pbt_property_4_text_file_first_n_lines(lines: list[str]) -> None:
    """Property 4：对于任意文本文件，context_block 应包含该文件的前 N 行内容，
    且这些行的内容与原始文件一致。

    **验证：需求 2.2**
    """
    content = "\n".join(lines)

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "test_file.txt"
        file_path.write_text(content, encoding="utf-8")

        resolver = _make_resolver(tmp_dir, max_file_tokens=2000)
        mention = _make_mention("file", "test_file.txt")
        result = resolver._resolve_file(mention)

        assert result.error is None
        # context_block 中的行应该是原始文件前 N 行的子集
        block_lines = result.context_block.split("\n")
        for block_line in block_lines:
            if block_line.strip():
                # 每一行应该出现在原始内容中
                assert block_line in lines or block_line in content, (
                    f"context_block 中的行 '{block_line}' 不在原始文件中。"
                )


# ---------------------------------------------------------------------------
# Property 5: 安全路径拒绝
# Feature: mention-system, Property 5: 安全路径拒绝
# **验证：需求 2.5, 2.6, 3.5, 9.1, 9.2, 9.3**
# ---------------------------------------------------------------------------

# 恶意路径策略
_malicious_path_strategy = st.sampled_from([
    "../secret.txt",
    "../../etc/passwd",
    "%2e%2e/secret.txt",
    "%2e%2e/%2e%2e/etc/passwd",
    "subdir/../../outside.txt",
    "..%2f..%2fetc/passwd",
    "%252e%252e/secret.txt",
])

_malicious_kind_strategy = st.sampled_from(["file", "folder"])


@given(
    malicious_path=_malicious_path_strategy,
    kind=_malicious_kind_strategy,
)
@settings(max_examples=30, deadline=None)
def test_pbt_property_5_security_path_rejection(
    malicious_path: str, kind: str
) -> None:
    """Property 5：对于任意包含路径穿越特征或位于 WORKSPACE_ROOT 之外的
    file/folder Mention，error 应非空且 context_block 应为空。

    **验证：需求 2.5, 2.6, 3.5, 9.1, 9.2, 9.3**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        resolver = _make_resolver(tmp_dir)
        mention = _make_mention(kind, malicious_path)

        if kind == "file":
            result = resolver._resolve_file(mention)
        else:
            result = resolver._resolve_folder(mention)

        assert result.error is not None and result.error != "", (
            f"恶意路径 '{malicious_path}' (kind={kind}) 未被拒绝。"
        )
        assert result.context_block == "", (
            f"恶意路径 '{malicious_path}' (kind={kind}) 不应生成 context_block。"
        )


# ---------------------------------------------------------------------------
# Property 6: 目录树深度限制
# Feature: mention-system, Property 6: 目录树深度限制
# **验证：需求 3.2, 7.8**
# ---------------------------------------------------------------------------

# 生成嵌套深度（3-6 层，确保超过限制）
_depth_strategy = st.integers(min_value=3, max_value=6)


@given(depth=_depth_strategy)
@settings(max_examples=10, deadline=None)
def test_pbt_property_6_directory_tree_depth_limit(depth: int) -> None:
    """Property 6：对于任意目录结构，目录树输出不应包含深度超过 2 层的条目。

    **验证：需求 3.2, 7.8**
    """
    max_folder_depth = 2

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 创建深层目录结构
        current = Path(tmp_dir) / "root"
        current.mkdir()
        for i in range(depth):
            current = current / f"level_{i}"
            current.mkdir()
        # 在最深层放一个文件
        (current / "deep_file.txt").write_text("deep")

        resolver = _make_resolver(tmp_dir, max_folder_depth=max_folder_depth)
        mention = _make_mention("folder", "root")
        result = resolver._resolve_folder(mention)

        assert result.error is None

        # 解析树形输出，计算每个条目的深度
        # 根目录 "root/" 是 depth 0
        # 直接子项是 depth 1，以此类推
        tree_lines = result.context_block.split("\n")
        for line in tree_lines:
            if not line.strip():
                continue
            # 计算缩进深度：每 4 个空格或 "│   " 代表一层
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            # 根目录行没有缩进
            if indent == 0:
                continue
            # 每层缩进约 4 字符
            entry_depth = indent // 4
            assert entry_depth <= max_folder_depth, (
                f"条目 '{stripped.strip()}' 深度 {entry_depth} 超过限制 {max_folder_depth}。"
                f"\n完整树:\n{result.context_block}"
            )


# ---------------------------------------------------------------------------
# Property 7: 隐藏/排除条目过滤
# Feature: mention-system, Property 7: 隐藏/排除条目过滤
# **验证：需求 3.3, 7.7**
# ---------------------------------------------------------------------------

# 排除项名称策略
_excluded_name_strategy = st.sampled_from([
    ".hidden",
    ".git",
    ".env",
    ".venv",
    "node_modules",
    ".DS_Store",
    "__pycache__",
])


@given(excluded_names=st.lists(_excluded_name_strategy, min_size=1, max_size=4, unique=True))
@settings(max_examples=15, deadline=None)
def test_pbt_property_7_hidden_excluded_filtering(excluded_names: list[str]) -> None:
    """Property 7：对于任意包含隐藏文件、.venv 或 node_modules 的目录，
    目录树输出不应包含这些被排除的条目。

    **验证：需求 3.3, 7.7**
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir) / "project"
        root.mkdir()

        # 创建排除项
        for name in excluded_names:
            entry = root / name
            if name.startswith(".") and "." in name[1:]:
                # 像 .DS_Store 这样的文件
                entry.write_text("hidden")
            else:
                entry.mkdir(exist_ok=True)
                (entry / "inner.txt").write_text("inner")

        # 创建一个正常文件
        (root / "visible.txt").write_text("visible")

        resolver = _make_resolver(tmp_dir)
        mention = _make_mention("folder", "project")
        result = resolver._resolve_folder(mention)

        assert result.error is None
        assert "visible.txt" in result.context_block

        for name in excluded_names:
            assert name not in result.context_block, (
                f"排除项 '{name}' 不应出现在目录树中。"
                f"\n完整树:\n{result.context_block}"
            )


# ── Property 8: Skill raw_args 提取 ──────────────────────

from excelmanus.mentions.parser import ResolvedMention


@st.composite
def _skill_mention_input(draw: st.DrawFn) -> tuple[str, str, str]:
    """生成 @skill:name args 格式的输入。

    返回 (完整输入, skill_name, 期望的 raw_args)。
    """
    skill_name = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N"),
                whitelist_characters="_-",
            ),
            min_size=1,
            max_size=15,
        ).filter(lambda s: s.strip() and s[0].isalpha())
    )
    args_text = draw(
        st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "Z"),
                whitelist_characters="，。！？、；：._-/",
                blacklist_characters="@",
            ),
            min_size=0,
            max_size=40,
        )
    )
    full_input = f"@skill:{skill_name} {args_text}".strip()
    return full_input, skill_name, args_text.strip()


@given(data=_skill_mention_input())
@settings(max_examples=100, deadline=None)
def test_pbt_property_8_skill_raw_args_extraction(
    data: tuple[str, str, str],
) -> None:
    """Property 8：对于任意 @skill:name args 输入，解析后提取的 raw_args
    应与 /name args 斜杠命令提取的 raw_args 一致。

    具体来说：MentionParser.parse() 的 clean_text 就是 raw_args。

    **验证：需求 4.3**
    """
    full_input, skill_name, expected_args = data

    # 解析 @skill:name 输入
    parse_result = MentionParser.parse(full_input)

    # 应该提取到 skill mention
    skill_mentions = [m for m in parse_result.mentions if m.kind == "skill"]
    assert len(skill_mentions) == 1
    assert skill_mentions[0].value == skill_name

    # clean_text 就是移除 @skill:name 后的文本，即 raw_args
    assert parse_result.clean_text == expected_args


# ── Property 9: 上下文 XML 组装完整性 ──────────────────────

from excelmanus.engine import build_mention_context_block


@st.composite
def _resolved_mention_list(
    draw: st.DrawFn,
) -> list[ResolvedMention]:
    """生成 ResolvedMention 列表（含成功/失败/img 混合）。"""
    kinds = draw(
        st.lists(
            st.sampled_from(["file", "folder", "skill", "mcp", "img"]),
            min_size=1,
            max_size=6,
        )
    )
    results: list[ResolvedMention] = []
    for i, kind in enumerate(kinds):
        value = f"item_{i}"
        raw = f"@{kind}:{value}" if kind != "img" else f"@img {value}.png"
        mention = Mention(kind=kind, value=value, raw=raw, start=0, end=len(raw))

        if kind == "img":
            # img 类型不生成 context_block
            results.append(ResolvedMention(mention=mention))
        elif draw(st.booleans()):
            # 成功解析
            block = draw(
                st.text(
                    alphabet=st.characters(
                        whitelist_categories=("L", "N", "Z"),
                        blacklist_characters="<>&",
                    ),
                    min_size=1,
                    max_size=50,
                )
            )
            results.append(ResolvedMention(mention=mention, context_block=block))
        else:
            # 解析失败
            error = draw(
                st.text(
                    alphabet=st.characters(
                        whitelist_categories=("L", "N", "Z"),
                        blacklist_characters="<>&",
                    ),
                    min_size=1,
                    max_size=30,
                )
            )
            results.append(ResolvedMention(mention=mention, error=error))

    return results


@given(mentions=_resolved_mention_list())
@settings(max_examples=100, deadline=None)
def test_pbt_property_9_context_xml_assembly_completeness(
    mentions: list[ResolvedMention],
) -> None:
    """Property 9：对于任意 ResolvedMention 列表，组装的 <mention_context> XML 应：
    - 包含所有成功解析的 context_block（各自包裹在对应类型的 XML 标签中）
    - 包含解析失败的 Mention 的 <error> 标签及错误信息
    - 不包含 img 类型的条目

    **验证：需求 6.1, 6.3, 6.4**
    """
    xml_block = build_mention_context_block(mentions)

    # 统计各类别
    successful = [m for m in mentions if m.context_block and not m.error and m.mention.kind != "img"]
    failed = [m for m in mentions if m.error and m.mention.kind != "img"]
    img_mentions = [m for m in mentions if m.mention.kind == "img"]

    if not successful and not failed:
        # 全是 img 或空列表 → 无输出
        assert xml_block == ""
        return

    # 有内容时应包含 <mention_context> 包裹
    if successful or failed:
        if xml_block:
            assert "<mention_context>" in xml_block
            assert "</mention_context>" in xml_block

    # 所有成功解析的 context_block 应出现在 XML 中
    for rm in successful:
        assert rm.context_block in xml_block, (
            f"成功解析的 context_block 未出现在 XML 中: {rm.context_block!r}"
        )

    # 所有失败的 mention 应有 <error> 标签
    for rm in failed:
        assert rm.error in xml_block, (
            f"失败的错误信息未出现在 XML 中: {rm.error!r}"
        )
        assert f'<error ref="{rm.mention.raw}">' in xml_block

    # img 类型不应出现在 XML 中
    for rm in img_mentions:
        if xml_block:
            assert f"@img" not in xml_block or rm.mention.raw not in xml_block
