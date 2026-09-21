"""System One 内部类型。业务代码只认这些，禁止直接抠 SDK 字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

PackId = Literal[
    "context.resolve",
    "exposure.turn",
    "observation.shape",
    "observation.prune",
    "mutation.verify",
    "recovery.next_step",
    "ui.surface",
    "approval.tool_call",
    "skill.pin",
    "loop.wrap",
]
DecisionKind = Literal["auto", "ask", "deny", "escalate", "noop", "outcome"]
# JEV has a binary runtime contract.  Legacy ``shadow`` values are migrated
# to ``enforce`` at the configuration boundary and must never reach the host.
GateLevel = Literal["off", "enforce"]
Family = Literal["security", "optimize"]
ExposureProfile = Literal["inspect", "edit", "file_code", "web", "minimal", "full"]


@dataclass(frozen=True)
class NoulAnswer:
    """是/否题。内部统一叫 noul；Gateway ``probability`` 在客户端已折到这里。"""

    noul: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    legend: tuple[str, ...] = ()


Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(frozen=True)
class Evaluation:
    pack_id: str
    answers: Mapping[str, Answer]
    model: str = ""
    latency_ms: float = 0.0
    usage: Mapping[str, Any] = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    provider_id: str = ""
    protocol: str = ""


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    evaluation: Evaluation | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)
    applied: bool = False

    @classmethod
    def noop(cls, reason: str, **extras: Any) -> "Decision":
        return cls(kind="noop", reason=reason, extras=extras, applied=False)

    @classmethod
    def ask(cls, reason: str, **extras: Any) -> "Decision":
        return cls(kind="ask", reason=reason, extras=extras, applied=False)
