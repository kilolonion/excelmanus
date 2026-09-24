"""Exact document slices, heading navigation and reproducible internal citations."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import cached_property
import hashlib
import re
import unicodedata


@dataclass(frozen=True)
class Section:
    anchor: str
    title: str
    level: int
    start: int
    end: int
    hierarchy: tuple[str, ...]


@dataclass(frozen=True)
class Document:
    ref: str
    title: str
    text: str
    kind: str
    source: str
    keywords: str = ""

    @cached_property
    def revision(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:20]

    @cached_property
    def offsets(self) -> tuple[int, ...]:
        return (0, *(match.end() for match in re.finditer("\n", self.text) if match.end() < len(self.text)))

    @cached_property
    def sections(self) -> tuple[Section, ...]:
        """Ignore fenced code; preserve explicit IDs and disambiguate duplicates."""
        headings: list[tuple[str, str, int, int, tuple[str, ...]]] = []
        counts: dict[str, int] = {}
        used: set[str] = set()
        stack: list[tuple[int, str]] = []
        fence = ""
        offset = 0
        for line in self.text.splitlines(keepends=True):
            marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if marker:
                token = marker[1]
                if not fence:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = ""
                offset += len(line)
                continue
            heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line) if not fence else None
            if heading:
                level, title = len(heading[1]), heading[2]
                explicit = re.search(r"\s+\{#([^\s{}]+)\}$", title)
                anchor = explicit[1] if explicit else re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", unicodedata.normalize("NFKC", title).casefold()).strip("-")
                if explicit:
                    title = title[:explicit.start()].strip()
                anchor = anchor or "section"
                base = anchor
                counts[base] = counts.get(base, 0) + 1
                if counts[base] > 1:
                    anchor = base + "-" + str(counts[base])
                while anchor in used:
                    counts[base] += 1
                    anchor = base + "-" + str(counts[base])
                used.add(anchor)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                headings.append((anchor, title, level, offset, tuple(value for _, value in stack)))
            offset += len(line)
        return tuple(Section(anchor, title, level, start,
                             next((h[3] for h in headings[i + 1:] if h[2] <= level), len(self.text)), hierarchy)
                     for i, (anchor, title, level, start, hierarchy) in enumerate(headings))

    def bounds(self, anchor: str = "", line_start: int | None = None,
               line_end: int | None = None) -> tuple[int, int]:
        start, end = 0, len(self.text)
        if anchor:
            section = next((s for s in self.sections if s.anchor == anchor.lstrip("#")), None)
            if section is None:
                raise LookupError("章节不存在；请先查询 knowledge_toc。")
            start, end = section.start, section.end
        if line_start is not None or line_end is not None:
            lo = 1 if line_start is None else line_start
            hi = len(self.offsets) if line_end is None else line_end
            if (isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int)
                    or not isinstance(hi, int) or lo < 1 or hi < lo or hi > len(self.offsets)):
                raise ValueError("行号必须为正文内的 1-based 闭区间。")
            start, end = max(start, self.offsets[lo - 1]), min(end, self.offsets[hi] if hi < len(self.offsets) else len(self.text))
            if start >= end and self.text:
                raise ValueError("所选行不在该章节内。")
        return start, end

    def citation(self, start: int = 0, end: int | None = None) -> dict:
        end = len(self.text) if end is None else end
        first = max(0, bisect_right(self.offsets, start) - 1)
        last = max(first, bisect_right(self.offsets, max(start, end - 1)) - 1)
        return {"ref": self.ref, "title": self.title, "source": self.source,
                "content_revision": self.revision, "line_start": first + 1, "line_end": last + 1,
                "start_column": start - self.offsets[first] + 1,
                "end_column_exclusive": end - self.offsets[last] + 1}

    def passages(self, size: int = 1200):
        """Non-overlapping, lossless windows, ending at a newline when possible."""
        starts = sorted({0, len(self.text), *(s.start for s in self.sections)})
        for block_start, block_end in zip(starts, starts[1:]):
            start = block_start
            while start < block_end:
                end = min(block_end, start + size)
                if end < block_end:
                    newline = self.text.rfind("\n", start + size // 2, end)
                    if newline >= 0:
                        end = newline + 1
                section = next((s for s in reversed(self.sections) if s.start <= start < s.end), None)
                yield start, end, section
                start = end


def find_literal(document: Document, query: str, *, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    """Literal case-insensitive search. No caller regex or casefold-offset drift."""
    end = len(document.text) if end is None else end
    if not query:
        raise ValueError("文档内查找需要非空 query，并用 ref 指定文档。")
    return [(match.start(), match.end()) for match in
            re.compile(re.escape(query), re.IGNORECASE).finditer(document.text, start, end)]
