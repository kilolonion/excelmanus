"""并行委派不变量：任一仍可写的 child 则整批拒绝。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.models import SubagentConfig, SubagentResult


@dataclass
class ParallelTask:
    """并行子任务输入。"""

    task: str
    agent_name: str | None = None
    file_paths: list[str] = field(default_factory=list)


@dataclass
class ParallelOutcome:
    """并行聚合。"""

    reply: str
    success: bool
    results: list[SubagentResult] = field(default_factory=list)
    conflict_error: str | None = None


def _child_is_writable(
    cfg: SubagentConfig,
    parent_capability: Any | None,
) -> bool:
    if parent_capability is not None:
        from excelmanus.tools.context import intersect_capability
        from excelmanus.subagent.child import child_extra_disallowed

        cap = intersect_capability(
            parent_capability,
            permission_mode=str(getattr(cfg, "permission_mode", "default") or "default"),
            allowed_tools=list(cfg.allowed_tools) if cfg.allowed_tools else None,
            disallowed_tools=list(getattr(cfg, "disallowed_tools", None) or []),
            extra_disallowed=child_extra_disallowed(cfg),
        )
        return cap.catalog_mode == "write"
    return str(getattr(cfg, "permission_mode", "") or "") != "readOnly"


def detect_parallel_conflict(
    tasks: list[tuple[ParallelTask, SubagentConfig]],
    parent_capability: Any | None = None,
) -> str | None:
    """发布前检查。任一交集后仍可写则整批拒绝；全只读可并行。

    ``file_paths`` 只是任务说明，不参与安全判定。
    """
    writable = [
        cfg
        for _task, cfg in tasks
        if _child_is_writable(cfg, parent_capability)
    ]
    if not writable:
        return None
    names = ", ".join(cfg.name for cfg in writable)
    return (
        f"禁止并行含可写子代理（{names}）。"
        "请合并到一个子代理，或改为只读 explorer。"
    )


def assert_parallel_allowed(
    tasks: list[tuple[ParallelTask, SubagentConfig]],
    parent_capability: Any | None = None,
) -> None:
    conflict = detect_parallel_conflict(tasks, parent_capability=parent_capability)
    if conflict:
        raise SubagentError("PARALLEL_CONFLICT", conflict)
