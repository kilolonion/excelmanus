"""窗口感知小模型协议测试。"""

from excelmanus.window_perception.advisor_context import AdvisorContext
from excelmanus.window_perception.models import PerceptionBudget, Viewport, WindowType
from excelmanus.window_perception.small_model import build_advisor_messages, parse_small_model_plan
from tests.window_factories import make_window


class TestWindowPerceptionSmallModel:
    """小模型消息与解析测试。"""

    def test_build_messages_contains_required_fields(self) -> None:
        windows = [
            make_window(
                id="sheet_1",
                type=WindowType.SHEET,
                title="A",
                file_path="sales.xlsx",
                sheet_name="Q1",
                viewport=Viewport(range_ref="A1:J25", total_rows=5000, total_cols=30),
            )
        ]
        messages = build_advisor_messages(
            windows=windows,
            active_window_id="sheet_1",
            budget=PerceptionBudget(),
            context=AdvisorContext(turn_number=3, task_type="GENERAL_BROWSE"),
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "task_type" in messages[0]["content"]
        assert "sheet_1" in messages[1]["content"]

    def test_parse_valid_json(self) -> None:
        text = (
            '{"task_type":"GENERAL_BROWSE","generated_turn":8,'
            '"advices":[{"window_id":"sheet_1","tier":"background","reason":"idle=1"}]}'
        )
        plan = parse_small_model_plan(text)
        assert plan is not None
        assert plan.task_type == "GENERAL_BROWSE"
        assert plan.generated_turn == 8
        assert plan.source == "small_model"
        assert plan.advices[0].tier == "background"

    def test_parse_json_from_code_fence(self) -> None:
        text = """```json
{"task_type":"DATA_COMPARISON","advices":[{"window_id":"sheet_1","tier":"suspended"}]}
```"""
        plan = parse_small_model_plan(text)
        assert plan is not None
        assert plan.task_type == "DATA_COMPARISON"
        assert plan.advices[0].tier == "suspended"

    def test_parse_invalid_json_returns_none(self) -> None:
        assert parse_small_model_plan("not-json") is None

    def test_parse_invalid_tier_is_filtered(self) -> None:
        text = (
            '{"task_type":"GENERAL_BROWSE","advices":['
            '{"window_id":"sheet_1","tier":"invalid"},'
            '{"window_id":"sheet_2","tier":"active"}'
            "]} "
        )
        plan = parse_small_model_plan(text)
        assert plan is not None
        assert len(plan.advices) == 1
        assert plan.advices[0].window_id == "sheet_2"

    def test_parse_unknown_task_type_returns_none(self) -> None:
        text = '{"task_type":"UNKNOWN","advices":[{"window_id":"sheet_1","tier":"active"}]}'
        assert parse_small_model_plan(text) is None
