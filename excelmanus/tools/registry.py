"""Tools 执行层：工具定义、注册、schema 输出与调用。"""

from __future__ import annotations

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
        """若文本超过 max_result_chars 则截断并附加提示。"""
        limit = self.max_result_chars
        if limit <= 0 or len(text) <= limit:
            return text
        return f"{text[:limit]}\n[结果已截断，原始长度: {len(text)} 字符]"

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

        try:
            return tool.func(**arguments)
        except Exception as exc:
            raise ToolExecutionError(f"工具 '{tool_name}' 执行失败: {exc}") from exc

    def register_builtin_tools(self, workspace_root: str) -> None:
        """注册内置工具集。"""
        from excelmanus.tools import (
            chart_tools,
            code_tools,
            data_tools,
            file_tools,
            format_tools,
            shell_tools,
            sheet_tools,
        )

        data_tools.init_guard(workspace_root)
        chart_tools.init_guard(workspace_root)
        format_tools.init_guard(workspace_root)
        file_tools.init_guard(workspace_root)
        code_tools.init_guard(workspace_root)
        shell_tools.init_guard(workspace_root)
        sheet_tools.init_guard(workspace_root)

        self.register_tools(data_tools.get_tools())
        self.register_tools(chart_tools.get_tools())
        self.register_tools(format_tools.get_tools())
        self.register_tools(file_tools.get_tools())
        self.register_tools(code_tools.get_tools())
        self.register_tools(shell_tools.get_tools())
        self.register_tools(sheet_tools.get_tools())

        from excelmanus.tools import memory_tools, skill_tools, task_tools

        self.register_tools(task_tools.get_tools())
        self.register_tools(skill_tools.get_tools())
        self.register_tools(memory_tools.get_tools())
