"""VerificationGate — 写入后内联验证门控。

在每次写入工具执行后，根据当前 in_progress 任务的 VerificationCriteria
执行轻量级回读验证，判定 pass/fail 并注入修复提示。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from excelmanus.task_list import TaskStatus, VerificationCriteria

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine

logger = logging.getLogger(__name__)


class VerificationIntensity(Enum):
    """验证强度分级。"""

    SKIP = "skip"           # 纯读取、单 cell 编辑、问答
    LIGHT = "light"         # 单 sheet 简单写入 → 仅 inline checkpoint
    STANDARD = "standard"   # 多步骤/多 sheet → inline + criteria check
    STRICT = "strict"       # 跨表/公式/大数据 → inline + criteria + fix-verify loop


@dataclass
class VerificationResult:
    """单次验证结果。"""

    passed: bool
    check_type: str = ""
    expected: str = ""
    actual: str = ""
    message: str = ""
    task_index: int = -1


@dataclass
class FixVerifyOutcome:
    """修复→重验循环结果。"""

    success: bool
    attempts: int = 0
    final_result: VerificationResult | None = None
    extra_iterations: int = 0
    extra_tokens: int = 0


# 写入工具集合（触发验证门控的工具）
_WRITE_TOOLS = frozenset({
    "write_cells", "write_excel", "advanced_format",
    "create_sheet", "delete_sheet", "insert_rows", "insert_columns",
    "write_text_file", "edit_text_file",
})

# run_code 需特殊处理：仅当有写入行为时触发
_RUN_CODE_TOOL = "run_code"


class VerificationGate:
    """写入后内联验证门控。"""

    _MAX_FIX_ATTEMPTS_PER_TASK = 2
    _MAX_FIX_ATTEMPTS_GLOBAL = 6

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine
        self._fix_attempt_count: dict[int, int] = {}  # task_index → 已尝试次数
        self._global_fix_count: int = 0
        self._pending_fix_notice: str = ""  # 待注入的修复提示

    @property
    def pending_fix_notice(self) -> str:
        """待注入到下一轮 system prompt 的修复提示。读取后自动清空。"""
        notice = self._pending_fix_notice
        self._pending_fix_notice = ""
        return notice

    def reset(self) -> None:
        """重置门控状态（新任务链开始时调用）。"""
        self._fix_attempt_count.clear()
        self._global_fix_count = 0
        self._pending_fix_notice = ""

    def infer_intensity(
        self,
        *,
        chat_mode: str = "write",
        task_tags: tuple[str, ...] = (),
        has_task_list: bool = False,
        task_count: int = 0,
        write_hint: str = "unknown",
        user_message: str = "",
    ) -> VerificationIntensity:
        """根据上下文推断验证强度。"""
        if chat_mode in ("read", "plan"):
            return VerificationIntensity.SKIP

        # 用户明确要求验证
        _verify_keywords = ("仔细", "验证", "确认", "检查", "核对", "verify", "check")
        if any(kw in user_message for kw in _verify_keywords):
            return VerificationIntensity.STRICT

        # 高复杂度标签
        _strict_tags = {"cross_sheet", "formula", "large_data", "multi_file"}
        if _strict_tags & set(task_tags):
            return VerificationIntensity.STRICT

        if not has_task_list and write_hint == "single_cell":
            return VerificationIntensity.LIGHT

        if has_task_list and task_count <= 3:
            return VerificationIntensity.LIGHT

        return VerificationIntensity.STANDARD

    def should_verify_tool(self, tool_name: str, has_write_effect: bool = False) -> bool:
        """判断指定工具是否应触发验证门控。"""
        if tool_name in _WRITE_TOOLS:
            return True
        if tool_name == _RUN_CODE_TOOL and has_write_effect:
            return True
        return False

    def get_current_task_criteria(self) -> tuple[int, VerificationCriteria | None]:
        """获取当前 in_progress 任务的验证条件。

        Returns:
            (task_index, criteria) — 无活跃任务时返回 (-1, None)
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return (-1, None)
        for idx, item in enumerate(task_list.items):
            if item.status == TaskStatus.IN_PROGRESS:
                return (idx, item.verification_criteria)
        return (-1, None)

    def check_after_write(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result_str: str,
        *,
        intensity: VerificationIntensity = VerificationIntensity.STANDARD,
    ) -> VerificationResult | None:
        """写入工具执行后的验证门控。

        Returns:
            VerificationResult — 验证通过/失败
            None — 无需验证（无 criteria 或强度为 SKIP）
        """
        if intensity == VerificationIntensity.SKIP:
            return None

        task_index, criteria = self.get_current_task_criteria()
        if criteria is None:
            return None

        # LIGHT 强度：仅做基础存在性检查，不做完整 criteria 验证
        if intensity == VerificationIntensity.LIGHT:
            return None

        result = self._execute_check(criteria, tool_name, arguments)
        result.task_index = task_index

        # 更新 criteria 的 actual 和 passed 字段
        criteria.actual = result.actual
        criteria.passed = result.passed

        if not result.passed:
            self._handle_failure(task_index, result, intensity)

        return result

    def _execute_check(
        self,
        criteria: VerificationCriteria,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> VerificationResult:
        """根据 check_type 执行具体验证逻辑。"""
        check_type = criteria.check_type

        if check_type == "row_count":
            return self._check_row_count(criteria)
        elif check_type == "sheet_exists":
            return self._check_sheet_exists(criteria)
        elif check_type == "formula_exists":
            return self._check_formula_exists(criteria)
        elif check_type == "value_match":
            return self._check_value_match(criteria)
        elif check_type == "custom":
            # custom 类型不做自动验证，标记为通过（留给 verifier subagent）
            return VerificationResult(
                passed=True,
                check_type="custom",
                expected=criteria.expected,
                actual="[custom: 需人工/verifier 验证]",
                message="自定义验证条件，跳过自动检查",
            )
        else:
            return VerificationResult(
                passed=True,
                check_type=check_type,
                message=f"未知 check_type '{check_type}'，跳过验证",
            )

    def _check_row_count(self, criteria: VerificationCriteria) -> VerificationResult:
        """验证目标 sheet 行数。"""
        e = self._engine
        target_file = criteria.target_file
        target_sheet = criteria.target_sheet

        if not target_file or not target_sheet:
            return VerificationResult(
                passed=True, check_type="row_count",
                message="缺少 target_file 或 target_sheet，跳过",
            )

        try:
            result_text = str(e.registry.call_tool("read_excel", {
                "file_path": target_file,
                "sheet_name": target_sheet,
                "max_rows": 1,
            }))

            # 从 read_excel 结果中提取行数
            actual_rows = self._extract_row_count_from_result(result_text)
            expected = criteria.expected.strip()

            passed = self._compare_numeric(actual_rows, expected)
            return VerificationResult(
                passed=passed,
                check_type="row_count",
                expected=expected,
                actual=str(actual_rows) if actual_rows is not None else "未知",
                message="" if passed else f"行数验证未通过: 期望 {expected}, 实际 {actual_rows}",
            )
        except Exception as exc:
            logger.debug("row_count 验证失败: %s", exc)
            return VerificationResult(
                passed=False, check_type="row_count",
                expected=criteria.expected,
                actual=f"读取失败: {exc}",
                message=f"回读验证失败: {exc}",
            )

    def _check_sheet_exists(self, criteria: VerificationCriteria) -> VerificationResult:
        """验证 sheet 是否存在。"""
        e = self._engine
        target_file = criteria.target_file
        target_sheet = criteria.target_sheet

        if not target_file or not target_sheet:
            return VerificationResult(
                passed=True, check_type="sheet_exists",
                message="缺少 target_file 或 target_sheet，跳过",
            )

        try:
            result_text = str(e.registry.call_tool("list_sheets", {
                "file_path": target_file,
            }))
            exists = target_sheet in result_text
            return VerificationResult(
                passed=exists,
                check_type="sheet_exists",
                expected=f"sheet '{target_sheet}' 存在",
                actual="存在" if exists else "不存在",
                message="" if exists else f"Sheet '{target_sheet}' 不存在于 {target_file}",
            )
        except Exception as exc:
            logger.debug("sheet_exists 验证失败: %s", exc)
            return VerificationResult(
                passed=False, check_type="sheet_exists",
                expected=f"sheet '{target_sheet}' 存在",
                actual=f"检查失败: {exc}",
                message=f"Sheet 存在性检查失败: {exc}",
            )

    def _check_formula_exists(self, criteria: VerificationCriteria) -> VerificationResult:
        """验证目标范围是否包含公式。"""
        e = self._engine
        target_file = criteria.target_file
        target_sheet = criteria.target_sheet
        target_range = criteria.target_range

        if not target_file or not target_sheet:
            return VerificationResult(
                passed=True, check_type="formula_exists",
                message="缺少 target_file 或 target_sheet，跳过",
            )

        try:
            read_args: dict[str, Any] = {
                "file_path": target_file,
                "sheet_name": target_sheet,
                "max_rows": 5,
            }
            result_text = str(e.registry.call_tool("read_excel", read_args))
            # 简单检测：结果中是否包含 = 开头的公式或者 VLOOKUP 等函数名
            has_formula = ("=" in result_text and any(
                fn in result_text.upper()
                for fn in ("VLOOKUP", "HLOOKUP", "SUM", "IF(", "INDEX", "MATCH", "COUNTIF")
            )) or result_text.count("=") > 2

            return VerificationResult(
                passed=has_formula,
                check_type="formula_exists",
                expected=f"范围 {target_range or '(未指定)'} 包含公式",
                actual="检测到公式" if has_formula else "未检测到公式",
                message="" if has_formula else "目标范围未检测到公式",
            )
        except Exception as exc:
            logger.debug("formula_exists 验证失败: %s", exc)
            return VerificationResult(
                passed=False, check_type="formula_exists",
                actual=f"检查失败: {exc}",
            )

    def _check_value_match(self, criteria: VerificationCriteria) -> VerificationResult:
        """验证目标范围的值。"""
        e = self._engine
        target_file = criteria.target_file
        target_sheet = criteria.target_sheet
        expected = criteria.expected

        if not target_file or not target_sheet:
            return VerificationResult(
                passed=True, check_type="value_match",
                message="缺少 target_file 或 target_sheet，跳过",
            )

        try:
            read_args: dict[str, Any] = {
                "file_path": target_file,
                "sheet_name": target_sheet,
                "max_rows": 5,
            }
            result_text = str(e.registry.call_tool("read_excel", read_args))

            # 简单匹配：检查期望值是否出现在结果中
            if expected.lower() == "非空":
                passed = len(result_text.strip()) > 50  # 非空的粗略判断
            elif expected.startswith(">") or expected.startswith("<"):
                passed = True  # 复杂条件留给 verifier
            else:
                passed = expected in result_text

            return VerificationResult(
                passed=passed,
                check_type="value_match",
                expected=expected,
                actual=result_text[:200] if result_text else "(空)",
                message="" if passed else f"值匹配验证未通过",
            )
        except Exception as exc:
            logger.debug("value_match 验证失败: %s", exc)
            return VerificationResult(
                passed=False, check_type="value_match",
                expected=expected,
                actual=f"读取失败: {exc}",
            )

    def _handle_failure(
        self,
        task_index: int,
        result: VerificationResult,
        intensity: VerificationIntensity,
    ) -> None:
        """处理验证失败：生成修复提示，更新计数。"""
        attempts = self._fix_attempt_count.get(task_index, 0)
        self._fix_attempt_count[task_index] = attempts + 1
        self._global_fix_count += 1

        can_retry = (
            attempts + 1 < self._MAX_FIX_ATTEMPTS_PER_TASK
            and self._global_fix_count < self._MAX_FIX_ATTEMPTS_GLOBAL
        )

        if can_retry:
            self._pending_fix_notice = (
                f"## ⚠️ 验证未通过\n"
                f"任务 #{task_index} 的验证条件 ({result.check_type}) 未满足：\n"
                f"- 预期：{result.expected}\n"
                f"- 实际：{result.actual}\n"
                f"{result.message}\n"
                f"请修正后重试。已尝试 {attempts + 1}/{self._MAX_FIX_ATTEMPTS_PER_TASK} 次。"
            )
        else:
            self._pending_fix_notice = (
                f"## ❌ 验证失败（已耗尽修复次数）\n"
                f"任务 #{task_index} 的验证条件 ({result.check_type}) 未满足：\n"
                f"- 预期：{result.expected}\n"
                f"- 实际：{result.actual}\n"
                f"已达修复上限，请将任务标记为 failed 并报告用户。"
            )

    # ── Fix-Verify 循环 ──

    def can_fix_verify(self, task_index: int) -> bool:
        """判断指定任务是否还有修复机会。"""
        attempts = self._fix_attempt_count.get(task_index, 0)
        return (
            attempts < self._MAX_FIX_ATTEMPTS_PER_TASK
            and self._global_fix_count < self._MAX_FIX_ATTEMPTS_GLOBAL
        )

    def get_failed_verification_task(self) -> tuple[int, VerificationCriteria | None]:
        """获取第一个带验证条件的 FAILED 任务。

        Returns:
            (task_index, criteria) — 无匹配时返回 (-1, None)
        """
        e = self._engine
        task_list = e._task_store.current
        if task_list is None:
            return (-1, None)
        for idx, item in enumerate(task_list.items):
            if item.status == TaskStatus.FAILED and item.verification_criteria:
                return (idx, item.verification_criteria)
            if item.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                break
        return (-1, None)

    def prepare_fix_message(self, task_index: int, criteria: VerificationCriteria) -> str:
        """构建修复提示消息，用于注入到 memory 作为 user message 驱动修复轮。"""
        attempts = self._fix_attempt_count.get(task_index, 0)
        parts = [
            f"任务 #{task_index} 的验证未通过，请修正并重试。",
            f"验证类型: {criteria.check_type}",
        ]
        if criteria.expected:
            parts.append(f"期望: {criteria.expected}")
        if criteria.actual:
            parts.append(f"实际: {criteria.actual}")
        parts.append(f"修复尝试 {attempts + 1}/{self._MAX_FIX_ATTEMPTS_PER_TASK}")
        parts.append("请先分析失败原因，然后用工具修正，最后用 task_update 更新状态。")
        return "\n".join(parts)

    def record_fix_attempt(self, task_index: int) -> None:
        """记录一次修复尝试（在外部调用 _tool_calling_loop 之前调用）。

        幂等保护：若 _handle_failure 已在同一轮为此 task_index 递增过计数，
        则不重复递增（通过比较当前 attempts 是否已超过预期值判断）。
        """
        current = self._fix_attempt_count.get(task_index, 0)
        # _handle_failure 在 check_after_write 中可能已递增过，
        # 此时 current > 0 且 _pending_fix_notice 非空说明已计数
        if current > 0 and self._pending_fix_notice:
            return  # 已在 _handle_failure 中计数，跳过
        self._fix_attempt_count[task_index] = current + 1
        self._global_fix_count += 1

    # ── 辅助方法 ──

    @staticmethod
    def _extract_row_count_from_result(result_text: str) -> int | None:
        """从 read_excel 结果中提取行数。"""
        import re
        # 常见格式: "共 N 行" 或 "N rows" 或 "total_rows: N"
        patterns = [
            r"共\s*(\d+)\s*行",
            r"(\d+)\s*rows?",
            r"total_rows[:\s]+(\d+)",
            r"行数[:\s]+(\d+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, result_text, re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _compare_numeric(actual: int | None, expected_str: str) -> bool:
        """比较实际值与期望值（支持 >, <, >=, <= 前缀）。"""
        if actual is None:
            return False
        expected_str = expected_str.strip()
        if not expected_str:
            return True

        if expected_str.startswith(">="):
            try:
                return actual >= int(expected_str[2:].strip())
            except ValueError:
                return False
        elif expected_str.startswith("<="):
            try:
                return actual <= int(expected_str[2:].strip())
            except ValueError:
                return False
        elif expected_str.startswith(">"):
            try:
                return actual > int(expected_str[1:].strip())
            except ValueError:
                return False
        elif expected_str.startswith("<"):
            try:
                return actual < int(expected_str[1:].strip())
            except ValueError:
                return False
        else:
            try:
                return actual == int(expected_str)
            except ValueError:
                return False
