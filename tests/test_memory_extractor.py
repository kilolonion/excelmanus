"""memory_extractor 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import excelmanus.memory_extractor as memory_extractor_module
from excelmanus.memory_extractor import MemoryExtractor
from excelmanus.memory_models import MemoryCategory


class _FakeCompletions:
    def __init__(self, response_content: object) -> None:
        self._response_content = response_content
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._response_content)
                )
            ]
        )


def _make_extractor(response_content: object) -> tuple[MemoryExtractor, _FakeCompletions]:
    completions = _FakeCompletions(response_content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return MemoryExtractor(client=client, model="test-model"), completions


class TestParseResponse:
    def test_parse_code_fence_json(self) -> None:
        raw = """```json
[
  {"content": "用户偏好蓝色图表", "category": "user_pref"}
]
```"""
        entries = MemoryExtractor._parse_response(raw)
        assert len(entries) == 1
        assert entries[0].category == MemoryCategory.USER_PREF
        assert entries[0].content == "用户偏好蓝色图表"

    def test_parse_skips_blank_content(self) -> None:
        raw = '[{"content": "   ", "category": "general"}]'
        entries = MemoryExtractor._parse_response(raw)
        assert entries == []

    def test_parse_deduplicates_entries(self) -> None:
        raw = (
            "["
            '{"content": "同一条", "category": "general"},'
            '{"content": "同一条", "category": "general"}'
            "]"
        )
        entries = MemoryExtractor._parse_response(raw)
        assert len(entries) == 1
        assert entries[0].content == "同一条"

    def test_parse_accepts_non_string_response_content(self) -> None:
        raw = [{"content": "结构化返回", "category": "general"}]
        entries = MemoryExtractor._parse_response(raw)
        assert len(entries) == 1
        assert entries[0].content == "结构化返回"


class TestPrepareMessages:
    def test_prepare_filters_system_tool_and_blank(self) -> None:
        extractor, _ = _make_extractor("[]")
        normalized = extractor._prepare_messages(
            [
                {"role": "system", "content": "系统提示"},
                {"role": "tool", "content": "工具结果"},
                {"role": "assistant", "content": "   "},
                {"role": "user", "content": "用户问题"},
                {"role": "assistant", "content": "助手回答"},
            ]
        )
        assert normalized == [
            ("user", "用户问题"),
            ("assistant", "助手回答"),
        ]

    def test_prepare_supports_list_content(self) -> None:
        extractor, _ = _make_extractor("[]")
        normalized = extractor._prepare_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "第一段"},
                        {"text": "第二段"},
                    ],
                }
            ]
        )
        assert normalized == [("assistant", "第一段\n第二段")]

    def test_prepare_enforces_total_token_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        extractor, _ = _make_extractor("[]")
        monkeypatch.setattr(memory_extractor_module, "_MAX_TOTAL_CHARS", 10_000)
        monkeypatch.setattr(memory_extractor_module, "_MAX_TOTAL_TOKENS", 8)
        monkeypatch.setattr(
            MemoryExtractor,
            "_count_tokens",
            staticmethod(lambda text: len(text)),
        )

        normalized = extractor._prepare_messages(
            [
                {"role": "user", "content": "aaaa"},
                {"role": "assistant", "content": "bbbb"},
                {"role": "user", "content": "cccc"},
            ]
        )
        # 预算仅允许保留最近两条（4+4 token）
        assert normalized == [("assistant", "bbbb"), ("user", "cccc")]


@pytest.mark.asyncio
async def test_extract_excludes_system_and_tool_from_prompt() -> None:
    extractor, completions = _make_extractor("[]")

    await extractor.extract(
        [
            {"role": "system", "content": "系统内容"},
            {"role": "tool", "content": "工具结果"},
            {"role": "user", "content": "用户消息"},
            {"role": "assistant", "content": "助手消息"},
        ]
    )

    payload = completions.last_kwargs
    assert payload is not None
    prompt_text = payload["messages"][1]["content"]
    assert "[system]" not in prompt_text
    assert "[tool]" not in prompt_text
    assert "[user]: 用户消息" in prompt_text
    assert "[assistant]: 助手消息" in prompt_text
