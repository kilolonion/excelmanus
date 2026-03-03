"""语义分块器：将长文本按 Markdown 结构智能切分，保证每块格式合法。

替代 base.py 中的 split_message 暴力切分逻辑，
在代码块、表格、列表等结构边界处安全断开，
并自动修复未闭合的 Markdown 标记。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto


class BlockType(Enum):
    """Markdown 块级元素类型。"""

    HEADING = auto()
    CODE_BLOCK = auto()
    TABLE = auto()
    LIST = auto()
    PARAGRAPH = auto()
    BLANK_LINE = auto()
    BLOCKQUOTE = auto()
    THEMATIC_BREAK = auto()  # ---, ***, ___


@dataclass
class Block:
    """解析后的 Markdown 块。"""

    type: BlockType
    text: str
    # 代码块的语言标注
    code_lang: str = ""
    # 列表项的嵌套深度（0-based）
    list_depth: int = 0


# ── 正则 ──

_RE_HEADING = re.compile(r"^#{1,6}\s")
_RE_CODE_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)")
_RE_TABLE_SEP = re.compile(r"^\|?[\s\-:|]+\|[\s\-:|]*$")
_RE_TABLE_ROW = re.compile(r"^\|.+\|")
_RE_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)]) ")
_RE_BLOCKQUOTE = re.compile(r"^>\s?")
_RE_THEMATIC_BREAK = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")
_RE_BLANK = re.compile(r"^\s*$")

# 内联标记跟踪
_INLINE_MARKERS = [
    ("```", "```"),   # 内联代码（三反引号）
    ("`", "`"),       # 内联代码
    ("**", "**"),     # 粗体
    ("__", "__"),     # 粗体
    ("*", "*"),       # 斜体
    ("_", "_"),       # 斜体
    ("~~", "~~"),     # 删除线
]


def _parse_blocks(text: str) -> list[Block]:
    """将文本解析为 Markdown 块序列。"""
    lines = text.split("\n")
    blocks: list[Block] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # 空行
        if _RE_BLANK.match(line):
            blocks.append(Block(type=BlockType.BLANK_LINE, text=line))
            i += 1
            continue

        # 主题分隔线（--- / *** / ___）
        if _RE_THEMATIC_BREAK.match(line):
            blocks.append(Block(type=BlockType.THEMATIC_BREAK, text=line))
            i += 1
            continue

        # 标题
        if _RE_HEADING.match(line):
            blocks.append(Block(type=BlockType.HEADING, text=line))
            i += 1
            continue

        # 代码块（围栏）
        m = _RE_CODE_FENCE.match(line)
        if m:
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            lang = m.group(2).strip()
            code_lines = [line]
            i += 1
            while i < n:
                code_lines.append(lines[i])
                # 结束围栏：同类字符、至少同样长度
                close_m = _RE_CODE_FENCE.match(lines[i])
                if close_m and close_m.group(1)[0] == fence_char and len(close_m.group(1)) >= fence_len:
                    i += 1
                    break
                i += 1
            blocks.append(Block(
                type=BlockType.CODE_BLOCK,
                text="\n".join(code_lines),
                code_lang=lang,
            ))
            continue

        # 表格（表头行 + 分隔行 + 数据行）
        if _RE_TABLE_ROW.match(line) and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            table_lines = [line]
            i += 1
            while i < n and (_RE_TABLE_ROW.match(lines[i]) or _RE_TABLE_SEP.match(lines[i])):
                table_lines.append(lines[i])
                i += 1
            blocks.append(Block(type=BlockType.TABLE, text="\n".join(table_lines)))
            continue

        # 块引用
        if _RE_BLOCKQUOTE.match(line):
            quote_lines = []
            while i < n and (_RE_BLOCKQUOTE.match(lines[i]) or (lines[i].strip() and not _RE_BLANK.match(lines[i]))):
                # 只在明确的块引用行或非空续行时继续
                if not _RE_BLOCKQUOTE.match(lines[i]) and quote_lines:
                    # 非引用行 — 检查是否为续行（前一行不以空行结尾）
                    if _RE_BLANK.match(lines[i]):
                        break
                    # 如果是新的块级元素，停止
                    if _RE_HEADING.match(lines[i]) or _RE_CODE_FENCE.match(lines[i]) or _RE_LIST_ITEM.match(lines[i]):
                        break
                quote_lines.append(lines[i])
                i += 1
            blocks.append(Block(type=BlockType.BLOCKQUOTE, text="\n".join(quote_lines)))
            continue

        # 列表
        lm = _RE_LIST_ITEM.match(line)
        if lm:
            list_lines = []
            base_depth = len(lm.group(1))
            while i < n:
                lm2 = _RE_LIST_ITEM.match(lines[i])
                if lm2:
                    list_lines.append(lines[i])
                    i += 1
                elif lines[i].strip() and not _RE_BLANK.match(lines[i]):
                    # 缩进续行
                    stripped = lines[i]
                    leading = len(stripped) - len(stripped.lstrip())
                    if leading > base_depth:
                        list_lines.append(lines[i])
                        i += 1
                    else:
                        break
                elif _RE_BLANK.match(lines[i]):
                    # 空行 — 查看下一行是否仍属于列表
                    if i + 1 < n and _RE_LIST_ITEM.match(lines[i + 1]):
                        list_lines.append(lines[i])
                        i += 1
                    else:
                        break
                else:
                    break
            blocks.append(Block(
                type=BlockType.LIST,
                text="\n".join(list_lines),
                list_depth=base_depth,
            ))
            continue

        # 普通段落
        para_lines = []
        while i < n:
            if _RE_BLANK.match(lines[i]):
                break
            if _RE_HEADING.match(lines[i]) and para_lines:
                break
            if _RE_CODE_FENCE.match(lines[i]) and para_lines:
                break
            if _RE_TABLE_ROW.match(lines[i]) and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]) and para_lines:
                break
            if _RE_LIST_ITEM.match(lines[i]) and para_lines:
                break
            if _RE_BLOCKQUOTE.match(lines[i]) and para_lines:
                break
            if _RE_THEMATIC_BREAK.match(lines[i]) and para_lines:
                break
            para_lines.append(lines[i])
            i += 1
        if para_lines:
            blocks.append(Block(type=BlockType.PARAGRAPH, text="\n".join(para_lines)))

    return blocks


def _count_unescaped(text: str, marker: str) -> int:
    """计算文本中未转义的 marker 出现次数。"""
    count = 0
    i = 0
    while i < len(text):
        if i > 0 and text[i - 1] == "\\":
            i += 1
            continue
        if text[i:i + len(marker)] == marker:
            count += 1
            i += len(marker)
        else:
            i += 1
    return count


def _fix_unclosed_inline(text: str) -> str:
    """修复未闭合的内联标记（在块尾补全）。"""
    # 按长度降序处理，避免 ** 被 * 误匹配
    for opener, closer in sorted(_INLINE_MARKERS, key=lambda x: -len(x[0])):
        # 跳过反引号（代码中的内容不应被修复）
        if opener == "`" or opener == "```":
            continue
        count = _count_unescaped(text, opener)
        if count % 2 != 0:
            text += closer
    return text


def _fix_unclosed_code_fence(chunk: str) -> str:
    """如果块中有未闭合的代码围栏，在末尾补上。"""
    fence_stack: list[tuple[str, int]] = []
    for line in chunk.split("\n"):
        m = _RE_CODE_FENCE.match(line)
        if m:
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            if fence_stack and fence_stack[-1][0] == fence_char and fence_len >= fence_stack[-1][1]:
                fence_stack.pop()
            else:
                fence_stack.append((fence_char, fence_len))
    # 补全所有未闭合的代码围栏
    for fence_char, fence_len in reversed(fence_stack):
        chunk += "\n" + fence_char * fence_len
    return chunk


def _prepend_code_fence(chunk: str, lang: str, fence_char: str = "`", fence_len: int = 3) -> str:
    """在块首添加代码围栏续接标记。"""
    prefix = fence_char * fence_len
    if lang:
        prefix += lang
    return prefix + "\n" + chunk


class SmartChunker:
    """将长文本按语义边界切分，保证每块 Markdown 合法。

    替代 ChannelAdapter.split_message 的暴力按字符数切分逻辑。
    """

    def chunk(
        self,
        text: str,
        max_len: int = 4000,
        output_format: str = "markdown",
    ) -> list[str]:
        """将文本切分为不超过 max_len 字符的语义块列表。

        Args:
            text: 待切分的文本。
            max_len: 每块的最大字符数。
            output_format: 输出格式 "markdown" | "html" | "plain"。

        Returns:
            切分后的块列表，每块 Markdown 格式合法。
        """
        if not text or not text.strip():
            return [text] if text else []

        if len(text) <= max_len:
            return [text]

        blocks = _parse_blocks(text)
        chunks = self._merge_blocks(blocks, max_len)

        # 后处理：修复每块的格式
        result: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            chunk = _fix_unclosed_code_fence(chunk)
            chunk = _fix_unclosed_inline(chunk)
            if output_format == "html":
                chunk = self._markdown_to_html(chunk)
            elif output_format == "plain":
                chunk = self._markdown_to_plain(chunk)
            result.append(chunk)

        return result if result else [text[:max_len]]

    def _merge_blocks(self, blocks: list[Block], max_len: int) -> list[str]:
        """将解析后的块按 max_len 合并为多个 chunk。"""
        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for block in blocks:
            block_text = block.text
            block_len = len(block_text)

            # 单个块就超过 max_len — 需要强制子切分
            if block_len > max_len:
                # 先刷出当前缓冲
                if current_parts:
                    chunks.append("\n".join(current_parts))
                    current_parts = []
                    current_len = 0

                # 对超长块做特殊处理
                sub_chunks = self._split_oversized_block(block, max_len)
                chunks.extend(sub_chunks)
                continue

            # 追加会超限 → 先刷出当前缓冲
            needed = block_len + (1 if current_parts else 0)  # +1 for \n join
            if current_len + needed > max_len and current_parts:
                chunks.append("\n".join(current_parts))
                current_parts = []
                current_len = 0

            # 标题前强制分块（除非是第一个块）
            if block.type == BlockType.HEADING and current_parts:
                # 只在当前缓冲已有一定内容时才分块
                if current_len > max_len // 4:
                    chunks.append("\n".join(current_parts))
                    current_parts = []
                    current_len = 0

            current_parts.append(block_text)
            current_len += block_len + (1 if len(current_parts) > 1 else 0)

        # 刷出剩余
        if current_parts:
            chunks.append("\n".join(current_parts))

        return chunks

    def _split_oversized_block(self, block: Block, max_len: int) -> list[str]:
        """将超长的单个块强制切分。"""
        if block.type == BlockType.CODE_BLOCK:
            return self._split_code_block(block, max_len)
        if block.type == BlockType.TABLE:
            return self._split_table(block, max_len)
        if block.type == BlockType.LIST:
            return self._split_list(block, max_len)
        # 普通段落 / 块引用 — 按换行或句子切
        return self._split_text_block(block.text, max_len)

    def _split_code_block(self, block: Block, max_len: int) -> list[str]:
        """切分超长代码块，保持围栏完整。"""
        lines = block.text.split("\n")
        if len(lines) < 3:
            return [block.text[:max_len]]

        # 提取围栏
        open_fence = lines[0]
        close_fence = lines[-1]
        m = _RE_CODE_FENCE.match(open_fence)
        fence_str = m.group(1) if m else "```"
        lang = block.code_lang

        content_lines = lines[1:-1]  # 去掉开闭围栏
        overhead = len(open_fence) + len(fence_str) + 2  # +2 for \n

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for cl in content_lines:
            line_cost = len(cl) + 1
            if current_len + line_cost + overhead > max_len and current:
                # 生成一个代码块 chunk
                chunk_text = open_fence + "\n" + "\n".join(current) + "\n" + fence_str
                chunks.append(chunk_text)
                current = []
                current_len = 0
            current.append(cl)
            current_len += line_cost

        if current:
            chunk_text = open_fence + "\n" + "\n".join(current) + "\n" + close_fence
            chunks.append(chunk_text)

        return chunks if chunks else [block.text[:max_len]]

    def _split_table(self, block: Block, max_len: int) -> list[str]:
        """切分超长表格，保持表头在每块。"""
        lines = block.text.split("\n")
        if len(lines) < 3:
            return [block.text[:max_len]]

        header = lines[0]
        separator = lines[1]
        header_block = header + "\n" + separator
        overhead = len(header_block) + 1

        data_lines = lines[2:]
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for dl in data_lines:
            line_cost = len(dl) + 1
            if current_len + line_cost + overhead > max_len and current:
                chunk_text = header_block + "\n" + "\n".join(current)
                chunks.append(chunk_text)
                current = []
                current_len = 0
            current.append(dl)
            current_len += line_cost

        if current:
            chunk_text = header_block + "\n" + "\n".join(current)
            chunks.append(chunk_text)

        return chunks if chunks else [block.text[:max_len]]

    def _split_list(self, block: Block, max_len: int) -> list[str]:
        """切分超长列表，按列表项边界切。"""
        lines = block.text.split("\n")
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            line_cost = len(line) + 1
            # 在列表项边界切（新的列表项开始）
            is_item_start = bool(_RE_LIST_ITEM.match(line))
            if is_item_start and current_len + line_cost > max_len and current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            current.append(line)
            current_len += line_cost

        if current:
            chunks.append("\n".join(current))

        return chunks if chunks else [block.text[:max_len]]

    def _split_text_block(self, text: str, max_len: int) -> list[str]:
        """按段落/换行/句子切分纯文本块。"""
        if len(text) <= max_len:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            # 优先在双换行处切
            idx = remaining.rfind("\n\n", 0, max_len)
            if idx > max_len // 3:
                chunks.append(remaining[:idx])
                remaining = remaining[idx:].lstrip("\n")
                continue

            # 其次在单换行处切
            idx = remaining.rfind("\n", 0, max_len)
            if idx > max_len // 3:
                chunks.append(remaining[:idx])
                remaining = remaining[idx:].lstrip("\n")
                continue

            # 在句号处切
            for sep in ("。", ".\n", ". ", "；", "; "):
                idx = remaining.rfind(sep, 0, max_len)
                if idx > max_len // 3:
                    cut = idx + len(sep)
                    chunks.append(remaining[:cut])
                    remaining = remaining[cut:].lstrip()
                    break
            else:
                # 最后手段：硬切
                chunks.append(remaining[:max_len])
                remaining = remaining[max_len:]

        return chunks

    # ── 格式转换 ──

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """Markdown → Telegram HTML 的轻量转换。

        仅处理最常用的格式：粗体、斜体、代码、代码块、链接。
        不使用外部库，保持轻量。
        """
        lines = text.split("\n")
        result: list[str] = []
        in_code_block = False
        code_lang = ""

        for line in lines:
            if not in_code_block:
                m = _RE_CODE_FENCE.match(line)
                if m:
                    in_code_block = True
                    code_lang = m.group(2).strip()
                    if code_lang:
                        result.append(f"<pre><code class=\"language-{_html_escape(code_lang)}\">")
                    else:
                        result.append("<pre><code>")
                    continue

                # 标题 → 粗体
                hm = _RE_HEADING.match(line)
                if hm:
                    heading_text = line.lstrip("# ").strip()
                    result.append(f"<b>{_html_escape(heading_text)}</b>")
                    continue

                # 内联格式转换
                converted = _html_escape(line)
                # 粗体 **text** → <b>text</b>
                converted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", converted)
                # 斜体 *text* → <i>text</i>
                converted = re.sub(r"\*(.+?)\*", r"<i>\1</i>", converted)
                # 内联代码 `text` → <code>text</code>
                converted = re.sub(r"`(.+?)`", r"<code>\1</code>", converted)
                # 删除线 ~~text~~ → <s>text</s>
                converted = re.sub(r"~~(.+?)~~", r"<s>\1</s>", converted)
                # 链接 [text](url) → <a href="url">text</a>
                converted = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', converted)

                result.append(converted)
            else:
                m = _RE_CODE_FENCE.match(line)
                if m:
                    in_code_block = False
                    result.append("</code></pre>")
                    continue
                result.append(_html_escape(line))

        # 未闭合的代码块
        if in_code_block:
            result.append("</code></pre>")

        return "\n".join(result)

    @staticmethod
    def _markdown_to_plain(text: str) -> str:
        """Markdown → 纯文本，去除格式标记。"""
        lines = text.split("\n")
        result: list[str] = []
        in_code_block = False

        for line in lines:
            if not in_code_block:
                m = _RE_CODE_FENCE.match(line)
                if m:
                    in_code_block = True
                    continue
                # 去标题标记
                line = re.sub(r"^#{1,6}\s+", "", line)
                # 去粗体/斜体
                line = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", line)
                line = re.sub(r"_{1,2}(.+?)_{1,2}", r"\1", line)
                # 去删除线
                line = re.sub(r"~~(.+?)~~", r"\1", line)
                # 去链接格式，保留文字
                line = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", line)
                # 保留内联代码内容
                # line = re.sub(r"`(.+?)`", r"\1", line)
                result.append(line)
            else:
                m = _RE_CODE_FENCE.match(line)
                if m:
                    in_code_block = False
                    continue
                result.append(line)

        return "\n".join(result)


def _html_escape(text: str) -> str:
    """转义 HTML 特殊字符。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ── 表格降级 ──


