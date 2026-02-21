"""自动能力图谱生成器。

从 SSOT 数据源（TOOL_CATEGORIES、TOOL_SHORT_DESCRIPTIONS、policy 分层）
自动生成结构化 Markdown 能力概览，替代手写的静态能力描述。
"""

from __future__ import annotations

from excelmanus.logger import get_logger
from excelmanus.tools.policy import (
    MUTATING_AUDIT_ONLY_TOOLS,
    MUTATING_CONFIRM_TOOLS,
    READ_ONLY_SAFE_TOOLS,
    TOOL_CATEGORIES,
    TOOL_SHORT_DESCRIPTIONS,
)
from excelmanus.tools.registry import ToolRegistry

logger = get_logger("introspection")

# ── 权限图标常量 ──────────────────────────────────────────

ICON_READ_ONLY = "🟢"
ICON_AUDIT_ONLY = "🟡"
ICON_CONFIRM = "🔴"
ICON_MCP = "🔵"
# 不属于三个权限集合的内置工具，默认标注为审计记录
ICON_DEFAULT = "🟡"

# ── 分类显示名映射 ────────────────────────────────────────

CATEGORY_DISPLAY_NAMES: dict[str, str] = {
    "data_read": "数据读取 (data_read)",
    "data_write": "数据写入 (data_write)",
    "format": "格式化 (format)",
    "advanced_format": "高级格式 (advanced_format)",
    "chart": "图表 (chart)",
    "sheet": "工作表 (sheet)",
    "file": "文件操作 (file)",
    "code": "代码执行 (code)",
    "macro": "声明式复合操作 (macro)",
    "vision": "图片视觉 (vision)",
}

# ── 自省指引段落 ──────────────────────────────────────────

INTROSPECTION_GUIDANCE = """\
## 自省指引
- 不确定某工具的参数或限制时，调用 introspect_capability 查询
- 遇到复杂能力判断且 introspect_capability 无法明确回答时，委派 introspector 子代理
- 禁止向用户暴露自省过程和内部实现细节"""


class CapabilityMapGenerator:
    """自动能力图谱生成器。

    从 ToolRegistry 和 SSOT 数据源生成结构化 Markdown 能力概览。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        categories: dict[str, tuple[str, ...]] = TOOL_CATEGORIES,
        descriptions: dict[str, str] = TOOL_SHORT_DESCRIPTIONS,
    ) -> None:
        self.registry = registry
        self.categories = categories
        self.descriptions = descriptions

    def generate(self) -> str:
        """生成结构化能力图谱 Markdown 文本。

        按分类组织工具，附加权限级别标注和一句话描述。
        检测 MCP 扩展工具并标注为"扩展能力"。
        末尾附加自省指引段落。
        """
        sections: list[str] = ["## 能力范围\n"]

        # 按分类生成段落
        for category_name, tool_names in self.categories.items():
            display = CATEGORY_DISPLAY_NAMES.get(category_name, category_name)
            section_lines = [f"### {display}"]
            for tool_name in tool_names:
                permission = self._classify_permission(tool_name)
                desc = self.descriptions.get(tool_name, "")
                if not desc and tool_name not in self.descriptions:
                    logger.warning(
                        "工具 %s 缺少描述（不在 TOOL_SHORT_DESCRIPTIONS 中）",
                        tool_name,
                    )
                section_lines.append(f"- {permission} {tool_name} — {desc}")
            sections.append("\n".join(section_lines))

        # 检测 MCP 扩展工具
        mcp_tools = self._detect_mcp_tools()
        if mcp_tools:
            mcp_lines = ["### 扩展能力 (MCP)"]
            for name in mcp_tools:
                tool = self.registry.get_tool(name)
                desc = tool.description if tool else ""
                mcp_lines.append(f"- {ICON_MCP} {name} — {desc}")
            sections.append("\n".join(mcp_lines))

        # 附加自省指引
        sections.append(INTROSPECTION_GUIDANCE)

        return "\n\n".join(sections)

    def _classify_permission(self, tool_name: str) -> str:
        """返回工具的权限级别图标。

        优先级：READ_ONLY_SAFE > MUTATING_CONFIRM > MUTATING_AUDIT_ONLY > 默认(🟡)
        """
        if tool_name in READ_ONLY_SAFE_TOOLS:
            return ICON_READ_ONLY
        if tool_name in MUTATING_CONFIRM_TOOLS:
            return ICON_CONFIRM
        if tool_name in MUTATING_AUDIT_ONLY_TOOLS:
            return ICON_AUDIT_ONLY
        return ICON_DEFAULT

    def _detect_mcp_tools(self) -> list[str]:
        """检测注册表中不属于 TOOL_CATEGORIES 任何分类的工具。

        内置工具（在 TOOL_CATEGORIES 中出现的）不会被标记为 MCP。
        同时排除已知的内部工具（在 READ_ONLY_SAFE_TOOLS 等策略集合中
        但不在 TOOL_CATEGORIES 中的工具，如 memory_read_topic 等）。
        """
        categorized: set[str] = set()
        for tool_names in self.categories.values():
            categorized.update(tool_names)

        # 已知内部工具集合（在策略集合中但不在分类中的工具）
        known_internal = (
            READ_ONLY_SAFE_TOOLS
            | MUTATING_CONFIRM_TOOLS
            | MUTATING_AUDIT_ONLY_TOOLS
        )

        return [
            t.name
            for t in self.registry.get_all_tools()
            if t.name not in categorized and t.name not in known_internal
        ]
