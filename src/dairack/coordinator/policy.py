"""Policy controls shared by routing, planning, review, and delegation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CoordinatorPolicy:
    name: str
    delegation_limit: int
    specialist_token_budget: int
    efficiency_weight: float
    residency_bonus: float
    planning_threshold: float
    review_threshold: float
    semantic_mode: str


POLICIES = {
    "efficient": CoordinatorPolicy(
        name="efficient",
        delegation_limit=1,
        specialist_token_budget=360,
        efficiency_weight=1.18,
        residency_bonus=0.075,
        planning_threshold=1.1,
        review_threshold=1.1,
        semantic_mode="off",
    ),
    "adaptive": CoordinatorPolicy(
        name="adaptive",
        delegation_limit=2,
        specialist_token_budget=640,
        efficiency_weight=0.62,
        residency_bonus=0.035,
        planning_threshold=0.70,
        review_threshold=0.73,
        semantic_mode="ambiguous",
    ),
    "quality": CoordinatorPolicy(
        name="quality",
        delegation_limit=3,
        specialist_token_budget=960,
        efficiency_weight=0.20,
        residency_bonus=0.008,
        planning_threshold=0.52,
        review_threshold=0.50,
        semantic_mode="substantive",
    ),
}


def policy_for(name: str) -> CoordinatorPolicy:
    return POLICIES.get(name.lower(), POLICIES["adaptive"])
