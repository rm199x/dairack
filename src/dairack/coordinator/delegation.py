"""Coordinator specialist delegation: specialty routing, admission, and execution."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from ..messages import IMAGE_EXTENSIONS, latest_user_task
from ..models import is_chat_model
from ..text import truncate
from .analysis import analyze_task, is_direct_answer_route, signal_hits
from .policy import POLICIES as COORDINATOR_POLICY_DEFINITIONS

COORDINATOR_SPECIALTIES = {"auto", "reasoning", "code_review", "vision", "general"}
COORDINATOR_DELEGATION_LIMITS = {
    name: policy.delegation_limit for name, policy in COORDINATOR_POLICY_DEFINITIONS.items()
}
COORDINATOR_TOKEN_BUDGETS = {
    name: policy.specialist_token_budget for name, policy in COORDINATOR_POLICY_DEFINITIONS.items()
}


def _runtime() -> Any:
    """Resolve runtime-owned collaborators at call time so patches on the runtime module apply."""
    from dairack import runtime

    return runtime


def specialty(call: dict[str, str]) -> str:
    requested = str(call.get("specialty") or "auto").strip().lower().replace("-", "_")
    path = str(call.get("path") or "")
    if Path(path).suffix.lower() in IMAGE_EXTENSIONS:
        return "vision"
    if requested in COORDINATOR_SPECIALTIES and requested != "auto":
        return requested
    text = " ".join((str(call.get("task") or ""), str(call.get("context") or ""))).lower()
    if any(term in text for term in ("review", "audit", "regression", "diff", "correctness", "bug")):
        return "code_review"
    if any(term in text for term in ("reason", "derive", "prove", "architecture", "tradeoff", "root cause")):
        return "reasoning"
    return "general"


def delegation_limit(config: dict[str, Any], route: dict[str, Any] | None = None) -> int:
    if not config.get("orchestrator_delegation", True):
        return 0
    if is_direct_answer_route(route):
        return 0
    policy = str((route or {}).get("policy") or config.get("orchestrator_policy") or "adaptive").lower()
    return COORDINATOR_DELEGATION_LIMITS.get(policy, COORDINATOR_DELEGATION_LIMITS["adaptive"])


def capability_score(capabilities: dict[str, float], specialty: str, task: str) -> float:
    code_signal = 1.0 if re.search(r"\b(code|script|function|class|bug|test|diff|ui|css|html)\b", task.lower()) else 0.0
    weights = {
        "vision": {"vision": 0.62, "reasoning": 0.18, "general": 0.12, "code": 0.08 * code_signal},
        "code_review": {"code": 0.42, "reasoning": 0.32, "agent": 0.16, "general": 0.10},
        "reasoning": {"reasoning": 0.60, "general": 0.20, "code": 0.12 * code_signal, "research": 0.08},
        "general": {"general": 0.52, "reasoning": 0.25, "research": 0.13, "code": 0.10 * code_signal},
    }[specialty]
    total = sum(weights.values())
    return sum(capabilities[name] * weight for name, weight in weights.items()) / max(0.001, total)


def quality_demand(
    specialty: str,
    task: str,
    context: str = "",
    quality: str = "",
    risk: str = "",
) -> float:
    text = f"{task} {context}".lower()
    word_count = len(re.findall(r"\b\w+\b", text))
    base = {"vision": 0.16, "code_review": 0.62, "reasoning": 0.66, "general": 0.14}[specialty]
    quality_levels = {"routine": 0.12, "balanced": 0.45, "high": 0.76, "critical": 1.0}
    risk_levels = {"low": 0.05, "medium": 0.48, "high": 0.86, "critical": 1.0}
    declared_quality = quality_levels.get(quality.strip().lower())
    declared_risk = risk_levels.get(risk.strip().lower())
    semantic = analyze_task([{"role": "user", "content": text}])
    signals = semantic["signals"]
    precision_hits = signal_hits(
        text,
        (
            "subtle",
            "exact",
            "precise",
            "small text",
            "ocr",
            "pixel",
            "compare",
            "difference",
            "diagnose",
            "root cause",
            "prove",
            "complex",
            "architecture",
            "concurrency",
            "security",
            "regression",
            "production",
            "medical",
            "clinical",
            "legal",
            "safety",
            "critical",
        ),
    )
    stakes_hits = signal_hits(
        text,
        ("medical", "clinical", "patient", "legal", "security", "production", "safety", "critical"),
    )
    demand = base * 0.72
    demand += float(signals.get("reasoning") or 0) * 0.16
    demand += float(signals.get("risk") or 0) * 0.22
    demand += min(0.20, len(precision_hits) * 0.045) + min(0.16, len(stakes_hits) * 0.08)
    demand += min(0.09, word_count / 1100.0)
    if declared_quality is not None:
        demand = max(demand, declared_quality)
    if declared_risk is not None:
        demand = max(demand, declared_risk * 0.92)
    return round(min(1.0, demand), 3)


def select_specialist(
    provider: Any,
    config: dict[str, Any],
    call: dict[str, str],
    parent_route: dict[str, Any] | None,
) -> dict[str, Any]:
    chosen_specialty = specialty(call)
    task = str(call.get("task") or "Provide focused specialist analysis.").strip()
    context = str(call.get("context") or "")
    demand = quality_demand(
        chosen_specialty,
        task,
        context,
        str(call.get("quality") or ""),
        str(call.get("risk") or ""),
    )
    policy = str((parent_route or {}).get("policy") or config.get("orchestrator_policy") or "adaptive").lower()
    if policy not in _runtime().ORCHESTRATOR_POLICIES:
        policy = "adaptive"
    parent = str((parent_route or {}).get("executor") or config.get("model") or "")
    models = [model for model in provider.list_models() if is_chat_model(model)]
    if not models:
        raise RuntimeError("no compute models are available for specialist delegation")
    try:
        resident = {name.lower() for name in provider.running_models()}
    except Exception:
        resident = set()
    efficiency_mix_by_specialty = {
        "vision": {"efficient": 0.34, "adaptive": 0.18, "quality": 0.03},
        "code_review": {"efficient": 0.22, "adaptive": 0.08, "quality": 0.02},
        "reasoning": {"efficient": 0.24, "adaptive": 0.08, "quality": 0.02},
        "general": {"efficient": 0.34, "adaptive": 0.18, "quality": 0.03},
    }
    efficiency_mix = efficiency_mix_by_specialty[chosen_specialty][policy]
    if policy == "adaptive":
        efficiency_mix *= 1.0 - demand * 0.74
    elif policy == "efficient":
        efficiency_mix *= 1.0 - demand * 0.28
    resident_bonus = {"efficient": 0.075, "adaptive": 0.035, "quality": 0.008}[policy]
    if policy == "adaptive":
        resident_bonus *= max(0.12, 1.0 - demand * 2.1)
    ranked: list[dict[str, Any]] = []
    preference_role = {"code_review": "coding", "reasoning": "reasoning", "vision": "vision", "general": "general"}[
        chosen_specialty
    ]
    preferred_model = _runtime()._coordinator_role_preference(config, preference_role, models)
    for model in models:
        capabilities = _runtime().model_capabilities(model)
        metadata = _runtime().model_capability_metadata(model)
        if chosen_specialty == "vision" and not _runtime().model_supports_vision(provider, model.name):
            continue
        capability = capability_score(capabilities, chosen_specialty, task)
        score = capability * (1.0 - efficiency_mix) + capabilities["efficiency"] * efficiency_mix
        score -= max(0.0, 0.72 - float(metadata.get("confidence") or 0.5)) * 0.035
        is_resident = model.name.lower() in resident
        if is_resident:
            score += resident_bonus
        independent = bool(parent and model.name.lower() != parent.lower())
        if independent and chosen_specialty == "code_review":
            score += 0.055
        elif independent and chosen_specialty == "reasoning":
            score += 0.018
        if preferred_model and model.name.lower() == preferred_model.lower():
            score += 0.055
        ranked.append(
            {
                "model": model.name,
                "score": min(0.999, score),
                "capability": capability,
                "resident": is_resident,
                "independent": independent,
                "confidence": float(metadata.get("confidence") or 0.5),
            }
        )
    if not ranked:
        raise RuntimeError(f"no installed model supports the {chosen_specialty} delegation")
    ranked.sort(key=lambda item: (-item["score"], item["model"].lower()))
    selected = ranked[0]
    parent_score = next(
        (float(item["capability"]) for item in ranked if item["model"].lower() == parent.lower()),
        0.0,
    )
    return {
        "specialty": chosen_specialty,
        "specialist": selected["model"],
        "policy": policy,
        "task": task,
        "quality_demand": demand,
        "independent": bool(selected["independent"]),
        "confidence": round(
            min(0.98, 0.58 + (selected["score"] - (ranked[1]["score"] if len(ranked) > 1 else 0.0)) * 2.2), 3
        ),
        "capability_gain": round(float(selected["capability"]) - parent_score, 3),
        "preference": preferred_model,
        "candidates": [
            {
                "model": item["model"],
                "score": round(float(item["score"]), 3),
                "resident": item["resident"],
            }
            for item in ranked[:4]
        ],
    }


def admits_delegation(
    decision: dict[str, Any],
    call: dict[str, str],
    parent_route: dict[str, Any] | None,
) -> tuple[bool, str]:
    if is_direct_answer_route(parent_route):
        return False, "direct conversation does not permit specialist delegation"
    specialty = str(decision.get("specialty") or "general")
    policy = str(decision.get("policy") or "adaptive")
    gain = float(decision.get("capability_gain") or 0)
    demand = float(decision.get("quality_demand") or 0)
    independent = bool(decision.get("independent"))
    parent = str((parent_route or {}).get("executor") or "")
    specialist = str(decision.get("specialist") or "")
    if specialty == "vision" and str(call.get("path") or "").strip():
        return True, "visual input requires a vision-capable inference pass"
    if not parent or specialist.lower() != parent.lower():
        if specialty == "code_review" and independent:
            return True, "independent review has verification value"
        if gain >= 0.025:
            return True, f"specialist capability gain {gain:+.3f}"
        if independent and demand >= 0.55:
            return True, f"independent perspective justified at {demand * 100:.0f}% quality demand"
        if policy == "quality" and independent:
            return True, "quality policy permits an independent specialist pass"
    if policy == "quality" and specialty == "reasoning" and demand >= 0.72:
        return True, "quality policy permits a focused second reasoning pass"
    return False, "expected quality gain does not justify model loading and another inference pass"


def execute_delegation(
    provider: Any,
    config: dict[str, Any],
    cwd: Path,
    call: dict[str, str],
    parent_route: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    cancel_event: threading.Event | None = None,
    decision: dict[str, Any] | None = None,
) -> tuple[int, str, dict[str, Any]]:
    if str(config.get("model_mode") or "direct") != "orchestrator":
        return 2, "Coordinator delegation is available only in COORDINATOR mode.", {}
    delegations = (parent_route or {}).setdefault("delegations", [])
    if not isinstance(delegations, list):
        delegations = []
        if parent_route is not None:
            parent_route["delegations"] = delegations
    limit = delegation_limit(config, parent_route)
    if len(delegations) >= limit:
        return (
            2,
            f"Coordinator delegation budget reached ({limit} for this policy). Continue with existing evidence.",
            {},
        )

    chosen_specialty = specialty(call)
    task = truncate(str(call.get("task") or "Provide focused specialist analysis.").strip(), 2400)
    context = truncate(str(call.get("context") or "").strip(), 7000)
    raw_path = str(call.get("path") or "").strip()
    image_path: Path | None = None
    if raw_path:
        try:
            image_path = _runtime().resolve_image_path(cwd, raw_path)
        except ValueError as exc:
            return 1, str(exc), {}
        chosen_specialty = "vision"
        call["specialty"] = "vision"
        call["path"] = str(image_path)

    fingerprint = "|".join((chosen_specialty, str(image_path or ""), re.sub(r"\s+", " ", task.lower())[:500]))
    decisions = (parent_route or {}).setdefault("coordination_decisions", [])
    if not isinstance(decisions, list):
        decisions = []
        if parent_route is not None:
            parent_route["coordination_decisions"] = decisions
    prior = [*delegations, *decisions]
    if any(isinstance(item, dict) and item.get("fingerprint") == fingerprint for item in prior):
        return 2, "Coordinator blocked a duplicate specialist request. Reuse the earlier evidence.", {}

    decision_model = str((decision or {}).get("specialist") or "")
    if decision is None or (image_path is not None and not _runtime().model_supports_vision(provider, decision_model)):
        # A provided decision is authoritative; re-ranking here would double the
        # full model scan every consult. Only a missing decision or an image the
        # chosen specialist cannot see forces one fresh selection.
        try:
            decision = select_specialist(provider, config, call, parent_route)
        except Exception as exc:
            return 1, f"Coordinator could not route specialist work: {exc}", {}
    specialist = str(decision["specialist"])
    if image_path is not None and not _runtime().model_supports_vision(provider, specialist):
        return 1, f"Coordinator selected {specialist}, which cannot accept images.", {}
    admitted, rationale = admits_delegation(decision, call, parent_route)
    decision["rationale"] = rationale
    if not admitted:
        retained = str((parent_route or {}).get("executor") or config.get("model") or specialist)
        record = {
            **decision,
            "id": len(decisions) + 1,
            "candidate_specialist": specialist,
            "specialist": retained,
            "parent": retained,
            "path": str(image_path) if image_path else "",
            "fingerprint": fingerprint,
            "seconds": 0.0,
            "status": "retained",
            "output_chars": 0,
            "created_at": _runtime().now_iso(),
        }
        decisions.append(record)
        result = (
            f"specialty: {chosen_specialty}\n"
            f"specialist: {retained}\n"
            f"policy: {decision['policy']}\n"
            "elapsed: 0.0s\n"
            f"evidence:\nCoordinator retained the primary executor: {rationale}. Continue with existing evidence."
        )
        return 0, result, record
    runtime = _runtime().runtime_config_for_model(config, specialist)
    policy = str(decision["policy"])
    if policy == "quality" and chosen_specialty == "reasoning":
        supports = getattr(provider, "supports", None)
        if callable(supports):
            try:
                runtime["think"] = bool(supports(specialist, "thinking"))
            except Exception:
                pass
    token_budget = COORDINATOR_TOKEN_BUDGETS[policy]
    original_task = truncate(latest_user_task(messages), 2600)
    brief = (
        f"Original user objective:\n{original_task or '(not available)'}\n\n"
        f"Delegated specialty: {chosen_specialty}\n"
        f"Bounded specialist task:\n{task}\n\n"
        f"Visual input path:\n{str(image_path) if image_path else '(none)'}\n\n"
        f"Executor-provided context:\n{context or '(none)'}\n\n"
        "Return compact evidence for the coordinator and primary executor. Be factual, identify uncertainty, and "
        "answer only the delegated question. Do not address the user, request tools, or propose unrelated work."
    )
    specialist_messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a bounded specialist inside a local multi-model coordinator. Your output is internal "
                "evidence consumed by another model. Preserve exact technical details and never claim actions."
            ),
        },
        {
            "role": "user",
            "content": brief,
            **({"image_paths": [str(image_path)]} if image_path else {}),
        },
    ]
    started = time.monotonic()
    try:
        output = _runtime()._collect_orchestrator_response(
            provider,
            specialist,
            specialist_messages,
            runtime,
            cancel_event,
            token_budget,
        )
        interrupted = bool(cancel_event and cancel_event.is_set())
        code = 130 if interrupted else 0 if output else 1
        if interrupted:
            output = output or "Specialist delegation was interrupted."
        elif not output:
            output = "Specialist returned no evidence."
    except Exception as exc:
        code = 1
        output = f"Specialist failed: {exc}"
    elapsed = round(time.monotonic() - started, 3)
    record = {
        **decision,
        "id": len(delegations) + 1,
        "parent": str((parent_route or {}).get("executor") or config.get("model") or ""),
        "path": str(image_path) if image_path else "",
        "fingerprint": fingerprint,
        "seconds": elapsed,
        "status": "interrupted" if code == 130 else "complete" if code == 0 else "failed",
        "output_chars": len(output),
        "created_at": _runtime().now_iso(),
    }
    delegations.append(record)
    if parent_route is not None:
        timings = parent_route.setdefault("timings", {})
        timings["delegate"] = round(float(timings.get("delegate") or 0) + elapsed, 3)
    result = (
        f"specialty: {chosen_specialty}\n"
        f"specialist: {specialist}\n"
        f"policy: {policy}\n"
        f"elapsed: {elapsed:.1f}s\n"
        f"evidence:\n{truncate(_runtime().strip_tool_markup(output), 12000)}"
    )
    return code, result, record
