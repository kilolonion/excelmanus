"""未执行即被放弃的工具调用：终态、文案与模型侧占位的唯一事实源。

背景：模型服务在流式输出中途断开（incomplete chunked read 等）时，
``run_tool_loop`` 会丢弃这一次"尝试"的流式产物并重试。正文可以 retract，
但已经下发给前端的工具参数（``write_text_file`` / ``edit_text_file`` /
``write_plan`` 的 args delta）不会自动消失——那次调用从未进入执行器，却
会在界面上永远停在"进行中"，用户和模型都无法判断它到底有没有落盘。

本模块把这类调用的状态一次性定案：

- **未执行就放弃**的调用 → 明确的失败终态（``TOOL_CALL_NOT_EXECUTED``，
  永不"进行中"），错误码与失败分类复用 ``error_payload`` 的 canonical 词表；
- **已经在后台运行**的命令类调用 → 保持运行，交给执行器自己的终态事件收口，
  本模块只负责不把它误标成失败；
- 面向模型的悬空 tool_call 占位同样按效果分类：写入 = 结果未确认（按失败给），
  命令 = 可能仍在后台运行，读取 = 未执行。

效果分类复用 ``excelmanus.tools.policy``（工具策略 SSOT），本模块不复制名单；
未登记效果的工具（MCP/自定义）归到 ``unknown``，与写入同口径保守处理，
绝不默认当成只读。
"""

from __future__ import annotations

import json
from typing import Any

from excelmanus.engine_core.error_payload import (
    RESULT_UNCERTAIN,
    TOOL_CALL_NOT_EXECUTED,
    make_error_payload,
)

# ── 放弃原因（机器可读，随 tool_call_aborted 事件下发） ──────────────

ABORT_REASON_RETRY = "llm_retry"               # 尝试失败，同一请求正在重试
ABORT_REASON_FALLBACK = "non_stream_fallback"  # 流式失败，回退为非流式重发
ABORT_REASON_INCOMPLETE = "stream_incomplete"  # 流式产物不完整，未能组装的调用
ABORT_REASON_EXHAUSTED = "retry_exhausted"     # 重试耗尽，本轮失败收尾
ABORT_REASON_FAILURE = "turn_failure"          # 其他不可恢复失败，本轮中止

_REASON_NOTES: dict[str, str] = {
    ABORT_REASON_RETRY: "模型连接中断，已自动重试同一请求",
    ABORT_REASON_FALLBACK: "流式输出中断，已改由非流式请求重发",
    ABORT_REASON_INCOMPLETE: "流式输出不完整，调用参数未能组装完成",
    ABORT_REASON_EXHAUSTED: "模型服务重试后仍未恢复，本轮已停止",
    ABORT_REASON_FAILURE: "本轮失败收尾",
}

# ── 效果分类 ────────────────────────────────────────────────────

EFFECT_WRITE = "write"
EFFECT_COMMAND = "command"
EFFECT_READ = "read"
# 未登记效果的工具（第三方/MCP/自定义注册）：副作用不可知，按最保守口径给状态。
EFFECT_UNKNOWN = "unknown"

# 运行外部命令的工具：执行体在宿主进程/后台任务里，属于"可能仍在跑"的一类。
_COMMAND_TOOLS: frozenset[str] = frozenset({"run_code", "run_shell"})
# 写入效果但不在 MUTATING_ALL_TOOLS 里的工具（计划文件、记忆等）。
_EXTRA_WRITE_TOOLS: frozenset[str] = frozenset({"write_plan", "memory_save"})

_policy_cache: tuple[frozenset[str], frozenset[str]] | None = None


def _policy_sets() -> tuple[frozenset[str], frozenset[str]]:
    """惰性读取工具策略 SSOT：避免 memory/events 层在 import 期拉起工具包。

    Returns:
        (写入类工具, 已知只读工具)
    """
    global _policy_cache
    if _policy_cache is None:
        try:
            from excelmanus.tools.policy import MUTATING_ALL_TOOLS, READ_ONLY_SAFE_TOOLS

            _policy_cache = (frozenset(MUTATING_ALL_TOOLS), frozenset(READ_ONLY_SAFE_TOOLS))
        except Exception:  # pragma: no cover - 策略模块不可用时退化为"不可知"
            _policy_cache = (frozenset(), frozenset())
    return _policy_cache


