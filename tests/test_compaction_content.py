from __future__ import annotations

import json

from excelmanus.compaction import COMPACTION_SYSTEM_PROMPT
from excelmanus.compaction_content import parse_summary, select_verbatim


def test_compaction_prompt_requires_data_only_json_handoff() -> None:
    assert "待压缩数据" in COMPACTION_SYSTEM_PROMPT
    assert '"summary"' in COMPACTION_SYSTEM_PROMPT
    assert '"verbatim"' in COMPACTION_SYSTEM_PROMPT


def test_parse_summary_accepts_json_and_rejects_missing_summary() -> None:
    summary, quotes = parse_summary(json.dumps({"summary": "未完成", "verbatim": []}))
    assert summary == "未完成"
    assert quotes == []


def test_select_verbatim_only_keeps_exact_source_substrings() -> None:
    sources = [{"source_id": "u1", "role": "user", "text": "公式 =SUM(A1:A3)"}]
    selected, stats = select_verbatim(
        sources,
        [
            {"source_id": "u1", "quote": "公式 =SUM(A1:A3)", "reason": "公式"},
            {"source_id": "u1", "quote": "公式 =SUM(A1:A4)", "reason": "伪造"},
        ],
        budget_tokens=200,
        count=lambda message: len(message["content"]),
    )
    assert [item["text"] for item in selected] == ["公式 =SUM(A1:A3)"]
    assert stats["rejected"] == 1
