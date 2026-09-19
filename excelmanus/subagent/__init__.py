"""Subagent 模块导出。"""

from excelmanus.subagent.builtin import BUILTIN_SUBAGENTS
from excelmanus.subagent.child import compose_child, resolve_child_runtime
from excelmanus.subagent.errors import SubagentError
from excelmanus.subagent.models import (
    SubagentConfig,
    SubagentDescriptor,
    SubagentFileChange,
    SubagentMemoryScope,
    SubagentPermissionMode,
    SubagentResult,
    SubagentRun,
    SubagentSource,
    SubagentStartRequest,
    SubagentStopReason,
)
from excelmanus.subagent.parallel import ParallelOutcome, ParallelTask
from excelmanus.subagent.registry import SubagentRegistry
from excelmanus.subagent.runtime import SubagentRuntime, normalize_file_paths

__all__ = [
    "BUILTIN_SUBAGENTS",
    "ParallelOutcome",
    "ParallelTask",
    "SubagentConfig",
    "SubagentDescriptor",
    "SubagentError",
    "SubagentFileChange",
    "SubagentMemoryScope",
    "SubagentPermissionMode",
    "SubagentRegistry",
    "SubagentResult",
    "SubagentRun",
    "SubagentRuntime",
    "SubagentSource",
    "SubagentStartRequest",
    "SubagentStopReason",
    "compose_child",
    "normalize_file_paths",
    "resolve_child_runtime",
]
