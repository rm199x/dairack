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

_DIRECTIVE_LEAD_PATTERN = re.compile(
    r"^(?:(?:please|kindly)\s+)?(?:"
    r"(?:can|could|would|will)\s+you\s+|"
    r"i(?:'d|\s+would)\s+like\s+(?:you\s+to\s+)?|"
    r"i\s+want\s+(?:you\s+to\s+)?"
    r")?(?:use|try(?:\s+using)?|choose|select|switch(?:\s+(?:this|it))?\s+to|"
    r"route(?:\s+(?:this|it|the\s+task))?\s+to|run(?:\s+(?:this|it|the\s+task))?\s+(?:on|with)|"
    r"answer(?:\s+(?:this|it))?\s+(?:using|with))\b"
)
_COMPUTE_TARGET_PATTERN = re.compile(r"\b(?:model|executor|inference|compute)\b")
_HIGHER_CAPACITY_PATTERN = re.compile(
    r"\b(?:larger|bigger|heavier|stronger|more\s+capable|higher[ -]capacity|most\s+capable|strongest)\b"
)
_EFFICIENCY_PATTERN = re.compile(
    r"\b(?:smaller|lighter|faster|quicker|more\s+efficient|lower[ -]latency|least\s+expensive)\b"
)
_DEICTIC_TARGET_PATTERN = re.compile(
    r"\b(?:something|one)\s+(?:larger|bigger|heavier|stronger|smaller|lighter|faster)\b"
)
_QUOTED_LANGUAGE_PATTERN = re.compile(r"\b(?:word|phrase|term|label|name|text)\b.{0,32}\b(?:model|executor)\b")


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


def explicit_routing_control(prompt: str, prior_task: str = "") -> RoutingControl:
    """Recognize only unambiguous per-turn compute directives.

    Semantic routing remains responsible for conversational controls such as
    "go deeper". This narrow grammar protects explicit model-capacity requests
    from classifier variance without treating discussion or content adjectives
    as execution controls.
    """

    normalized = re.sub(r"\s+", " ", str(prompt or "").lower().replace("’", "'")).strip()
    if not normalized or not _DIRECTIVE_LEAD_PATTERN.search(normalized):
        return RoutingControl()

    first_clause = re.split(r"[.!?;]", normalized, maxsplit=1)[0]
    if _QUOTED_LANGUAGE_PATTERN.search(first_clause):
        return RoutingControl()
    has_explicit_target = bool(_COMPUTE_TARGET_PATTERN.search(first_clause))
    has_contextual_target = bool(prior_task.strip() and _DEICTIC_TARGET_PATTERN.search(first_clause))
    if not (has_explicit_target or has_contextual_target):
        return RoutingControl()

    if _HIGHER_CAPACITY_PATTERN.search(first_clause):
        preference = "higher_capacity"
    elif _EFFICIENCY_PATTERN.search(first_clause):
        preference = "efficiency"
    else:
        return RoutingControl()

    remainder = normalized[len(first_clause) :].strip(" .!?;:-")
    control_only = not remainder and len(re.findall(r"\b\w+\b", first_clause)) <= 18
    applies_to_previous = bool(control_only and prior_task.strip())
    if control_only and not applies_to_previous:
        return RoutingControl(
            preference=preference,
            target="compute",
            strength=0.9,
            confidence=0.99,
            status="no prior task available",
        )
    return RoutingControl(
        preference=preference,
        target="compute",
        strength=0.9,
        confidence=0.99,
        applies_to_previous=applies_to_previous,
        resolved_task=_clean_task(prior_task) if applies_to_previous else "",
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
