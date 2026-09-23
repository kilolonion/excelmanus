"""Tools 执行层：工具定义、注册、schema 输出与调用。"""

from __future__ import annotations

import hashlib
import inspect
from importlib import import_module
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

from excelmanus.engine_core.tool_result import ToolResult, error_result
from excelmanus.logger import get_logger
from excelmanus.security import SecurityViolationError
from excelmanus.tools._helpers import (
    OUTSIDE_WORKSPACE_MESSAGE,
    OUTSIDE_WORKSPACE_REMEDIATION,
)

logger = get_logger("tools")

_PATH_LIKE_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "file_path",
        "path",
        "source",
        "destination",
        "directory",
        "workdir",
        "script_path",
        "stdout_file",
        "stderr_file",
        "spec_path",
        "excel_path",
        "output_path",
        "target_path",
        "source_file",
        "target_file",
    }
)

OpenAISchemaMode = Literal["responses", "chat_completions"]
SchemaValidationMode = Literal["off", "shadow", "enforce"]
WriteEffect = Literal[
    "none",
    "workspace_write",
    "external_write",
    "dynamic",
    "unknown",
]
ToolVisibility = Literal["always", "hide_in_read"]
ToolConsistency = Literal["local_commit", "external_unverified", "none"]
ApprovalMode = Literal["none", "audit", "confirm"]


@dataclass(frozen=True)
class ToolCapability:
    """一次工具调用的副作用合同。

    ``ToolDef`` 仍保留旧字段以兼容插件，但运行时策略应读取这个派生
    合同。这样审批、审计、撤销、幂等与超时不会再由多个静态集合各自
    猜测。插件可通过 ``ToolDef(capability=...)`` 显式声明；未声明时由
    ``ToolDef.effective_capability`` 根据写效应和动作表生成保守默认值。
    """

    effect: WriteEffect = "unknown"
    approval: ApprovalMode = "confirm"
    audit: bool = True
    undoable: bool = False
    idempotent: bool = False
    timeout_seconds: float | None = None
    consistency: ToolConsistency = "external_unverified"
    compensation: str | None = None

    @property
    def is_read_only(self) -> bool:
        return self.effect == "none"

    @property
    def is_external(self) -> bool:
        return self.effect == "external_write" or self.consistency == "external_unverified"

# 内置工具模块清单（单一事实源）：
# 1) 该顺序即注册顺序；
# 2) register_builtin_tools 与说明文档均以此为准，避免注释与实现漂移。
_WORKBOOK_IMPL_MODULE_PATHS: tuple[str, ...] = (
    "excelmanus.workbook.data",
    "excelmanus.workbook.sheets",
    "excelmanus.workbook.charts",
    "excelmanus.workbook.cells",
    "excelmanus.workbook.styles",
)
_GUARD_ONLY_MODULE_PATHS: tuple[str, ...] = (
    "excelmanus.tools.reference_tools",
)
_BUILTIN_TOOL_MODULE_PATHS: tuple[str, ...] = (
    "excelmanus.tools.file_tools",
    "excelmanus.tools.code_tools",
    "excelmanus.tools.shell_tools",
    "excelmanus.tools.intent_tools",
    "excelmanus.tools.image_tools",
    "excelmanus.tools.memory_tools",
    "excelmanus.tools.sleep_tools",
    "excelmanus.tools.word_tools",
)


_ALIAS_FOLDS: tuple[tuple[str, str], ...] = (
    ("path", "file_path"),
    ("sheet", "sheet_name"),
    ("content_version", "expected_version"),
    ("cell_range", "range"),
    ("other_path", "file_b"),
    ("file_path", "file_a"),
    ("sheet", "sheet_a"),
    ("other_sheet", "sheet_b"),
    ("paths", "file_paths"),
    ("column", "by_column"),
    ("limit", "max_rows"),
)

# 工具名兼容别名：模型常见的近名误称 → 规范名。
# 只在查找时折叠，不进入 _tools 字典——catalog/schema/digest 仍以规范名面示，
# 别名工具不会作为独立条目出现在工具目录中。
_TOOL_NAME_ALIASES: dict[str, str] = {
    "write_text": "write_text_file",
}


def canonical_tool_name(tool_name: str) -> str:
    """把已知的工具名别名折到规范名；未知名原样返回。"""
    return _TOOL_NAME_ALIASES.get(tool_name, tool_name)


def example_arguments(schema: dict[str, Any] | None, *, _depth: int = 0) -> Any:
    """从 input_schema 合成最小合法参数示例，附在参数错误上供模型自纠。

    只生成 required 字段；anyOf/oneOf 的 required 分支并入第一项；深度限 6 层。
    """
    if not isinstance(schema, dict) or _depth > 6:
        return {}
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((t for t in schema_type if t != "null"), None)
    properties = schema.get("properties")
    if schema_type == "object" or isinstance(properties, dict):
        props = properties if isinstance(properties, dict) else {}
        names: list[str] = []
        required = schema.get("required")
        if isinstance(required, list):
            names.extend(name for name in required if isinstance(name, str))
        for combo in ("anyOf", "oneOf"):
            branches = schema.get(combo)
            if not isinstance(branches, list):
                continue
            for branch in branches:
                if isinstance(branch, dict) and isinstance(branch.get("required"), list):
                    names.extend(
                        name for name in branch["required"] if isinstance(name, str)
                    )
                    break
        example: dict[str, Any] = {}
        for name in dict.fromkeys(names):
            field_schema = props.get(name)
            example[name] = (
                example_arguments(field_schema, _depth=_depth + 1)
                if isinstance(field_schema, dict)
                else f"<{name}>"
            )
        return example
    if schema_type == "array":
        items = schema.get("items")
        item = (
            example_arguments(items, _depth=_depth + 1)
            if isinstance(items, dict)
            else "<item>"
        )
        return [item]
    if schema_type in {"integer", "number"}:
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            return minimum
        return 1
    if schema_type == "boolean":
        return True
    if schema_type == "string":
        return "<string>"
    for combo in ("oneOf", "anyOf"):
        options = schema.get(combo)
        if isinstance(options, list):
            for option in options:
                if isinstance(option, dict):
                    return example_arguments(option, _depth=_depth + 1)
    return {}


