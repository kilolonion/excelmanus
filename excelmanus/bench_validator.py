"""Bench 断言校验引擎：对 BenchResult 执行声明式断言规则。

suite JSON 中通过 ``assertions`` 字段声明规则，runner 执行完毕后
调用本模块自动校验，结果嵌入输出 JSON 的 ``validation`` 字段。

支持 **suite 级默认** + **case 级覆盖** 的两层合并策略。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 断言结果 ──────────────────────────────────────────────


@dataclass
class AssertionResult:
    """单条断言的校验结果。"""

    rule: str
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str = ""
    severity: str = "error"  # "error" | "warning"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "rule": self.rule,
            "passed": self.passed,
        }
        if self.expected is not None:
            d["expected"] = self.expected
        if self.actual is not None:
            d["actual"] = self.actual
        if self.message:
            d["message"] = self.message
        if self.severity != "error":
            d["severity"] = self.severity
        return d


@dataclass
class ValidationSummary:
    """一个 case 的断言校验汇总。"""

    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list[AssertionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class SuiteValidationSummary:
    """一个 suite 的断言校验汇总。"""

    total_assertions: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    failed_cases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_assertions": self.total_assertions,
            "passed": self.passed,
            "failed": self.failed,
            "pass_rate": self.pass_rate,
            "failed_cases": self.failed_cases,
        }


# ── 断言合并 ──────────────────────────────────────────────


def merge_assertions(
    suite_assertions: dict[str, Any] | None,
    case_assertions: dict[str, Any] | None,
) -> dict[str, Any]:
    """合并 suite 级和 case 级断言，case 级覆盖 suite 级同名字段。"""
    merged: dict[str, Any] = {}
    if suite_assertions:
        merged.update(suite_assertions)
    if case_assertions:
        merged.update(case_assertions)
    return merged


# ── 校验规则实现 ──────────────────────────────────────────


def _check_status(result_dict: dict[str, Any], expected: str) -> AssertionResult:
    actual = result_dict.get("execution", {}).get("status", "unknown")
    return AssertionResult(
        rule="status",
        passed=actual == expected,
        expected=expected,
        actual=actual,
        message="" if actual == expected else f"状态 {actual!r} != 期望 {expected!r}",
    )


def _check_max_int(
    result_dict: dict[str, Any],
    rule_name: str,
    path: tuple[str, ...],
    limit: int,
) -> AssertionResult:
    """通用的 max_xxx 校验：按 path 从 result_dict 取值，判断 <= limit。"""
    obj = result_dict
    for key in path:
        obj = obj.get(key, {}) if isinstance(obj, dict) else {}
    actual = obj if isinstance(obj, (int, float)) else 0
    passed = actual <= limit
    return AssertionResult(
        rule=rule_name,
        passed=passed,
        expected=f"<= {limit}",
        actual=actual,
        message="" if passed else f"{rule_name}: {actual} 超过上限 {limit}",
    )


def _check_expected_skill(
    result_dict: dict[str, Any],
    expected_skill: str,
) -> AssertionResult:
    skills_used = result_dict.get("execution", {}).get("skills_used", [])
    route_mode = result_dict.get("execution", {}).get("route_mode", "")
    # 在 skills_used 或 route_mode 中匹配
    matched = expected_skill in skills_used or route_mode == expected_skill
    actual_str = ", ".join(skills_used) if skills_used else route_mode
    return AssertionResult(
        rule="expected_skill",
        passed=matched,
        expected=expected_skill,
        actual=actual_str,
        message="" if matched else f"路由未命中期望技能 {expected_skill!r}，实际: {actual_str}",
    )


def _check_required_tools(
    result_dict: dict[str, Any],
    required: list[str],
) -> AssertionResult:
    tool_calls = result_dict.get("artifacts", {}).get("tool_calls", [])
    called_names = {tc.get("tool_name", "") for tc in tool_calls}
    missing = [t for t in required if t not in called_names]
    return AssertionResult(
        rule="required_tools",
        passed=len(missing) == 0,
        expected=required,
        actual=sorted(called_names),
        message="" if not missing else f"缺少必要工具调用: {missing}",
    )


def _check_forbidden_tools(
    result_dict: dict[str, Any],
    forbidden: list[str],
) -> AssertionResult:
    tool_calls = result_dict.get("artifacts", {}).get("tool_calls", [])
    called_names = {tc.get("tool_name", "") for tc in tool_calls}
    violations = [t for t in forbidden if t in called_names]
    return AssertionResult(
        rule="forbidden_tools",
        passed=len(violations) == 0,
        expected=f"不应调用 {forbidden}",
        actual=sorted(called_names),
        message="" if not violations else f"调用了禁止的工具: {violations}",
    )


def _check_no_empty_promise(result_dict: dict[str, Any]) -> AssertionResult:
    """首轮 LLM 响应不应有"空承诺"：content 非空 + 无 tool_calls。"""
    llm_calls = result_dict.get("artifacts", {}).get("llm_calls", [])
    if not llm_calls:
        return AssertionResult(
            rule="no_empty_promise",
            passed=True,
            message="无 LLM 调用记录，跳过检查",
            severity="warning",
        )
    first_resp = llm_calls[0].get("response", {})
    content = first_resp.get("content") or ""
    tool_calls = first_resp.get("tool_calls") or []
    # 空承诺 = 有文字回复但没有工具调用
    is_empty_promise = bool(content.strip()) and not tool_calls
    return AssertionResult(
        rule="no_empty_promise",
        passed=not is_empty_promise,
        expected="首轮应直接行动（tool_calls）或纯文本回复（无需工具时）",
        actual=f"content={len(content)}chars, tool_calls={len(tool_calls)}",
        message="" if not is_empty_promise else "首轮存在空承诺：有文字回复但未发起工具调用",
    )


def _check_reply_contains(
    result_dict: dict[str, Any],
    keywords: list[str],
) -> AssertionResult:
    reply = result_dict.get("result", {}).get("reply", "")
    missing = [kw for kw in keywords if kw not in reply]
    return AssertionResult(
        rule="reply_contains",
        passed=len(missing) == 0,
        expected=keywords,
        actual=reply[:200] if reply else "(空回复)",
        message="" if not missing else f"回复缺少关键词: {missing}",
    )


def _check_reply_not_contains(
    result_dict: dict[str, Any],
    keywords: list[str],
) -> AssertionResult:
    reply = result_dict.get("result", {}).get("reply", "")
    violations = [kw for kw in keywords if kw in reply]
    return AssertionResult(
        rule="reply_not_contains",
        passed=len(violations) == 0,
        expected=f"不应包含 {keywords}",
        actual=reply[:200] if reply else "(空回复)",
        message="" if not violations else f"回复包含不期望的关键词: {violations}",
    )


# ── 主校验函数 ────────────────────────────────────────────


def validate_case(
    result_dict: dict[str, Any],
    assertions: dict[str, Any],
) -> ValidationSummary:
    """对单个用例的输出 dict 执行所有断言规则。

    Args:
        result_dict: ``BenchResult.to_dict()`` 的输出。
        assertions: 合并后的断言规则字典。

    Returns:
        ValidationSummary 包含所有断言结果。
    """
    if not assertions:
        return ValidationSummary()

    results: list[AssertionResult] = []

    # status
    if "status" in assertions:
        results.append(_check_status(result_dict, assertions["status"]))

    # max_iterations
    if "max_iterations" in assertions:
        results.append(_check_max_int(
            result_dict, "max_iterations",
            ("execution", "iterations"), assertions["max_iterations"],
        ))

    # max_llm_calls
    if "max_llm_calls" in assertions:
        results.append(_check_max_int(
            result_dict, "max_llm_calls",
            ("stats", "llm_call_count"), assertions["max_llm_calls"],
        ))

    # max_tool_calls
    if "max_tool_calls" in assertions:
        results.append(_check_max_int(
            result_dict, "max_tool_calls",
            ("stats", "tool_call_count"), assertions["max_tool_calls"],
        ))

    # max_tool_failures
    if "max_tool_failures" in assertions:
        results.append(_check_max_int(
            result_dict, "max_tool_failures",
            ("stats", "tool_failures"), assertions["max_tool_failures"],
        ))

    # max_tokens
    if "max_tokens" in assertions:
        results.append(_check_max_int(
            result_dict, "max_tokens",
            ("stats", "total_tokens"), assertions["max_tokens"],
        ))

    # max_duration_seconds
    if "max_duration_seconds" in assertions:
        results.append(_check_max_int(
            result_dict, "max_duration_seconds",
            ("execution", "duration_seconds"), assertions["max_duration_seconds"],
        ))

    # expected_skill
    if "expected_skill" in assertions:
        results.append(_check_expected_skill(result_dict, assertions["expected_skill"]))

    # required_tools
    if "required_tools" in assertions:
        results.append(_check_required_tools(result_dict, assertions["required_tools"]))

    # forbidden_tools
    if "forbidden_tools" in assertions:
        results.append(_check_forbidden_tools(result_dict, assertions["forbidden_tools"]))

    # no_empty_promise
    if assertions.get("no_empty_promise"):
        results.append(_check_no_empty_promise(result_dict))

    # reply_contains
    if "reply_contains" in assertions:
        results.append(_check_reply_contains(result_dict, assertions["reply_contains"]))

    # reply_not_contains
    if "reply_not_contains" in assertions:
        results.append(_check_reply_not_contains(result_dict, assertions["reply_not_contains"]))

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    return ValidationSummary(
        total=len(results),
        passed=passed,
        failed=failed,
        results=results,
    )


def aggregate_suite_validation(
    case_validations: list[tuple[str, ValidationSummary]],
) -> SuiteValidationSummary:
    """聚合多个 case 的校验结果为 suite 级汇总。

    Args:
        case_validations: [(case_id, ValidationSummary), ...]
    """
    total = sum(v.total for _, v in case_validations)
    passed = sum(v.passed for _, v in case_validations)
    failed = total - passed
    failed_cases = [cid for cid, v in case_validations if v.failed > 0]
    pass_rate = round(passed / total * 100, 1) if total > 0 else 100.0

    return SuiteValidationSummary(
        total_assertions=total,
        passed=passed,
        failed=failed,
        pass_rate=pass_rate,
        failed_cases=failed_cases,
    )
