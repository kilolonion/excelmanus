from __future__ import annotations

import pytest

from excelmanus.engine import _contains_formula_advice


@pytest.mark.parametrize(
    "formula",
    [
        "=SUM(A1:A10)",
        "=LARGE(A1:A10,2)",
        "=TEXTJOIN(\",\",TRUE,A1:A10)",
        "=LET(x,1,x+1)",
        "=TEXTSPLIT(A1,\",\")",
        "=XMATCH(\"k\",A:A)",
        "=VSTACK(A1:B2,D1:E2)",
        "=SEQUENCE(10)",
        "=FILTER(A1:C100,C1:C100>0)",
        "=SORT(A1:C100,1,1)",
        "=UNIQUE(A1:A100)",
        "=LAMBDA(x,x+1)(1)",
        "=CHOOSECOLS(A1:C10,1,3)",
        "=CHOOSEROWS(A1:C10,1,3)",
        "=HSTACK(A1:B2,D1:E2)",
    ],
)
def test_contains_formula_advice_supports_classic_and_modern_excel_functions(formula: str) -> None:
    reply_text = f"你可以使用公式 {formula} 来完成。"
    assert _contains_formula_advice(reply_text) is True


@pytest.mark.parametrize(
    "text",
    [
        "这是普通描述，不包含公式函数调用。",
        "在 Python 里，a == sum(values) 不是 Excel 公式。",
    ],
)
def test_contains_formula_advice_does_not_match_non_formula_text(text: str) -> None:
    assert _contains_formula_advice(text) is False
