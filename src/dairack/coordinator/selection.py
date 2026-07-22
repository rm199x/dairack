"""Coordinator executor selection: candidate scoring, continuity, and route assembly."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..messages import latest_user_images, latest_user_task
from ..models import is_chat_model
from .analysis import (
    analyze_task,
    execution_scope,
    merge_semantic_assessment,
    referenced_task_analysis,
    runtime_action_contract,
    semantic_context,
    semantic_gate,
    task_kind,
    task_role,
)
from .calibration import estimate
from .control import explicit_routing_control, materially_larger
from .policy import policy_for
from .ranking import candidate_score, stage_model
from .ranking import role_preference as ranking_role_preference
from .ranking import semantic_router_model as ranking_semantic_router_model
from .semantic import assessment
from .tuning import DEFAULT_TUNING
from .tuning import for_config as tuning_for_config


def _runtime() -> Any:
    """Resolve runtime-owned collaborators at call time so patches on the runtime module apply."""
    from dairack import runtime

    return runtime


def candidate_score_for(
    model: Any,
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
    return candidate_score(
        str(getattr(model, "name", model) or ""),
        _runtime().model_capabilities(model),
        signals,
        policy,
        resident,
        preferred_model,
        profile_confidence,
        learned_adjustment,
        task_complexity,
        tuning,
        routing_preference,
        preference_strength,
    )


def role_preference(config: dict[str, Any], role: str, models: list[Any]) -> str:
    return ranking_role_preference(config, role, models)


def specialist_model(
    provider: Any,
    candidates: list[dict[str, Any]],
    purpose: str,
    executor: str,
    policy: str,
    signals: dict[str, float],
    preferred_model: str = "",
    task_complexity: float = 0.0,
) -> str:
    return stage_model(
        provider,
        candidates,
        purpose,
        executor,
        policy,
        signals,
        _runtime().model_supports_vision,
        preferred_model,
        task_complexity,
    )


def semantic_router_model(models: list[Any], resident: set[str]) -> str:
    return ranking_semantic_router_model(models, resident, _runtime().model_capabilities)


def _capability_fit_gap(
    winner: dict[str, Any],
    incumbent: dict[str, Any],
    signals: dict[str, float],
    floors: dict[str, float],
) -> float:
    """Return the demanded-capability advantage of one candidate over another."""
    requirements = [
        (name, max(0.0, float(signals.get(name) or 0)))
        for name, floor in floors.items()
        if float(signals.get(name) or 0) >= floor
    ]
    if not requirements:
        return 0.0
    winner_capabilities = winner.get("capabilities") or {}
    incumbent_capabilities = incumbent.get("capabilities") or {}
    weight = sum(demand for _name, demand in requirements)
    winner_fit = sum(float(winner_capabilities.get(name) or 0) * demand for name, demand in requirements) / weight
    incumbent_fit = sum(float(incumbent_capabilities.get(name) or 0) * demand for name, demand in requirements) / weight
    return winner_fit - incumbent_fit


def direct_route(config: dict[str, Any], model: str | None = None) -> dict[str, Any]:
    selected = str(model or config.get("model") or "")
    return {
        "mode": "direct",
        "policy": "direct",
        "task_kind": "direct selection",
        "executor": selected,
        "planner": "",
        "reviewer": "",
        "strategy": "single",
        "complexity": 0.0,
        "confidence": 1.0,
        "signals": {},
        "evidence": ["direct model mode"],
        "candidates": [{"model": selected, "score": 1.0}] if selected else [],
        "delegations": [],
        "created_at": _runtime().now_iso(),
    }


def executor_continuity(
    scored: list[dict[str, Any]],
    previous_route: dict[str, Any] | None,
    policy: str,
    signals: dict[str, float],
) -> dict[str, Any] | None:
    """Keep a resident incumbent executor when a cold challenger's lead is within the reload margin.

    Switching models on local hardware pays a real load cost; a marginal score
    lead does not. The margin scales with the incumbent's size (bigger models
    cost more to reload) and the policy's efficiency stance, and never applies
    when the challenger is already resident, when there is no incumbent, or when
    the task needs vision the incumbent lacks.
    """
    if not isinstance(previous_route, dict) or not scored:
        return None
    incumbent = str(previous_route.get("executor") or "").strip()
    if not incumbent or scored[0]["model"].lower() == incumbent.lower():
        return None
    entry = next((item for item in scored if str(item["model"]).lower() == incumbent.lower()), None)
    if entry is None or not entry.get("resident") or scored[0].get("resident"):
        return None
    if float(signals.get("vision") or 0) > 0:
        capabilities = entry.get("capabilities")
        if not isinstance(capabilities, dict) or float(capabilities.get("vision") or 0) < 0.50:
            return None
    capability_gap = _capability_fit_gap(
        scored[0],
        entry,
        signals,
        {
            "code": 0.52,
            "agent": 0.58,
            "reasoning": 0.58,
            "research": 0.62,
            "vision": 0.25,
        },
    )
    if capability_gap >= 0.10:
        return None
    size_gb = float(getattr(entry.get("descriptor"), "size", 0) or 0) / 1e9
    policy_scale = {"efficient": 1.4, "adaptive": 1.0, "quality": 0.5}.get(policy, 1.0)
    margin = min(0.05, (0.015 + min(0.03, size_gb * 0.0035)) * policy_scale)
    gap = float(scored[0]["score"]) - float(entry["score"])
    if gap > margin:
        return None
    return {
        "executor": str(entry["model"]),
        "over": str(scored[0]["model"]),
        "gap": round(gap, 4),
        "margin": round(margin, 4),
        "capability_gap": round(capability_gap, 4),
    }


def warm_pool_continuity(
    scored: list[dict[str, Any]],
    policy: str,
    complexity: float,
    signals: dict[str, float] | None = None,
    task_kind: str = "",
) -> dict[str, Any] | None:
    """Prefer a near-best warm executor when loading the winner is poor value."""
    if (
        policy == "quality"
        or not scored
        or scored[0].get("resident")
        or complexity >= 0.78
        or float((signals or {}).get("reasoning") or 0) >= 0.85
        or task_kind == "deep reasoning"
    ):
        return None
    warm = next((candidate for candidate in scored[1:] if candidate.get("resident")), None)
    if warm is None:
        return None
    winner_efficiency = float((scored[0].get("capabilities") or {}).get("efficiency") or 0)
    warm_efficiency = float((warm.get("capabilities") or {}).get("efficiency") or 0)
    capability_gap = _capability_fit_gap(
        scored[0],
        warm,
        signals or {},
        {"code": 0.60, "agent": 0.65, "research": 0.70},
    )
    if capability_gap >= 0.12:
        return None
    efficiency_gain = max(0.0, warm_efficiency - winner_efficiency)
    policy_base = 0.055 if policy == "efficient" else 0.025
    margin = policy_base + max(0.0, 1.0 - complexity) * 0.05 + efficiency_gain * 0.08
    margin = min(0.14 if policy == "efficient" else 0.09, margin)
    gap = float(scored[0]["score"]) - float(warm["score"])
    if gap > margin:
        return None
    return {
        "executor": str(warm["model"]),
        "over": str(scored[0]["model"]),
        "gap": round(gap, 4),
        "margin": round(margin, 4),
        "source": "warm pool",
    }


def select_route(
    provider: Any,
    config: dict[str, Any],
    messages: list[dict[str, str]],
    cwd: Path,
    cancel_event: threading.Event | None = None,
    previous_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    action_contract = runtime_action_contract(messages)
    if str(config.get("model_mode") or "direct") != "orchestrator":
        route = direct_route(config)
        route["prompt"] = latest_user_task(messages)
        route["action_contract"] = action_contract
        route["execution_scope"] = "agentic" if action_contract else "direct-answer"
        return route
    policy = str(config.get("orchestrator_policy") or "adaptive").lower()
    if policy not in _runtime().ORCHESTRATOR_POLICIES:
        policy = "adaptive"
    policy_definition = policy_for(policy)
    tuning = tuning_for_config(config)
    try:
        installed_models = list(provider.list_models())
    except Exception as exc:
        route = direct_route(config)
        route.update(
            {
                "mode": "orchestrator",
                "policy": policy,
                "task_kind": "registry fallback",
                "confidence": 0.0,
                "evidence": ["model registry unavailable; retained fallback model"],
                "routing_error": str(exc),
                "action_contract": action_contract,
            }
        )
        return route
    models = [model for model in installed_models if is_chat_model(model)]
    if not models:
        if installed_models:
            raise RuntimeError("no installed model supports chat completion")
        route = direct_route(config)
        route["prompt"] = latest_user_task(messages)
        route["action_contract"] = action_contract
        return route
    try:
        resident = set(provider.running_models())
    except Exception:
        resident = set()
    analysis = analyze_task(messages, cwd)
    prior_analysis = referenced_task_analysis(messages, cwd)
    explicit_control = explicit_routing_control(
        str(analysis.get("prompt") or ""),
        str(prior_analysis.get("prompt") or ""),
    )
    if explicit_control.active:
        control_payload = explicit_control.to_dict()
        if explicit_control.applies_to_previous and prior_analysis:
            current_prompt = str(analysis.get("prompt") or "")
            current_contract = analysis.get("action_contract")
            analysis = dict(prior_analysis)
            analysis["prompt"] = current_prompt
            if isinstance(current_contract, dict) and current_contract.get("capability"):
                analysis["action_contract"] = current_contract
        analysis["routing_control"] = control_payload
        analysis["evidence"] = ["explicit per-turn compute directive", *list(analysis.get("evidence") or [])][:6]
    signals = analysis["signals"]
    complexity = float(analysis["complexity"])
    resident_lower = {value.lower() for value in resident}
    learning_state = (
        _runtime().load_calibration_state(_runtime().coordinator_learning_path())
        if bool(config.get("coordinator_learning", True))
        else {"records": {}}
    )

    def rank_candidates(
        active_signals: dict[str, float],
        active_complexity: float,
        routing_control: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        routing_control = routing_control if isinstance(routing_control, dict) else {}
        preference = str(routing_control.get("preference") or "auto") if routing_control.get("active") else "auto"
        preference_strength = float(routing_control.get("strength") or 0) if routing_control.get("active") else 0.0
        role = task_role(active_signals)
        kind = task_kind(active_signals)
        preferred = role_preference(config, role, models)
        ranked: list[dict[str, Any]] = []
        for model in models:
            if float(active_signals.get("vision") or 0) > 0 and not _runtime().model_supports_vision(
                provider, model.name
            ):
                continue
            metadata = _runtime().model_capability_metadata(model)
            profile_confidence = float(metadata.get("confidence") or 0.5)
            calibration = estimate(learning_state, model.name, role, kind)
            raw_learned = calibration.value
            learned = _runtime()._effective_learning_adjustment(active_signals, active_complexity, raw_learned)
            score = candidate_score_for(
                model,
                active_signals,
                policy,
                resident,
                preferred,
                profile_confidence,
                learned,
                active_complexity,
                tuning,
                preference,
                preference_strength,
            )
            ranked.append(
                {
                    "model": model.name,
                    "descriptor": model,
                    "score": score,
                    "resident": model.name.lower() in resident_lower,
                    "capabilities": _runtime().model_capabilities(model),
                    "preferred": bool(preferred and model.name.lower() == preferred.lower()),
                    "confidence": profile_confidence,
                    "profile_source": str(metadata.get("source") or "inferred"),
                    "learned_adjustment": learned,
                    "learning_evidence": calibration.effective_evidence,
                    "learning_role_evidence": calibration.role_evidence,
                    "learning_kind_evidence": calibration.kind_evidence,
                    "learning_kind_weight": calibration.kind_weight,
                    "learning_guarded": bool(raw_learned and not learned),
                }
            )
        ranked.sort(
            key=lambda item: (
                -item["score"],
                not item["resident"],
                -float(item["capabilities"].get("efficiency") or 0),
                item["model"].lower(),
            )
        )
        return ranked

    scored = rank_candidates(signals, complexity)
    if not scored:
        raise RuntimeError("no installed model can satisfy the required input modality")
    score_gap = scored[0]["score"] - scored[1]["score"] if len(scored) > 1 else 1.0
    semantic_model = semantic_router_model(models, resident)
    context_text = semantic_context(messages)
    semantic_trigger = semantic_gate(policy, analysis, score_gap, bool(context_text))
    referenced_analysis = (
        prior_analysis
        if semantic_trigger in {"conversation-dependent follow-up", "short contextual turn"}
        or explicit_control.applies_to_previous
        else {}
    )
    should_assess = (
        bool(config.get("orchestrator_semantic_routing", True))
        and policy in {"adaptive", "quality"}
        and bool(semantic_model)
        and bool(semantic_trigger)
    )
    if should_assess and not (cancel_event and cancel_event.is_set()):
        try:
            assessed = assessment(
                provider,
                config,
                semantic_model,
                latest_user_task(messages),
                context_text,
                f"attached images: {len(latest_user_images(messages))}",
                cancel_event,
                tuning,
            )
        except Exception as exc:
            assessed = {"error": str(exc), "model": semantic_model}
        if assessed and "error" not in assessed:
            assessed["trigger"] = semantic_trigger
            analysis = merge_semantic_assessment(analysis, assessed, tuning, referenced_analysis)
            signals = analysis["signals"]
            complexity = float(analysis["complexity"])
        elif assessed:
            assessed["trigger"] = semantic_trigger
            analysis["semantic_assessment"] = assessed
        # Semantic arbitration is itself an inference and may have made the
        # efficient router resident. Re-sample before the final ranking so the
        # executor decision uses current, not pre-arbitration, load state.
        try:
            resident = set(provider.running_models())
            resident_lower = {value.lower() for value in resident}
        except Exception:
            pass
    baseline_scored = rank_candidates(signals, complexity)
    routing_control = analysis.get("routing_control")
    routing_control = dict(routing_control) if isinstance(routing_control, dict) else {}
    scored = baseline_scored
    if routing_control.get("active"):
        controlled_scored = rank_candidates(signals, complexity, routing_control)
        baseline = baseline_scored[0]
        if routing_control.get("preference") == "higher_capacity":
            controlled_baseline = next(
                (
                    candidate
                    for candidate in controlled_scored
                    if candidate["model"].lower() == baseline["model"].lower()
                ),
                baseline,
            )
            allowed_drop = 0.04 + float(routing_control.get("strength") or 0) * 0.06
            eligible = [
                candidate
                for candidate in controlled_scored
                if materially_larger(candidate["descriptor"], baseline["descriptor"])
                and float(candidate["capabilities"].get("efficiency") or 0) >= 0.25
                and float(candidate["score"]) >= float(controlled_baseline["score"]) - allowed_drop
            ]
            if eligible:
                selected_names = {candidate["model"].lower() for candidate in eligible}
                scored = eligible + [
                    candidate for candidate in controlled_scored if candidate["model"].lower() not in selected_names
                ]
                routing_control["honored"] = True
            else:
                routing_control["honored"] = False
                routing_control["status"] = "no suitable higher-capacity fit"
        else:
            scored = controlled_scored
            routing_control["honored"] = True
        routing_control["baseline_executor"] = baseline["model"]
        routing_control["selected_executor"] = scored[0]["model"]
        routing_control["changed_executor"] = scored[0]["model"].lower() != baseline["model"].lower()
        analysis["routing_control"] = routing_control
        semantic_assessment = analysis.get("semantic_assessment")
        if isinstance(semantic_assessment, dict):
            semantic_assessment["routing_control"] = routing_control
    executor = scored[0]["model"]
    continuity = (
        None
        if routing_control.get("active") and routing_control.get("honored")
        else executor_continuity(scored, previous_route, policy, signals)
    )
    if continuity is None and not routing_control.get("active"):
        continuity = warm_pool_continuity(scored, policy, complexity, signals, str(analysis.get("task_kind") or ""))
    if continuity:
        executor = continuity["executor"]
    planner = ""
    reviewer = ""
    use_plan = False
    use_review = False
    if policy == "quality":
        use_plan = (
            bool(config.get("orchestrator_planning", True))
            and complexity >= policy_definition.planning_threshold
            and (signals["agent"] >= 0.42 or signals["reasoning"] >= 0.48)
        )
        use_review = (
            bool(config.get("orchestrator_review", True))
            and complexity >= policy_definition.review_threshold
            and (
                signals["code"] >= 0.40
                or signals["reasoning"] >= 0.48
                or signals["risk"] >= 0.34
                or signals["vision"] >= 0.50
            )
        )
        if bool(config.get("orchestrator_review", True)) and signals["vision"] and complexity >= 0.30:
            use_review = True
    elif policy == "adaptive":
        use_plan = (
            bool(config.get("orchestrator_planning", True))
            and complexity >= policy_definition.planning_threshold
            and (signals["agent"] >= 0.50 or signals["reasoning"] >= 0.65)
        )
        use_review = (
            bool(config.get("orchestrator_review", True))
            and complexity >= policy_definition.review_threshold
            and (
                signals["code"] >= 0.52
                or signals["reasoning"] >= 0.66
                or signals["risk"] >= 0.42
                or signals["vision"] >= 0.50
            )
        )
        if (
            bool(config.get("orchestrator_review", True))
            and str(analysis.get("task_kind") or "") == "coding agent"
            and complexity >= 0.52
            and signals["code"] >= 0.60
            and signals["agent"] >= 0.65
        ):
            use_review = True
    semantic_assessment = analysis.get("semantic_assessment")
    if isinstance(semantic_assessment, dict) and policy != "efficient":
        # Confident semantic assessments lower — never remove — the deterministic support floors,
        # so requests the keyword layer cannot see are still eligible for planning and review.
        semantic_confidence = max(0.0, min(1.0, float(semantic_assessment.get("confidence") or 0)))
        floor_relax = 0.16 if semantic_confidence >= 0.72 else 0.08 if semantic_confidence >= 0.55 else 0.0
        signal_relax = floor_relax / 2
        semantic_plan_supported = complexity >= ((0.62 if policy == "adaptive" else 0.48) - floor_relax) and (
            signals["agent"] >= 0.42 - signal_relax or signals["reasoning"] >= 0.48 - signal_relax
        )
        semantic_review_supported = complexity >= ((0.64 if policy == "adaptive" else 0.46) - floor_relax) and (
            signals["code"] >= 0.40 - signal_relax
            or signals["reasoning"] >= 0.48 - signal_relax
            or signals["risk"] >= 0.34 - signal_relax
            or signals["vision"] >= 0.50
        )
        if (
            bool(config.get("orchestrator_planning", True))
            and semantic_assessment.get("needs_plan")
            and semantic_plan_supported
        ):
            use_plan = True
        if (
            bool(config.get("orchestrator_review", True))
            and semantic_assessment.get("needs_review")
            and semantic_review_supported
        ):
            use_review = True
    if use_plan:
        planner = specialist_model(
            provider,
            scored,
            "planner",
            executor,
            policy,
            signals,
            role_preference(config, "planner", models),
            complexity,
        )
    if use_review:
        reviewer = specialist_model(
            provider,
            scored,
            "reviewer",
            executor,
            policy,
            signals,
            role_preference(config, "reviewer", models),
            complexity,
        )
    strategy = "plan-review" if planner and reviewer else "planned" if planner else "reviewed" if reviewer else "single"
    gap = scored[0]["score"] - scored[1]["score"] if len(scored) > 1 else 0.25
    profile_confidence = float(scored[0].get("confidence") or 0.5)
    confidence = min(0.97, (0.57 + max(0.0, gap) * 2.4) * (0.82 + profile_confidence * 0.18))
    return {
        "mode": "orchestrator",
        "policy": policy,
        "prompt": analysis["prompt"],
        "task_kind": analysis["task_kind"],
        "executor": executor,
        "planner": planner,
        "reviewer": reviewer,
        "strategy": strategy,
        "complexity": complexity,
        "confidence": round(confidence, 3),
        "signals": signals,
        "execution_scope": execution_scope(
            analysis["task_kind"],
            signals,
            analysis.get("action_contract") if isinstance(analysis.get("action_contract"), dict) else None,
            analysis.get("routing_control") if isinstance(analysis.get("routing_control"), dict) else None,
        ),
        "evidence": analysis["evidence"],
        "semantic_assessment": analysis.get("semantic_assessment") or {},
        "routing_control": analysis.get("routing_control") or {},
        "action_contract": analysis.get("action_contract") or action_contract,
        "preference_role": task_role(signals),
        "preferred_model": role_preference(config, task_role(signals), models),
        "candidates": [
            {
                "model": item["model"],
                "score": round(item["score"], 3),
                "resident": item["resident"],
                "preferred": item.get("preferred", False),
                "confidence": round(float(item.get("confidence") or 0.5), 3),
                "profile_source": item.get("profile_source") or "inferred",
                "learned_adjustment": round(float(item.get("learned_adjustment") or 0), 4),
                "learning_evidence": round(float(item.get("learning_evidence") or 0), 2),
                "learning_role_evidence": round(float(item.get("learning_role_evidence") or 0), 2),
                "learning_kind_evidence": round(float(item.get("learning_kind_evidence") or 0), 2),
                "learning_kind_weight": round(float(item.get("learning_kind_weight") or 0), 3),
                "learning_guarded": bool(item.get("learning_guarded")),
            }
            for item in scored
        ],
        "continuity": continuity or {},
        "delegations": [],
        "created_at": _runtime().now_iso(),
    }
