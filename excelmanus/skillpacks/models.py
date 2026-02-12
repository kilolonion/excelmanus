"""Skillpack 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SkillpackSource = Literal["system", "user", "project"]


@dataclass(frozen=True)
class Skillpack:
    """单个 Skillpack 定义。"""

    name: str
    description: str
    allowed_tools: list[str]
    triggers: list[str]
    instructions: str
    source: SkillpackSource
    root_dir: str
    file_patterns: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    priority: int = 0
    version: str = "1.0.0"
    disable_model_invocation: bool = False
    resource_contents: dict[str, str] = field(default_factory=dict)

    def render_context(self) -> str:
        """渲染注入到 system 消息中的技能上下文。"""
        lines = [
            f"[Skillpack] {self.name}",
            f"描述：{self.description}",
            f"授权工具：{', '.join(self.allowed_tools) if self.allowed_tools else '(空)'}",
            "执行指引：",
            self.instructions.strip() or "(无)",
        ]
        if self.resource_contents:
            lines.append("补充资源：")
            for path, content in self.resource_contents.items():
                lines.append(f"- {path}:")
                lines.append(content.strip())
        return "\n".join(lines).strip()


@dataclass(frozen=True)
class SkillMatchResult:
    """Skill 路由结果。"""

    skills_used: list[str]
    tool_scope: list[str]
    route_mode: str
    system_contexts: list[str] = field(default_factory=list)
