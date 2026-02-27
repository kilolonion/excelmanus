"""Skillpack 参数解析与占位符替换测试。"""

from __future__ import annotations

from excelmanus.skillpacks.arguments import parse_arguments, substitute


class TestParseArguments:
    def test_parse_empty_returns_empty_list(self) -> None:
        assert parse_arguments("") == []
        assert parse_arguments("   ") == []

    def test_parse_space_separated_arguments(self) -> None:
        assert parse_arguments("a b c") == ["a", "b", "c"]

    def test_parse_double_and_single_quotes(self) -> None:
        raw = '"销售 数据.xlsx" bar \'月份 列\''
        assert parse_arguments(raw) == ["销售 数据.xlsx", "bar", "月份 列"]

    def test_parse_unclosed_quote_treats_rest_as_one_argument(self) -> None:
        raw = '"销售 数据.xlsx bar 月份'
        assert parse_arguments(raw) == ["销售 数据.xlsx bar 月份"]


class TestSubstitute:
    def test_replace_arguments_and_positional_placeholders(self) -> None:
        template = "全部=$ARGUMENTS, 第1=$0, 第2=$ARGUMENTS[1], 第3=$2"
        args = ["a", "b"]
        assert substitute(template, args) == "全部=a b, 第1=a, 第2=b, 第3="

    def test_out_of_range_indexes_replace_with_empty_string(self) -> None:
        template = "$5-$ARGUMENTS[9]"
        assert substitute(template, ["x"]) == "-"

    def test_no_arguments_replace_all_placeholders_with_empty(self) -> None:
        template = "$ARGUMENTS|$0|$ARGUMENTS[1]"
        assert substitute(template, []) == "||"

    def test_template_without_placeholders_keeps_original_text(self) -> None:
        template = "  原始文本，不应变化  "
        assert substitute(template, ["a", "b"]) == template

    def test_whitespace_only_after_replace_returns_empty_string(self) -> None:
        assert substitute("  $0  ", []) == ""
