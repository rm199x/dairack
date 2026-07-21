"""Pure coordinator ranking and stage-selection policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .calibration import MAX_TOTAL_ADJUSTMENT
from .policy import policy_for
from .tuning import DEFAULT_TUNING


def candidate_score(
    model_name: str,
    capabilities: dict[str, float],
    signals: dict[str, float],
    policy: str,
    resident: set[str],
    preferred_model: str = "",
    profile_confidence: float = 0.5,
    learned_adjustment: float = 0.0,
    task_complexity: float = 0.0,
    tuning: Any = DEFAULT_TUNING,
    routing_preference: str = "auto",
    preference_strength: float = 0.0,
) -> float:
    policy_definition = policy_for(policy)
    capability_fields = ("code", "agent", "reasoning", "general", "research", "vision")
    demands = {key: max(0.0, float(signals.get(key) or 0)) for key in capability_fields}
    simplicity = max(0.0, min(1.0, float(signals.get("simple") or 0)))
    ease = max(simplicity, 1.0 - max(0.0, min(1.0, task_complexity)))
    grounding_balance = demands["research"] - demands["reasoning"]
    grounding_multiplier = max(
        0.72,
        min(1.38, 1.0 + grounding_balance * float(tuning.grounding_balance_weight)),
    )
    preference_strength = max(0.0, min(1.0, preference_strength))
    quality_shift = preference_strength if routing_preference in {"quality", "higher_capacity"} else 0.0
    efficiency_shift = preference_strength if routing_preference == "efficiency" else 0.0
    demands["efficiency"] = ease * policy_definition.efficiency_weight * grounding_multiplier
    demands["efficiency"] *= max(0.04, 1.0 - quality_shift * 0.96)
    demands["efficiency"] *= 1.0 + efficiency_shift * 1.75
    if not sum(demands.values()):
        demands["general"] = 1.0
    capability_ceiling = (
        1.0
        if policy == "quality"
        else 0.68 + 0.20 * task_complexity
        if policy == "efficient"
        else 0.74 + 0.24 * task_complexity
    )
    capability_ceiling += (1.0 - capability_ceiling) * quality_shift
    weighted = sum(
        (capabilities[key] if key == "efficiency" else min(capabilities[key], capability_ceiling)) * weight
        for key, weight in demands.items()
    )
    score = weighted / max(0.001, sum(demands.values()))
    lowered_resident = {value.lower() for value in resident}
    if model_name.lower() in lowered_resident:
        complexity_discount = 1.0 - max(0.0, min(1.0, task_complexity))
        preference_residency = max(0.04, 1.0 - quality_shift * 0.96) * (1.0 + efficiency_shift * 1.5)
        score += (
            policy_definition.residency_bonus
            * simplicity**2
            * complexity_discount
            * float(tuning.residency_scale)
            * preference_residency
        )
    if preferred_model and model_name.lower() == preferred_model.lower():
        score += {"efficient": 0.045, "adaptive": 0.060, "quality": 0.045}[policy]
    score += max(-MAX_TOTAL_ADJUSTMENT, min(MAX_TOTAL_ADJUSTMENT, learned_adjustment))
    score *= 0.96 + max(0.0, min(1.0, profile_confidence)) * 0.04
    if signals["vision"] and capabilities["vision"] < 0.50:
        score *= 0.12
    return max(0.0, min(0.999, score))


def effective_learning_adjustment(
    signals: dict[str, float],
    task_complexity: float,
    learned_adjustment: float,
) -> float:
    specialized = max(
        float(signals.get(key) or 0) for key in ("code", "agent", "reasoning", "research", "vision", "risk")
    )
    simplicity = float(signals.get("simple") or 0)
    if simplicity >= 0.72 and specialized < 0.25 and task_complexity < 0.36:
        return 0.0
    confidence = max(0.15, min(1.0, max(specialized, task_complexity)))
    return learned_adjustment * confidence


def role_preference(config: dict[str, Any], role: str, models: list[Any]) -> str:
    raw = config.get("coordinator_role_preferences")
    if not isinstance(raw, dict):
        return ""
    preferred = str(raw.get(role) or "").strip()
    if not preferred:
        return ""
    return next((str(model.name) for model in models if str(model.name).lower() == preferred.lower()), "")


def stage_model(
    provider: Any,
    candidates: list[dict[str, Any]],
    purpose: str,
    executor: str,
    policy: str,
    signals: dict[str, float],
    supports_vision: Callable[[Any, str], bool],
    preferred_model: str = "",
) -> str:
    has_resident = any(bool(candidate.get("resident")) for candidate in candidates)
    best_name = executor
    best_score = -1.0
    for candidate in candidates:
        if signals["vision"] and not supports_vision(provider, str(candidate["model"])):
            continue
        capabilities = candidate["capabilities"]
        if purpose == "planner":
            weights = {
                "reasoning": 0.34 + signals["reasoning"] * 0.14,
                "general": 0.18,
                "agent": 0.10 + signals["agent"] * 0.16,
                "code": 0.06 + signals["code"] * 0.12,
                "research": 0.06 + signals["research"] * 0.10,
                "vision": 0.02 + signals["vision"] * 0.20,
            }
            capability_score = sum(capabilities[key] * weight for key, weight in weights.items()) / sum(
                weights.values()
            )
        else:
            weights = {
                "route": 0.40,
                "reasoning": 0.25,
                "general": 0.08,
                "code": 0.05 + signals["code"] * 0.07,
                "agent": 0.05,
                "vision": 0.02 + signals["vision"] * 0.08,
            }
            capability_score = (
                float(candidate["score"]) * weights["route"]
                + sum(capabilities[key] * weight for key, weight in weights.items() if key != "route")
            ) / sum(weights.values())
        efficiency_mix = {"efficient": 0.32, "adaptive": 0.16, "quality": 0.03}[policy]
        score = capability_score * (1.0 - efficiency_mix) + capabilities["efficiency"] * efficiency_mix
        same_executor = candidate["model"].lower() == executor.lower()
        if candidate.get("resident"):
            score += {"efficient": 0.11, "adaptive": 0.07, "quality": 0.015}[policy]
        if same_executor:
            if purpose == "planner":
                score += 0.08 if policy == "adaptive" and not has_resident else 0.015
            elif purpose == "reviewer":
                score += {"efficient": 0.07, "adaptive": 0.04, "quality": 0.005}[policy]
        elif purpose == "reviewer":
            score += {"efficient": 0.0, "adaptive": 0.025, "quality": 0.055}[policy]
        elif purpose == "planner":
            score += 0.005
        if preferred_model and candidate["model"].lower() == preferred_model.lower():
            score += 0.045
        if score > best_score:
            best_name = candidate["model"]
            best_score = score
    return best_name


def semantic_router_model(
    models: list[Any],
    resident: set[str],
    capabilities_for: Callable[[Any], dict[str, float]],
) -> str:
    lowered_resident = {name.lower() for name in resident}
    if not models:
        return ""
    eligible = list(models)
    eligible.sort(
        key=lambda model: (
            -(
                capabilities_for(model)["efficiency"] * 0.54
                + capabilities_for(model)["general"] * 0.24
                + capabilities_for(model)["reasoning"] * 0.22
                + (0.08 if model.name.lower() in lowered_resident else 0.0)
            )
        )
    )
    return str(eligible[0].name)
