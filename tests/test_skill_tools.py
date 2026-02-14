"""skill_tools 单元测试。"""

from __future__ import annotations

import pytest

from excelmanus.skillpacks.models import Skillpack
from excelmanus.tools import skill_tools


class _StubLoader:
    """最小化 loader stub。"""

    def __init__(self, skillpacks: dict[str, Skillpack]) -> None:
        self._skillpacks = dict(skillpacks)

    def get_skillpacks(self) -> dict[str, Skillpack]:
        return dict(self._skillpacks)


def _make_skill(
    *,
    name: str,
    description: str,
    triggers: list[str],
    allowed_tools: list[str],
) -> Skillpack:
    return Skillpack(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        triggers=triggers,
        instructions="测试说明",
        source="system",
        root_dir=f"/tmp/{name}",
    )


@pytest.fixture(autouse=True)
def _reset_loader() -> None:
    skill_tools._loader = None
    yield
    skill_tools._loader = None


class TestListSkills:
    """list_skills 输出行为测试。"""

    def test_default_returns_minimal_fields(self) -> None:
        skill = _make_skill(
            name="data_basic",
            description="数据处理",
            triggers=["分析", "统计"],
            allowed_tools=["read_excel", "filter_rows"],
        )
        skill_tools.init_loader(_StubLoader({"data_basic": skill}))

        result = skill_tools.list_skills()
        assert "【data_basic】" in result
        assert "描述：数据处理" in result
        assert "触发词：" not in result
        assert "可用工具：" not in result

    def test_verbose_includes_triggers_and_tools(self) -> None:
        skill = _make_skill(
            name="chart_basic",
            description="图表生成",
            triggers=["图表"],
            allowed_tools=["create_chart"],
        )
        skill_tools.init_loader(_StubLoader({"chart_basic": skill}))

        result = skill_tools.list_skills(verbose=True)
        assert "【chart_basic】" in result
        assert "描述：图表生成" in result
        assert "触发词：图表" in result
        assert "可用工具：create_chart" in result

    def test_empty_skillpacks_returns_hint(self) -> None:
        skill_tools.init_loader(_StubLoader({}))
        assert skill_tools.list_skills() == "当前没有已加载的技能包。"
        assert skill_tools.list_skills(verbose=True) == "当前没有已加载的技能包。"


class TestGetTools:
    """工具定义 schema 测试。"""

    def test_schema_supports_verbose_boolean(self) -> None:
        tools = skill_tools.get_tools()
        assert len(tools) == 1
        tool = tools[0]
        assert tool.name == "list_skills"
        verbose_prop = tool.input_schema["properties"]["verbose"]
        assert verbose_prop["type"] == "boolean"
        assert tool.input_schema["required"] == []

