"""ToolRuntime — 工具执行流水线（阶段 1–9）。

包住现有 ToolDispatcher / workbook_commit，不重写 openpyxl。
``present_as`` 与执行器用同一谓词：code 模式下直调非 ``run_code``
在策略之前解析为 ``UNKNOWN_TOOL``。
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from excelmanus.engine_core.tool_result import (
    ToolError,
    ToolResult,
    coerce_legacy_result,
    finalize_content,
)
from excelmanus.engine_types import ToolCallResult, _ToolCallBatch
from excelmanus.tools.policy import is_concurrency_safe as policy_is_concurrency_safe

PreExecuteDecision = Literal["allow", "deny", "ask"]
RUN_CODE_NAME = "run_code"
UNKNOWN_TOOL = "UNKNOWN_TOOL"

PreExecuteHook = Callable[["ExecutionToken"], PreExecuteDecision]
GuardHook = Callable[["ExecutionToken", PreExecuteDecision], PreExecuteDecision]
PostExecuteHook = Callable[["ExecutionToken", ToolCallResult], ToolCallResult | None]


@dataclass(frozen=True)
class ExecutionToken:
    """一次调用的冻结身份。Code Mode 子调用带 parent。"""

    call_id: str
    name: str
    arguments: dict[str, Any]
    parent: str | None = None


def preferred_present_as(value: str | None) -> str:
    """Store preference only. Plan/read still force native via ``present_as_of``."""
    raw = str(value or "native").strip().lower()
    if raw in {"code", "both"}:
        return "code"
    return "native"


def normalize_present_as(value: str | None, *, chat_mode: str = "write") -> str:
    """``native|code`` only. Plan/read force native. Legacy ``both`` maps to code in write."""
    chat = str(chat_mode or "write")
    if chat in {"read", "plan"}:
        return "native"
    return preferred_present_as(value)


def present_as_of(engine: Any) -> str:
    chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
    return normalize_present_as(getattr(engine, "_present_as", None), chat_mode=chat)


def set_present_as_preference(engine: Any, value: str | None) -> str:
    preferred = preferred_present_as(value)
    engine._present_as = preferred
    engine._tools_cache = None
    return preferred


def catalog_allows(name: str, present_as: str) -> bool:
    """组装期与执行器共用的直调谓词（不含 parent 豁免）。"""
    if present_as == "code":
        return name == RUN_CODE_NAME
    return True


def is_direct_call_allowed(
    name: str,
    *,
    present_as: str,
    parent: str | None,
) -> bool:
    if parent:
        return True
    return catalog_allows(name, present_as)


def unknown_tool_message(name: str) -> str:
    return (
        f"只能直接调用 `{RUN_CODE_NAME}` — "
        f"请在 `{RUN_CODE_NAME}` 程序内部调用 `{name}`。"
    )


def schema_tool_name(schema: dict[str, Any]) -> str:
    func = schema.get("function")
    if isinstance(func, dict) and func.get("name"):
        return str(func["name"])
    return str(schema.get("name") or "")


def collapse_schemas(
    schemas: Sequence[dict[str, Any]],
    present_as: str,
) -> list[dict[str, Any]]:
    if present_as != "code":
        return list(schemas)
    return [schema for schema in schemas if schema_tool_name(schema) == RUN_CODE_NAME]


class ToolRuntime:
    """固定阶段的执行入口。阶段 6 仍委托现有 dispatcher。"""

    def __init__(self, dispatcher: Any, engine: Any | None = None) -> None:
        self.dispatcher = dispatcher
        self.engine = engine if engine is not None else getattr(dispatcher, "_engine", None)
        self._pre_hooks: list[PreExecuteHook] = []
        self._guards: list[GuardHook] = []
        self._post_hooks: list[PostExecuteHook] = []
        self._token_seq = 0

    @property
    def present_as(self) -> str:
        return present_as_of(self.engine)

    def present(self, mode: str) -> None:
        if mode not in {"native", "code"}:
            raise ValueError(f"unknown present_as: {mode}")
        engine = self.engine
        if engine is None:
            raise RuntimeError("present_as 需要 engine")
        chat = str(getattr(engine, "_current_chat_mode", "write") or "write")
        engine._present_as = normalize_present_as(mode, chat_mode=chat)
        engine._tools_cache = None

    def add_pre_execute(self, hook: PreExecuteHook) -> None:
        self._pre_hooks.append(hook)

    def add_guard(self, hook: GuardHook) -> None:
        self._guards.append(hook)

    def add_post_execute(self, hook: PostExecuteHook) -> None:
        self._post_hooks.append(hook)

    def allocate_token(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str = "",
        parent: str | None = None,
    ) -> ExecutionToken:
        self._token_seq += 1
        frozen = copy.deepcopy(arguments) if arguments else {}
        cid = call_id or f"exec_{self._token_seq}"
        return ExecutionToken(call_id=cid, name=name, arguments=frozen, parent=parent)

    def allows_direct(self, name: str, *, parent: str | None = None) -> bool:
        return is_direct_call_allowed(name, present_as=self.present_as, parent=parent)

    def visible_direct_names(self, schemas: Sequence[dict[str, Any]]) -> set[str]:
        return {schema_tool_name(s) for s in collapse_schemas(schemas, self.present_as)}

    def is_concurrency_safe(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        """仅 PARALLELIZABLE_READONLY_TOOLS。autoApprove / MCP 默认放行不授予并行。"""
        if str(tool_name).startswith("mcp_"):
            getter = getattr(self.engine, "get_tool_write_effect", None)
            effect = getter(tool_name) if callable(getter) else "unknown"
            if effect != "none":
                return False
            # write_effect=none 仍须落在只读并行名单；MCP 名默认不在其中。
        try:
            return policy_is_concurrency_safe(tool_name, args) is True
        except Exception:
            return False

    def split_batches(self, tool_calls: Sequence[Any]) -> list[_ToolCallBatch]:
        """按 ``is_concurrency_safe(args)`` 拆相邻并行批。缺省/抛错/False → 独占。"""
        batches: list[_ToolCallBatch] = []
        current_parallel: list[Any] = []
        parse = getattr(self.dispatcher, "parse_arguments", None)
        for tc in tool_calls:
            name, args = self._name_and_args(tc, parse)
            if self.is_concurrency_safe(name, args):
                current_parallel.append(tc)
            else:
                if current_parallel:
                    batches.append(
                        _ToolCallBatch(current_parallel, len(current_parallel) > 1)
                    )
                    current_parallel = []
                batches.append(_ToolCallBatch([tc], False))
        if current_parallel:
            batches.append(_ToolCallBatch(current_parallel, len(current_parallel) > 1))
        return batches

    def reclassify_batch(self, tool_calls: Sequence[Any]) -> list[_ToolCallBatch]:
        """启动前重新分类。"""
        return self.split_batches(tool_calls)

    def render_sdk_section(self) -> str:
        engine = self.engine
        registry = getattr(engine, "registry", None) or getattr(engine, "_registry", None)
        getter = getattr(registry, "get_all_tools", None)
        if not callable(getter):
            return ""
        try:
            tool_defs = list(getter() or [])
        except Exception:
            return ""
        from excelmanus.code_mode import render_sdk_section

        return render_sdk_section(tool_defs)

    async def execute(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: Any,
        iteration: int,
        route_result: Any | None = None,
        skip_start_event: bool = False,
    ) -> ToolCallResult:
        parse = getattr(self.dispatcher, "parse_arguments", None)
        name, args = self._name_and_args(tc, parse)
        parent = getattr(tc, "parent_call_id", None) or None
        if isinstance(parent, str) and not parent.strip():
            parent = None
        token = self.allocate_token(
            name,
            args,
            call_id=str(getattr(tc, "id", "") or ""),
            parent=parent,
        )

        # 2. Code Mode 坍缩（策略之前）
        if not is_direct_call_allowed(name, present_as=self.present_as, parent=parent):
            return self._apply_finalize(self._unknown_result(tc, token), token)

        # 3. pre_execute：只返回 allow | deny | ask，禁止改参数
        snapshot = copy.deepcopy(token.arguments)
        decision: PreExecuteDecision = "allow"
        for hook in self._pre_hooks:
            raw = hook(token)
            if raw not in {"allow", "deny", "ask"}:
                raw = "deny"
            decision = raw
            if token.arguments != snapshot:
                token = ExecutionToken(
                    call_id=token.call_id,
                    name=token.name,
                    arguments=snapshot,
                    parent=token.parent,
                )
            if decision == "deny":
                break

        # 4. ask → 本次一次性审批；通道缺失 = 拒绝该调用
        if decision == "ask":
            if not await self._one_shot_ask(token):
                decision = "deny"
            else:
                decision = "allow"

        # 5. 单调 guard：只能维持或升级为 deny
        for guard in self._guards:
            nxt = guard(token, decision)
            if nxt not in {"allow", "deny", "ask"}:
                nxt = "deny"
            if decision == "deny" and nxt == "allow":
                nxt = "deny"
            if nxt == "deny":
                decision = "deny"

        if decision == "deny":
            message = getattr(self.engine, "_last_guard_deny", None) or "工具调用被拒绝。"
            engine = self.engine
            if engine is not None and getattr(engine, "_last_guard_deny", None):
                engine._last_guard_deny = None
            return self._apply_finalize(
                self._denied_result(tc, token, "PRE_EXECUTE_DENIED", str(message)),
                token,
            )

        # 6. 现有 dispatcher（超时/重试/workbook_commit 仍在里面）
        tcr = await self.dispatcher.execute(
            tc,
            tool_scope,
            on_event,
            iteration,
            route_result=route_result,
            skip_start_event=skip_start_event,
        )
        if not isinstance(tcr, ToolCallResult):
            structured = coerce_legacy_result(tcr)
            tcr = ToolCallResult(
                tool_name=name,
                arguments=dict(token.arguments),
                result=structured.model_text,
                success=structured.success,
                error=structured.error.code if structured.error else None,
                structured=structured,
            )

        # 7. post_execute
        for hook in self._post_hooks:
            updated = hook(token, tcr)
            if updated is not None:
                tcr = updated

        # 8–9. finalize_content + 冻结给模型的文本
        return self._apply_finalize(tcr, token)

    def _apply_finalize(self, tcr: ToolCallResult, token: ExecutionToken) -> ToolCallResult:
        structured = tcr.structured
        if structured is None:
            structured = coerce_legacy_result(tcr.result)
        cap = 0
        engine = self.engine
        config = getattr(engine, "config", None) or getattr(engine, "_config", None)
        if config is not None:
            cap = int(getattr(config, "tool_result_hard_cap_chars", 0) or 0)
        structured = finalize_content(structured, max_chars=cap)
        error = tcr.error
        if not tcr.success and structured.error is not None and not error:
            error = structured.error.code
        return replace(
            tcr,
            result=structured.model_text,
            structured=structured,
            error=error,
            arguments=dict(token.arguments) if not tcr.arguments else tcr.arguments,
        )

    def _unknown_result(self, tc: Any, token: ExecutionToken) -> ToolCallResult:
        message = unknown_tool_message(token.name)
        structured = ToolResult(
            success=False,
            model_text=message,
            value={"status": "error", "code": UNKNOWN_TOOL, "message": message},
            error=ToolError(code=UNKNOWN_TOOL, message=message),
        )
        return ToolCallResult(
            tool_name=token.name,
            arguments=dict(token.arguments),
            result=message,
            success=False,
            error=UNKNOWN_TOOL,
            structured=structured,
        )

    def _denied_result(
        self,
        tc: Any,
        token: ExecutionToken,
        code: str,
        message: str,
    ) -> ToolCallResult:
        structured = ToolResult(
            success=False,
            model_text=message,
            value={"status": "error", "code": code, "message": message},
            error=ToolError(code=code, message=message),
        )
        return ToolCallResult(
            tool_name=token.name,
            arguments=dict(token.arguments),
            result=message,
            success=False,
            error=code,
            structured=structured,
        )

    async def _one_shot_ask(self, token: ExecutionToken) -> bool:
        engine = self.engine
        approval = getattr(engine, "approval", None) or getattr(engine, "_approval", None)
        if approval is None:
            return False
        create = getattr(approval, "create_pending", None)
        if not callable(create):
            return False
        try:
            pending = create(
                tool_name=token.name,
                arguments=dict(token.arguments),
            )
        except Exception:
            return False
        resolver = getattr(engine, "_approval_resolver", None)
        if not callable(resolver):
            reject = getattr(approval, "reject_pending", None)
            if callable(reject) and getattr(pending, "approval_id", None):
                try:
                    reject(pending.approval_id)
                except Exception:
                    pass
            return False
        try:
            decision = await resolver(pending)
        except Exception:
            return False
        return str(decision or "").lower() in {"accept", "approved", "allow", "yes"}

    @staticmethod
    def _name_and_args(
        tc: Any,
        parse: Callable[[Any], tuple[dict[str, Any], str | None]] | None,
    ) -> tuple[str, dict[str, Any]]:
        function = getattr(tc, "function", None)
        name = str(getattr(function, "name", "") or "")
        raw = getattr(function, "arguments", None)
        if callable(parse):
            args, _err = parse(raw)
            return name, args if isinstance(args, dict) else {}
        if isinstance(raw, dict):
            return name, raw
        return name, {}
