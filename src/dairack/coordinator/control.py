"""Validated, per-turn natural-language routing controls."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

ROUTING_PREFERENCES = {"auto", "quality", "higher_capacity", "efficiency"}
CONTROL_TARGETS = {"none", "compute", "content", "discussion"}
MIN_CONTROL_CONFIDENCE = {
    "quality": 0.70,
    "higher_capacity": 0.70,
    "efficiency": 0.75,
}
MIN_CONTROL_STRENGTH = 0.55
MAX_RESOLVED_TASK_CHARS = 1200


def _bounded_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return round(parsed, 3)


def _clean_task(value: object) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:MAX_RESOLVED_TASK_CHARS]


@dataclass(frozen=True, slots=True)
class RoutingControl:
    preference: str = "auto"
    target: str = "none"
    strength: float = 0.0
    confidence: float = 0.0
    applies_to_previous: bool = False
    resolved_task: str = ""
    active: bool = False
    status: str = "automatic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_semantic_payload(cls, payload: Mapping[str, Any], *, has_context: bool) -> "RoutingControl":
        preference = str(payload.get("compute_preference") or "auto").strip().lower()
        target = str(payload.get("control_target") or "none").strip().lower()
        strength = _bounded_number(payload.get("preference_strength"))
        confidence = _bounded_number(payload.get("control_confidence"))
        applies = payload.get("applies_to_previous")
        resolved_task = _clean_task(payload.get("resolved_task"))
        if (
            preference not in ROUTING_PREFERENCES
            or target not in CONTROL_TARGETS
            or strength is None
            or confidence is None
        ):
            return cls(status="invalid semantic control")
        if not isinstance(applies, bool):
            return cls(status="invalid semantic control")
        if preference == "auto":
            return cls(preference=preference, target=target, confidence=confidence, status="automatic")
        if target in {"content", "discussion"}:
            return cls(
                preference=preference,
                target=target,
                strength=strength,
                confidence=confidence,
                applies_to_previous=applies,
                resolved_task=resolved_task,
                status="not a compute directive",
            )
        target = "compute"
        if confidence < MIN_CONTROL_CONFIDENCE[preference]:
            return cls(
                preference=preference,
                target=target,
                strength=strength,
                confidence=confidence,
                applies_to_previous=applies,
                resolved_task=resolved_task,
                status="confidence below threshold",
            )
        if strength < MIN_CONTROL_STRENGTH:
            return cls(
                preference=preference,
                target=target,
                strength=strength,
                confidence=confidence,
                applies_to_previous=applies,
                resolved_task=resolved_task,
                status="strength below threshold",
            )
        if applies and not has_context:
            return cls(
                preference=preference,
                target=target,
                strength=strength,
                confidence=confidence,
                applies_to_previous=applies,
                resolved_task=resolved_task,
                status="no prior task available",
            )
        if applies and not resolved_task:
            return cls(
                preference=preference,
                target=target,
                strength=strength,
                confidence=confidence,
                applies_to_previous=applies,
                status="prior task was not resolved",
            )
        return cls(
            preference=preference,
            target=target,
            strength=strength,
            confidence=confidence,
            applies_to_previous=applies,
            resolved_task=resolved_task,
            active=True,
            status="applied",
        )


def model_capacity(model: Any) -> float:
    """Return a provider-neutral scale proxy without relying on model names."""

    try:
        parameters = float(getattr(model, "parameter_billions", 0.0) or 0.0)
    except (TypeError, ValueError):
        parameters = 0.0
    if parameters > 0:
        return parameters
    try:
        size = max(0, int(getattr(model, "size", 0) or 0))
    except (TypeError, ValueError):
        size = 0
    return size / 1024**3


def materially_larger(candidate: Any, baseline: Any, *, ratio: float = 1.20) -> bool:
    baseline_capacity = model_capacity(baseline)
    candidate_capacity = model_capacity(candidate)
    return baseline_capacity > 0 and candidate_capacity >= baseline_capacity * max(1.01, ratio)
