"""Tools 执行层：工具定义、注册、schema 输出与调用。"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Sequence

from excelmanus.logger import get_logger

logger = get_logger("tools")

OpenAISchemaMode = Literal["responses", "chat_completions"]


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
                    f"[{target_field} 已截断: 显示 {mid}/{original_len} 条]"
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

    def fork(self) -> "ToolRegistry":
        """创建一个 per-session 的 overlay registry。

        新实例持有当前所有工具的浅拷贝，后续注册互不影响。
        用于 API 多会话场景：每个 AgentEngine 持有独立 registry，
        避免会话级工具（task_tools / skill_tools）重复注册冲突。
        """
        child = ToolRegistry()
        child._tools = dict(self._tools)  # 浅拷贝：ToolDef 不可变，安全
        return child

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
        """注册内置工具集。"""
        from excelmanus.tools import (
            advanced_format_tools,
            cell_tools,
            chart_tools,
            code_tools,
            data_tools,
            file_tools,
            focus_tools,
            format_tools,
            shell_tools,
            sheet_tools,
            worksheet_tools,
        )

        data_tools.init_guard(workspace_root)
        chart_tools.init_guard(workspace_root)
        format_tools.init_guard(workspace_root)
        advanced_format_tools.init_guard(workspace_root)
        file_tools.init_guard(workspace_root)
        code_tools.init_guard(workspace_root)
        shell_tools.init_guard(workspace_root)
        sheet_tools.init_guard(workspace_root)
        cell_tools.init_guard(workspace_root)
        worksheet_tools.init_guard(workspace_root)

        self.register_tools(data_tools.get_tools())
        self.register_tools(chart_tools.get_tools())
        self.register_tools(format_tools.get_tools())
        self.register_tools(advanced_format_tools.get_tools())
        self.register_tools(file_tools.get_tools())
        self.register_tools(code_tools.get_tools())
        self.register_tools(shell_tools.get_tools())
        self.register_tools(sheet_tools.get_tools())
        self.register_tools(cell_tools.get_tools())
        self.register_tools(worksheet_tools.get_tools())
        self.register_tools(focus_tools.get_tools())

        from excelmanus.tools import memory_tools

        # task_tools 和 skill_tools 需要会话级实例，由 AgentEngine.__init__ 单独注册
        self.register_tools(memory_tools.get_tools())
