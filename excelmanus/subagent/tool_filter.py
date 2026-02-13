"""子代理工具过滤视图。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from excelmanus.tools import ToolNotAllowedError, ToolRegistry


class FilteredToolRegistry:
    """ToolRegistry 的受限视图。"""

    def __init__(
        self,
        parent: ToolRegistry,
        *,
        allowed: list[str] | None = None,
        disallowed: list[str] | None = None,
    ) -> None:
        self._parent = parent
        self._allowed = set(allowed) if allowed else None
        self._disallowed = set(disallowed or [])

    def is_tool_available(self, name: str) -> bool:
        """判断工具是否在可用范围内。"""
        if name in self._disallowed:
            return False
        if self._allowed is not None and name not in self._allowed:
            return False
        return self._parent.get_tool(name) is not None

    def get_tool_names(self) -> list[str]:
        """返回过滤后的工具名列表。"""
        return [name for name in self._parent.get_tool_names() if self.is_tool_available(name)]

    def get_tool(self, tool_name: str) -> Any:
        """返回过滤后的工具定义。"""
        if not self.is_tool_available(tool_name):
            return None
        return self._parent.get_tool(tool_name)

    def get_openai_schemas(
        self,
        *,
        mode: str = "chat_completions",
        tool_scope: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回过滤后的 OpenAI tool schema。"""
        allowed_scope = self.get_tool_names()
        if tool_scope is None:
            final_scope = allowed_scope
        else:
            final_scope = [name for name in tool_scope if name in set(allowed_scope)]
        return self._parent.get_openai_schemas(mode=mode, tool_scope=final_scope)

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        tool_scope: Sequence[str] | None = None,
    ) -> Any:
        """执行工具，超出过滤范围时抛错。"""
        if not self.is_tool_available(tool_name):
            raise ToolNotAllowedError(f"工具 '{tool_name}' 不在子代理授权范围内。")
        if tool_scope is not None and tool_name not in set(tool_scope):
            raise ToolNotAllowedError(f"工具 '{tool_name}' 不在当前调用授权范围内。")
        return self._parent.call_tool(tool_name, arguments, tool_scope=tool_scope)
