"""Small, interpretable tuning vector for coordinator policy calibration."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import Any, Mapping

TUNING_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CoordinatorTuning:
    semantic_evidence_scale: float = 1.0
    residency_scale: float = 1.0
    grounding_balance_weight: float = 0.65
    intent_floor_strength: float = 0.25
    operational_risk_weight: float = 0.25

    def to_dict(self) -> dict[str, float | int]:
        return {"schema_version": TUNING_SCHEMA_VERSION, **asdict(self)}


DEFAULT_TUNING = CoordinatorTuning()
TUNING_BOUNDS: dict[str, tuple[float, float]] = {
    "semantic_evidence_scale": (0.82, 1.18),
    "residency_scale": (0.55, 1.20),
    "grounding_balance_weight": (0.35, 0.90),
    "intent_floor_strength": (0.16, 0.34),
    "operational_risk_weight": (0.16, 0.34),
}


def from_mapping(value: Mapping[str, Any] | None) -> CoordinatorTuning:
    if not isinstance(value, Mapping):
        return DEFAULT_TUNING
    parsed: dict[str, float] = {}
    for field, (minimum, maximum) in TUNING_BOUNDS.items():
        fallback = float(getattr(DEFAULT_TUNING, field))
        try:
            candidate = float(value.get(field, fallback))
        except (TypeError, ValueError):
            candidate = fallback
        if not minimum <= candidate <= maximum:
            candidate = fallback
        parsed[field] = candidate
    return CoordinatorTuning(**parsed)


def for_config(config: Mapping[str, Any]) -> CoordinatorTuning:
    return from_mapping(config.get("_coordinator_tuning"))


def candidate_vectors(count: int, seed: int) -> list[CoordinatorTuning]:
    """Generate a deterministic bounded search set with the baseline first."""
    requested = max(1, min(512, count))
    values = [DEFAULT_TUNING]
    for field, (minimum, maximum) in TUNING_BOUNDS.items():
        for value in (
            minimum,
            (minimum + getattr(DEFAULT_TUNING, field)) / 2,
            (maximum + getattr(DEFAULT_TUNING, field)) / 2,
            maximum,
        ):
            candidate = asdict(DEFAULT_TUNING)
            candidate[field] = value
            values.append(CoordinatorTuning(**candidate))
    rng = random.Random(seed)
    while len(values) < requested:
        values.append(
            CoordinatorTuning(
                **{field: rng.uniform(minimum, maximum) for field, (minimum, maximum) in TUNING_BOUNDS.items()}
            )
        )
    unique: list[CoordinatorTuning] = []
    seen: set[tuple[float, ...]] = set()
    for candidate in values:
        key = tuple(round(float(getattr(candidate, field)), 8) for field in TUNING_BOUNDS)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= requested:
            break
    return unique


def normalized_distance(candidate: CoordinatorTuning, baseline: CoordinatorTuning = DEFAULT_TUNING) -> float:
    distances = []
    for field, (minimum, maximum) in TUNING_BOUNDS.items():
        span = maximum - minimum
        distances.append(abs(getattr(candidate, field) - getattr(baseline, field)) / span)
    return sum(distances) / len(distances)
