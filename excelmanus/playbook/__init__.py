"""Playbook — 自进化战术手册系统。

基于 Agentic Context Engineering (ACE) 范式，通过执行反馈
自动积累、精炼和组织可复用的策略教训。
"""

from excelmanus.playbook.curator import CuratorReport, PlaybookCurator
from excelmanus.playbook.reflector import PlaybookDelta, TaskReflector
from excelmanus.playbook.store import PlaybookBullet, PlaybookStore

__all__ = [
    "CuratorReport",
    "PlaybookBullet",
    "PlaybookCurator",
    "PlaybookDelta",
    "PlaybookStore",
    "TaskReflector",
]