def _schema_property_names(schema: dict[str, Any] | None) -> set[str] | None:
    if schema is None:
        return None
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return set()
    return {str(name) for name in properties}


def normalize_tool_aliases(
    arguments: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any] | ToolResult:
    """把 path/sheet/content_version 折到规范名；冲突则报错。

    仅当 schema 含规范名时才折叠，避免把只接受 ``path`` 的工具改坏。
    未传 schema 时保持无条件折叠，供单测直接断言冲突。
    """
    from excelmanus.tools.reference_contract import normalize_structured_references

    args, reference_error = normalize_structured_references(dict(arguments), schema=schema)
    if reference_error is not None:
        return reference_error
    accepted = _schema_property_names(schema)
    if accepted is not None and "request" in accepted and isinstance(args.get("request"), dict):
        nested = args.pop("request")
        if "request" in nested:
            return error_result("request 不能继续嵌套 request", code="TOOL_ARGUMENT_VALIDATION_ERROR")
        normalized_nested = normalize_tool_aliases(nested, schema)
        normalized_flat = normalize_tool_aliases(args, schema)
        if isinstance(normalized_nested, ToolResult):
            return normalized_nested
        if isinstance(normalized_flat, ToolResult):
            return normalized_flat
        for key, value in normalized_nested.items():
            if key in normalized_flat and normalized_flat[key] not in (None, "") and normalized_flat[key] != value:
                return error_result(f"request.{key} 与平铺 {key} 冲突", code="TOOL_ARGUMENT_VALIDATION_ERROR")
            normalized_flat[key] = value
        args = normalized_flat
    for src, dest in (_ALIAS_FOLDS if accepted is not None else _ALIAS_FOLDS[:3]):
        if src not in args:
            continue
        if accepted is not None and dest not in accepted:
            continue
        src_val = args[src]
        dest_val = args.get(dest)
        if (
            dest_val not in (None, "")
            and src_val not in (None, "")
            and dest_val != src_val
        ):
            return error_result(
                f"别名冲突：{src} 与 {dest} 值不同",
                code="TOOL_ARGUMENT_VALIDATION_ERROR",
                fields={
                    "violations": [f"{src}={src_val!r} 与 {dest}={dest_val!r}"],
                    "accepted_fields": [dest],
                    "required_fields": [],
                },
            )
        if dest not in args or args[dest] in (None, ""):
            args[dest] = src_val
        args.pop(src, None)
    return args


class ToolRegistryError(Exception):
    """工具注册失败。"""


class ToolNotFoundError(Exception):
    """调用未注册工具。"""


class ToolExecutionError(Exception):
    """工具执行失败。"""


class ToolNotAllowedError(Exception):
    """工具未被当前 Skillpack 授权。"""


