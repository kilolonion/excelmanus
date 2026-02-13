"""技能查询工具：让 Agent 在路由匹配失败时主动查询所有可用 Skillpack。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.skillpacks.loader import SkillpackLoader

# 模块级 SkillpackLoader 引用，由 init_loader() 注入
_loader: SkillpackLoader | None = None


def init_loader(loader: SkillpackLoader) -> None:
    """注入 SkillpackLoader 实例。"""
    global _loader
    _loader = loader


def _get_loader() -> SkillpackLoader:
    if _loader is None:
        raise RuntimeError("SkillpackLoader 未初始化")
    return _loader


def list_skills() -> str:
    """列出所有已加载的 Skillpack 名称、描述和触发词。"""
    loader = _get_loader()
    skillpacks = loader.get_skillpacks()
    if not skillpacks:
        return "当前没有已加载的技能包。"

    lines: list[str] = [f"共 {len(skillpacks)} 个可用技能包：\n"]
    for name, skill in sorted(skillpacks.items()):
        lines.append(f"【{name}】")
        lines.append(f"  描述：{skill.description}")
        if skill.triggers:
            lines.append(f"  触发词：{', '.join(skill.triggers)}")
        if skill.allowed_tools:
            lines.append(f"  可用工具：{', '.join(skill.allowed_tools)}")
        lines.append("")
    return "\n".join(lines).strip()


def get_tools() -> list[ToolDef]:
    """返回技能查询工具定义。"""
    return [
        ToolDef(
            name="list_skills",
            description="列出所有可用的技能包（Skillpack），包括名称、描述和触发词。当你不确定应该使用哪个技能来完成用户任务时，可以调用此工具查看完整的技能目录。",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            func=list_skills,
        ),
    ]
