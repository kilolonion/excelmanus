"""Skills 系统：ToolDef、SkillRegistry 与工具动态加载。"""

from __future__ import annotations

from collections import Counter
import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from excelmanus.logger import get_logger

logger = get_logger("skills")

OpenAISchemaMode = Literal["responses", "chat_completions"]


# ── 异常定义 ──────────────────────────────────────────────


class SkillRegistryError(Exception):
    """Skill 注册失败时抛出的异常。"""


class ToolNotFoundError(Exception):
    """调用不存在的工具时抛出的异常。"""


class ToolExecutionError(Exception):
    """工具执行失败时抛出的异常。"""


# ── ToolDef ───────────────────────────────────────────────


@dataclass
class ToolDef:
    """工具定义：包含名称、描述、JSON Schema 输入参数和执行函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    sensitive_fields: set[str] = field(default_factory=set)

    def to_openai_schema(
        self, mode: OpenAISchemaMode = "responses"
    ) -> dict[str, Any]:
        """转换为 OpenAI tool calling 格式的 JSON Schema。

        Args:
            mode: schema 目标格式：
                - responses: OpenAI Responses API 工具格式
                - chat_completions: Chat Completions API 工具格式
        """
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

    def to_mcp_tool(self) -> dict:
        """转换为 MCP 协议的工具定义格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ── SkillRegistry ─────────────────────────────────────────


class SkillRegistry:
    """Skill 注册中心：管理所有已注册 Skill 的元数据和工具。"""

    def __init__(self) -> None:
        # skill_name -> (description, list[ToolDef])
        self._skills: dict[str, tuple[str, list[ToolDef]]] = {}
        # tool_name -> ToolDef（扁平索引，加速查找）
        self._tools: dict[str, ToolDef] = {}

    def register(
        self, skill_name: str, description: str, tools: list[ToolDef]
    ) -> None:
        """注册一个 Skill 及其工具列表。

        Args:
            skill_name: Skill 名称，必须唯一。
            description: Skill 描述。
            tools: 该 Skill 包含的工具定义列表。

        Raises:
            SkillRegistryError: Skill 名称重复时抛出。
        """
        if skill_name in self._skills:
            raise SkillRegistryError(
                f"Skill '{skill_name}' 已注册，不允许重复注册。"
            )

        # 先完成校验，再写入内部状态，避免部分注册成功
        tool_name_counts = Counter(tool.name for tool in tools)
        duplicate_tool_names = sorted(
            name for name, count in tool_name_counts.items() if count > 1
        )
        if duplicate_tool_names:
            names = ", ".join(duplicate_tool_names)
            raise SkillRegistryError(
                f"Skill '{skill_name}' 内存在重复工具名: {names}"
            )

        conflicts = sorted(
            name for name in tool_name_counts if name in self._tools
        )
        if conflicts:
            names = ", ".join(conflicts)
            raise SkillRegistryError(
                f"工具名冲突（已被其他 Skill 注册）: {names}"
            )

        self._skills[skill_name] = (description, tools)
        for tool in tools:
            self._tools[tool.name] = tool

        logger.info(
            "已注册 Skill '%s'，包含 %d 个工具", skill_name, len(tools)
        )

    def auto_discover(
        self, package_name: str = "excelmanus.skills"
    ) -> None:
        """自动扫描包命名空间下所有符合约定的 Skill 模块并注册。

        约定：每个 Skill 模块导出 SKILL_NAME、SKILL_DESCRIPTION、get_tools()。

        Args:
            package_name: 要扫描的包名称，默认为 excelmanus.skills。
        """
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            logger.warning("无法导入包 '%s'，跳过自动发现", package_name)
            return

        # 使用 pkgutil.iter_modules 扫描包内子模块（非硬编码磁盘路径）
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            logger.warning("包 '%s' 没有 __path__，跳过自动发现", package_name)
            return

        for importer, module_name, is_pkg in pkgutil.iter_modules(package_path):
            full_name = f"{package_name}.{module_name}"
            try:
                module = importlib.import_module(full_name)
            except Exception:
                logger.warning("导入模块 '%s' 失败，跳过", full_name, exc_info=True)
                continue

            # 检查模块是否符合 Skill 约定
            skill_name = getattr(module, "SKILL_NAME", None)
            skill_desc = getattr(module, "SKILL_DESCRIPTION", None)
            get_tools_fn = getattr(module, "get_tools", None)

            if skill_name is None or skill_desc is None or get_tools_fn is None:
                logger.debug(
                    "模块 '%s' 不符合 Skill 约定（缺少 SKILL_NAME/SKILL_DESCRIPTION/get_tools），跳过",
                    full_name,
                )
                continue

            if not callable(get_tools_fn):
                logger.warning("模块 '%s' 的 get_tools 不可调用，跳过", full_name)
                continue

            try:
                tools = get_tools_fn()
                self.register(skill_name, skill_desc, tools)
            except SkillRegistryError:
                logger.warning(
                    "Skill '%s'（来自 '%s'）注册失败：名称重复",
                    skill_name,
                    full_name,
                )
            except Exception:
                logger.warning(
                    "Skill '%s'（来自 '%s'）注册失败",
                    skill_name,
                    full_name,
                    exc_info=True,
                )

    def get_all_tools(self) -> list[ToolDef]:
        """返回所有已注册工具的列表。"""
        return list(self._tools.values())

    def get_openai_schemas(
        self, mode: OpenAISchemaMode = "responses"
    ) -> list[dict[str, Any]]:
        """返回所有工具的 OpenAI tool calling JSON Schema 列表。"""
        return [
            tool.to_openai_schema(mode=mode) for tool in self._tools.values()
        ]

    def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """按名称调用工具。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            工具函数的返回值。

        Raises:
            ToolNotFoundError: 工具不存在时抛出。
            ToolExecutionError: 工具执行失败时抛出。
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"工具 '{tool_name}' 未注册。")

        try:
            return tool.func(**arguments)
        except Exception as exc:
            raise ToolExecutionError(
                f"工具 '{tool_name}' 执行失败: {exc}"
            ) from exc