def classify_tool_effect(tool_name: str) -> str:
    """把工具名归到 write / command / read / unknown 之一。

    只认登记过的工具：写入与只读名单来自 ``tools.policy``，命令类显式列出；
    其余（MCP、自定义注册工具）返回 ``unknown``，调用方按"副作用不可知"处理，
    不能默认当成只读。
    """
    name = str(tool_name or "")
    if name in _COMMAND_TOOLS:
        return EFFECT_COMMAND
    mutating, read_only = _policy_sets()
    if name in _EXTRA_WRITE_TOOLS or name in mutating:
        return EFFECT_WRITE
    if name in read_only:
        return EFFECT_READ
    return EFFECT_UNKNOWN


def abort_reason_note(reason: str) -> str:
    """放弃原因 → 一句可读说明（未知原因退化为通用描述）。"""
    return _REASON_NOTES.get(str(reason or "").strip(), "调用已被放弃")


def aborted_call_message(tool_name: str, reason: str) -> str:
    """未执行即放弃的调用：一句用户/模型都能读懂的确切状态。"""
    effect = classify_tool_effect(tool_name)
    if effect == EFFECT_WRITE:
        fact = "本次写入未执行，目标文件没有被这次调用修改"
    elif effect == EFFECT_COMMAND:
        fact = "本次命令未启动；更早发起、仍在后台运行的命令不受影响"
    elif effect == EFFECT_READ:
        fact = "本次读取未执行，没有产生任何结果"
    else:
        fact = "本次调用未执行，没有产生任何副作用"
    return f"{fact}（{abort_reason_note(reason)}）。"


def aborted_call_payload(tool_name: str, reason: str) -> dict[str, Any]:
    """未执行调用的结构化状态（canonical 形状，供模型/日志/事件共用）。"""
    effect = classify_tool_effect(tool_name)
    return make_error_payload(
        aborted_call_message(tool_name, reason),
        error_code=TOOL_CALL_NOT_EXECUTED,
        executed=False,
        abort_reason=str(reason or ""),
        effect=effect,
    )


def dangling_call_placeholder(tool_name: str) -> str:
    """中断遗留的悬空 tool_call 的 tool result 占位（模型可见，JSON）。

    与"未执行"不同：调用已经进入过执行路径，结果没有回传，所以写入按
    ``RESULT_UNCERTAIN``（结果未确认，按失败处理，不要假定已生效）、命令类
    明确按"可能仍在后台运行"给（不要重复发起）、读取按未执行给。
    """
    effect = classify_tool_effect(tool_name)
    if effect in {EFFECT_WRITE, EFFECT_UNKNOWN}:
        # 写入与"副作用不可知"的自定义工具同口径：结果未确认，按失败给。
        payload: dict[str, Any] = make_error_payload(
            "这次调用的结果没有回传，且不能假定已经生效。继续前先用只读方式核对目标文件/"
            "外部系统的实际状态，再决定是否重新发起。",
            error_code=RESULT_UNCERTAIN,
            remediation="先只读核对实际现状：未生效则重新发起，已生效则不要重放。",
            executed=None,
            effect=effect,
        )
    elif effect == EFFECT_COMMAND:
        payload = {
            "status": "running",
            "error_code": RESULT_UNCERTAIN,
            "failure_class": "internal",
            "message": "这条命令的结果没有回传，进程可能仍在后台运行：不要重复发起同一条命令，"
                       "先用只读方式核对它的输出与副作用。",
            "remediation": "查看后台任务输出，或用只读命令确认结果，再决定下一步。",
            "executed": None,
            "effect": effect,
        }
    else:
        payload = make_error_payload(
            "这次读取没有执行，没有产生任何结果；需要这批数据时重新发起读取。",
            error_code=TOOL_CALL_NOT_EXECUTED,
            remediation="重新发起这次只读调用。",
            executed=False,
            effect=effect,
        )
    return json.dumps(payload, ensure_ascii=False, default=str)