def degrade_tables(text: str, max_table_rows: int = 30) -> str:
    """将 Markdown 表格转为代码块，用于不支持表格渲染的平台（Telegram / QQ）。

    检测 ``| ... |`` 格式的表格并替换为等宽代码块，保留可读性。
    P6b: 超长表格自动截断并标注行数。
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        # 检测表格：当前行是 |...|，下一行是分隔行
        if (
            _RE_TABLE_ROW.match(lines[i])
            and i + 1 < n
            and _RE_TABLE_SEP.match(lines[i + 1])
        ):
            table_lines: list[str] = []
            while i < n and (
                _RE_TABLE_ROW.match(lines[i]) or _RE_TABLE_SEP.match(lines[i])
            ):
                table_lines.append(lines[i])
                i += 1
            # 转为代码块（去掉分隔行，保留表头和数据行）
            code_lines: list[str] = []
            for tl in table_lines:
                if _RE_TABLE_SEP.match(tl):
                    code_lines.append("-" * max(len(tl), 20))
                else:
                    code_lines.append(tl)
            # P6b: 截断超长表格
            total_rows = len(code_lines)
            if total_rows > max_table_rows:
                # 保留表头(2行) + 前 max_table_rows-2 行数据
                truncated = code_lines[:max_table_rows]
                omitted = total_rows - max_table_rows
                result.append("```")
                result.extend(truncated)
                result.append(f"... （省略 {omitted} 行）")
                result.append("```")
            else:
                result.append("```")
                result.extend(code_lines)
                result.append("```")
        else:
            result.append(lines[i])
            i += 1

    return "\n".join(result)


# ── 自然语言断句 ──

# 断句优先级（从高到低）：
#   L1: 段落边界 \n\n
#   L2: 换行 + 句末标点（句号/问号/感叹号后的换行）
#   L3: 句末标点（中文：。！？；  英文：. ! ? ; 后跟空格或换行）
#   L4: 逗号级（中文：，、  英文：, 后跟空格）
#   L5: 换行（裸 \n）
# 在代码块内部不断句。

# 句末标点正则：匹配标点及其后的空白
_RE_SENT_END_ZH = re.compile(r"[。！？；]\s*")          # 中文句末
_RE_SENT_END_EN = re.compile(r"[.!?;]\s+")              # 英文句末（需跟空白）
_RE_COMMA_ZH = re.compile(r"[，、]\s*")                  # 中文逗号级
_RE_COMMA_EN = re.compile(r",\s+")                       # 英文逗号级
_RE_NEWLINE_AFTER_SENT = re.compile(r"[。！？；.!?;]\s*\n")  # 句末 + 换行


def _is_inside_code_fence(text: str, pos: int) -> bool:
    """检查 pos 位置是否在代码围栏内部。"""
    fence_open = False
    i = 0
    while i < pos:
        if text[i:i+3] in ("```", "~~~"):
            fence_open = not fence_open
            i += 3
        else:
            i += 1
    return fence_open


def find_sentence_boundary(
    text: str,
    min_pos: int = 0,
    max_pos: int | None = None,
) -> int:
    """在 text[min_pos:max_pos] 范围内寻找最佳自然语言断句位置。

    返回断句位置（切分点之后的字符索引），即 text[:result] 为前半部分。
    找不到合适断点时返回 -1。

    断句优先级：段落 > 句末换行 > 句末标点 > 逗号级 > 裸换行。
    代码块内部的标点不作为断点。
    """
    if max_pos is None:
        max_pos = len(text)
    search = text[min_pos:max_pos]
    if not search:
        return -1

    def _best_match(pattern: re.Pattern, s: str) -> int:
        """找到 s 中最后一个匹配的结束位置。"""
        best = -1
        for m in pattern.finditer(s):
            best = m.end()
        return best

    # L1: 段落边界 \n\n
    idx = search.rfind("\n\n")
    if idx >= 0:
        absolute = min_pos + idx + 2  # 跳过 \n\n
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L2: 句末标点 + 换行
    pos = _best_match(_RE_NEWLINE_AFTER_SENT, search)
    if pos > 0:
        absolute = min_pos + pos
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L3: 中文句末标点
    pos = _best_match(_RE_SENT_END_ZH, search)
    if pos > 0:
        absolute = min_pos + pos
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L3: 英文句末标点（需跟空白，避免切断 URL/数字）
    pos = _best_match(_RE_SENT_END_EN, search)
    if pos > 0:
        absolute = min_pos + pos
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L4: 中文逗号级
    pos = _best_match(_RE_COMMA_ZH, search)
    if pos > 0:
        absolute = min_pos + pos
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L4: 英文逗号级
    pos = _best_match(_RE_COMMA_EN, search)
    if pos > 0:
        absolute = min_pos + pos
        if not _is_inside_code_fence(text, absolute):
            return absolute

    # L5: 裸换行
    idx = search.rfind("\n")
    if idx >= 0:
        absolute = min_pos + idx + 1
        if not _is_inside_code_fence(text, absolute):
            return absolute

    return -1


def has_sentence_boundary(text: str, min_pos: int = 0) -> bool:
    """快速检测 text[min_pos:] 中是否存在自然语言断句点。"""
    return find_sentence_boundary(text, min_pos) > 0


# ── 便捷函数 ──

_default_chunker = SmartChunker()


def smart_chunk(
    text: str,
    max_len: int = 4000,
    output_format: str = "markdown",
) -> list[str]:
    """便捷函数：使用默认 SmartChunker 切分文本。"""
    return _default_chunker.chunk(text, max_len, output_format)
