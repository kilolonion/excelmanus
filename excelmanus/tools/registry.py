"""Tools 执行层：工具定义、注册、schema 输出与调用。"""

from __future__ import annotations

import hashlib
import inspect
from importlib import import_module
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

from excelmanus.logger import get_logger

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

# 内置工具模块清单（单一事实源）：
# 1) 该顺序即注册顺序；
# 2) register_builtin_tools 与说明文档均以此为准，避免注释与实现漂移。
_BUILTIN_TOOL_MODULE_PATHS: tuple[str, ...] = (
    "excelmanus.tools.data_tools",
    "excelmanus.tools.file_tools",
    "excelmanus.tools.code_tools",
    "excelmanus.tools.shell_tools",
    "excelmanus.tools.sheet_tools",
    "excelmanus.tools.chart_tools",
    "excelmanus.tools.focus_tools",
    "excelmanus.tools.image_tools",
    "excelmanus.tools.memory_tools",
    "excelmanus.tools.sleep_tools",
)


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
    sensitive_fields: set[str] = field(default_factory=set)
    max_result_chars: int = 3000
    truncate_head_chars: int | None = None
    truncate_tail_chars: int = 0
    # 写入语义声明（用于写入追踪，不用于审批/审计策略判定）
    write_effect: WriteEffect = "unknown"

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
        """JSON 感知截断：保留元数据，缩减最大的 list 字段。

        策略：找到 dict 中最大的 list 字段，逐步减半其元素数量，
        直到序列化后长度 <= limit。如果缩减到 0 个元素仍超限，返回 None 回退字符截断。

        注意：操作副本，不修改原始 data 对象。
        """
        # 找到所有 list 类型的字段及其大小
        list_fields = {
            k: len(v) for k, v in data.items()
            if isinstance(v, list) and len(v) > 0
        }
        if not list_fields:
            # 没有可缩减的 list 字段，回退
            return None

        # 按 list 长度降序排列，优先缩减最大的
        sorted_fields = sorted(list_fields.keys(), key=lambda k: list_fields[k], reverse=True)
        target_field = sorted_fields[0]
        original_list = data[target_field]
        original_len = len(original_list)

        # 在副本上操作，完全避免修改原始 data
        working = dict(data)

        # 二分搜索合适的截断长度
        lo, hi = 0, original_len
        best_result: str | None = None

        while lo <= hi:
            mid = (lo + hi) // 2
            working[target_field] = original_list[:mid] if mid > 0 else []

            # 添加截断提示到副本中
            if mid < original_len:
                working[f"_{target_field}_truncated"] = True
                working[f"_{target_field}_note"] = (
                    f"⚠️ 完整数据有 {original_len} 行，仅展示前 {mid} 行"
                    f"（字段: {target_field}）。"
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
                lo = mid + 1  # 尝试保留更多元素
            else:
                hi = mid - 1

        return best_result

    def to_openai_schema(
        self, mode: OpenAISchemaMode = "responses"
    ) -> dict[str, Any]:
        """转换为 OpenAI 工具 schema。"""
        if mode == "responses":
            return {
                "type": "function",
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            }
        if mode == "chat_completions":
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.input_schema,
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
        return child

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
                if Path(field_value).is_absolute():
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
    ) -> str | None:
        """按当前策略校验参数 schema。

        Returns:
            str | None: 当命中 enforce 且校验失败时返回结构化错误 JSON；
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
            logger.info("已批量注册 %d 个工具", len(tools_list))

    def get_tool(self, tool_name: str) -> ToolDef | None:
        """按名称查找工具定义，未找到返回 None。"""
        return self._tools.get(tool_name)

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
        """返回 OpenAI 工具 schema，可按 scope 过滤。"""
        if tool_scope is None:
            tools = self._tools.values()
        else:
            scope = set(tool_scope)
            tools = [tool for name, tool in self._tools.items() if name in scope]
        return [tool.to_openai_schema(mode=mode) for tool in tools]

    def get_tiered_schemas(
        self,
        mode: OpenAISchemaMode = "responses",
    ) -> list[dict[str, Any]]:
        """返回所有工具的完整 schema。"""
        return [tool.to_openai_schema(mode=mode) for tool in self._tools.values()]

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """执行工具，可按 scope 做运行期授权。"""
        if tool_scope is not None and tool_name not in set(tool_scope):
            raise ToolNotAllowedError(f"工具 '{tool_name}' 不在授权范围内。")

        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"工具 '{tool_name}' 未注册。")

        schema_error = self.validate_arguments_by_schema(
            tool_name=tool_name,
            arguments=arguments,
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
            return tool.func(**arguments)
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

    @staticmethod
    def _format_argument_validation_error(
        *,
        tool: ToolDef,
        arguments: dict[str, Any],
        detail: str,
    ) -> str:
        """构造统一的参数校验错误返回（JSON 字符串）。"""
        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
        required_raw = schema.get("required")
        required = [item for item in required_raw if isinstance(item, str)] if isinstance(required_raw, list) else []
        properties_raw = schema.get("properties")
        accepted_fields = sorted(str(item) for item in properties_raw.keys()) if isinstance(properties_raw, dict) else []
        payload = {
            "status": "error",
            "error_code": "TOOL_ARGUMENT_VALIDATION_ERROR",
            "tool": tool.name,
            "message": "工具参数不完整或不匹配，请根据工具 schema 补齐后重试。",
            "detail": detail,
            "required_fields": required,
            "accepted_fields": accepted_fields,
            "provided_fields": sorted(arguments.keys()),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _format_argument_schema_validation_error(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        schema: dict[str, Any],
        violations: list[str],
    ) -> str:
        """构造 schema 级参数校验错误（JSON 字符串）。"""
        required_raw = schema.get("required")
        required = [item for item in required_raw if isinstance(item, str)] if isinstance(required_raw, list) else []
        properties_raw = schema.get("properties")
        accepted_fields = sorted(str(item) for item in properties_raw.keys()) if isinstance(properties_raw, dict) else []
        payload = {
            "status": "error",
            "error_code": "TOOL_ARGUMENT_VALIDATION_ERROR",
            "tool": tool_name,
            "message": "工具参数与 schema 不匹配，请修正后重试。",
            "detail": "; ".join(violations[:5]),
            "violations": violations[:20],
            "required_fields": required,
            "accepted_fields": accepted_fields,
            "provided_fields": sorted(arguments.keys()),
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _format_execution_error(
        *,
        tool_name: str,
        exc: Exception,
    ) -> str:
        """构造统一的工具执行错误返回（JSON 字符串），透传原始异常信息。"""
        payload = {
            "status": "error",
            "error_code": "TOOL_EXECUTION_ERROR",
            "tool": tool_name,
            "exception": type(exc).__name__,
            "message": str(exc),
        }
        # 如果有链式异常（__cause__），也透传
        if exc.__cause__ is not None and str(exc.__cause__) != str(exc):
            payload["cause"] = str(exc.__cause__)
        return json.dumps(payload, ensure_ascii=False)
    @staticmethod
    def is_error_result(result: Any) -> bool:
        """检测工具返回值是否为 registry 层格式化的错误 JSON。"""
        if not isinstance(result, str):
            return False
        # 快速前缀检测，避免对所有返回值做 JSON 解析
        if not result.startswith('{"status": "error"'):
            return False
        try:
            parsed = json.loads(result)
            return isinstance(parsed, dict) and parsed.get("status") == "error"
        except (json.JSONDecodeError, AttributeError):
            return False

    def register_builtin_tools(self, workspace_root: str) -> None:
        """注册内置工具集。

        默认模块清单由 `_BUILTIN_TOOL_MODULE_PATHS` 统一维护，
        避免注释与实现各自维护导致的漂移。

        其中 task_tools 和 skill_tools 需要会话级实例，由 AgentEngine.__init__ 单独注册。
        """
        from excelmanus.security import FileAccessGuard
        from excelmanus.tools._guard_ctx import set_guard

        # 设置 contextvar fallback，确保 CLI 模式下直接调用工具函数
        # （不经过 tool_dispatcher.execute）也能拿到正确的 guard
        set_guard(FileAccessGuard(workspace_root))

        for module_path in _BUILTIN_TOOL_MODULE_PATHS:
            module = import_module(module_path)
            init_guard = getattr(module, "init_guard", None)
            if callable(init_guard):
                init_guard(workspace_root)
            self.register_tools(module.get_tools())
