"""MUTATION SSE contract: identity + contentVersion on the wire."""

from __future__ import annotations

from excelmanus.api_sse import sse_event_to_sse
from excelmanus.events import EventType, ToolCallEvent


def test_mutation_sse_carries_identity_and_version() -> None:
    event = ToolCallEvent(
        event_type=EventType.MUTATION,
        changed_files=["a.xlsx"],
        mutations=[{
            "identity": "a.xlsx",
            "contentVersion": "sha256:abc",
            "source": "runtime",
        }],
    )
    result = sse_event_to_sse(event)
    assert result is not None
    assert "mutation" in result
    assert "a.xlsx" in result
    assert "sha256:abc" in result


def test_legacy_files_changed_still_replays() -> None:
    event = ToolCallEvent(
        event_type=EventType.FILES_CHANGED,
        changed_files=["legacy.xlsx"],
    )
    result = sse_event_to_sse(event)
    assert result is not None
    assert "files_changed" in result
    assert "legacy.xlsx" in result
