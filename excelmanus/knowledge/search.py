"""Local passage retrieval: BM25, bilingual aliases and exact identifier boosts.

Indexes are request-local and contain only authorized documents; no embedding
service, external model or process-global copy of a session's private content.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import unicodedata

from excelmanus.knowledge.reading import Document, Section

_STOP = frozenset("a an the and or is are be to for of in on how what can i do please with using 如何 怎么 怎样 是否 可以 需要 使用 支持 的了 吗呢".split())
_ALIASES = (
    ("approval", "permission", "审批", "权限"), ("configuration", "settings", "config", "配置", "设置"),
    ("read", "读取", "观察"), ("write", "edit", "修改", "编辑", "写入"),
    ("render", "preview", "渲染", "预览"), ("formula", "recalculate", "公式", "重算"),
    ("version", "conflict", "版本", "冲突"), ("memory", "记忆"),
    ("context", "compaction", "上下文", "压缩"), ("skills", "skill", "技能"),
    ("delegate", "subagent", "委派", "子代理"), ("background", "后台"),
    ("freeze", "frozen", "冻结"), ("examples", "example", "示例"),
    ("architecture", "设计", "架构"), ("workflow", "流程"),
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def terms(text: str) -> list[str]:
    result = []
    for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalize(text)):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 2:
            result.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.append(word)
        if "_" in word:
            result.extend(word.split("_"))
    return [word for word in result if word not in _STOP]


@dataclass(frozen=True)
class Hit:
    document: Document
    start: int
    end: int
    section: Section | None
    score: float
    matched_terms: tuple[str, ...]


def rank(documents: list[Document], query: str) -> list[Hit]:
    primary = set(terms(query))
    if not primary:
        return []
    expanded = set()
    raw_query = normalize(query)
    identifier = re.fullmatch(r"[a-z][a-z0-9_.:-]*_[a-z0-9_.:-]*", raw_query) is not None
    for group in _ALIASES:
        if any((term in raw_query if re.search(r"[\u4e00-\u9fff]", term)
                else term in primary) for term in group):
            expanded.update(terms(" ".join(group)))
    expanded -= primary
    rows = []
    frequency: Counter = Counter()
    for doc in documents:
        for start, end, section in doc.passages():
            body = doc.text[start:end]
            counts = Counter(terms(body))
            title_terms = set(terms(doc.title + " " + (section.title if section else "")))
            hints = set(terms(doc.keywords))
            rows.append((doc, start, end, section, counts, title_terms, hints, len(terms(body))))
            frequency.update(set(counts) | title_terms | hints)
    average = sum(row[-1] for row in rows) / max(1, len(rows))
    best: dict[str, Hit] = {}
    for doc, start, end, section, counts, title_terms, hints, length in rows:
        available = set(counts) | title_terms | hints
        if identifier and raw_query not in normalize(doc.ref + " " + doc.title + " " + doc.text[start:end]):
            continue
        matches = primary & available
        alternatives = expanded & available
        if not matches and not alternatives:
            continue
        # Reject accidental one-bigram overlaps in long unrelated questions.
        if len(matches) / len(primary) < 0.2 and not alternatives:
            continue
        score = 0.0
        for term in matches | alternatives:
            tf = counts[term] + 3 * (term in title_terms) + 0.5 * (term in hints)
            idf = math.log(1 + (len(rows) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            weight = 1.0 if term in primary else 0.3
            score += weight * idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(1, average)))
        if raw_query == normalize(doc.ref) or raw_query == normalize(doc.title):
            score += 40
        elif raw_query in normalize(doc.title):
            score += 10
        if raw_query in normalize(doc.text[start:end]):
            score += 6
        score *= 0.5 + 0.5 * len(matches) / len(primary)
        hit = Hit(doc, start, end, section, round(score, 6), tuple(sorted(matches | alternatives)))
        if doc.ref not in best or hit.score > best[doc.ref].score:
            best[doc.ref] = hit
    return sorted(best.values(), key=lambda hit: (-hit.score, hit.document.ref))
