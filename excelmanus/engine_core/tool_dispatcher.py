"""ToolDispatcher — 从 AgentEngine 解耦的工具调度组件。

负责管理：
- 工具参数解析（JSON string / dict / None）
- 普通工具的 registry 调用（含线程池执行）
- 单个工具调用的完整执行流程（execute）
- 工具结果截断
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from excelmanus.engine_core.tool_errors import (
    DEFAULT_RETRY_POLICY,
    classify_tool_error,
    compact_error,
)
from excelmanus.engine_core.tool_result import (
    ToolError,
    ToolResult,
    ToolUiMeta,
    coerce_legacy_result,
    error_result,
)
from excelmanus.engine_core.workspace_probe import (
    collect_workspace_mtime_index,
    has_workspace_mtime_changes,
)
from excelmanus.hooks import HookDecision, HookEvent
from excelmanus.logger import get_logger, log_tool_call
from excelmanus.security.policy import (
    RESTRICTED_WRITE_EFFECTS,
    is_plan_active,
    resolve_approval_policy,
    writes_denied,
)
from excelmanus.tools.registry import ToolNotAllowedError
from excelmanus.workspace.identity import IdentityError


class _SyntheticToolCall:
    """Code Mode 子调用用的最小 ToolCall 形状。"""

    def __init__(
        self,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        parent_call_id: str | None = None,
    ) -> None:
        self.id = call_id
        self.parent_call_id = parent_call_id
        self.function = type("_Fn", (), {"name": name, "arguments": arguments})()


@dataclass
class _ToolExecOutcome:
    """特殊工具 handler 的结构化返回，收敛副作用信号。"""

    result_str: str
    success: bool
    error: str | None = None
    error_kind: str | None = None  # ToolErrorKind.value: retryable/permanent/needs_human/overflow
    pending_approval: bool = False
    approval_id: str | None = None
    audit_record: Any = None
    pending_question: bool = False
    question_id: str | None = None
    defer_tool_result: bool = False
    finish_accepted: bool = False
    raw_result_str: str | None = None  # 截断前的 model_text，供兼容路径使用
    structured: ToolResult | None = None

if TYPE_CHECKING:
    from excelmanus.engine import AgentEngine
    from excelmanus.events import EventCallback
    from excelmanus.stores.tool_call_store import ToolCallStore

logger = get_logger("tool_dispatcher")

# 单次 run_code 内的嵌套 SDK 调用上限；与回合步数无关。
_CODE_MODE_NESTED_CALL_BUDGET = 128

# 子调用审批等待终态哨兵（区别于正常 decision 值）。
_WAIT_TIMEOUT = object()
_WAIT_PARENT_CANCELLED = object()


def _image_content_hash(raw_bytes: bytes) -> str:
    """计算图片内容的稳定 hash（全文 sha256，截取前 16 hex）。

    所有图片去重均应使用此函数，
    确保同一张图片在不同代码路径产生相同 hash。
    """
    import hashlib
    return hashlib.sha256(raw_bytes).hexdigest()[:16]


def _image_content_hash_b64(b64_str: str) -> str:
    """从 base64 编码字符串计算图片内容 hash（先解码为原始字节）。

    如果 base64 解码失败（如数据不完整），回退到直接 hash 字符串字节。
    """
    import base64 as _b64
    try:
        raw = _b64.b64decode(b64_str, validate=True)
    except Exception:
        # 容错：无法解码时直接 hash 原始字符串
        raw = b64_str.encode("utf-8") if isinstance(b64_str, str) else b64_str
    return _image_content_hash(raw)


_REPEAT_REMINDER_THRESHOLDS = (3, 5, 8)


def _canonical_args(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _example_from_structured(structured: ToolResult | None) -> Any:
    """从上次失败回执里取出可复制示例（若有）。"""
    if structured is None:
        return None
    value = structured.value if isinstance(getattr(structured, "value", None), dict) else None
    if isinstance(value, dict) and value.get("example") is not None:
        return value["example"]
    error = getattr(structured, "error", None)
    fields = getattr(error, "fields", None) if error is not None else None
    if isinstance(fields, dict) and fields.get("example") is not None:
        return fields["example"]
    return None


def _identical_failure_result(
    *,
    previous_message: str,
    example: Any = None,
) -> ToolResult:
    """同参失败硬拦截：不执行工具，把上次错误与可复制示例一并返回。"""
    from excelmanus.engine_core.error_payload import INVALID_ARGS

    prev = (previous_message or "").strip() or "上次调用失败"
    message = f"参数与上次失败完全相同。上次错误：{prev}"
    extra: dict[str, Any] = {}
    if example is not None:
        extra["example"] = example
        try:
            message += f"。最小合法示例：{json.dumps(example, ensure_ascii=False)}"
        except (TypeError, ValueError):
            pass
    return error_result(message, code=INVALID_ARGS, fields=extra or None)


class ToolDispatcher:
    """工具调度器：参数解析、分支路由、执行、审计。"""

    def __init__(self, engine: "AgentEngine") -> None:
        self._engine = engine
        self._deferred_image_injections: list[dict[str, Any]] = []
        # 已注入图片的 hash 集合（用于去重）
        self._injected_image_hashes: set[str] = set()
        # 每会话 sleep 取消事件（abort 时中断正在执行的 sleep 工具）
        self._sleep_cancel_event = threading.Event()
        # 任务级取消：abort 后拒绝新的 execute / 子调用
        self._cancel_event = threading.Event()
        # Code Mode 子调用与父 run_code 共用的调用次数预算；None 表示不限制
        self._call_budget: int | None = None
        self._call_count: int = 0
        self._call_budget_reason: str = "已达到调用上限"
        self._runtime: Any = None
        # 最近一次工具调用的截断前 model_text
        self._last_call_raw_result: str = ""
        self._last_call_structured: ToolResult | None = None
        # 连续同参调用链：3/5/8 软提醒；上一笔失败且 canonical args 相同则硬拦截
        self._repeat_key: str | None = None
        self._repeat_count: int = 0
        self._last_repeat_success: bool = True
        self._last_repeat_error_message: str | None = None
        self._last_repeat_example: Any = None
        self._readonly_replay_cache: dict[tuple[str, str, str], ToolResult] = {}
        self._readonly_replay_expiry: dict[tuple[str, str, str], float] = {}

        self._tool_call_store: "ToolCallStore | None" = None
        db = getattr(engine, "_database", None)
        if db is not None:
            try:
                from excelmanus.stores.tool_call_store import ToolCallStore as _TCS
                self._tool_call_store = _TCS(db)
            except Exception:
                logger.debug("工具调用审计日志初始化失败", exc_info=True)

        # ── 策略处理器表 ──
        from excelmanus.engine_core.tool_handlers import (
            AskUserHandler,
            ShowWorkbookHandler,
            AuditOnlyHandler,
            CodePolicyHandler,
            DefaultToolHandler,
            DelegationHandler,
            HighRiskApprovalHandler,
            SkillActivationHandler,
            SkillManagementHandler,
        )
        # T2: 按工具名建立 O(1) 索引，跳过需动态判断的 handler
        # 每个 handler 只实例化一次，specific 和 generic 复用同一对象
        _specific: dict[str, Any] = {}
        _skill = SkillActivationHandler(engine, self)
        _specific["skill"] = _skill
        _specific["activate_skill"] = _skill
        _specific["manage_skills"] = SkillManagementHandler(engine, self)
        _deleg = DelegationHandler(engine, self)
        for _dn in ("delegate", "delegate_to_subagent", "list_subagents", "parallel_delegate"):
            _specific[_dn] = _deleg
        _specific["ask_user"] = AskUserHandler(engine, self)
        _specific["show_workbook"] = ShowWorkbookHandler(engine, self)
        self._specific_handlers: dict[str, Any] = _specific
        # 动态/条件 handler + 兜底（保持原有顺序）
        _code_policy = CodePolicyHandler(engine, self)
        _audit_only = AuditOnlyHandler(engine, self)
        _high_risk = HighRiskApprovalHandler(engine, self)
        _default = DefaultToolHandler(engine, self)
        self._generic_handlers = [_code_policy, _audit_only, _high_risk, _default]
        # 从当前 registry 建立宿主侧副作用能力快照。MCP 仍使用已有
        # ToolDef/名称启发式，不要求 provider 增加任何新字段。
        approval_manager = getattr(engine, "approval", None)
        if approval_manager is not None:
            bind_defs = getattr(approval_manager, "bind_tool_definitions", None)
            if callable(bind_defs):
                try:
                    bind_defs(self._registry.get_all_tools())
                except Exception:
                    logger.debug("绑定工具副作用能力失败，回退静态策略", exc_info=True)

    @property
    def _registry(self) -> Any:
        return self._engine.registry

    @property
    def _persistent_memory(self) -> Any:
        return self._engine._persistent_memory

    def _capture_unknown_write_probe(self, tool_name: str) -> tuple[dict[str, tuple[int, int]] | None, bool]:
        """为 unknown 写入语义工具采集执行前快照。"""
        e = self._engine
        if e.get_tool_write_effect(tool_name) != "unknown":
            return None, False
        try:
            return collect_workspace_mtime_index(e.config.workspace_root)
        except Exception:
            logger.debug("unknown 写入探针前置快照失败", exc_info=True)
            return None, False

    def _apply_unknown_write_probe(
        self,
        *,
        tool_name: str,
        before_snapshot: dict[str, tuple[int, int]] | None,
        before_partial: bool,
    ) -> None:
        """对 unknown 写入语义工具执行后做 mtime 兜底检测。"""
        if before_snapshot is None:
            return
        e = self._engine
        try:
            after_snapshot, after_partial = collect_workspace_mtime_index(e.config.workspace_root)
        except Exception:
            logger.debug("unknown 写入探针后置快照失败", exc_info=True)
            return

        if has_workspace_mtime_changes(before_snapshot, after_snapshot):
            e.record_workspace_write_action()
            logger.info(
                "unknown 写入探针命中: tool=%s partial_before=%s partial_after=%s",
                tool_name,
                before_partial,
                after_partial,
            )

    def _seed_seen_versions(self) -> None:
        from excelmanus.workbook_commit import seed_seen_versions

        state = getattr(self._engine, "state", None)
        mapping = getattr(state, "file_content_versions", None) if state is not None else None
        seed_seen_versions(mapping if isinstance(mapping, dict) else {})

    def _remember_tool_versions(self, result: ToolResult) -> None:
        from excelmanus.workbook_commit import (
            export_seen_versions,
            remember_content_version,
        )

        version = getattr(result.ui_meta, "content_version", None)
        for path in result.ui_meta.files or []:
            remember_content_version(path, version)
        value = result.value
        if isinstance(value, dict):
            nested = value.get("content_version")
            path = value.get("file_path")
            if nested and path:
                remember_content_version(str(path), str(nested))
            saves = value.get("save_versions")
            if isinstance(saves, dict):
                for save_path, save_ver in saves.items():
                    if save_path and save_ver:
                        remember_content_version(str(save_path), str(save_ver))
        state = getattr(self._engine, "state", None)
        remember = getattr(state, "remember_file_version", None)
        if callable(remember):
            for path, ver in export_seen_versions().items():
                remember(path, ver)

    # ── ToolResult 归一化与 ui_meta 副作用 ──────────────────────

    @staticmethod
    def _coerce_tool_result(result_value: Any) -> ToolResult:
        """唯一消费边界：任意工具返回值 → ToolResult。"""
        return coerce_legacy_result(result_value)

    def _schedule_image_injection(self, injection: dict[str, Any]) -> None:
        e = self._engine
        attachment = injection.get("attachment")
        base64_data = injection.get("base64")
        if not attachment and not base64_data:
            return

        if e.is_vision_capable:
            if isinstance(attachment, dict) and attachment.get("attachmentId"):
                _img_hash = str(attachment["attachmentId"])
            else:
                _img_hash = _image_content_hash_b64(str(base64_data or ""))
            if _img_hash in self._injected_image_hashes:
                logger.info("图片已在上下文中 (hash=%s)，跳过重复注入", _img_hash)
            else:
                self._deferred_image_injections.append({
                    "attachment": attachment,
                    "base64": base64_data,
                    "mime_type": injection.get("mime_type", "image/png"),
                    "detail": injection.get("detail", "auto"),
                })
                self._injected_image_hashes.add(_img_hash)
                logger.info(
                    "图片已缓存待注入 (hash=%s, mime=%s)",
                    _img_hash,
                    injection.get("mime_type"),
                )
        else:
            logger.info("当前模型无视觉能力，跳过图片注入")

    def _apply_ui_meta_effects(self, tool_result: ToolResult) -> None:
        ui = tool_result.ui_meta
        if ui.image:
            self._schedule_image_injection(ui.image)

    def flush_deferred_images(self) -> int:
        """将延迟的图片注入实际写入 memory。

        必须在当前 assistant tool_calls 对应的所有 tool result 写入 memory 之后调用，
        否则 user 角色的图片消息会破坏 tool_calls → tool_responses 的消息序列，
        导致 OpenAI 兼容 API 返回 400 错误。

        Returns:
            注入的图片数量。
        """
        if not self._deferred_image_injections:
            return 0
        e = self._engine
        count = 0
        for inj in self._deferred_image_injections:
            attachment = inj.get("attachment")
            if not attachment and inj.get("base64"):
                try:
                    from excelmanus.attachments.admit import admit_image_bytes, decode_image_payload
                    raw = decode_image_payload(str(inj["base64"]))
                    ref = admit_image_bytes(raw, media_type=inj.get("mime_type", "image/png"))
                    attachment = ref.to_dict()
                except Exception:
                    logger.warning("延迟图片准入失败，写入占位文本", exc_info=True)
            if attachment:
                e.memory.add_user_message([{"type": "image", "attachment": attachment}])
            else:
                e.memory.add_user_message([{"type": "text", "text": "[image omitted: unreadable attachment]"}])
            count += 1
            logger.info("已注入 %d 张延迟图片到 memory", count)
        self._deferred_image_injections.clear()
        return count

    def parse_arguments(self, raw_args: Any) -> tuple[dict[str, Any], str | None]:
        """解析工具调用参数，返回 (arguments, error)。

        error 为 None 表示解析成功。
        """
        if raw_args is None or raw_args == "":
            return {}, None
        if isinstance(raw_args, dict):
            return raw_args, None
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if not isinstance(parsed, dict):
                    return {}, f"参数必须为 JSON 对象，当前类型: {type(parsed).__name__}"
                return parsed, None
            except (json.JSONDecodeError, TypeError) as exc:
                return {}, f"JSON 解析失败: {exc}"
        return {}, f"参数类型无效: {type(raw_args).__name__}"

    def _note_repeat_outcome(
        self,
        success: bool,
        structured: ToolResult | None,
        error: str | None,
        result_str: str,
    ) -> None:
        """记录上一笔 (tool, args) 的成败，供下一次同参硬拦截使用。"""
        self._last_repeat_success = bool(success)
        if success:
            self._last_repeat_error_message = None
            self._last_repeat_example = None
            return
        if self._repeat_count != 1:
            return
        if structured is not None and structured.error is not None and structured.error.message:
            self._last_repeat_error_message = structured.error.message
        else:
            self._last_repeat_error_message = str(error or result_str or "")
        self._last_repeat_example = _example_from_structured(structured)

    def cancel_active_sleep(self) -> None:
        """中断当前会话正在执行的 sleep 工具调用。"""
        self._sleep_cancel_event.set()
        runtime = getattr(self, "_runtime", None)
        for row in getattr(runtime, "_calls", {}).values():
            if row.name == "sleep" and row.status in {"running", "cancelling"}:
                row.cancel_event.set()

    def request_cancel(self) -> None:
        """取消当前任务：打断 sleep，并拒绝后续 execute / 子调用。"""
        self._cancel_event.set()
        self._sleep_cancel_event.set()
        session = getattr(self._engine, "_active_code_mode_session", None)
        if session is not None:
            session.stop()

    def reset_cancel(self) -> None:
        self._cancel_event.clear()
        self._sleep_cancel_event.clear()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def begin_call_budget(
        self,
        max_calls: int | None,
        *,
        reason: str = "已达到调用上限",
    ) -> None:
        """开始一段共享调用预算。``None`` 表示不限制。"""
        self._call_budget = max_calls
        self._call_count = 0
        self._call_budget_reason = reason
        self._readonly_replay_cache.clear()
        self._readonly_replay_expiry.clear()

    def begin_nested_call_budget(self) -> tuple[int | None, int, str]:
        """run_code 内层预算：重置计数但不丢弃父消耗。"""
        snapshot = (self._call_budget, self._call_count, self._call_budget_reason)
        self.begin_call_budget(
            _CODE_MODE_NESTED_CALL_BUDGET,
            reason=(
                f"本次 run_code 内嵌套调用达上限（{_CODE_MODE_NESTED_CALL_BUDGET} 次）；"
                "批量任务请拆成多次 run_code，或改用工具直接调用分批执行"
            ),
        )
        return snapshot

    def restore_parent_call_budget(self, snapshot: tuple[int | None, int, str]) -> None:
        """恢复父预算上限，并把子调用消耗加回父计数。"""
        nested_used = self._call_count
        budget, parent_count, reason = snapshot
        self._call_budget = budget
        self._call_budget_reason = reason
        self._call_count = parent_count + nested_used

    def _readonly_replay_key(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        """同一 run 内只读工具：工具名 + 规范参数 + 文件 content_version。"""
        # Session settings/permissions can change without a file version bump.
        if tool_name == "inspect_agent":
            return None
        # Provider state is not represented by workspace content_version. MCP
        # unknown/写入工具因此不能重放；只有宿主现有 scope/描述启发式明确
        # 推导为 none 的只读工具才保留普通 replay 行为，不增加 provider 字段。
        if tool_name.startswith("mcp_"):
            mcp_tool = self._registry.get_tool(tool_name)
            if mcp_tool is None or getattr(mcp_tool, "write_effect", "unknown") != "none":
                return None
        owner = getattr(self._engine, "_background_parent", None) or self._engine
        runtime = getattr(owner, "_subagent_runtime", None)
        if runtime is not None and runtime.has_active_runs:
            # 后台子代理可能已改变文件，旧的会话观察版本不能作为缓存命中依据。
            return None
        from excelmanus.tools.policy import is_mutating_write_effect
        from excelmanus.tools.registry import normalize_tool_aliases

        tool = self._registry.get_tool(tool_name)
        if tool is None:
            return None
        effect = getattr(tool, "write_effect", "unknown")
        if is_mutating_write_effect(effect):
            return None
        folded = normalize_tool_aliases(dict(arguments or {}), getattr(tool, "input_schema", None))
        if isinstance(folded, ToolResult):
            return None
        file_path = str(folded.get("file_path") or folded.get("path") or "")
        version = ""
        if file_path:
            state = getattr(self._engine, "state", None)
            mapping = getattr(state, "file_content_versions", None) if state is not None else None
            if isinstance(mapping, dict):
                version = str(mapping.get(file_path) or "")
        # A read without a local file version (search, provider state, time,
        # network metadata) is not replayable unless the tool explicitly
        # declares a finite TTL.  This prevents same-turn reuse of stale
        # external observations.
        if not file_path and getattr(tool, "cache_ttl_seconds", None) is None:
            return None
        payload = json.dumps(folded, ensure_ascii=False, sort_keys=True, default=str)
        return (tool_name, payload, version)

    def external_receipt(self, operation_id: str) -> dict[str, Any] | None:
        """Read a durable provider-side commit receipt without replaying a call."""
        from excelmanus.workspace.txlog import TxLog

        return TxLog(self._workspace_root()).read_external_receipt(operation_id)

    def resolve_external_receipt(
        self,
        operation_id: str,
        *,
        status: str,
        result: str | None = None,
    ) -> dict[str, Any]:
        """Record an explicit provider query/compensation outcome.

        Only terminal provider states are accepted.  The operation remains
        blocked for automatic replay until a caller explicitly resolves it.
        """
        normalized = str(status or "").strip().lower()
        if normalized not in {"committed", "rolled_back", "failed", "unknown"}:
            raise ValueError("external receipt status must be committed/rolled_back/failed/unknown")
        from excelmanus.workspace.txlog import TxLog

        txlog = TxLog(self._workspace_root())
        current = txlog.read_external_receipt(operation_id)
        if current is None:
            raise KeyError(operation_id)
        updated = dict(current)
        updated["status"] = normalized
        if result is not None:
            updated["result"] = str(result)
        updated["resolved_at"] = time.time()
        txlog.write_external_receipt(operation_id, updated)
        return updated

    def consume_call_budget(self) -> bool:
        """消耗一次调用额度。超预算返回 False。"""
        if self._call_budget is None:
            return True
        if self._call_count >= self._call_budget:
            return False
        self._call_count += 1
        return True

    def has_call_budget_remaining(self) -> bool:
        """主循环在下一轮 LLM 之前检查：工具调用预算是否还剩额度。"""
        if self._call_budget is None:
            return True
        return self._call_count < self._call_budget

    @staticmethod
    def _blocked_tool_result(code: str, message: str) -> ToolResult:
        from excelmanus.engine_core.tool_result import error_result

        return error_result(message, code=code)

    def _blocked_call_result(
        self, tc: Any, *, code: str, message: str
    ) -> Any:
        from excelmanus.engine_types import ToolCallResult

        function = getattr(tc, "function", None)
        arguments = getattr(function, "arguments", None)
        if not isinstance(arguments, dict):
            arguments = {}
        structured = self._blocked_tool_result(code, message)
        return ToolCallResult(
            tool_name=getattr(function, "name", "") or "",
            arguments=arguments,
            result=structured.model_text,
            success=False,
            error=code,
            structured=structured,
        )

    _PLAN_MODE_ALLOWED = frozenset({"write_plan", "exit_plan_mode"})
    _READ_MODE_DENIED_BY_NAME = frozenset({"write_plan", "exit_plan_mode"})

    def _write_effect_of(self, tool_name: str, args: dict[str, Any] | None = None) -> str:
        getter = getattr(self._engine, "get_tool_write_effect", None)
        effect = getter(tool_name) if callable(getter) else "unknown"
        declared = effect if isinstance(effect, str) else "unknown"
        from excelmanus.tools.policy import write_effect_for_call

        registry = self._registry

        tool = registry.get_tool(tool_name) if registry is not None else None
        actions = getattr(tool, "actions", None) if tool is not None else None
        return write_effect_for_call(
            tool_name, args, declared=declared, actions=actions if isinstance(actions, dict) else None,
        )

    def _denied_in_read_mode(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        """read-only sandbox: catalog stays full; executor rejects writes."""
        if tool_name in self._READ_MODE_DENIED_BY_NAME:
            return True
        from excelmanus.tools.policy import CODE_POLICY_DYNAMIC_TOOLS

        if tool_name in CODE_POLICY_DYNAMIC_TOOLS and getattr(
            self._engine, "_subagent_config", None
        ) is not None:
            return False
        return self._write_effect_of(tool_name, args) in RESTRICTED_WRITE_EFFECTS

    def _denied_in_plan_mode(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        """Plan is not sandbox. Hard-reject writes except plan tools."""
        if tool_name in self._PLAN_MODE_ALLOWED:
            return False
        effect = self._write_effect_of(tool_name, args)
        if effect == "dynamic" and tool_name in {"delegate", "delegate_to_subagent", "parallel_delegate"}:
            return self._delegate_would_write(args)
        return effect in RESTRICTED_WRITE_EFFECTS

    def _delegate_would_write(self, args: dict[str, Any] | None) -> bool:
        """交集后的 child 若仍可写，则 plan 拒绝委派。"""
        from excelmanus.subagent.child import child_capability

        parent = self._engine
        registry = getattr(parent, "_subagent_registry", None)
        names: list[str] = []
        raw_tasks = (args or {}).get("tasks")
        if isinstance(raw_tasks, list) and len(raw_tasks) >= 2:
            for item in raw_tasks:
                if isinstance(item, dict):
                    names.append(str(item.get("agent_name") or "subagent"))
        else:
            names.append(str((args or {}).get("agent_name") or "subagent"))
        for raw_name in names:
            cfg = registry.get(raw_name) if registry is not None else None
            if cfg is None:
                continue
            if child_capability(parent, cfg).catalog_mode == "write":
                return True
        return False

    def _is_excel_mutating_call(self, tool_name: str, args: dict[str, Any] | None = None) -> bool:
        if tool_name not in self._EXCEL_WRITE_TOOLS:
            return False
        return self._write_effect_of(tool_name, args) != "none"

    def _workspace_root(self) -> str:
        e = self._engine
        cfg = getattr(e, "_config", None) or getattr(e, "config", None)
        if cfg is None:
            return ""
        return str(getattr(cfg, "workspace_root", "") or "")

    def _spill_store(self):
        from excelmanus.engine_core.spill import SpillStore

        root = self._workspace_root()
        if not root:
            return None
        return SpillStore(root)

    async def call_registry_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
        root_call_id: str | None = None,
    ) -> ToolResult:
        """调用工具，返回归一化后的 ToolResult（截断仅作用于 model_text）。

        MCP 工具（具有 async_func）直接 await，避免线程池 + asyncio.run 开销。
        普通工具仍走 asyncio.to_thread 线程池路径。
        ``root_call_id`` 为 Code Mode 子调用挂到父 ``run_code`` 的 tool_call_id。
        """
        if root_call_id:
            logger.debug(
                "code_mode subcall tool=%s root_call_id=%s",
                tool_name,
                root_call_id,
            )
        from excelmanus.engine_core.spill import extract_spill_locator, retrieve_spill_result
        from excelmanus.tools import memory_tools
        from excelmanus.tools.sleep_tools import set_cancel_event, reset_cancel_event
        from excelmanus.tools.runtime import current_execution

        active_execution = current_execution()
        if active_execution is not None and active_execution.cancel_requested:
            return self._blocked_tool_result("CANCELLED", "本次工具调用已取消，未开始新的操作。")

        replay_key = self._readonly_replay_key(tool_name, arguments)
        cached = self._readonly_replay_cache.get(replay_key) if replay_key else None
        if replay_key and cached is not None:
            expires = self._readonly_replay_expiry.get(replay_key, float("inf"))
            if time.monotonic() >= expires:
                self._readonly_replay_cache.pop(replay_key, None)
                self._readonly_replay_expiry.pop(replay_key, None)
                cached = None
        if cached is not None:
            return cached

        spill_locator = extract_spill_locator(arguments)
        if spill_locator and tool_name in self._SPILL_RETRIEVE_TOOLS:
            store_root = self._workspace_root()
            if store_root:
                return retrieve_spill_result(spill_locator, workspace_root=store_root)

        registry = self._registry

        # Approval replay of a specialized meta tool must execute the same
        # handler as the normal dispatch path. Calling its stub ToolDef.func
        # would otherwise bypass the SkillpackManager.
        if tool_name == "manage_skills":
            handler = self._specific_handlers.get(tool_name)
            if handler is not None:
                outcome = await handler.handle(
                    tool_name, "", dict(arguments), tool_scope=tool_scope,
                )
                if isinstance(outcome, _ToolExecOutcome):
                    return self._coerce_tool_result(outcome.structured or outcome.result_str)

        # 检测是否有异步快速路径（MCP 工具）
        tool_def = registry.get_tool(tool_name)
        capability = None
        if tool_def is not None:
            try:
                capability = tool_def.effective_capability(arguments)
            except Exception:
                capability = None
        external_op_id = None
        external_txlog = None
        external_intent_hash = None
        if tool_def is not None and (
            (capability is not None and capability.is_external)
            or tool_name.startswith("mcp_")
        ):
            import hashlib
            import json as _json
            from excelmanus.tools.context import operation_id_for
            from excelmanus.workspace.txlog import TxLog

            external_op_id = operation_id_for(tool_name)
            if external_op_id:
                external_txlog = TxLog(self._workspace_root())
                external_intent_hash = hashlib.sha256(
                    _json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str).encode()
                ).hexdigest()
                existing = external_txlog.read_external_receipt(external_op_id)
                if existing is not None:
                    if existing.get("intent_hash") != external_intent_hash:
                        return error_result(
                            "external operation id 已用于不同意图",
                            code="OPERATION_ID_REUSED",
                        )
                    if existing.get("status") == "committed":
                        return self._coerce_tool_result(str(existing.get("result") or ""))
                    return error_result(
                        "外部写入状态不确定，禁止自动重放；请查询或由 provider 确认后继续。",
                        code="EXTERNAL_COMMIT_UNKNOWN",
                        fields={"operation_id": external_op_id, "status": existing.get("status")},
                    )
                external_txlog.write_external_receipt(
                    external_op_id,
                    {
                        "operation_id": external_op_id,
                        "tool_name": tool_name,
                        "intent_hash": external_intent_hash,
                        "status": "pending",
                        "query_tool": getattr(tool_def, "actions", {}).get("query_tool"),
                        "compensation": getattr(capability, "compensation", None),
                    },
                )
        _has_async = (
            tool_def is not None
            and getattr(tool_def, "async_func", None) is not None
            and callable(tool_def.async_func)
            and asyncio.iscoroutinefunction(tool_def.async_func)
        )
        self._seed_seen_versions()
        if _has_async:
            # MCP 异步快速路径：直接 await，不经线程池
            result_value = await registry.call_tool_async(
                tool_name,
                arguments,
                tool_scope=tool_scope,
            )
        else:
            # 普通工具：走线程池路径
            persistent_memory = self._persistent_memory
            from excelmanus.tools.runtime import current_execution

            active_execution = current_execution()
            sleep_cancel_event = active_execution.cancel_event if active_execution is not None else self._sleep_cancel_event

            # 将每会话的 sleep 取消事件注入 contextvar，
            # asyncio.to_thread 会自动拷贝到工作线程。
            _sleep_token = set_cancel_event(sleep_cancel_event)

            def _call() -> Any:
                with memory_tools.bind_memory_context(persistent_memory):
                    return registry.call_tool(
                        tool_name,
                        arguments,
                        tool_scope=tool_scope,
                    )

            try:
                work = asyncio.create_task(asyncio.to_thread(_call))
                try:
                    result_value = await asyncio.shield(work)
                except asyncio.CancelledError:
                    # Python 线程不能被 Task.cancel 终止。等当前调用落定后再
                    # 结束 actor，避免暂停/恢复后同一文件仍有旧调用在提交。
                    if active_execution is None or not active_execution.cancel_requested:
                        self.request_cancel()
                    sleep_cancel_event.set()
                    settled = await asyncio.gather(work, return_exceptions=True)
                    if not isinstance(settled[0], BaseException):
                        completed = self._coerce_tool_result(settled[0])
                        if active_execution is not None:
                            active_execution.outcome = completed
                        self._remember_tool_versions(completed)
                        self._apply_ui_meta_effects(completed)
                        if self._write_effect_of(tool_name, arguments) != "none" and completed.ui_meta.files:
                            self._record_public_identities(self._engine, completed.ui_meta.files)
                    raise
            finally:
                reset_cancel_event(_sleep_token)

        tool_result = self._coerce_tool_result(result_value)
        from excelmanus.tools.runtime import current_execution

        active_execution = current_execution()
        if active_execution is not None:
            active_execution.outcome = tool_result
        if external_txlog is not None and external_op_id is not None:
            external_txlog.write_external_receipt(
                external_op_id,
                {
                    "operation_id": external_op_id,
                    "tool_name": tool_name,
                    "intent_hash": external_intent_hash,
                    "status": (
                        "committed"
                        if tool_result.success and getattr(capability, "consistency", getattr(tool_def, "consistency", "external_unverified")) == "local_commit"
                        else "external_unverified" if tool_result.success else "unknown"
                    ),
                    "result": tool_result.model_text,
                    "consistency": getattr(capability, "consistency", getattr(tool_def, "consistency", "external_unverified")),
                    "query_tool": getattr(tool_def, "actions", {}).get("query_tool"),
                    "compensation": getattr(capability, "compensation", None),
                },
            )
        self._remember_tool_versions(tool_result)
        self._apply_ui_meta_effects(tool_result)
        from excelmanus.tools.output_contracts import enforce_output_contract

        tool_result = enforce_output_contract(tool_result, tool_name, arguments, tool_def=tool_def)
        self._last_call_structured = tool_result
        self._last_call_raw_result = tool_result.model_text

        tool_def = getattr(registry, "get_tool", lambda _: None)(tool_name)
        if tool_def is not None:
            tool_result = tool_result.with_model_text(
                tool_def.truncate_result(tool_result.model_text)
            )

        from excelmanus.tools.policy import is_mutating_write_effect

        effect = getattr(tool_def, "write_effect", "unknown") if tool_def is not None else "unknown"
        if is_mutating_write_effect(effect):
            self._readonly_replay_cache.clear()
            self._readonly_replay_expiry.clear()
        elif replay_key and tool_result.success:
            ttl = getattr(tool_def, "cache_ttl_seconds", None)
            expiry = float("inf")
            if ttl is not None:
                try:
                    expiry = time.monotonic() + max(0.0, float(ttl))
                except (TypeError, ValueError):
                    expiry = time.monotonic()
            self._readonly_replay_cache[replay_key] = tool_result
            self._readonly_replay_expiry[replay_key] = expiry

        return tool_result

    async def execute_subcall(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
        root_call_id: str | None = None,
        call_id: str | None = None,
        on_event: "EventCallback | None" = None,
    ) -> ToolResult:
        """Code Mode 子调用：走完整 ``execute()``（审批、hooks、事件），再投影为 ToolResult。"""
        from excelmanus.engine_types import ToolCallResult

        if self.is_cancelled():
            return self._blocked_tool_result("CANCELLED", "任务已取消")
        self._seed_seen_versions()
        if not call_id:
            self._subcall_seq = getattr(self, "_subcall_seq", 0) + 1
            prefix = root_call_id or "sub"
            call_id = f"{prefix}:{tool_name}:{self._subcall_seq}"
        tc = _SyntheticToolCall(
            call_id=call_id,
            name=tool_name,
            arguments=arguments,
            parent_call_id=root_call_id,
        )
        if on_event is None:
            on_event = getattr(self, "_current_on_event", None)
        runtime = getattr(self, "_runtime", None)

        async def execute_and_resolve() -> ToolResult:
            execute = runtime.execute if runtime is not None else self.execute
            tcr = await execute(tc, tool_scope, on_event, 0)
            if isinstance(tcr, ToolResult):
                return tcr
            if isinstance(tcr, ToolCallResult):
                if tcr.pending_approval:
                    return await self._await_subcall_approval(tcr, tc, on_event)
                if tcr.structured is not None:
                    if tcr.success or not tcr.structured.success:
                        return tcr.structured
                    return ToolResult(
                        success=False,
                        model_text=tcr.result,
                        value=tcr.structured.value,
                        error=tcr.structured.error
                        or ToolError(
                            code=str(tcr.error or "TOOL_ERROR"),
                            message=tcr.error or tcr.result,
                        ),
                        ui_meta=tcr.structured.ui_meta,
                    )
                return ToolResult.from_text(tcr.result, success=bool(tcr.success))
            return self._coerce_tool_result(tcr)

        if runtime is None:
            return await execute_and_resolve()
        result = await runtime.run_managed(tc, execute_and_resolve, on_event, 0)
        return result.structured or ToolResult.from_text(result.result, success=result.success)

    async def _await_subcall_approval(
        self,
        tcr: Any,
        tc: Any,
        on_event: "EventCallback | None",
    ) -> ToolResult:
        """子调用命中确认门：等待与顶层一致的决策通道，accept 后原调用恢复一次。

        与回合级 pending+重放不同：子调用在 ``run_code`` 内部等待，批准后
        经 ``_apply_approval_decision`` 恢复同一调用（同一审计路径），不
        重新触发 ASK、不产生第二次执行。等待期间父取消经
        ``session._subcall_cancel`` 传播；超时/取消/迟到批准都收敛为
        确定终态并清理 pending，不留孤儿审批卡。
        """
        from excelmanus.engine_core.error_payload import (
            APPROVAL_DENIED,
            APPROVAL_TIMEOUT,
            PENDING_APPROVAL,
        )
        from excelmanus.engine_core.tool_result import error_result
        from excelmanus.interaction import DEFAULT_INTERACTION_TIMEOUT

        e = self._engine
        approval_id = tcr.approval_id or ""
        pending = e.approval.pending
        if pending is None or pending.approval_id != approval_id:
            return error_result(
                tcr.result or "工具等待审批",
                code=PENDING_APPROVAL,
                fields={"approval_id": approval_id},
            )

        inflight = getattr(e, "_inflight_approval_ids", None)
        if inflight is None:
            inflight = e._inflight_approval_ids = set()
        inflight.add(approval_id)
        try:
            resolver = getattr(e, "_approval_resolver", None)
            session = getattr(e, "_active_code_mode_session", None)
            cancel_event = getattr(session, "_subcall_cancel", None)

            registry = getattr(e, "_interaction_registry", None)
            if callable(resolver):
                wait_coro = self._resolve_decision_via_resolver(resolver, pending)
            elif registry is not None:
                interaction = getattr(e, "_interaction_handler", None)
                if interaction is not None:
                    wait_coro = interaction.wait_approval_decision(approval_id)
                else:
                    wait_coro = self._resolve_decision_via_registry(registry.create(approval_id))
            else:
                e._approval.reject_pending(approval_id)
                return error_result(
                    "无可用审批决策通道，审批已撤销",
                    code=APPROVAL_DENIED,
                    fields={"approval_id": approval_id},
                )

            wait_timeout = DEFAULT_INTERACTION_TIMEOUT
            timeout_for = getattr(session, "timeout_for", None) if session is not None else None
            if callable(timeout_for):
                fn = getattr(tc, "function", None)
                tool = (
                    str(getattr(fn, "name", "") or "")
                    or str(getattr(tc, "name", "") or "")
                    or "ask_user"
                )
                wait_timeout = timeout_for(tool)
            decision = await self._wait_approval_decision(
                wait_coro, cancel_event, wait_timeout,
            )
            if decision is _WAIT_PARENT_CANCELLED:
                e._approval.reject_pending(approval_id)
                return error_result(
                    "父 run_code 已取消，审批撤销",
                    code="CANCELLED",
                    fields={"approval_id": approval_id},
                )
            if decision is _WAIT_TIMEOUT:
                reject_msg = e._approval.reject_pending(approval_id, timeout=True)
                return error_result(
                    reject_msg,
                    code=APPROVAL_TIMEOUT,
                    fields={"approval_id": approval_id},
                )
            # 批准后、执行前再查一次取消：提交后取消不回滚已提交版本，
            # 但未执行的调用不得继续。
            if self.is_cancelled():
                e._approval.reject_pending(approval_id)
                return error_result(
                    "任务已取消，审批未执行",
                    code="CANCELLED",
                    fields={"approval_id": approval_id},
                )
            apply_decision = getattr(e, "_apply_approval_decision", None)
            if not callable(apply_decision):
                e._approval.reject_pending(approval_id)
                return error_result(
                    "引擎缺少审批恢复通道，审批已撤销",
                    code=APPROVAL_DENIED,
                    fields={"approval_id": approval_id},
                )
            updates, _wrote = await apply_decision(
                decision, pending, approval_id,
                getattr(tc, "id", None) or getattr(tc, "call_id", None),
                on_event, 0, "Code Mode 子调用审批",
            )
            if updates.get("success"):
                # 批准恢复走 _execute_approved_pending → registry.call_tool，
                # 结构化结果由引擎暂存，不是 _last_call_structured。
                structured = getattr(e, "_last_approved_structured", None)
                e._last_approved_structured = None
                if structured is not None:
                    return structured
                return ToolResult.from_text(
                    str(updates.get("result") or ""), success=True,
                )
            return error_result(
                str(updates.get("result") or "审批未通过"),
                code=APPROVAL_DENIED,
                fields={"approval_id": approval_id},
            )
        finally:
            inflight.discard(approval_id)
            # 兜底：本子调用创建的 pending 不得跨过自身终态残留。
            leftover = e.approval.pending
            if leftover is not None and leftover.approval_id == approval_id:
                e._approval.clear_pending()

    @staticmethod
    async def _resolve_decision_via_resolver(resolver: Any, pending: Any) -> Any:
        """resolver 回调通道（CLI/bench）：返回 decision 字符串或 dict。"""
        try:
            return await resolver(pending)
        except Exception:  # noqa: BLE001
            logger.warning("子调用审批 resolver 异常，视为拒绝", exc_info=True)
            return "reject"

    @staticmethod
    async def _resolve_decision_via_registry(fut: Any) -> Any:
        """InteractionRegistry 通道（Web /approve）：返回 payload dict。"""
        return await fut

    @staticmethod
    async def _wait_approval_decision(
        wait_coro: Any,
        cancel_event: Any,
        timeout: float,
    ) -> Any:
        """等待决策，父取消先到返回 _WAIT_PARENT_CANCELLED，超时返回 _WAIT_TIMEOUT。"""
        tasks: set[asyncio.Task] = {asyncio.ensure_future(wait_coro)}
        cancel_task: asyncio.Task | None = None
        if cancel_event is not None:
            cancel_task = asyncio.ensure_future(cancel_event.wait())
            tasks.add(cancel_task)
        try:
            done, _ = await asyncio.wait(
                tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if not done:
            return _WAIT_TIMEOUT
        if cancel_task is not None and cancel_task in done:
            return _WAIT_PARENT_CANCELLED
        decision_task = next(iter(done))
        payload = decision_task.result()
        if isinstance(payload, dict):
            return payload.get("decision")
        return payload

    # ── 核心执行方法：从 AgentEngine._execute_tool_call 搬迁 ──

    async def execute(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: "EventCallback | None",
        iteration: int,
        route_result: Any | None = None,
        skip_start_event: bool = False,
    ) -> Any:
        """单个工具调用：参数解析 → 执行 → 事件发射 → 返回结果。

        从 AgentEngine._execute_tool_call 整体搬迁，通过 self._engine
        引用回调 AgentEngine 上的基础设施方法。
        """
        from excelmanus.tools.code_tools import set_sandbox_env as _set_sandbox_env
        from excelmanus.tools.code_tools import set_readonly_exec as _set_readonly_exec
        from excelmanus.security.policy import writes_denied as _writes_denied
        from excelmanus.attachments.offload import attachment_ids_from_engine
        from excelmanus.tools.context import ToolCallContext, bind_call, binding_from_engine, reset_call

        e = self._engine  # 引擎快捷引用

        from excelmanus.tools.runtime import current_execution

        active_execution = current_execution()
        if self.is_cancelled() or (active_execution is not None and active_execution.cancel_requested):
            return self._blocked_call_result(tc, code="CANCELLED", message="任务已取消")
        turn_budget = getattr(self._engine, "_turn_budget", None)
        if turn_budget is not None:
            try:
                turn_budget.reserve_tool()
            except Exception as exc:
                code = "TURN_TIMEOUT" if getattr(exc, "kind", "") == "wall_clock" else "BUDGET_EXCEEDED"
                return self._blocked_call_result(tc, code=code, message=str(exc))
        if not self.consume_call_budget():
            return self._blocked_call_result(
                tc,
                code="BUDGET_EXCEEDED",
                message=self._call_budget_reason,
            )

        function = getattr(tc, "function", None)
        loaded_tool_names = getattr(e, "_loaded_tool_names", None)
        if not isinstance(loaded_tool_names, set):
            loaded_tool_names = set()
            e._loaded_tool_names = loaded_tool_names
        _sandbox_token = _set_sandbox_env(e.sandbox_env)
        _readonly_token = _set_readonly_exec(_writes_denied(e))
        _call_token = bind_call(
            ToolCallContext(
                binding=binding_from_engine(e),
                call_id=str(getattr(tc, "id", "") or ""),
                tool_name=str(getattr(function, "name", "") or ""),
                parent_call_id=getattr(tc, "parent_call_id", None) or None,
                durable_attachment_ids=attachment_ids_from_engine(e),
                loaded_tool_names=loaded_tool_names,
            )
        )
        prev_event = getattr(self, "_current_on_event", None)
        self._current_on_event = on_event
        try:
            return await self._execute_inner(
                tc, tool_scope, on_event, iteration, route_result, skip_start_event,
                _sandbox_token,
            )
        finally:
            self._current_on_event = prev_event
            from excelmanus.tools.code_tools import _current_sandbox_env
            from excelmanus.tools.code_tools import _current_readonly_exec
            _current_sandbox_env.reset(_sandbox_token)
            _current_readonly_exec.reset(_readonly_token)
            reset_call(_call_token)

    async def _execute_inner(
        self,
        tc: Any,
        tool_scope: Sequence[str] | None,
        on_event: "EventCallback | None",
        iteration: int,
        route_result: Any | None,
        skip_start_event: bool,
        _sandbox_token: Any,
    ) -> Any:
        from excelmanus.engine import ToolCallResult
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine
        _t0 = time.monotonic()

        function = getattr(tc, "function", None)
        tool_name = getattr(function, "name", "")
        raw_args = getattr(function, "arguments", None)
        tool_call_id = getattr(tc, "id", "") or f"call_{int(time.time() * 1000)}"

        # 参数解析
        arguments, parse_error = self.parse_arguments(raw_args)

        # 发射 TOOL_CALL_START 事件（并行路径已预发射，跳过避免重复）
        if not skip_start_event:
            e.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.TOOL_CALL_START,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    iteration=iteration,
                    parent_call_id=getattr(tc, "parent_call_id", "") or "",
                ),
            )

        # /tools 开启时额外发射简要工具调用通知
        if getattr(e, "_show_tool_calls", False):
            e.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.TOOL_CALL_NOTICE,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    iteration=iteration,
                ),
            )

        pending_approval = False
        approval_id: str | None = None
        audit_record = None
        pending_question = False
        question_id: str | None = None
        defer_tool_result = False
        finish_accepted = False
        error_kind: str | None = None
        _cow_reminders: list[str] = []
        _raw_result_str: str | None = None
        structured: ToolResult | None = None

        # 连续同参：计数先于执行。上一笔失败且本次 hash 相同 → 硬拦截（G3/G5）。
        _chain_key = f"{tool_name}\x00{_canonical_args(arguments)}"
        _identical_failed_retry = (
            _chain_key == self._repeat_key and not self._last_repeat_success
        )
        if _chain_key == self._repeat_key:
            self._repeat_count += 1
        else:
            self._repeat_key, self._repeat_count = _chain_key, 1
        if (
            not _identical_failed_retry
            and self._repeat_count in _REPEAT_REMINDER_THRESHOLDS
        ):
            if self._repeat_count == _REPEAT_REMINDER_THRESHOLDS[0]:
                _cow_reminders.append(
                    f"[提示] 已连续 {self._repeat_count} 次以相同参数调用 {tool_name}；"
                    "若无新信息请换参数、换工具或先交付。"
                )
            else:
                _cow_reminders.append(
                    f"[提示] {tool_name} 已连续 {self._repeat_count} 次以相同参数调用："
                    f"参数 {_canonical_args(arguments)[:300]}。"
                    "重复调用没有推进任务，不要再以相同参数重试。"
                )

        # 执行工具调用
        hook_skill = e.pick_route_skill(route_result)
        if parse_error is not None:
            from excelmanus.engine_core.error_payload import INVALID_ARGS
            from excelmanus.engine_core.tool_result import error_result

            structured = error_result(
                f"工具参数解析错误: {parse_error}",
                code=INVALID_ARGS,
            )
            result_str = structured.model_text
            success = False
            error = INVALID_ARGS
            log_tool_call(
                logger,
                tool_name,
                {"_raw_arguments": raw_args},
                error=error,
            )
        elif _identical_failed_retry:
            from excelmanus.engine_core.error_payload import INVALID_ARGS

            structured = _identical_failure_result(
                previous_message=self._last_repeat_error_message or "",
                example=self._last_repeat_example,
            )
            result_str = structured.model_text
            success = False
            error = (
                structured.error.code
                if structured.error is not None
                else INVALID_ARGS
            )
            log_tool_call(logger, tool_name, arguments, error=error)
        elif writes_denied(e) and self._denied_in_read_mode(tool_name, arguments):
            result_str = "当前是只读模式，写入被拒绝。"
            success = False
            error = "PERMISSION_DENIED"
            structured = self._blocked_tool_result("PERMISSION_DENIED", result_str)
            result_str = structured.model_text
            log_tool_call(logger, tool_name, arguments, error=error)
        elif is_plan_active(e) and self._denied_in_plan_mode(tool_name, arguments):
            result_str = "当前是计划模式，写入被拒绝。"
            success = False
            error = "PERMISSION_DENIED"
            structured = self._blocked_tool_result("PERMISSION_DENIED", result_str)
            result_str = structured.model_text
            log_tool_call(logger, tool_name, arguments, error=error)
        else:
            pre_hook_raw = e.run_skill_hook(
                skill=hook_skill,
                event=HookEvent.PRE_TOOL_USE,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            pre_hook = await e.resolve_hook_result(
                event=HookEvent.PRE_TOOL_USE,
                hook_result=pre_hook_raw,
                on_event=on_event,
            )
            if pre_hook is not None and isinstance(pre_hook.updated_input, dict):
                arguments = dict(pre_hook.updated_input)
            skip_high_risk_approval_by_hook = (
                pre_hook is not None and pre_hook.decision == HookDecision.ALLOW
            )
            if skip_high_risk_approval_by_hook:
                logger.info(
                    "Hook ALLOW 已生效，跳过确认门禁：tool=%s iteration=%s",
                    tool_name,
                    iteration,
                )

            preflight_error = None
            if getattr(tc, "_parallel_admitted", False) and not self._runtime.is_concurrency_safe(tool_name, arguments):
                from excelmanus.engine_core.tool_result import error_result

                preflight_error = error_result(
                    "工具在排队或 Hook 更新后不再满足只读并发条件；本调用未执行，请重新安排串行调用。",
                    code="TOOL_NOT_ALLOWED", fields={"executed": False},
                )
            if tool_name == "run_shell" and preflight_error is None:
                from excelmanus.tools.shell_tools import preflight_shell

                preflight_error = preflight_shell(arguments, e.file_access_guard)

            if preflight_error is not None:
                structured = preflight_error
                result_str = structured.model_text
                success = False
                error = structured.error.code if structured.error else "INVALID_ARGS"
            elif pre_hook is not None and pre_hook.decision == HookDecision.DENY:
                from excelmanus.engine_core.error_payload import PRE_EXECUTE_DENIED
                from excelmanus.engine_core.tool_result import error_result

                reason = pre_hook.reason or "Hook 拒绝执行该工具。"
                structured = error_result(
                    f"工具调用被 Hook 拒绝：{reason}",
                    code=PRE_EXECUTE_DENIED,
                    fields={"tool": tool_name},
                )
                result_str = structured.model_text
                success = False
                error = PRE_EXECUTE_DENIED
                log_tool_call(logger, tool_name, arguments, error=error)
            elif (
                pre_hook is not None
                and pre_hook.decision == HookDecision.ASK
                and resolve_approval_policy(e) != "never"
            ):
                try:
                    pending = e.approval.create_pending(
                        tool_name=tool_name,
                        arguments=arguments,
                        tool_scope=tool_scope,
                    )
                    persist_runtime = getattr(getattr(e, "_driver", None), "_persist_runtime_state", None)
                    if callable(persist_runtime):
                        persist_runtime()
                    pending_approval = True
                    approval_id = pending.approval_id
                    result_str = e.format_pending_prompt(pending)
                    success = True
                    error = None
                    e.emit_pending_approval_event(
                        pending=pending, on_event=on_event, iteration=iteration,
                        tool_call_id=tool_call_id,
                    )
                    log_tool_call(logger, tool_name, arguments, result=result_str)
                except ValueError:
                    result_str = e.approval.pending_block_message()
                    success = False
                    error = result_str
                    log_tool_call(logger, tool_name, arguments, error=error)
            else:
                from excelmanus.engine_core.spill import (
                    extract_spill_locator,
                    retrieve_spill_result,
                )

                spill_locator = extract_spill_locator(arguments)
                if spill_locator and tool_name in self._SPILL_RETRIEVE_TOOLS and self._workspace_root():
                    structured = retrieve_spill_result(
                        spill_locator, workspace_root=self._workspace_root(),
                    )
                    result_str = structured.model_text
                    success = structured.success
                    error = (
                        None
                        if success
                        else (
                            structured.error.code
                            if structured.error is not None
                            else "NOT_FOUND"
                        )
                    )
                    log_tool_call(logger, tool_name, arguments, result=result_str)
                else:
                    outcome = await self._dispatch_via_handlers(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=arguments,
                        tool_scope=tool_scope,
                        on_event=on_event,
                        iteration=iteration,
                        route_result=route_result,
                        skip_high_risk_approval_by_hook=skip_high_risk_approval_by_hook,
                    )
                    result_str = outcome.result_str
                    success = outcome.success
                    error = outcome.error
                    error_kind = outcome.error_kind
                    pending_approval = outcome.pending_approval
                    approval_id = outcome.approval_id
                    audit_record = outcome.audit_record
                    pending_question = outcome.pending_question
                    question_id = outcome.question_id
                    defer_tool_result = outcome.defer_tool_result
                    finish_accepted = outcome.finish_accepted
                    _raw_result_str = outcome.raw_result_str
                    structured = outcome.structured

            # ── 检测 registry 层返回的结构化错误 JSON ──
            if success and structured is not None and not structured.success:
                success = False
                error = (
                    structured.error.message
                    if structured.error is not None
                    else structured.model_text
                )
                result_str = structured.model_text
            elif success and e.registry.is_error_result(result_str):
                success = False
                try:
                    _err = json.loads(result_str)
                    error = _err.get("message") or _err.get("error") or result_str
                except Exception:
                    error = result_str

            post_hook_event = HookEvent.POST_TOOL_USE if success else HookEvent.POST_TOOL_USE_FAILURE
            post_hook_raw = e.run_skill_hook(
                skill=hook_skill,
                event=post_hook_event,
                payload={
                    "tool_name": tool_name,
                    "arguments": dict(arguments),
                    "success": success,
                    "result": result_str,
                    "error": error,
                    "iteration": iteration,
                },
                tool_name=tool_name,
            )
            post_hook = await e.resolve_hook_result(
                event=post_hook_event,
                hook_result=post_hook_raw,
                on_event=on_event,
            )
            if post_hook is not None:
                if post_hook.additional_context:
                    result_str = f"{result_str}\n[Hook] {post_hook.additional_context}"
                if post_hook.decision == HookDecision.DENY:
                    reason = post_hook.reason or "post hook 拒绝"
                    success = False
                    error = reason
                    result_str = f"{result_str}\n[Hook 拒绝] {reason}"

        # ── 后处理流水线 ──
        result_str, success, error, structured = await self._postprocess_result(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments,
            result_str=result_str,
            success=success,
            error=error,
            iteration=iteration,
            on_event=on_event,
            cow_reminders=_cow_reminders,
            start_time=_t0,
            raw_result_str=_raw_result_str,
            error_kind=error_kind,
            structured=structured,
            parent_call_id=getattr(tc, "parent_call_id", "") or "",
            output_pending=pending_approval or pending_question or defer_tool_result,
        )

        self._note_repeat_outcome(success, structured, error, result_str)

        return ToolCallResult(
            tool_name=tool_name,
            arguments=arguments,
            result=result_str,
            success=success,
            error=error,
            error_kind=error_kind,
            pending_approval=pending_approval,
            approval_id=approval_id,
            audit_record=audit_record,
            pending_question=pending_question,
            question_id=question_id,
            defer_tool_result=defer_tool_result,
            finish_accepted=finish_accepted,
            structured=structured,
        )

    async def _dispatch_via_handlers(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        on_event: "EventCallback | None" = None,
        iteration: int = 0,
        route_result: Any = None,
        skip_high_risk_approval_by_hook: bool = False,
    ) -> "_ToolExecOutcome":
        """通过策略处理器表分发工具执行。

        先查 _specific_handlers O(1) 索引，未命中则遍历 _generic_handlers。
        对 RETRYABLE 错误自动重试（指数退避，不消耗 Agent 迭代预算）。
        """
        session = None
        session_token = None
        sdk_unavailable_token = None
        nested_prev: tuple[int | None, int, str] | None = None
        if tool_name == "run_code":
            from excelmanus.code_mode import (
                attach_sdk_calls,
                build_session_for_run_code,
                get_code_mode_session,
                reset_code_mode_session,
                reset_sdk_unavailable,
                script_uses_sdk,
                set_code_mode_session,
                set_sdk_unavailable,
                timeout_seconds_from_args,
            )

            if get_code_mode_session() is None:
                try:
                    session = build_session_for_run_code(
                        self,
                        root_call_id=tool_call_id,
                        tool_scope=tool_scope,
                        on_event=on_event,
                        timeout_seconds=timeout_seconds_from_args(arguments),
                    )
                    session_token = set_code_mode_session(session)
                    session.start()
                    # 引擎级句柄：子调用协程与 session 处于不同 context，
                    # ContextVar 传不过去，审批等待需经此读到取消事件。
                    engine = getattr(self, "_engine", None)
                    if engine is not None:
                        engine._active_code_mode_session = session
                    nested_prev = self.begin_nested_call_budget()
                except Exception as exc:
                    logger.debug("Code Mode 桥启动失败", exc_info=True)
                    if session_token is not None:
                        reset_code_mode_session(session_token)
                    if session is not None:
                        try:
                            session.stop()
                        except Exception:
                            pass
                    session = None
                    session_token = None
                    # 启动失败：依赖 SDK 的脚本必须明确失败；只有可靠判定
                    # 不依赖 SDK 时才降级执行，并在结果中如实标记。
                    engine = getattr(self, "_engine", None)
                    ws_root = getattr(getattr(engine, "config", None), "workspace_root", None)
                    if script_uses_sdk(arguments, workspace_root=ws_root):
                        from excelmanus.engine_core.error_payload import CODE_MODE_UNAVAILABLE
                        from excelmanus.engine_core.tool_result import error_result

                        structured = error_result(
                            f"Code Mode 桥启动失败，脚本依赖 em SDK，已中止执行: {exc}",
                            code=CODE_MODE_UNAVAILABLE,
                        )
                        return _ToolExecOutcome(
                            result_str=structured.model_text,
                            success=False,
                            error=CODE_MODE_UNAVAILABLE,
                            structured=structured,
                        )
                    sdk_unavailable_token = set_sdk_unavailable(str(exc))
        outcome = None
        try:
            outcome = await self._dispatch_via_handlers_loop(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                tool_scope=tool_scope,
                on_event=on_event,
                iteration=iteration,
                route_result=route_result,
                skip_high_risk_approval_by_hook=skip_high_risk_approval_by_hook,
            )
        finally:
            if session is not None:
                try:
                    session.stop()
                except Exception:
                    logger.debug("Code Mode 桥停止失败", exc_info=True)
                try:
                    await session.wait_settlement(timeout=2.0)
                except Exception:
                    logger.debug("Code Mode 桥结算等待失败", exc_info=True)
                if outcome is not None:
                    if outcome.structured is not None:
                        outcome.structured = attach_sdk_calls(
                            outcome.structured, session,
                        )
                        outcome.result_str = outcome.structured.model_text
                    elif outcome.result_str:
                        from excelmanus.code_mode import apply_sdk_calls_summary

                        outcome.result_str = apply_sdk_calls_summary(
                            outcome.result_str, session,
                        )
                engine = getattr(self, "_engine", None)
                if engine is not None:
                    settled = not session.has_unsettled_work()
                    if settled and getattr(engine, "_active_code_mode_session", None) is session:
                        engine._active_code_mode_session = None
                    elif not settled:
                        logger.warning("Code Mode 桥线程仍在结算，已关闭新提交")
                    # run 结算兜底：子调用审批 pending 不得跨 run 残留。
                    inflight_ids = getattr(engine, "_inflight_approval_ids", None)
                    if inflight_ids:
                        leftover = getattr(getattr(engine, "_approval", None), "pending", None)
                        if leftover is not None and getattr(leftover, "approval_id", None) in inflight_ids:
                            engine._approval.clear_pending()
                        inflight_ids.clear()
                if nested_prev is not None:
                    self.restore_parent_call_budget(nested_prev)
                else:
                    self.begin_call_budget(None)
            if session_token is not None:
                from excelmanus.code_mode import reset_code_mode_session as _reset_cm

                _reset_cm(session_token)
            if sdk_unavailable_token is not None:
                reset_sdk_unavailable(sdk_unavailable_token)
        return outcome

    async def _dispatch_via_handlers_loop(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        on_event: "EventCallback | None" = None,
        iteration: int = 0,
        route_result: Any = None,
        skip_high_risk_approval_by_hook: bool = False,
    ) -> "_ToolExecOutcome":
        """通过策略处理器表分发工具执行（含可重试循环）。"""
        policy = DEFAULT_RETRY_POLICY
        last_outcome: _ToolExecOutcome | None = None
        # 任何可能改变工作区、外部服务或通过 Code Mode 间接改变状态的
        # 调用都不能由 dispatcher 盲目重放。只有工具自己声明幂等并由
        # 上层显式包住 request token 时，才允许未来增加重试。
        write_effect = "none"
        try:
            write_effect = str(self._engine._get_tool_write_effect(tool_name) or "none")
        except Exception:
            write_effect = "unknown"
        retry_mutation = write_effect in {
            "workspace_write", "external_write", "dynamic", "unknown",
        }

        for attempt in range(policy.max_retries + 1):
            outcome = await self._dispatch_single_attempt(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                tool_scope=tool_scope,
                on_event=on_event,
                iteration=iteration,
                route_result=route_result,
                skip_high_risk_approval_by_hook=skip_high_risk_approval_by_hook,
            )
            # 成功或非错误结果直接返回
            if outcome.success:
                return outcome

            # 对失败结果做错误分类
            tool_error = classify_tool_error(
                outcome.error or outcome.result_str,
                tool_name=tool_name,
            )

            # 非可重试错误：压缩后直接返回。
            # 工具已给出契约错误码（版本冲突、越权等）时不要改写成 generic compact JSON。
            if not tool_error.retryable:
                structured = outcome.structured
                code = (
                    structured.error.code
                    if structured is not None and structured.error is not None
                    else ""
                )
                if code and code not in {"TOOL_ERROR", "TOOL_EXECUTION_ERROR"}:
                    return outcome
                compacted = compact_error(outcome.error, tool_error=tool_error)
                return _ToolExecOutcome(
                    result_str=compacted,
                    success=False,
                    error=compacted,
                    error_kind=tool_error.kind.value,
                    audit_record=outcome.audit_record,
                    structured=structured,
                )

            if retry_mutation:
                logger.warning(
                    "工具 %s 可能产生副作用，禁止自动重放；请由模型依据结构化错误决定下一步",
                    tool_name,
                )
                return outcome

            last_outcome = outcome
            # 最后一次重试也失败了
            if attempt >= policy.max_retries:
                break

            # 可重试：等待后重试
            delay = policy.delay_for_attempt(attempt)
            logger.info(
                "工具 %s 可重试错误（%s），第 %d/%d 次重试，等待 %.1fs",
                tool_name, tool_error.summary[:80],
                attempt + 1, policy.max_retries, delay,
            )
            await asyncio.sleep(delay)

        # 所有重试均失败
        final_error = classify_tool_error(
            last_outcome.error or last_outcome.result_str if last_outcome else "unknown",
            tool_name=tool_name,
        )
        last_structured = last_outcome.structured if last_outcome else None
        last_code = (
            last_structured.error.code
            if last_structured is not None and last_structured.error is not None
            else ""
        )
        if last_code and last_code not in {"TOOL_ERROR", "TOOL_EXECUTION_ERROR"}:
            return last_outcome
        compacted = compact_error(
            last_outcome.error if last_outcome else "unknown",
            tool_error=final_error,
        )
        retried_msg = f"{compacted}\n[已自动重试 {policy.max_retries} 次仍失败]"
        return _ToolExecOutcome(
            result_str=retried_msg,
            success=False,
            error=retried_msg,
            error_kind=final_error.kind.value,
            audit_record=last_outcome.audit_record if last_outcome else None,
            structured=last_structured,
        )

    async def _dispatch_single_attempt(
        self,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
        on_event: "EventCallback | None" = None,
        iteration: int = 0,
        route_result: Any = None,
        skip_high_risk_approval_by_hook: bool = False,
    ) -> "_ToolExecOutcome":
        """单次工具执行尝试（从 _dispatch_via_handlers 提取）。"""
        from excelmanus.engine import _AuditedExecutionError

        try:
            # T2: O(1) 索引查找特定工具 handler，未命中时走动态/兜底链
            handler = self._specific_handlers.get(tool_name)
            if handler is None:
                for candidate in self._generic_handlers:
                    if candidate.can_handle(tool_name, arguments=arguments):
                        handler = candidate
                        break
                else:
                    raise RuntimeError(f"No handler found for tool: {tool_name}")

            handler_kwargs: dict[str, Any] = {
                "tool_scope": tool_scope,
                "on_event": on_event,
                "iteration": iteration,
                "route_result": route_result,
            }
            if handler.__class__.__name__ == "HighRiskApprovalHandler":
                handler_kwargs["skip_high_risk_approval_by_hook"] = skip_high_risk_approval_by_hook

            outcome = await handler.handle(
                tool_name,
                tool_call_id,
                arguments,
                **handler_kwargs,
            )
            # 专用 handler（例如 manage_skills）必须保留自己的异步实现，
            # 但不能因此绕过副作用审计。将无审计回执的 side effect 调用
            # 在这里补成同一份 manifest/DB 记录；待审批状态不视为已执行。
            if (
                isinstance(outcome, _ToolExecOutcome)
                and not outcome.pending_approval
                and outcome.audit_record is None
            ):
                await self._attach_side_effect_audit(
                    tool_name=tool_name,
                    arguments=arguments,
                    tool_scope=tool_scope,
                    outcome=outcome,
                )
            return outcome
        except IdentityError as exc:
            from excelmanus.engine_core.tool_result import error_result

            structured = error_result(str(exc), code="PATH_INVALID")
            log_tool_call(logger, tool_name, arguments, error=str(exc))
            return _ToolExecOutcome(
                result_str=structured.model_text,
                success=False,
                error=str(exc),
                structured=structured,
            )
        except ValueError as exc:
            result_str = str(exc)
            log_tool_call(logger, tool_name, arguments, error=result_str)
            return _ToolExecOutcome(result_str=result_str, success=False, error=result_str)
        except ToolNotAllowedError:
            from excelmanus.engine_core.tool_errors import ToolError, ToolErrorKind
            from excelmanus.engine_core.tool_result import error_result

            message = f"工具 '{tool_name}' 不在当前授权范围内。"
            # 与其它工具错误一致，输出 classify/compact 后的 JSON，
            # 携带 error_kind/summary 供模型决策（未授权为永久错误）。
            classified = ToolError(
                kind=ToolErrorKind.PERMANENT,
                summary=f"工具 {tool_name} 未授权（不在当前授权范围内）",
                suggestion="请改用当前授权范围内已启用的工具，或提示用户调整工具授权。",
                original_error=message,
            )
            structured = error_result(
                message,
                code="TOOL_NOT_ALLOWED",
                fields={"tool": tool_name},
            )
            log_tool_call(logger, tool_name, arguments, error=classified.to_compact_str())
            return _ToolExecOutcome(
                result_str=structured.model_text,
                success=False,
                error="TOOL_NOT_ALLOWED",
                structured=structured,
            )
        except Exception as exc:
            root_exc: Exception = exc
            audit_record = None
            if isinstance(exc, _AuditedExecutionError):
                audit_record = exc.record
                root_exc = exc.cause
            result_str = f"工具执行错误: {root_exc}"
            log_tool_call(logger, tool_name, arguments, error=str(root_exc))
            return _ToolExecOutcome(
                result_str=result_str,
                success=False,
                error=str(root_exc),
                audit_record=audit_record,
            )

    async def _attach_side_effect_audit(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None,
        outcome: _ToolExecOutcome,
    ) -> None:
        """为专用/MCP 直通 handler 补齐宿主侧副作用审计。"""
        if tool_name in {"run_code", "run_shell"}:
            return  # 两者各自已有动态审计/审批路径。
        effect = self._write_effect_of(tool_name, arguments)
        if effect == "none":
            return
        # delegate/task 等编排工具的 dynamic effect 不是一次外部提交，
        # 其子调用各自审计；MCP 和专用外部写路径必须留痕，unknown 宿主
        # 工具继续使用既有 mtime 探针，避免给普通调用增加额外线程任务。
        if effect == "dynamic" and tool_name not in {"manage_skills"}:
            return
        if (
            tool_name.startswith("mcp_")
            or tool_name == "manage_skills"
            or effect == "external_write"
        ):
            approval = getattr(self._engine, "approval", None)
            recorder = getattr(approval, "record_completed_call", None)
            if not callable(recorder):
                return
            payload: Any = outcome.structured or outcome.result_str
            try:
                record = await asyncio.to_thread(
                    recorder,
                    approval_id=approval.new_approval_id(),
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    tool_scope=list(tool_scope) if tool_scope else None,
                    result=payload,
                    undoable=(
                        False
                        if tool_name == "manage_skills"
                        else approval.is_undoable_tool(tool_name, arguments)
                    ),
                    created_at_utc=approval.utc_now(),
                    session_turn=getattr(
                        getattr(self._engine, "state", None),
                        "session_turn",
                        None,
                    ),
                    session_id=getattr(self._engine, "_session_id", None),
                )
            except Exception:
                logger.warning("工具副作用审计补写失败: %s", tool_name, exc_info=True)
                return
            outcome.audit_record = record

    async def _postprocess_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        result_str: str,
        success: bool,
        error: str | None,
        iteration: int,
        on_event: "EventCallback | None",
        cow_reminders: list[str],
        start_time: float = 0.0,
        raw_result_str: str | None = None,
        error_kind: str | None = None,
        structured: ToolResult | None = None,
        parent_call_id: str = "",
        output_pending: bool = False,
    ) -> tuple[str, bool, str | None, ToolResult | None]:
        """后处理流水线：CoW/备份/VLM/硬截断/事件/审计/任务清单。

        返回 (result_str, success, error, structured)，其中 success/error 可能被
        结构化错误检测修改。
        """
        from excelmanus.events import EventType, ToolCallEvent

        e = self._engine

        if structured is None and result_str:
            structured = self._coerce_tool_result(result_str)
            if not structured.success:
                success = False
                if not error:
                    error = structured.error.message if structured.error else structured.model_text
        if structured is not None and not success and structured.success:
            structured = ToolResult(
                success=False,
                model_text=result_str,
                value=structured.value,
                error=structured.error
                or ToolError(code="TOOL_ERROR", message=error or result_str),
                ui_meta=structured.ui_meta,
            )
        if structured is not None:
            self._remember_tool_versions(structured)
        if structured is not None and success and not output_pending:
            from excelmanus.tools.output_contracts import enforce_output_contract

            structured = enforce_output_contract(
                structured, tool_name, arguments, tool_def=e.registry.get_tool(tool_name),
            )
            if not structured.success:
                success = False
                error = structured.error.code if structured.error else "SDK_CONTRACT_VIOLATION"
                result_str = structured.model_text
        if structured is not None and structured.error is not None and structured.error.code == "SDK_CONTRACT_VIOLATION":
            error = structured.error.code

        # Handlers may have applied a local text limit already. Recover their
        # original result before making an immutable projection; the final
        # deterministic spill/cap below still bounds what reaches the model.
        if success and not output_pending and raw_result_str and len(raw_result_str) > len(result_str):
            result_str = raw_result_str

        # ── CoW 路径拦截提醒：追加到 model_text ──
        if cow_reminders:
            result_str = result_str + "\n" + "\n".join(cow_reminders)

        # ── 写后语义校验（替代 max_row 维度假象）──
        from excelmanus.engine_core.spill import (
            attach_write_verification,
            compact_write_verification,
            spill_result_text,
            verify_write,
        )

        write_verification: dict[str, Any] | None = None
        skip_hard_cap = bool(
            structured is not None
            and structured.coverage
            and structured.coverage.get("spill_retrieve")
        )
        if success and (
            self._is_excel_mutating_call(tool_name, arguments)
            or tool_name in self._WORD_WRITE_TOOLS
        ):
            verify_args = arguments
            if structured is not None and isinstance(getattr(structured, "value", None), dict):
                post_version = (
                    structured.value.get("content_version")
                    or structured.value.get("target_content_version")
                )
                if post_version:
                    verify_args = {**arguments, "after_version": str(post_version)}
            write_verification = verify_write(
                tool_name, verify_args, workspace_root=self._workspace_root(),
            )
            structured, result_str = attach_write_verification(
                structured, write_verification, result_str,
            )

        if structured is not None and tool_name in self._EXCEL_READ_TOOLS | self._EXCEL_WRITE_TOOLS | {"split_spreadsheet"}:
            from excelmanus.engine_core.spill import expose_spreadsheet_value

            store = self._spill_store()
            if store is not None:
                structured = expose_spreadsheet_value(
                    structured.with_model_text(result_str), store=store, project_large=False,
                )
                result_str = structured.model_text

        unshaped_text = result_str
        # Semantic shaping sees the full result before deterministic spilling.
        # The subsequent spill/hard cap remains authoritative on failure/keep.
        if structured is not None and success and not output_pending:
            from excelmanus.system_one.host import maybe_shape_observation

            try:
                structured = await maybe_shape_observation(
                    e, structured.with_model_text(result_str),
                    tool_name=tool_name, arguments=arguments,
                )
                result_str = structured.model_text
            except Exception:
                logger.debug("Jev result shaping unavailable; using bounded projection", exc_info=True)

        if result_str == unshaped_text and structured is not None and tool_name in self._EXCEL_READ_TOOLS | self._EXCEL_WRITE_TOOLS | {"split_spreadsheet"}:
            from excelmanus.engine_core.spill import expose_spreadsheet_value

            store = self._spill_store()
            if store is not None:
                structured = expose_spreadsheet_value(structured.with_model_text(result_str), store=store)
                result_str = structured.model_text

        result_str, structured = spill_result_text(
            result_str,
            structured,
            store=self._spill_store(),
        )
        if not skip_hard_cap:
            result_str = e._apply_tool_result_hard_cap(result_str)
            if error:
                error = e._apply_tool_result_hard_cap(str(error))
        if structured is not None:
            structured = structured.with_model_text(result_str)

        ui_payload = structured.ui_meta.to_sse_ui() if structured is not None else None

        # 发射 TOOL_CALL_END 事件
        e.emit(
            on_event,
            ToolCallEvent(
                event_type=EventType.TOOL_CALL_END,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result_str,
                success=success,
                error=error,
                iteration=iteration,
                ui=ui_payload,
                parent_call_id=parent_call_id,
            ),
        )

        # 工具执行失败时发出结构化失败引导（仅限基础设施级错误）
        if not success and error:
            _err_lower = str(error).lower()
            _emit_tool_guidance = any(kw in _err_lower for kw in (
                "permission denied", "errno 13",
                "no space left", "disk full", "errno 28",
                "codec can't decode", "codec can't encode",
                "unicodedecodeerror", "unicodeencodeerror",
                "invalid start byte", "invalid continuation byte",
            ))
            if _emit_tool_guidance:
                try:
                    from excelmanus.error_guidance import classify_failure as _clf
                    _tool_guidance = _clf(
                        RuntimeError(str(error)),
                        stage="tool_execution",
                        provider="",
                        model="",
                    )
                    e.emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.FAILURE_GUIDANCE,
                            fg_category=_tool_guidance.category,
                            fg_code=_tool_guidance.code,
                            fg_title=_tool_guidance.title,
                            fg_message=_tool_guidance.message,
                            fg_stage=_tool_guidance.stage,
                            fg_retryable=_tool_guidance.retryable,
                            fg_diagnostic_id=_tool_guidance.diagnostic_id,
                            fg_actions=_tool_guidance.actions,
                            fg_provider=_tool_guidance.provider,
                            fg_model=_tool_guidance.model,
                        ),
                    )
                except Exception:
                    pass

        # 工具调用审计日志
        if self._tool_call_store is not None:
            try:
                _session_id = getattr(e, "_session_id", None)
                self._tool_call_store.log(
                    session_id=_session_id,
                    turn=e.state.session_turn,
                    iteration=iteration,
                    tool_name=tool_name,
                    arguments_hash=e.state._args_fingerprint(arguments) if arguments else None,
                    success=success,
                    duration_ms=(time.monotonic() - start_time) * 1000 if start_time else 0.0,
                    result_chars=len(result_str) if result_str else 0,
                    error_type=error_kind if error_kind else (error[:50] if error else None),
                    error_preview=str(error)[:200] if error else None,
                    call_id=str(tool_call_id or "") or None,
                    parent_call_id=parent_call_id or None,
                )
            except Exception:
                pass

        # Excel/Text/Download 事件：从 ui_meta 投影，不再解析 result 字符串
        if success and structured is not None:
            self._emit_ui_meta_events(
                e,
                on_event,
                tool_call_id,
                tool_name,
                arguments,
                structured.ui_meta,
                iteration,
            )

        # ── 自动追踪 affected_files + write_operations_log ──
        if success:
            _state = getattr(e, "_state", None)
            if _state is not None:
                if (
                    self._is_excel_mutating_call(tool_name, arguments)
                    or tool_name in self._WORD_WRITE_TOOLS
                ):
                    _afp = (arguments.get("file_path") or "").strip()
                    if _afp:
                        _state.record_affected_file(_afp)
                    # 写入操作日志（会话级索引；单元格语义在 meta.write_verification）
                    _summary = self._extract_write_summary(tool_name, arguments, result_str)
                    _verify_bit = compact_write_verification(write_verification)
                    if _verify_bit:
                        _summary = f"{_summary}; {_verify_bit}".strip("; ")
                    _state.record_write_operation(
                        tool_name=tool_name,
                        file_path=_afp,
                        sheet=(arguments.get("sheet") or "").strip(),
                        cell_range=(arguments.get("range") or "").strip(),
                        summary=_summary,
                    )
                elif tool_name == "split_spreadsheet":
                    # 多文件拆分：源文件只读，产物按结果 files 逐条登记
                    try:
                        _split_files: list = []
                        if structured is not None and isinstance(structured.value, dict):
                            _split_files = structured.value.get("files") or []
                        _split_paths: list[str] = []
                        for _sf in _split_files:
                            _sfp = str(_sf.get("file_path") or "").strip() if isinstance(_sf, dict) else ""
                            if _sfp:
                                _state.record_affected_file(_sfp)
                                _split_paths.append(_sfp)
                        _by_col = (arguments.get("by_column") or arguments.get("column") or "").strip()
                        _state.record_write_operation(
                            tool_name=tool_name,
                            file_path=", ".join(_split_paths),
                            summary=f"split_spreadsheet 按 {_by_col or '?'} 拆出 {len(_split_paths)} 个文件",
                        )
                    except Exception:
                        pass
                elif tool_name == "run_code":
                    try:
                        _published_paths = ""
                        if structured is not None and isinstance(structured.value, dict):
                            _items = structured.value.get("published") or []
                            if isinstance(_items, list):
                                for _item in _items:
                                    if not isinstance(_item, dict):
                                        continue
                                    if _item.get("status") != "committed":
                                        continue
                                    _p = str(_item.get("path") or "").strip()
                                    if _p:
                                        _state.record_affected_file(_p)
                                        _published_paths = (
                                            f"{_published_paths}, {_p}" if _published_paths else _p
                                        )
                        if _published_paths or _state.has_write_tool_call:
                            _already_logged = any(
                                e.get("tool_name") == "run_code"
                                for e in _state.write_operations_log
                            )
                            if not _already_logged:
                                _state.record_write_operation(
                                    tool_name="run_code",
                                    file_path=_published_paths,
                                    summary=self._extract_run_code_write_summary(result_str),
                                )
                    except Exception:
                        pass
                elif self._write_effect_of(tool_name, arguments) == "workspace_write":
                    for _pk in ("file_path", "output_path", "path", "target_path",
                                "source", "destination"):
                        _pv = (arguments.get(_pk) or "").strip()
                        if _pv:
                            _state.record_affected_file(_pv)
                    # 通用写入工具日志
                    _first_path = next(
                        ((arguments.get(k) or "").strip() for k in ("file_path", "output_path", "path", "target_path",
                                                                     "source", "destination")
                         if (arguments.get(k) or "").strip()),
                        "",
                    )
                    _state.record_write_operation(
                        tool_name=tool_name,
                        file_path=_first_path,
                    )

        # 写后事件记录到 FileRegistry
        if success:
            _freg = e.file_registry
            if _freg is not None:
                try:
                    # rename_file 特殊处理：原子迁移路径，保留 file_id / provenance
                    if tool_name == "rename_file":
                        _src = (arguments.get("source") or "").strip()
                        _dst = (arguments.get("destination") or "").strip()
                        if _src and _dst:
                            _freg.rename_entry(
                                _src, _dst,
                                session_id=getattr(e, "session_id", None),
                                turn=e.state.session_turn,
                            )

                    _write_paths: list[str] = []
                    if (
                        self._is_excel_mutating_call(tool_name, arguments)
                        or tool_name in self._WORD_WRITE_TOOLS
                    ):
                        _wp = (arguments.get("file_path") or "").strip()
                        if _wp:
                            _write_paths.append(_wp)
                    elif tool_name == "split_spreadsheet":
                        # 源文件只读；产物路径在结果 files 里
                        if structured is not None and isinstance(structured.value, dict):
                            for _sf2 in structured.value.get("files") or []:
                                _sf2p = str(_sf2.get("file_path") or "").strip() if isinstance(_sf2, dict) else ""
                                if _sf2p:
                                    _write_paths.append(_sf2p)
                    elif self._write_effect_of(tool_name, arguments) == "workspace_write":
                        for _pk2 in ("file_path", "output_path", "path", "target_path",
                                     "source", "destination"):
                            _pv2 = (arguments.get(_pk2) or "").strip()
                            if _pv2:
                                _write_paths.append(_pv2)
                    for _wpath in _write_paths:
                        _entry = _freg.get_by_path(_wpath)
                        if _entry is not None:
                            _freg.record_event(
                                _entry.id,
                                "tool_write",
                                tool_name=tool_name,
                                turn=e.state.session_turn,
                            )
                except Exception:
                    logger.debug("FileRegistry 写后事件记录失败", exc_info=True)

        # 任务清单事件：成功执行 task_create/task_update/write_plan 后发射对应事件
        if success and tool_name == "write_plan":
            task_list = e._task_store.current
            if task_list is not None:
                plan_path = e._task_store.plan_file_path or ""
                e.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.PLAN_CREATED,
                        plan_file_path=plan_path,
                        plan_title=task_list.title,
                        plan_task_count=len(task_list.items),
                    ),
                )
                # 同时发射 TASK_LIST_CREATED 以复用前端任务清单渲染
                e.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_LIST_CREATED,
                        task_list_data=task_list.to_dict(),
                    ),
                )
            # write_plan 返回纯文本（非 JSON），_emit_excel_events 无法检测 _text_diff，
            # 因此在此处直接计算 diff 并发射 TEXT_DIFF 事件。
            _plan_content = (arguments.get("content") or "").strip()
            _plan_path = e._task_store.plan_file_path or ""
            if _plan_content and _plan_path and on_event is not None:
                from excelmanus.tools.code_tools import _generate_text_diff
                from excelmanus.workspace.identity import public_identity, workspace_root_of
                _td = _generate_text_diff("", _plan_content, _plan_path)
                if _td is not None:
                    e.emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.TEXT_DIFF,
                            tool_call_id=tool_call_id,
                            text_diff_file_path=public_identity(
                                _td.get("file_path", ""),
                                workspace_root_of(e),
                            ),
                            text_diff_hunks=_td.get("hunks", [])[:300],
                            text_diff_additions=_td.get("additions", 0),
                            text_diff_deletions=_td.get("deletions", 0),
                            text_diff_truncated=_td.get("truncated", False),
                        ),
                    )
        elif success and tool_name == "task_create":
            task_list = e._task_store.current
            if task_list is not None:
                e.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_LIST_CREATED,
                        task_list_data=task_list.to_dict(),
                    ),
                )
        elif success and tool_name == "task_update":
            task_list = e._task_store.current
            if task_list is not None:
                e.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.TASK_ITEM_UPDATED,
                        task_index=arguments.get("task_index"),
                        task_status=arguments.get("status", ""),
                        task_result=arguments.get("result"),
                        task_list_data=task_list.to_dict(),
                    ),
                )

        return result_str, success, error, structured

    # ── uploads 目录快照（检测 run_code 新建/变更文件）────────

    @staticmethod
    def _snapshot_uploads_dir(workspace_root: str) -> dict[str, float] | None:
        """对 uploads/ 目录做轻量 mtime 快照，返回 {rel_path: mtime}。"""
        import os
        from pathlib import Path as _P
        uploads = _P(workspace_root) / "uploads"
        if not uploads.is_dir():
            return None
        snap: dict[str, float] = {}
        try:
            for root, _dirs, files in os.walk(uploads):
                _dirs[:] = [d for d in _dirs if not d.startswith(".")]
                for fname in files:
                    if fname.startswith("."):
                        continue
                    full = os.path.join(root, fname)
                    try:
                        rel = _P("uploads") / os.path.relpath(full, uploads)
                        snap[rel.as_posix()] = os.path.getmtime(full)
                    except OSError:
                        continue
        except OSError:
            return None
        return snap

    @staticmethod
    def _diff_uploads_snapshots(
        before: dict[str, float] | None,
        after: dict[str, float] | None,
    ) -> list[str]:
        """对比 uploads 快照，返回新建或修改的相对路径列表。"""
        if before is None or after is None:
            return []
        changed: list[str] = []
        for rel_path, mtime in after.items():
            if rel_path not in before or before[rel_path] != mtime:
                changed.append(rel_path)
        return changed

    # ── Post-Write Inline Checkpoint ────────────────────────

    @staticmethod
    def _post_write_checkpoint(
        tool_name: str,
        arguments: dict,
        workspace_root: str,
    ) -> str:
        """写入工具成功后的轻量回读（零 LLM）。

        Excel 路径走语义抽样（值/公式），不再只报 max_row。
        空路径 / 未知工具 / 文件不存在时静默返回空串（不影响主流程）。
        """
        from pathlib import Path as _P

        from excelmanus.engine_core.spill import format_write_verification_line, verify_write

        file_path = (arguments.get("file_path") or "").strip()
        if not file_path:
            return ""
        if tool_name not in ToolDispatcher._EXCEL_WRITE_TOOLS and tool_name not in ToolDispatcher._WORD_WRITE_TOOLS:
            return ""

        abs_path = _P(file_path) if _P(file_path).is_absolute() else _P(workspace_root) / file_path
        try:
            abs_path = abs_path.resolve()
        except OSError:
            return ""
        if not abs_path.is_file():
            return ""

        try:
            payload = verify_write(tool_name, arguments, workspace_root=workspace_root)
            if payload.get("skipped"):
                if payload.get("verification_kind") == "style":
                    payload.setdefault(
                        "sheet",
                        arguments.get("sheet") or arguments.get("sheet_name") or "",
                    )
                return format_write_verification_line(payload)
            return format_write_verification_line(payload)
        except Exception:
            return ""

    # ── 写入操作日志辅助 ──────────────────────────────────

    @staticmethod
    def _extract_write_summary(tool_name: str, arguments: dict, result_str: str) -> str:
        """从写入工具的参数/结果中提取简洁摘要。"""
        if tool_name == "edit_spreadsheet":
            ops = arguments.get("operations") or []
            return f"edit_spreadsheet {len(ops)} 项操作" if ops else "edit_spreadsheet"
        if tool_name == "format_spreadsheet":
            ops = arguments.get("operations") or []
            return f"format_spreadsheet {len(ops)} 项操作"
        if tool_name == "manage_spreadsheet_objects":
            ops = arguments.get("operations") or []
            return f"manage_spreadsheet_objects {len(ops)} 项操作"
        if tool_name == "manage_spreadsheet_versions":
            return f"manage_spreadsheet_versions {arguments.get('action') or ''}".strip()
        if tool_name == "write_word":
            ops = arguments.get("operations", [])
            return f"Word 文档写入 {len(ops)} 项操作"
        return ""

    @staticmethod
    def _extract_run_code_write_summary(result_str: str) -> str:
        """从 run_code 的 stdout 中提取写入摘要（取首行非空输出）。"""
        if not result_str:
            return "run_code 写入"
        for line in result_str.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("{") and not stripped.startswith("["):
                return stripped[:120]
        return "run_code 写入"

    # ── Excel 预览/Diff 事件辅助 ────────────────────────────

    _EXCEL_READ_TOOLS = {
        "inspect_spreadsheet",
        "analyze_spreadsheet",
        "compare_spreadsheets",
        "trace_spreadsheet_formulas",
    }
    _SPILL_RETRIEVE_TOOLS = _EXCEL_READ_TOOLS | {
        "read_text_file",
    }
    _EXCEL_WRITE_TOOLS = {
        "edit_spreadsheet",
        "format_spreadsheet",
        "manage_spreadsheet_objects",
        "manage_spreadsheet_versions",
    }
    _WORD_WRITE_TOOLS = {"write_word"}

    @staticmethod
    def _extract_preview_styles(
        file_path: str, sheet_name: str | None, num_rows: int, num_cols: int,
        workspace_root: str,
    ) -> list[list]:
        """Best-effort: 提取 preview 区域的单元格样式（header + data rows）。"""
        from pathlib import Path
        from excelmanus.tools._style_extract import extract_cell_style
        import openpyxl

        abs_path = Path(file_path) if Path(file_path).is_absolute() else Path(workspace_root) / file_path
        abs_path = abs_path.resolve()
        if not abs_path.is_file() or abs_path.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".xlsb"):
            return []

        # .xls/.xlsb → 透明转换为 xlsx
        from excelmanus.tools._helpers import ensure_openpyxl_compatible
        abs_path = ensure_openpyxl_compatible(abs_path)

        wb = openpyxl.load_workbook(str(abs_path), read_only=False, data_only=True)
        try:
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            if ws is None:
                return []
            styles: list[list] = []
            for r in range(1, num_rows + 2):  # +1 header row, +1 for 1-based
                row_styles: list = []
                for c in range(1, num_cols + 1):
                    cell = ws.cell(row=r, column=c)
                    row_styles.append(extract_cell_style(cell))
                styles.append(row_styles)
            return styles
        finally:
            wb.close()

    @staticmethod
    def _extract_file_merge_ranges(
        file_path: str, sheet_name: str | None, workspace_root: str,
    ) -> list[dict[str, int]]:
        """Best-effort: 提取指定工作表的合并单元格区域。"""
        merges, _ = ToolDispatcher._extract_sheet_metadata(file_path, sheet_name, workspace_root)
        return merges

    @staticmethod
    def _extract_sheet_metadata(
        file_path: str, sheet_name: str | None, workspace_root: str,
    ) -> tuple[list[dict[str, int]], list[str]]:
        """Best-effort: 一次打开文件，提取合并区域 + 元数据提示。"""
        from pathlib import Path
        from excelmanus.tools._style_extract import extract_merge_ranges, extract_worksheet_hints
        import openpyxl

        abs_path = Path(file_path) if Path(file_path).is_absolute() else Path(workspace_root) / file_path
        abs_path = abs_path.resolve()
        if not abs_path.is_file() or abs_path.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".xlsb"):
            return [], []

        # .xls/.xlsb → 透明转换为 xlsx
        from excelmanus.tools._helpers import ensure_openpyxl_compatible
        abs_path = ensure_openpyxl_compatible(abs_path)

        wb = openpyxl.load_workbook(str(abs_path), read_only=False, data_only=False)
        try:
            ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
            if ws is None:
                return [], []
            return extract_merge_ranges(ws), extract_worksheet_hints(ws)
        finally:
            wb.close()

    def _record_public_identities(self, e: Any, paths: list[str]) -> list[str]:
        """Normalize to CanonicalPath and remember for the loop-end MutationEvent."""
        from excelmanus.workspace.identity import collect_public_identities, workspace_root_of

        changed = collect_public_identities(paths, workspace_root_of(e))
        state = getattr(e, "_state", None)
        if state is not None:
            for ident in changed:
                state.record_affected_file(ident)
        return changed

    def _emit_ui_meta_events(
        self,
        e: Any,
        on_event: Any,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        ui_meta: ToolUiMeta,
        iteration: int,
    ) -> None:
        """从 ToolResult.ui_meta 投影 SSE 预览/Diff/Download 事件。"""
        from excelmanus.events import EventType, ToolCallEvent

        preview_data = ui_meta.preview
        if isinstance(preview_data, dict):
            columns = preview_data.get("columns") or []
            rows_data = preview_data.get("rows") or []
            if not rows_data:
                preview_records = preview_data.get("preview") or []
                for record in preview_records[:50]:
                    if isinstance(record, dict):
                        rows_data.append([record.get(c) for c in columns])
                    elif isinstance(record, list):
                        rows_data.append(record)
            if columns and rows_data:
                from excelmanus.workspace.identity import public_identity, workspace_root_of
                _ws_root = workspace_root_of(e)
                file_path = public_identity(
                    ui_meta.files[0]
                    if ui_meta.files
                    else arguments.get("file_path", ""),
                    _ws_root,
                )
                sheet_name = preview_data.get("sheet") or arguments.get("sheet_name", "")
                cell_styles: list[list] = []
                merge_ranges: list[dict[str, int]] = []
                metadata_hints: list[str] = []
                try:
                    cell_styles = self._extract_preview_styles(
                        file_path,
                        sheet_name or None,
                        len(rows_data),
                        len(columns),
                        e.config.workspace_root,
                    )
                except Exception:
                    logger.debug("提取预览单元格样式失败", exc_info=True)
                try:
                    merge_ranges, metadata_hints = self._extract_sheet_metadata(
                        file_path,
                        sheet_name or None,
                        e.config.workspace_root,
                    )
                except Exception:
                    logger.debug("提取工作表元数据失败", exc_info=True)
                e.emit(
                    on_event,
                    ToolCallEvent(
                        event_type=EventType.EXCEL_PREVIEW,
                        tool_call_id=tool_call_id,
                        excel_file_path=file_path,
                        excel_sheet=sheet_name,
                        excel_columns=columns[:100],
                        excel_rows=rows_data[:50],
                        excel_total_rows=int(preview_data.get("total_rows") or 0),
                        excel_truncated=bool(preview_data.get("truncated", False)),
                        excel_cell_styles=cell_styles,
                        excel_merge_ranges=merge_ranges,
                        excel_metadata_hints=metadata_hints,
                    ),
                )

        diff_data = ui_meta.diff
        if isinstance(diff_data, dict):
            changes = diff_data.get("sample_diffs") or diff_data.get("changes") or []
            if changes:
                from excelmanus.workspace.identity import public_identity, workspace_root_of
                _ws_root = workspace_root_of(e)
                diff_mode = diff_data.get("diff_mode", "")
                if diff_mode:
                    e.emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.EXCEL_DIFF,
                            tool_call_id=tool_call_id,
                            excel_file_path=public_identity(
                                diff_data.get("file_a") or arguments.get("file_a", ""),
                                _ws_root,
                            ),
                            excel_sheet=diff_data.get("sheet_a") or arguments.get("sheet_a", ""),
                            excel_changes=changes[:200],
                            excel_diff_mode=diff_mode,
                            excel_file_b=public_identity(
                                diff_data.get("file_b") or arguments.get("file_b", ""),
                                _ws_root,
                            ),
                            excel_sheet_b=diff_data.get("sheet_b") or arguments.get("sheet_b", ""),
                            excel_diff_summary=diff_data.get("summary"),
                        ),
                    )
                else:
                    e.emit(
                        on_event,
                        ToolCallEvent(
                            event_type=EventType.EXCEL_DIFF,
                            tool_call_id=tool_call_id,
                            excel_file_path=public_identity(
                                diff_data.get("file_path", ""),
                                _ws_root,
                            ),
                            excel_sheet=diff_data.get("sheet", ""),
                            excel_affected_range=diff_data.get("affected_range", ""),
                            excel_changes=changes[:200],
                            excel_merge_ranges=diff_data.get("new_merge_ranges", []),
                            excel_old_merge_ranges=diff_data.get("old_merge_ranges", []),
                        ),
                    )

        dl_data = ui_meta.download
        if isinstance(dl_data, dict) and dl_data.get("file_path"):
            e.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.FILE_DOWNLOAD,
                    tool_call_id=tool_call_id,
                    download_file_path=dl_data.get("file_path", ""),
                    download_filename=dl_data.get("filename", ""),
                    download_description=dl_data.get("description", ""),
                ),
            )

        td_data = ui_meta.text_diff
        if isinstance(td_data, dict) and td_data.get("hunks"):
            from excelmanus.workspace.identity import public_identity, workspace_root_of
            e.emit(
                on_event,
                ToolCallEvent(
                    event_type=EventType.TEXT_DIFF,
                    tool_call_id=tool_call_id,
                    text_diff_file_path=public_identity(
                        td_data.get("file_path", ""),
                        workspace_root_of(e),
                    ),
                    text_diff_hunks=(td_data.get("hunks") or [])[:300],
                    text_diff_additions=int(td_data.get("additions") or 0),
                    text_diff_deletions=int(td_data.get("deletions") or 0),
                    text_diff_truncated=bool(td_data.get("truncated", False)),
                ),
            )

    def _record_files_from_run_code(
        self,
        e: Any,
        extra_changed_paths: list[str] | None = None,
    ) -> None:
        """Remember Runtime-published / mtime identities. No AST guess, no cow_mapping."""
        raw_paths: list[str] = []
        if extra_changed_paths:
            raw_paths.extend(p for p in extra_changed_paths if p)
        self._record_public_identities(e, raw_paths)
