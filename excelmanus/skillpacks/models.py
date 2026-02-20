"""Skillpack 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SkillpackSource = Literal["system", "user", "project"]
SkillCommandDispatchMode = Literal["none", "tool"]


@dataclass(frozen=True)
class Skillpack:
    """单个 Skillpack 定义。"""

    name: str
    description: str
    instructions: str
    source: SkillpackSource
    root_dir: str
    file_patterns: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    version: str = "1.0.0"
    disable_model_invocation: bool = False
    user_invocable: bool = True
    argument_hint: str = ""
    hooks: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    command_dispatch: SkillCommandDispatchMode = "none"
    command_tool: str | None = None
    required_mcp_servers: list[str] = field(default_factory=list)
    required_mcp_tools: list[str] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    resource_contents: dict[str, str] = field(default_factory=dict)

    def render_context(self) -> str:
        """渲染注入到 system 消息中的技能上下文。"""
        lines = [
            f"[Skillpack] {self.name}",
            f"描述：{self.description}",
            "执行指引：",
            self.instructions.strip() or "(无)",
        ]
        if self.required_mcp_servers or self.required_mcp_tools:
            lines.append("MCP 依赖：")
            if self.required_mcp_servers:
                lines.append(f"- servers: {', '.join(self.required_mcp_servers)}")
            if self.required_mcp_tools:
                lines.append(f"- tools: {', '.join(self.required_mcp_tools)}")
        if self.resource_contents:
            lines.append("补充资源：")
            for path, content in self.resource_contents.items():
                lines.append(f"- {path}:")
                lines.append(content.strip())
        return "\n".join(lines).strip()

    def render_context_minimal(self) -> str:
        """仅返回 name + description，用于预算耗尽时的降级。"""
        return f"[Skillpack] {self.name}\n描述：{self.description}"

    def render_context_truncated(self, max_chars: int) -> str:
        """返回完整头部 + 正文前 N 行（按行截断至 max_chars 内）+ 截断提示。"""
        truncate_suffix = "[正文已截断，完整内容见 SKILL.md]"
        header_lines = [
            f"[Skillpack] {self.name}",
            f"描述：{self.description}",
            "执行指引：",
        ]
        header = "\n".join(header_lines) + "\n"
        remaining = max_chars - len(header) - len(truncate_suffix) - 1
        if remaining <= 0:
            return self.render_context_minimal()
        instructions = self.instructions.strip() or "(无)"
        body_lines = instructions.split("\n")
        chosen: list[str] = []
        for line in body_lines:
            if remaining <= 0:
                break
            line_with_nl = line + "\n"
            if len(line_with_nl) <= remaining:
                chosen.append(line)
                remaining -= len(line_with_nl)
            else:
                trim = line[:remaining]
                if trim:
                    chosen.append(trim)
                remaining = 0
                break
        body = "\n".join(chosen) if chosen else ""
        return header + body + "\n" + truncate_suffix


@dataclass(frozen=True)
class SkillMatchResult:
    """Skill 路由结果。"""

    skills_used: list[str]
    route_mode: str
    system_contexts: list[str] = field(default_factory=list)
    tool_scope: list[str] = field(default_factory=list)  # v5: 始终为空，保留向后兼容
    parameterized: bool = False
    write_hint: str = "unknown"  # "may_write" | "read_only" | "unknown"
    sheet_count: int = 0  # 路由阶段检测到的 sheet 数量
    max_total_rows: int = 0  # 路由阶段检测到的最大 sheet 行数
    task_tags: tuple[str, ...] = ()  # LLM/词法 推断的任务标签
