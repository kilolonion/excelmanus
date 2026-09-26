"""Vision: user declaration > scoped endpoint observation > sourced model facts."""
from __future__ import annotations
from excelmanus.model_catalog import model_spec


def keyword_implies_vision(model: str) -> bool:
    """Compatibility API: exact sourced records, never family keyword guesses."""
    spec = model_spec(model)
    return bool(spec and "image" in spec.get("input_modalities", []))


def infer_vision_capable(model: str, *, override: str = "auto", probe: bool | None = None,
                         canonical_model: str = "", base_url: str = "") -> bool:
    if override in {"true", "false"}:
        return override == "true"
    if probe is not None:
        return probe
    # Canonical aliases are hints, not endpoint evidence.
    spec = model_spec(model, base_url)
    return bool(spec and "image" in spec.get("input_modalities", []))
