"""Coordinator semantic requirement assessment with a bounded per-process cache."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from typing import Any

from ..text import truncate
from .control import RoutingControl
from .tuning import DEFAULT_TUNING

_SEMANTIC_ASSESSMENT_CACHE_LIMIT = 96
_semantic_assessment_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_semantic_assessment_cache_lock = threading.Lock()


def _runtime() -> Any:
    """Resolve runtime-owned collaborators at call time so patches on the runtime module apply."""
    from dairack import runtime

    return runtime


def reset_assessment_cache() -> None:
    """Clear the per-process semantic assessment cache."""
    with _semantic_assessment_cache_lock:
        _semantic_assessment_cache.clear()


def _assessment_cache_key(
    model: str,
    config: dict[str, Any],
    task: str,
    context: str,
    observations: str,
    tuning: Any,
) -> str:
    payload = "\x1f".join(
        (
            model,
            str(config.get("num_ctx") or ""),
            str(float(getattr(tuning, "intent_floor_strength", 0) or 0)),
            task,
            context,
            observations,
        )
    )
    return hashlib.sha1(payload.encode("utf-8", "surrogatepass")).hexdigest()


def assessment(
    provider: Any,
    config: dict[str, Any],
    model: str,
    task: str,
    context: str = "",
    observations: str = "",
    cancel_event: threading.Event | None = None,
    tuning: Any = DEFAULT_TUNING,
) -> dict[str, Any]:
    if not model or not task.strip():
        return {}
    cache_key = _assessment_cache_key(model, config, task, context, observations, tuning)
    with _semantic_assessment_cache_lock:
        cached = _semantic_assessment_cache.get(cache_key)
        if cached is not None:
            _semantic_assessment_cache.move_to_end(cache_key)
            return deepcopy(cached)
    runtime = _runtime().runtime_config_for_model(config, model)
    runtime["think"] = False
    options = runtime.get("model_options")
    runtime["model_options"] = dict(options) if isinstance(options, dict) else {}
    runtime["model_options"]["temperature"] = 0
    prompt = (
        "Assess semantic requirements for a model-routing control plane. Do not solve the task and do not choose "
        "a model. Return one JSON object only. Classify intent as conversation, general, research, reasoning, "
        "coding, system_action, visual, or mixed. Return numeric values from 0.0 to 1.0 for code, agent, reasoning, "
        "general, research, vision, risk, complexity, and confidence; booleans needs_plan, needs_review, and "
        "requires_action; and a "
        "reason under 120 characters. Code means software source, debugging, or implementation, never music, writing, "
        "or generic technical language. Agent means the task requires files, commands, tools, or another external "
        "action; imperative grammar alone is not agentic. requires_action is true only when fulfilling this turn "
        "requires the runtime to inspect or change external state now. It is false for explanations, instructions, "
        "hypothetical examples, and questions about what the runtime could do. Research means current external facts "
        "or sources are needed. "
        "Reasoning means conceptual analysis, comparison, derivation, or judgment. General includes conversation, "
        "creative work, naming, prose, and ordinary explanations. Planning and review require genuine multi-stage or "
        "high-stakes work, not merely a request to improve an answer. Also classify the user's per-turn compute "
        "preference as auto, quality, "
        "higher_capacity, or efficiency. Quality means a more careful or deeper answer; higher_capacity means an explicit "
        "request for a stronger or larger executor; efficiency means a faster or lighter executor. Use auto when the user "
        "is merely asking about or discussing models rather than directing execution. Content adjectives describe the "
        "requested output, not compute: heavier music, deeper color, faster motion, or stronger prose remain auto. A "
        "preference applies only when the user directs the assistant, answer, model, executor, or reasoning effort. "
        "Control_target describes what the preference wording modifies, not what kind of task will be answered. Use "
        "compute when it modifies model selection or answer effort, content for output styling, discussion for questions "
        "about compute, and none when unrelated. A non-compute target must use compute_preference auto. Return "
        "preference_strength and "
        "control_confidence from 0.0 to 1.0. Set applies_to_previous only when the current utterance is primarily a control "
        "instruction for an earlier task, and then return that underlying task as a concise standalone resolved_task. "
        "Otherwise applies_to_previous is false and resolved_task is empty. A compute instruction is not a system action; "
        "score the underlying task's capabilities and tool needs. Base values on the actual intent, precision, stakes, and number of "
        "dependent steps. When the current request confirms, compares, or refers back to prior work, resolve that reference "
        "first and score the requirements of the resolved task, not the short surface utterance. The intent capability score "
        "must be at least 0.65. A judgment between alternatives is reasoning rather than general conversation. Vision must be 0 "
        "unless Observed inputs explicitly reports one or more attached images. Do not infer risk, research, or "
        "specialized work merely because the request is vague.\n\n"
        f"Current request:\n{truncate(task, 5000)}\n\n"
        f"Recent context:\n{context or '(none)'}\n\n"
        f"Observed inputs:\n{observations or '(none)'}"
    )
    raw = _runtime()._collect_orchestrator_response(
        provider,
        model,
        [
            {
                "role": "system",
                "content": "You are a semantic requirements classifier inside a coordinator. Output strict JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        runtime,
        cancel_event,
        240,
        _runtime().COORDINATOR_SEMANTIC_SCHEMA,
    )
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError, RecursionError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    required = set(_runtime().COORDINATOR_SEMANTIC_SCHEMA["required"])
    if not required.issubset(parsed):
        return {}
    intents = {"conversation", "general", "research", "reasoning", "coding", "system_action", "visual", "mixed"}
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent not in intents:
        return {}
    assessment: dict[str, Any] = {"model": model, "intent": intent}
    for key in (
        "code",
        "agent",
        "reasoning",
        "general",
        "research",
        "vision",
        "risk",
        "complexity",
        "confidence",
    ):
        try:
            if isinstance(parsed[key], bool):
                return {}
            value = float(parsed[key])
        except (TypeError, ValueError):
            return {}
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return {}
        assessment[key] = round(value, 3)
    for key in ("needs_plan", "needs_review", "requires_action"):
        value = parsed.get(key)
        if not isinstance(value, bool):
            return {}
        assessment[key] = value
    control = RoutingControl.from_semantic_payload(parsed, has_context=bool(context.strip()))
    if control.status == "invalid semantic control":
        return {}
    assessment["routing_control"] = control.to_dict()
    intent_capability = {
        "coding": "code",
        "reasoning": "reasoning",
        "research": "research",
        "system_action": "agent",
    }.get(intent)
    if intent_capability:
        intent_floor = 0.50 + assessment["confidence"] * float(tuning.intent_floor_strength)
        assessment[intent_capability] = round(max(assessment[intent_capability], intent_floor), 3)
    assessment["reason"] = truncate(str(parsed.get("reason") or "semantic ambiguity"), 160)
    with _semantic_assessment_cache_lock:
        _semantic_assessment_cache[cache_key] = deepcopy(assessment)
        while len(_semantic_assessment_cache) > _SEMANTIC_ASSESSMENT_CACHE_LIMIT:
            _semantic_assessment_cache.popitem(last=False)
    return assessment
