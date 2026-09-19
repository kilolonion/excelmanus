"""Wave D 接缝：压缩 / 技能 catalog 挂到 Driver.pre_step，不进循环体。"""

from __future__ import annotations

from typing import Any

from excelmanus.compaction import compact_for_pre_step
from excelmanus.prompt.skill_catalog import attach_skill_catalog


async def _compact_attachment(engine: Any) -> str:
    return await compact_for_pre_step(engine)


async def _skill_catalog_attachment(engine: Any) -> str:
    from excelmanus.system_one.host import maybe_pin_skills

    await maybe_pin_skills(engine)
    attach_skill_catalog(engine)
    return "enter"


def attach_wave_d(engine: Any) -> None:
    """一行接线。逻辑留在 compaction / skill_catalog / plan_mode。"""
    if getattr(engine, "_wave_d_attached", False):
        return
    driver = getattr(engine, "_driver", None)
    if driver is None:
        return
    driver.add_pre_step_attachment(_compact_attachment)
    driver.add_pre_step_attachment(_skill_catalog_attachment)
    if not hasattr(engine, "_pending_plan_exit"):
        engine._pending_plan_exit = None
    if not hasattr(engine, "_last_compact_failed"):
        engine._last_compact_failed = False
    engine._wave_d_attached = True