@dataclass
class ToolDef:
    """工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    async_func: Callable[..., Any] | None = None
    sensitive_fields: set[str] = field(default_factory=set)
    max_result_chars: int = 3000
    truncate_head_chars: int | None = None
    truncate_tail_chars: int = 0
    # 写入语义声明（宿主审批、审计、缓存和写入追踪的能力来源）
    write_effect: WriteEffect = "unknown"
    visibility: ToolVisibility = "always"
    consistency: ToolConsistency = "local_commit"
    # Success value JSON Schema (2020-12). When absent, use the built-in
    # OUTPUT_CONTRACTS declaration; undeclared external tools remain unknown.
    output_schema: dict[str, Any] | None = None
    actions: dict[str, Any] = field(default_factory=dict)
    # Optional unified capability contract.  Kept at the end so third-party
    # ToolDef constructors using the historical positional fields keep working.
    capability: ToolCapability | None = None
    cache_ttl_seconds: float | None = None
    timeout_seconds: float | None = None

    def effective_capability(self, arguments: dict[str, Any] | None = None) -> ToolCapability:
        """Return the single capability contract used by policy/runtime layers."""
        if self.capability is not None:
            # Explicit provider/host declarations are authoritative.  Never
            # re-infer an MCP capability from a remote name or argument key.
            return self.capability

        from excelmanus.tools.policy import (
            MUTATING_CONFIRM_TOOLS,
            MUTATING_AUDIT_ONLY_TOOLS,
            normalize_write_effect,
            write_effect_for_call,
        )

        effect = normalize_write_effect(self.write_effect)
        if arguments:
            effect = write_effect_for_call(
                self.name, arguments, declared=effect, actions=self.actions,
            )
        if effect == "none":
            return ToolCapability(
                effect="none", approval="none", audit=False, undoable=False,
                idempotent=True, timeout_seconds=self._default_timeout(),
                consistency="none",
            )
        if effect == "workspace_write":
            approval: ApprovalMode = (
                "confirm" if self.name in MUTATING_CONFIRM_TOOLS else "audit"
            )
            # A declared workspace mutation is undoable only when it has a
            # local commit path. Dynamic action descriptors can explicitly
            # opt out (for example a destructive irreversible action).
            undoable = bool(self.actions.get("undoable", True))
            if self.name in {"run_code", "run_shell"}:
                undoable = False
            return ToolCapability(
                effect="workspace_write", approval=approval, audit=True,
                undoable=undoable, idempotent=bool(self.actions.get("idempotent", False)),
                timeout_seconds=self._default_timeout(), consistency=self.consistency,
                compensation=self.actions.get("compensation"),
            )
        # External, dynamic and unknown effects fail closed.  They retain a
        # durable audit record and never claim automatic undo.
        return ToolCapability(
            effect=effect, approval="confirm", audit=True, undoable=False,
            idempotent=False, timeout_seconds=self._default_timeout(),
            consistency=("external_unverified" if effect == "external_write" else self.consistency),
            compensation=self.actions.get("compensation"),
        )

    def _default_timeout(self) -> float | None:
        if self.timeout_seconds is not None:
            try:
                return float(self.timeout_seconds)
            except (TypeError, ValueError):
                return None
        value = self.actions.get("timeout_seconds") if isinstance(self.actions, dict) else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def truncate_result(self, text: str) -> str:
        """若文本超过 max_result_chars 则截断并附加提示。

        对 JSON 格式的工具结果采用智能截断策略：
        保留所有非 list 的元数据字段，仅缩减最大的 list 字段（通常是 data/preview），
        确保截断后的结果仍是合法 JSON，且关键元数据不丢失。
        """
        limit = self.max_result_chars
        if limit <= 0 or len(text) <= limit:
            return text
        # 尝试 JSON 感知截断
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                truncated = self._truncate_json_smart(parsed, limit)
                if truncated is not None:
                    return truncated
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        # 回退到字符截断
        return self._truncate_text_fallback(text, limit)

    def _truncate_text_fallback(self, text: str, limit: int) -> str:
        """纯文本回退截断：默认前缀截断，可配置首尾保留。"""
        tail_chars = int(self.truncate_tail_chars)
        if tail_chars > 0:
            head_chars = self.truncate_head_chars
            if head_chars is None or head_chars <= 0:
                head_chars = max(limit - tail_chars, 0)
            if head_chars > 0 and len(text) > head_chars + tail_chars:
                return (
                    f"{text[:head_chars]}\n"
                    f"[结果中间已截断，保留前 {head_chars} 字符和后 {tail_chars} 字符，"
                    f"原始长度: {len(text)} 字符]\n"
                    f"{text[-tail_chars:]}"
                )

        return f"{text[:limit]}\n[结果已截断，原始长度: {len(text)} 字符]"

    @staticmethod
    def _truncate_json_smart(data: dict, limit: int) -> str | None:
        """JSON 感知截断：保留元数据，缩减最大的 list 或 string 字段。

        策略：
        1. 优先找 dict 中最大的 list 字段，逐步减半其元素数量。
        2. 若无可缩减的 list 字段（或 list 缩减到 0 仍超限），
           则找最大的 string 字段，从头部截断（保留尾部，因为 stdout 尾部更有用）。

        注意：操作副本，不修改原始 data 对象。
        """
        # ── 阶段 1：尝试缩减最大的 list 字段 ──
        list_fields = {
            k: len(v) for k, v in data.items()
            if isinstance(v, list) and len(v) > 0
        }
        if list_fields:
            sorted_fields = sorted(list_fields.keys(), key=lambda k: list_fields[k], reverse=True)
            target_field = sorted_fields[0]
            original_list = data[target_field]
            original_len = len(original_list)

            working = dict(data)

            lo, hi = 0, original_len
            best_result: str | None = None

            while lo <= hi:
                mid = (lo + hi) // 2
                working[target_field] = original_list[:mid] if mid > 0 else []

                if mid < original_len:
                    working[f"_{target_field}_truncated"] = True
                    working[f"_{target_field}_note"] = (
                        f"⚠️ 完整数据有 {original_len} 行，仅展示前 {mid} 行"
                        f"（字段: {target_field}）。"
                        f"如需更多数据，可用 offset/max_rows 分页读取，"
                        f"或用 run_code 执行 pandas 操作全量数据。"
                    )
                else:
                    working.pop(f"_{target_field}_truncated", None)
                    working.pop(f"_{target_field}_note", None)

                try:
                    candidate = json.dumps(working, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    return None

                if len(candidate) <= limit:
                    best_result = candidate
                    lo = mid + 1
                else:
                    hi = mid - 1

            if best_result is not None:
                return best_result

        # ── 阶段 2：缩减最大的 dict 字段（如 styles、dtypes） ──
        # 按序列化大小过滤，跳过小 dict（如 shape: {rows, columns}）
        dict_fields: dict[str, int] = {}
        for k, v in data.items():
            if not isinstance(v, dict) or k.startswith("_"):
                continue
            try:
                sz = len(json.dumps(v, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                continue
            if sz > 200:
                dict_fields[k] = sz
        if dict_fields:
            working_d = dict(data)
            sorted_dicts = sorted(dict_fields.keys(), key=lambda k: dict_fields[k], reverse=True)
            target_dict_field = sorted_dicts[0]
            original_dict = working_d[target_dict_field]
            original_dict_len = len(original_dict)
            dict_items = list(original_dict.items())

            lo_d, hi_d = 0, original_dict_len
            best_dict_result: str | None = None

            while lo_d <= hi_d:
                mid_d = (lo_d + hi_d) // 2
                trimmed = dict(dict_items[:mid_d]) if mid_d > 0 else {}
                if mid_d < original_dict_len:
                    trimmed["__truncated__"] = (
                        f"保留 {mid_d}/{original_dict_len} 项"
                    )
                working_d[target_dict_field] = trimmed

                try:
                    candidate_d = json.dumps(working_d, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    break

                if len(candidate_d) <= limit:
                    best_dict_result = candidate_d
                    lo_d = mid_d + 1
                else:
                    hi_d = mid_d - 1

            if best_dict_result is not None:
                return best_dict_result

        # ── 阶段 3：缩减最大的 string 字段（保留尾部） ──
        str_fields = {
            k: len(v) for k, v in data.items()
            if isinstance(v, str) and len(v) > 100  # 只考虑较长的字符串
            and not k.startswith("_")  # 跳过内部标记字段
        }
        if not str_fields:
            return None

        sorted_str = sorted(str_fields.keys(), key=lambda k: str_fields[k], reverse=True)
        target_str_field = sorted_str[0]
        original_str = data[target_str_field]
        original_str_len = len(original_str)

        working = dict(data)

        # 二分搜索：保留尾部多少字符可以 fit
        lo, hi = 0, original_str_len
        best_result = None

        while lo <= hi:
            mid = (lo + hi) // 2
            if mid < original_str_len:
                kept = original_str[-mid:] if mid > 0 else ""
                working[target_str_field] = kept
                working[f"_{target_str_field}_note"] = (
                    f"⚠️ 原始输出 {original_str_len} 字符，已截断前部，保留后 {mid} 字符。"
                    f"如需完整输出，可在 run_code 中将结果写入文件后用 read_text_file 查看。"
                )
            else:
                working[target_str_field] = original_str
                working.pop(f"_{target_str_field}_note", None)

            try:
                candidate = json.dumps(working, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                return None

            if len(candidate) <= limit:
                best_result = candidate
                lo = mid + 1
            else:
                hi = mid - 1

        return best_result

    def to_openai_schema(
        self, mode: OpenAISchemaMode = "responses"
    ) -> dict[str, Any]:
        """转换为 OpenAI 工具 schema。"""
        from excelmanus.tools.reference_contract import augment_reference_schema

        parameters = augment_reference_schema(self.input_schema)
        if mode == "responses":
            return {
                "type": "function",
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            }
        if mode == "chat_completions":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": parameters,
                },
            }
        raise ValueError(f"不支持的 OpenAI schema 模式: {mode!r}")


class ToolRegistry:
    """工具注册中心。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}
        self._schema_validation_mode: SchemaValidationMode = "off"
        self._schema_validation_canary_percent: int = 100
        self._schema_strict_path: bool = False
        self._catalog_revision: int = 0
        self._catalog_mode: str = "write"
        self._catalog_allowed: tuple[str, ...] | None = None
        self._catalog_disallowed: tuple[str, ...] = ()
        self._catalog_extra_tools: tuple[ToolDef, ...] = ()
        self._catalog_skill_names: tuple[str, ...] = ()
        self._catalog_allow_run_code: bool = False
        self._catalog_families: frozenset[str] | None = None

    def fork(self) -> "ToolRegistry":
        """创建一个 per-session 的 overlay registry。

        新实例持有当前所有工具的浅拷贝，后续注册互不影响。
        用于 API 多会话场景：每个 AgentEngine 持有独立 registry，
        避免会话级工具（task_tools / skill_tools）重复注册冲突。
        """
        child = ToolRegistry()
        child._tools = dict(self._tools)  # 浅拷贝：ToolDef 不可变，安全
        child._schema_validation_mode = self._schema_validation_mode
        child._schema_validation_canary_percent = self._schema_validation_canary_percent
        child._schema_strict_path = self._schema_strict_path
        child._catalog_revision = self._catalog_revision
        child._catalog_mode = self._catalog_mode
        child._catalog_allowed = self._catalog_allowed
        child._catalog_disallowed = self._catalog_disallowed
        child._catalog_extra_tools = self._catalog_extra_tools
        child._catalog_skill_names = self._catalog_skill_names
        child._catalog_allow_run_code = self._catalog_allow_run_code
        child._catalog_families = self._catalog_families
        return child

    def _bump_catalog(self) -> None:
        self._catalog_revision += 1

    def bind_catalog(
        self,
        *,
        mode: str = "write",
        allowed: Sequence[str] | None = None,
        disallowed: Sequence[str] = (),
        extra_tools: Sequence[ToolDef] = (),
        skill_names: Sequence[str] = (),
        allow_run_code: bool = False,
        families: frozenset[str] | None = None,
    ) -> None:
        """绑定有效目录投影参数。digest / schemas 都读这组参数。"""
        from excelmanus.tools.catalog import resolve_catalog_mode

        self._catalog_mode = resolve_catalog_mode(chat_mode=mode)
        self._catalog_allowed = tuple(allowed) if allowed is not None else None
        self._catalog_disallowed = tuple(str(name) for name in disallowed)
        self._catalog_extra_tools = tuple(extra_tools)
        self._catalog_skill_names = tuple(str(name) for name in skill_names if str(name).strip())
        self._catalog_allow_run_code = bool(allow_run_code)
        self._catalog_families = families

    def effective_catalog(self) -> Any:
        """当前绑定参数下的 EffectiveToolCatalog。"""
        from excelmanus.tools.catalog import derive_effective_catalog

        return derive_effective_catalog(
            tools=self.get_all_tools(),
            mode=self._catalog_mode,
            allowed=self._catalog_allowed,
            disallowed=self._catalog_disallowed,
            extra_tools=self._catalog_extra_tools,
            skill_names=self._catalog_skill_names,
            allow_run_code=self._catalog_allow_run_code,
            families=self._catalog_families,
        )

    def catalog_digest(self) -> str:
        """有效目录的稳定内容摘要，供 EpochIdentity.catalog_digest。"""
        return str(self.effective_catalog().digest())

    def restrict(
        self,
        *,
        allowed: Sequence[str] | None = None,
        disallowed: Sequence[str] = (),
    ) -> None:
        """按名收窄目录。``allowed`` 为 None 时只剔除 ``disallowed``。"""
        blocked = set(disallowed)
        if allowed is not None:
            keep = set(allowed) - blocked
            self._tools = {name: tool for name, tool in self._tools.items() if name in keep}
        else:
            for name in blocked:
                self._tools.pop(name, None)
        self._bump_catalog()

    def remove_tools(self, names: Sequence[str]) -> int:
        """按名移除工具并 bump catalog。返回实际移除数量。"""
        removed = 0
        for name in names:
            if self._tools.pop(name, None) is not None:
                removed += 1
        if removed:
            self._bump_catalog()
        return removed

    def configure_schema_validation(
        self,
        *,
        mode: SchemaValidationMode,
        canary_percent: int,
        strict_path: bool,
    ) -> None:
        """配置工具参数 schema 校验策略。"""
        normalized_mode = str(mode or "off").strip().lower()
        if normalized_mode not in {"off", "shadow", "enforce"}:
            raise ValueError(
                "schema validation mode 仅支持 off/shadow/enforce，"
                f"当前值: {mode!r}"
            )
        canary = int(canary_percent)
        if canary < 0 or canary > 100:
            raise ValueError(
                "schema validation canary_percent 必须在 0..100，"
                f"当前值: {canary_percent!r}"
            )
        self._schema_validation_mode = normalized_mode  # type: ignore[assignment]
        self._schema_validation_canary_percent = canary
        self._schema_strict_path = bool(strict_path)

    @staticmethod
    def _schema_type_matches(value: Any, schema_type: Any) -> bool:
        """检查值是否匹配 JSON Schema type（支持 type 字符串/数组）。"""
        if schema_type is None:
            return True

        expected_types: list[str] = []
        if isinstance(schema_type, str):
            expected_types = [schema_type]
        elif isinstance(schema_type, list):
            expected_types = [item for item in schema_type if isinstance(item, str)]
        if not expected_types:
            return True

        def _single_match(tp: str) -> bool:
            if tp == "object":
                return isinstance(value, dict)
            if tp == "array":
                return isinstance(value, list)
            if tp == "string":
                return isinstance(value, str)
            if tp == "integer":
                return isinstance(value, int) and not isinstance(value, bool)
            if tp == "number":
                return (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                )
            if tp == "boolean":
                return isinstance(value, bool)
            if tp == "null":
                return value is None
            return True

        return any(_single_match(tp) for tp in expected_types)

    @staticmethod
    def _contains_parent_traversal(path_value: str) -> bool:
        """检查路径文本是否包含父目录穿越片段。"""
        normalized = path_value.replace("\\", "/")
        return ".." in Path(normalized).parts

    @staticmethod
    def _is_path_like_field(field_name: str) -> bool:
        """判断字段名是否属于路径类参数。"""
        return field_name in _PATH_LIKE_FIELD_NAMES

    def _collect_schema_violations(
        self,
        *,
        value: Any,
        schema: dict[str, Any],
        path: str,
        violations: list[str],
    ) -> None:
        """递归收集 schema 违规项（轻量子集校验）。"""
        one_of = schema.get("oneOf")
        if isinstance(one_of, list) and one_of:
            matched_count = 0
            for option in one_of:
                if not isinstance(option, dict):
                    continue
                option_violations: list[str] = []
                self._collect_schema_violations(
                    value=value,
                    schema=option,
                    path=path,
                    violations=option_violations,
                )
                if not option_violations:
                    matched_count += 1
            if matched_count != 1:
                violations.append(
                    f"{path}: 必须匹配 oneOf 的 1 个分支，当前匹配 {matched_count} 个"
                )
                return

        any_of = schema.get("anyOf")
        if isinstance(any_of, list) and any_of:
            matched_count = 0
            branch_hints: list[str] = []
            for option in any_of:
                if not isinstance(option, dict):
                    continue
                option_violations: list[str] = []
                self._collect_schema_violations(
                    value=value,
                    schema=option,
                    path=path,
                    violations=option_violations,
                )
                if not option_violations:
                    matched_count += 1
                elif option_violations:
                    branch_hints.append(option_violations[0])
            if matched_count == 0:
                detail = f"（{'；'.join(branch_hints[:3])}）" if branch_hints else ""
                violations.append(f"{path}: 必须满足 anyOf 至少一个分支{detail}")
                return

        schema_type = schema.get("type")
        if not self._schema_type_matches(value, schema_type):
            violations.append(
                f"{path}: 类型不匹配，期望 {schema_type!r}，实际 {type(value).__name__}"
            )
            return

        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values and value not in enum_values:
            violations.append(f"{path}: 不在允许枚举中，当前值 {value!r}")

        if isinstance(value, str):
            min_length = schema.get("minLength")
            if isinstance(min_length, int) and len(value) < min_length:
                violations.append(f"{path}: 长度不能小于 {min_length}")
            max_length = schema.get("maxLength")
            if isinstance(max_length, int) and len(value) > max_length:
                violations.append(f"{path}: 长度不能超过 {max_length}")
            pattern = schema.get("pattern")
            if isinstance(pattern, str) and pattern:
                import re as _re
                if not _re.search(pattern, value):
                    violations.append(f"{path}: 不匹配 pattern {pattern!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            if isinstance(minimum, (int, float)) and value < minimum:
                violations.append(f"{path}: 不能小于 {minimum}")
            maximum = schema.get("maximum")
            if isinstance(maximum, (int, float)) and value > maximum:
                violations.append(f"{path}: 不能超过 {maximum}")

        if isinstance(value, list):
            min_items = schema.get("minItems")
            if isinstance(min_items, int) and len(value) < min_items:
                violations.append(f"{path}: 列表长度不能小于 {min_items}")
            max_items = schema.get("maxItems")
            if isinstance(max_items, int) and len(value) > max_items:
                violations.append(f"{path}: 列表长度不能超过 {max_items}")
            items_schema = schema.get("items")
            if isinstance(items_schema, dict):
                for index, item in enumerate(value):
                    self._collect_schema_violations(
                        value=item,
                        schema=items_schema,
                        path=f"{path}[{index}]",
                        violations=violations,
                    )
            return

        if not isinstance(value, dict):
            return

        properties = schema.get("properties")
        properties_map = properties if isinstance(properties, dict) else {}

        required = schema.get("required")
        if isinstance(required, list):
            for field_name in required:
                if isinstance(field_name, str) and field_name not in value:
                    violations.append(f"{path}.{field_name}: 缺少必填字段")

        if schema.get("additionalProperties") is False and properties_map:
            for field_name in sorted(value.keys()):
                if field_name not in properties_map:
                    violations.append(f"{path}.{field_name}: 非法字段（schema 未声明）")

        for field_name, field_value in value.items():
            field_path = f"{path}.{field_name}"
            if (
                self._schema_strict_path
                and self._is_path_like_field(field_name)
                and isinstance(field_value, str)
                and field_value.strip()
            ):
                if (
                    Path(field_value).is_absolute()
                    # Windows 上 '/etc/passwd' 不算绝对路径，但模型传的
                    # POSIX 风格绝对路径同样不是相对路径，一并拒绝。
                    or field_value.startswith(("/", "\\"))
                ):
                    violations.append(f"{field_path}: 必须使用相对路径，不允许绝对路径")
                if self._contains_parent_traversal(field_value):
                    violations.append(f"{field_path}: 不允许包含 '..' 路径穿越片段")

            field_schema = properties_map.get(field_name)
            if isinstance(field_schema, dict):
                self._collect_schema_violations(
                    value=field_value,
                    schema=field_schema,
                    path=field_path,
                    violations=violations,
                )

    def _is_canary_hit(self, *, tool_name: str, arguments: dict[str, Any]) -> bool:
        """判断当前请求是否命中 enforce 灰度桶（稳定哈希）。"""
        percent = self._schema_validation_canary_percent
        if percent >= 100:
            return True
        if percent <= 0:
            return False
        payload = {
            "tool": tool_name,
            "arguments": arguments,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        bucket = int(hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < percent

    def _resolve_schema_validation_decision(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> SchemaValidationMode:
        """解析本次调用的 schema 校验决策（off/shadow/enforce）。"""
        mode = self._schema_validation_mode
        if mode == "off":
            return "off"
        if mode == "shadow":
            return "shadow"
        # 模式为 "enforce" 时
        return "enforce" if self._is_canary_hit(tool_name=tool_name, arguments=arguments) else "shadow"

    def validate_arguments_by_schema(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        schema: dict[str, Any] | None,
    ) -> ToolResult | None:
        """按当前策略校验参数 schema。

        Returns:
            ToolResult | None: 当命中 enforce 且校验失败时返回结构化错误；
            其余情况返回 None（包括 shadow 仅打日志）。
        """
        if not isinstance(schema, dict):
            return None

        decision = self._resolve_schema_validation_decision(
            tool_name=tool_name,
            arguments=arguments,
        )
        if decision == "off":
            return None

        violations: list[str] = []
        self._collect_schema_violations(
            value=arguments,
            schema=schema,
            path="$",
            violations=violations,
        )
        if not violations:
            return None

        if decision == "shadow":
            logger.warning(
                "工具 '%s' schema 校验失败（shadow）：%s; arguments=%s",
                tool_name,
                violations,
                arguments,
            )
            return None

        logger.warning(
            "工具 '%s' schema 校验失败（enforce）：%s; arguments=%s",
            tool_name,
            violations,
            arguments,
        )
        return self._format_argument_schema_validation_error(
            tool_name=tool_name,
            arguments=arguments,
            schema=schema,
            violations=violations,
        )

    def register_tool(self, tool: ToolDef) -> None:
        """注册单个工具。"""
        if tool.name in self._tools:
            raise ToolRegistryError(f"工具 '{tool.name}' 已注册，不允许重复。")
        self._tools[tool.name] = tool
        self._bump_catalog()
        logger.info("已注册工具 '%s'", tool.name)

    def register_tools(self, tools: Iterable[ToolDef]) -> None:
        """批量注册工具。"""
        tools_list = list(tools)
        names = [tool.name for tool in tools_list]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ToolRegistryError(f"本次注册存在重复工具名: {', '.join(duplicates)}")
        conflicts = sorted(name for name in names if name in self._tools)
        if conflicts:
            raise ToolRegistryError(f"工具名冲突: {', '.join(conflicts)}")
        for tool in tools_list:
            self._tools[tool.name] = tool
        if tools_list:
            self._bump_catalog()
            logger.info("已批量注册 %d 个工具", len(tools_list))

    def get_tool(self, tool_name: str) -> ToolDef | None:
        """按名称查找工具定义，未找到返回 None。别名先折到规范名。"""
        return self._tools.get(canonical_tool_name(tool_name))

    def get_all_tools(self) -> list[ToolDef]:
        """返回全部工具定义。"""
        return list(self._tools.values())

    def get_tool_names(self) -> list[str]:
        """返回全部工具名。"""
        return list(self._tools.keys())

    def get_openai_schemas(
        self,
        mode: OpenAISchemaMode = "responses",
        tool_scope: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回 OpenAI 工具 schema，可按 scope 过滤。按工具名排序。"""
        if tool_scope is None:
            tools = list(self._tools.values())
        else:
            scope = set(tool_scope)
            tools = [tool for name, tool in self._tools.items() if name in scope]
        tools.sort(key=lambda tool: tool.name)
        from excelmanus.tools.catalog import _prune_wire_parameters

        return [
            _prune_wire_parameters(tool.to_openai_schema(mode=mode), schema_name=tool.name)
            for tool in tools
        ]

    def get_tiered_schemas(
        self,
        mode: OpenAISchemaMode = "responses",
    ) -> list[dict[str, Any]]:
        """有效目录的完整 schema（按名排序）。分层裁剪由 EffectiveToolCatalog 负责。"""
        return list(self.effective_catalog().tool_schemas(schema_mode=mode))

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """执行工具，可按 scope 做运行期授权。"""
        if tool_scope is not None and tool_name not in set(tool_scope):
            raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")

        tool = self._tools.get(canonical_tool_name(tool_name))
        if tool is None:
            raise ToolNotFoundError(f"工具 '{tool_name}' 未注册。")

        normalized = normalize_tool_aliases(arguments, schema=tool.input_schema)
        if isinstance(normalized, ToolResult):
            return normalized
        arguments = normalized

        validation_args = arguments
        if tool_name == "run_code" and "sandbox_tier" in arguments:
            # sandbox_tier 由宿主按代码策略注入（或模型回显），不在模型面 schema；
            # 校验时忽略，但执行参数原样传递——审批重放依赖它恢复审批时的沙箱档。
            validation_args = {k: v for k, v in arguments.items() if k != "sandbox_tier"}

        schema_error = self.validate_arguments_by_schema(
            tool_name=tool_name,
            arguments=validation_args,
            schema=tool.input_schema,
        )
        if schema_error is not None:
            return schema_error

        # 先做函数签名绑定校验，拦截缺参/多参等调用层错误，
        # 以结构化错误返回给模型，便于其在下一轮自动修正参数。
        signature: inspect.Signature | None = None
        try:
            signature = inspect.signature(tool.func)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            try:
                signature.bind(**arguments)
            except TypeError as exc:
                logger.warning(
                    "工具 '%s' 参数绑定失败: %s; arguments=%s",
                    tool_name,
                    exc,
                    arguments,
                )
                return self._format_argument_validation_error(
                    tool=tool,
                    arguments=arguments,
                    detail=str(exc),
                )

        try:
            from excelmanus.engine_core.tool_result import coerce_legacy_result

            if tool_name == "introspect_capability":
                from excelmanus.tools.introspection_tools import _call_catalog
                catalog = self.effective_catalog()
                token = _call_catalog.set(catalog)
                try:
                    return coerce_legacy_result(tool.func(**arguments))
                finally:
                    _call_catalog.reset(token)
            return coerce_legacy_result(tool.func(**arguments))
        except Exception as exc:
            logger.warning(
                "工具 '%s' 执行异常: %s; arguments=%s",
                tool_name,
                exc,
                arguments,
            )
            return self._format_execution_error(
                tool_name=tool_name,
                exc=exc,
            )

    async def call_tool_async(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """异步执行工具（MCP 工具直接 await，避免线程池开销）。

        仅适用于具有 ``async_func`` 的工具（如 MCP 工具）。
        校验逻辑与 ``call_tool`` 完全一致。
        """
        if tool_scope is not None and tool_name not in set(tool_scope):
            raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")

        tool = self._tools.get(canonical_tool_name(tool_name))
        if tool is None:
            raise ToolNotFoundError(f"工具 '{tool_name}' 未注册。")

        if tool.async_func is None:
            raise RuntimeError(
                f"工具 '{tool_name}' 未提供 async_func，不能使用 call_tool_async。"
            )

        normalized = normalize_tool_aliases(arguments, schema=tool.input_schema)
        if isinstance(normalized, ToolResult):
            return normalized
        arguments = normalized

        schema_error = self.validate_arguments_by_schema(
            tool_name=tool_name,
            arguments=arguments,
            schema=tool.input_schema,
        )
        if schema_error is not None:
            return schema_error

        signature: inspect.Signature | None = None
        try:
            signature = inspect.signature(tool.async_func)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            try:
                signature.bind(**arguments)
            except TypeError as exc:
                logger.warning(
                    "工具 '%s' 参数绑定失败: %s; arguments=%s",
                    tool_name,
                    exc,
                    arguments,
                )
                return self._format_argument_validation_error(
                    tool=tool,
                    arguments=arguments,
                    detail=str(exc),
                )

        try:
            from excelmanus.engine_core.tool_result import coerce_legacy_result

            return coerce_legacy_result(await tool.async_func(**arguments))
        except Exception as exc:
            logger.warning(
                "工具 '%s' 异步执行异常: %s; arguments=%s",
                tool_name,
                exc,
                arguments,
            )
            return self._format_execution_error(
                tool_name=tool_name,
                exc=exc,
            )

    @staticmethod
    def _format_argument_validation_error(
        *,
        tool: ToolDef,
        arguments: dict[str, Any],
        detail: str,
    ) -> ToolResult:
        """构造统一的参数校验错误。"""
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
        required_raw = schema.get("required")
        required = [item for item in required_raw if isinstance(item, str)] if isinstance(required_raw, list) else []
        properties_raw = schema.get("properties")
        accepted_fields = sorted(str(item) for item in properties_raw.keys()) if isinstance(properties_raw, dict) else []
        return error_result(
            "工具参数不完整或不匹配，请根据工具 schema 补齐后重试。",
            code="TOOL_ARGUMENT_VALIDATION_ERROR",
            fields={
                "tool": tool.name,
                "detail": detail,
                "required_fields": required,
                "accepted_fields": accepted_fields,
                "provided_fields": sorted(arguments.keys()),
                "example": example_arguments(schema),
            },
        )

    @staticmethod
    def _format_argument_schema_validation_error(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        schema: dict[str, Any],
        violations: list[str],
    ) -> ToolResult:
        """构造 schema 级参数校验错误。"""
        required_raw = schema.get("required")
        required = [item for item in required_raw if isinstance(item, str)] if isinstance(required_raw, list) else []
        properties_raw = schema.get("properties")
        accepted_fields = sorted(str(item) for item in properties_raw.keys()) if isinstance(properties_raw, dict) else []
        return error_result(
            "工具参数与 schema 不匹配，请修正后重试。",
            code="TOOL_ARGUMENT_VALIDATION_ERROR",
            fields={
                "tool": tool_name,
                "detail": "; ".join(violations[:5]),
                "violations": violations[:20],
                "required_fields": required,
                "accepted_fields": accepted_fields,
                "provided_fields": sorted(arguments.keys()),
                "example": example_arguments(schema),
            },
        )

    @staticmethod
    def _format_execution_error(
        *,
        tool_name: str,
        exc: Exception,
    ) -> ToolResult:
        """路径越界返回人话；其他未捕获异常仍透传。"""
        extra: dict[str, Any] = {
            "tool": tool_name,
            "exception": type(exc).__name__,
        }
        if exc.__cause__ is not None and str(exc.__cause__) != str(exc):
            extra["cause"] = str(exc.__cause__)
        if isinstance(exc, SecurityViolationError):
            return error_result(
                OUTSIDE_WORKSPACE_MESSAGE,
                code="PATH_INVALID",
                remediation=OUTSIDE_WORKSPACE_REMEDIATION,
                fields=extra,
            )
        return error_result(str(exc), code="TOOL_EXECUTION_ERROR", fields=extra)
    @staticmethod
    def is_error_result(result: Any) -> bool:
        """检测工具返回值是否为失败。优先认 ToolResult.success。"""
        if isinstance(result, ToolResult):
            return not result.success
        if not isinstance(result, str):
            return False
        # 快速前缀检测，避免对所有返回值做 JSON 解析
        if not result.startswith('{"status": "error"') and not result.startswith('{"error"'):
            return False
        try:
            parsed = json.loads(result)
            if not isinstance(parsed, dict):
                return False
            if parsed.get("status") == "error":
                return True
            # 简写格式：顶层有 "error" 键，且无数据键（shape/columns/data/sheets）
            if "error" in parsed and not any(
                k in parsed for k in ("shape", "columns", "data", "sheets", "file")
            ):
                return True
            return False
        except (json.JSONDecodeError, AttributeError):
            return False

    def register_builtin_tools(self, workspace_root: str) -> None:
        """注册内置工具集。

        默认模块清单由 `_BUILTIN_TOOL_MODULE_PATHS` 统一维护，
        避免注释与实现各自维护导致的漂移。

        其中 task_tools 和 skill_tools 需要会话级实例，由 AgentEngine.__init__ 单独注册。
        """
        for module_path in _BUILTIN_TOOL_MODULE_PATHS:
            module = import_module(module_path)
            get_tools = getattr(module, "get_tools", None)
            if callable(get_tools):
                self.register_tools(get_tools())
        from excelmanus.tools.meta_tool_defs import get_meta_tools

        self.register_tools(get_meta_tools())
