"""@ 提及解析器：正则提取 + 数据模型定义。

从用户输入中提取 @file:、@folder:、@skill:、@mcp: 标记以及旧 @img 语法，
返回结构化的 ParseResult。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── 数据模型 ──────────────────────────────────────────────

@dataclass(frozen=True)
class Mention:
    """单个 @ 引用的数据模型。"""

    kind: str   # "file" | "folder" | "skill" | "mcp" | "img"
    value: str  # 路径或名称（如 "sales.xlsx"、"data_basic"、"mongodb"）
    raw: str    # 原始文本（如 "@file:sales.xlsx"）
    start: int  # 在原始输入中的起始位置（字符索引）
    end: int    # 在原始输入中的结束位置（字符索引）
    range_spec: str | None = None  # Excel 区域选取（如 "Sheet1!A1:C10"）


@dataclass(frozen=True)
class ParseResult:
    """解析结果。"""

    mentions: tuple[Mention, ...]  # 按出现顺序排列的 Mention 元组（不可变）
    clean_text: str                # 移除所有 @ 标记后的文本
    display_text: str              # 将 @ 标记替换为其 value（保留引用名称，去除 @type: 前缀）
    original: str                  # 原始输入（保留标记）


@dataclass
class ResolvedMention:
    """已解析的 Mention，包含注入内容或错误信息。"""

    mention: Mention
    context_block: str = ""   # 注入系统提示词的内容块
    error: str | None = None  # 解析失败时的错误信息


# ── 正则常量 ──────────────────────────────────────────────

# 统一匹配 @type:value 格式，可选 [range_spec] 后缀
# 示例：@file:sales.xlsx  或  @file:sales.xlsx[Sheet1!A1:C10]
_MENTION_PATTERN = re.compile(
    r"@(file|folder|skill|mcp):([^\s,;!?\[\]]+)(?:\[([^\]]+)\])?",
    re.IGNORECASE,
)

# 兼容旧 @img 语法（@img 后跟空格和图片路径）
_IMG_PATTERN = re.compile(
    r"@img\s+(\S+\.(?:png|jpg|jpeg|gif|bmp|webp))",
    re.IGNORECASE,
)


# ── MentionParser ─────────────────────────────────────────

class MentionParser:
    """从用户输入中提取所有 @ 提及标记。"""

    @staticmethod
    def parse(text: str) -> ParseResult:
        """解析输入文本，返回 ParseResult。

        - 匹配 @file:path、@folder:path、@skill:name、@mcp:server_name
        - 兼容旧 @img path.png 语法
        - 返回按出现顺序排列的 Mention 列表
        - clean_text 为移除所有 @ 标记后的文本
        - original 保留原始输入
        """
        mentions: list[Mention] = []

        # 收集所有 @type:value[range] 匹配
        for m in _MENTION_PATTERN.finditer(text):
            kind = m.group(1).lower()
            value = m.group(2)
            range_spec = m.group(3)  # None if no [range] suffix
            mentions.append(
                Mention(
                    kind=kind,
                    value=value,
                    raw=m.group(0),
                    start=m.start(),
                    end=m.end(),
                    range_spec=range_spec,
                )
            )

        # 收集所有 @img 匹配
        for m in _IMG_PATTERN.finditer(text):
            mentions.append(
                Mention(
                    kind="img",
                    value=m.group(1),
                    raw=m.group(0),
                    start=m.start(),
                    end=m.end(),
                )
            )

        # 按出现顺序排序（start 位置）
        mentions.sort(key=lambda x: x.start)

        # 生成 clean_text：从后往前移除标记，避免位置偏移
        clean_text = text
        for mention in reversed(mentions):
            before = clean_text[: mention.start]
            after = clean_text[mention.end :]
            clean_text = before + after

        # 清理多余空格（连续空格合并为单个）
        clean_text = re.sub(r"  +", " ", clean_text).strip()

        # 生成 display_text：将 @type:value 替换为 value（保留引用名称）
        display_text = text
        for mention in reversed(mentions):
            before = display_text[: mention.start]
            after = display_text[mention.end :]
            display_text = before + mention.value + after
        display_text = re.sub(r"  +", " ", display_text).strip()

        return ParseResult(
            mentions=tuple(mentions),
            clean_text=clean_text,
            display_text=display_text,
            original=text,
        )
