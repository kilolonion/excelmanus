"""预路由解析逻辑单元测试。"""

from __future__ import annotations

from excelmanus.skillpacks.pre_router import _parse_pre_route_response


def test_parse_pre_route_response_supports_skill_names_list() -> None:
    result = _parse_pre_route_response(
        text='{"skill_names":["chart_basic","format_basic"],"confidence":0.88,"reason":"复合任务"}',
        model_used="router-model",
        latency_ms=12.3,
    )
    assert result.skill_name == "chart_basic"
    assert result.skill_names == ["chart_basic", "format_basic"]
    assert result.confidence == 0.88


def test_parse_pre_route_response_backfills_skill_names_for_legacy_skill_name() -> None:
    result = _parse_pre_route_response(
        text='{"skill_name":"data_basic","confidence":0.7,"reason":"legacy"}',
        model_used="router-model",
        latency_ms=9.0,
    )
    assert result.skill_name == "data_basic"
    assert result.skill_names == ["data_basic"]


def test_parse_pre_route_response_null_skill_keeps_empty_skill_names() -> None:
    result = _parse_pre_route_response(
        text='{"skill_name":null,"confidence":0.5,"reason":"chat"}',
        model_used="router-model",
        latency_ms=4.0,
    )
    assert result.skill_name is None
    assert result.skill_names == []
