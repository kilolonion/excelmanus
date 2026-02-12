"""excelmanus.skills 已在 v3 中废弃。

请迁移到 excelmanus.tools（ToolRegistry）和 excelmanus.skillpacks（SkillpackLoader/Router）。
"""

from __future__ import annotations


class SkillRegistryError(Exception):
    """旧版 Skill 注册异常（保留以兼容异常捕获）。"""


class SkillRegistry:
    """旧版 SkillRegistry 已废弃，实例化时抛出 ImportError。"""

    def __init__(self, *args, **kwargs):
        raise ImportError(
            "SkillRegistry 已在 v3 中废弃。"
            "请使用 excelmanus.tools.ToolRegistry 和 excelmanus.skillpacks.SkillpackLoader。"
        )


__all__ = [
    "SkillRegistry",
    "SkillRegistryError",
]
