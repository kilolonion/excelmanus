"""技能上下文构建：按字符预算控制注入内容。"""

from __future__ import annotations

from excelmanus.skillpacks.models import Skillpack


def build_contexts_with_budget(skills: list[Skillpack], budget: int) -> list[str]:
    """按 priority 降序，在 budget 内构建 contexts。

    降级顺序：完整正文 → 截断正文 → 仅 name+description。
    budget <= 0 时不限制。
    """
    if not skills:
        return []
    if budget <= 0:
        return [s.render_context() for s in skills]
    ordered = sorted(skills, key=lambda s: (-s.priority, s.name))
    result: list[str] = []
    used = 0
    for skill in ordered:
        remaining = budget - used
        full = skill.render_context()
        if len(full) <= remaining:
            result.append(full)
            used += len(full)
        else:
            minimal = skill.render_context_minimal()
            if remaining >= len(minimal) + 50:
                truncated = skill.render_context_truncated(remaining)
                result.append(truncated)
                used += len(truncated)
            else:
                result.append(minimal)
                used += len(minimal)
    return result
