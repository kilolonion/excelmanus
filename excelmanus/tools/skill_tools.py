"""技能查询工具：让 Agent 在路由匹配失败时主动查询所有可用 Skillpack。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from excelmanus.tools.registry import ToolDef

if TYPE_CHECKING:
    from excelmanus.skillpacks.loader import SkillpackLoader


_loader: "SkillpackLoader | None" = None


def init_loader(loader: "SkillpackLoader | None") -> None:
    """兼容旧接口：设置模块级 SkillpackLoader。"""
    global _loader
    _loader = loader


def _resolve_loader(loader: "SkillpackLoader | None" = None) -> "SkillpackLoader | None":
    """解析可用 loader（优先显式注入，其次模块级实例）。"""
    global _loader
    if loader is not None:
        _loader = loader
        return loader
    return _loader


def list_skills(verbose: bool = False, *, loader: "SkillpackLoader | None" = None) -> str:
    """列出所有已加载的 Skillpack。"""
    active_loader = _resolve_loader(loader)
    if active_loader is None:
        return "当前没有已加载的技能包。"
    skillpacks = active_loader.get_skillpacks()
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


def get_tools(loader: "SkillpackLoader | None" = None) -> list[ToolDef]:
    """返回绑定到指定 SkillpackLoader 实例的工具定义。"""
    active_loader = _resolve_loader(loader)

    def _list_skills(verbose: bool = False) -> str:
        return list_skills(verbose=verbose, loader=active_loader)

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
            func=_list_skills,
        ),
    ]
