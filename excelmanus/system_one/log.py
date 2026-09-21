"""Jev 决策记录：pack、model、usage、answers、合成动作。不打网。"""

from __future__ import annotations

from typing import Any

from excelmanus.logger import get_logger
from excelmanus.system_one.types import ChoiceAnswer, Decision, Evaluation, NoulAnswer, ScoreAnswer

logger = get_logger("system_one")


def record_jev_decision(
    *,
    pack_id: str,
    gate: str,
    decision: Decision,
    evaluation: Evaluation | None = None,
) -> None:
    evaluation = evaluation or decision.evaluation
    answers: dict[str, Any] = {}
    if evaluation is not None:
        for key, answer in evaluation.answers.items():
            if isinstance(answer, NoulAnswer):
                answers[key] = {"noul": answer.noul}
            elif isinstance(answer, ChoiceAnswer):
                answers[key] = {
                    "choice": answer.choice,
                    "confidence": answer.confidence,
                }
            elif isinstance(answer, ScoreAnswer):
                answers[key] = {"score": answer.score, "confidence": answer.confidence}
    logger.info(
        "jev decision pack=%s gate=%s kind=%s reason=%s applied=%s extras=%s provider=%s protocol=%s model=%s latency_ms=%s answers=%s usage=%s",
        pack_id,
        gate,
        decision.kind,
        decision.reason,
        decision.applied,
        dict(decision.extras),
        getattr(evaluation, "provider_id", "") if evaluation else "",
        getattr(evaluation, "protocol", "") if evaluation else "",
        getattr(evaluation, "model", "") if evaluation else "",
        getattr(evaluation, "latency_ms", 0) if evaluation else 0,
        answers,
        dict(getattr(evaluation, "usage", {}) or {}) if evaluation else {},
    )


# Import compatibility for integrations that used the old recorder name; the
# emitted event is now a normal decision record and never denotes a shadow
# runtime mode.
record_shadow = record_jev_decision
