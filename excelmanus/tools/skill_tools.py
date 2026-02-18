"""技能查询工具：让 Agent 在路由匹配失败时主动查询所有可用 Skillpack。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.skillpacks.loader import SkillpackLoader


def get_tools(loader: "SkillpackLoader") -> list[ToolDef]:
    """返回绑定到指定 SkillpackLoader 实例的工具定义（闭包注入，无全局状态）。"""

    def list_skills(verbose: bool = False) -> str:
        """列出所有已加载的 Skillpack。

        默认返回 name + description，verbose=True 时补充触发词和可用工具。
        """
        skillpacks = loader.get_skillpacks()
        if not skillpacks:
            return "当前没有已加载的技能包。"

        lines: list[str] = [f"共 {len(skillpacks)} 个可用技能包：\n"]
        for name, skill in sorted(skillpacks.items()):
            lines.append(f"【{name}】")
            lines.append(f"  描述：{skill.description}")
            if verbose and skill.triggers:
                lines.append(f"  触发词：{', '.join(skill.triggers)}")
            if verbose and skill.allowed_tools:
                lines.append(f"  可用工具：{', '.join(skill.allowed_tools)}")
            lines.append("")
        return "\n".join(lines).strip()

    return [
        ToolDef(
            name="list_skills",
            description="列出所有可用技能包。默认返回技能名称和描述；当需要查看触发词与可用工具时，将 verbose 设为 true。",
            input_schema={
                "type": "object",
                "properties": {
                    "verbose": {
                        "type": "boolean",
                        "description": "是否返回详细信息（触发词、可用工具）。默认 false，仅返回名称与描述。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            func=list_skills,
        ),
    ]
