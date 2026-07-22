"""Coordinator oversight: route reporting, executor directives, recovery, learning, and plan/review stages."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from ..messages import latest_user_images, latest_user_task
from ..text import truncate
from .analysis import is_direct_answer_route, task_role
from .calibration import observe_estimate
from .delegation import delegation_limit

OUTPUT_ONLY_PATTERN = re.compile(
    r"\b(?:reply|respond|answer|report|return|output|print|state)\s+"
    r"(?:(?:with|in)\s+)?only\b|"
    r"\b(?:reply|respond|answer|report|return|output|print|state)\s+(?:with|in)\s+exactly\b|"
    r"\b(?:reply|respond|answer|report|return|output|print|state)\s+exactly"
    r"(?:\s+with\b|\s*:|\s+(?:the\s+)?(?:value|values|number|path|line|heading|text|word|words|format)\b)|"
    r"\b(?:only|exactly)\s+(?:the\s+)?(?:value|values|number|path|line|heading|text|word|words|format)\b|"
    r"\b(?:in\s+this|using\s+this)\s+format\s+only\b",
    re.IGNORECASE,
)


def _runtime() -> Any:
    """Resolve runtime-owned collaborators at call time so patches on the runtime module apply."""
    from dairack import runtime

    return runtime


def format_route_report(route: dict[str, Any] | None) -> str:
    if not route:
        return "No route has been selected in this conversation yet."
    mode = str(route.get("mode") or "direct")
    if mode == "direct":
        return f"DIRECT MODEL\nExecutor: {route.get('executor') or '(none)'}"
    policy = str(route.get("policy") or "adaptive").upper()
    lines = [
        f"COORDINATOR / {policy}",
        f"Task: {route.get('task_kind') or 'general'} | complexity {float(route.get('complexity') or 0) * 100:.0f}% | confidence {float(route.get('confidence') or 0) * 100:.0f}%",
        *(
            [
                f"Continuity: kept resident {continuity['executor']} over {continuity['over']} "
                f"(gap {float(continuity.get('gap') or 0):.3f} within reload margin {float(continuity.get('margin') or 0):.3f})"
            ]
            if (continuity := route.get("continuity"))
            else []
        ),
        f"Executor: {route.get('executor') or '(none)'}",
        f"Strategy: {str(route.get('strategy') or 'single').replace('-', ' + ')}",
    ]
    if route.get("planner"):
        lines.append(f"Planner: {route['planner']}")
    if route.get("reviewer"):
        lines.append(f"Reviewer: {route['reviewer']}")
    recoveries = route.get("executor_recoveries")
    if isinstance(recoveries, list):
        for recovery in recoveries:
            if not isinstance(recovery, dict):
                continue
            lines.append(
                "Executor recovery: "
                f"{recovery.get('from') or '(unknown)'} > {recovery.get('to') or '(unknown)'} | "
                f"{recovery.get('reason') or 'unusable continuation'}"
            )
    if route.get("preferred_model"):
        lines.append(f"Preference: {route.get('preference_role')} > {route.get('preferred_model')} (soft)")
    routing_control = route.get("routing_control")
    if isinstance(routing_control, dict) and routing_control.get("active"):
        preference = str(routing_control.get("preference") or "auto").replace("_", " ").upper()
        outcome = "APPLIED" if routing_control.get("honored") else "RETAINED BASELINE"
        lines.append(
            f"Turn preference: {preference} | {outcome} | confidence "
            f"{float(routing_control.get('confidence') or 0) * 100:.0f}%"
        )
        if routing_control.get("applies_to_previous") and routing_control.get("resolved_task"):
            lines.append(f"Resolved task: {truncate(str(routing_control['resolved_task']), 180)}")
    semantic = route.get("semantic_assessment")
    if isinstance(semantic, dict) and semantic.get("model"):
        if semantic.get("error"):
            lines.append(f"Semantic arbitration: {semantic['model']} | skipped: {semantic['error']}")
        else:
            lines.append(
                f"Semantic arbitration: {semantic['model']} | complexity {float(semantic.get('complexity') or 0) * 100:.0f}%"
                f" | {semantic.get('reason') or 'ambiguous route'}"
            )
            if semantic.get("trigger"):
                lines.append(f"Arbitration trigger: {semantic['trigger']}")
    delegations = route.get("delegations")
    if isinstance(delegations, list) and delegations:
        lines.append(f"Delegations: {len(delegations)}")
        for item in delegations[-3:]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "complete").upper()
            seconds = float(item.get("seconds") or 0)
            demand = float(item.get("quality_demand") or 0)
            lines.append(
                f"  {item.get('id', '?')}. {str(item.get('specialty') or 'general').replace('_', ' ')}"
                f" > {item.get('specialist') or '(none)'} | {status} | demand {demand * 100:.0f}% | {seconds:.1f}s"
            )
    decisions = route.get("coordination_decisions")
    if isinstance(decisions, list) and decisions:
        for item in decisions[-2:]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"Retained: {item.get('parent') or route.get('executor') or '(none)'} | "
                f"declined {item.get('candidate_specialist') or '(none)'} | {item.get('rationale') or 'no material gain'}"
            )
    review = route.get("review")
    if isinstance(review, dict) and review.get("verdict"):
        lines.append(f"Review: {str(review['verdict']).upper()}")
    timings = route.get("timings")
    if isinstance(timings, dict) and timings:
        rendered_timings = [
            f"{name} {float(seconds):.1f}s" for name, seconds in timings.items() if isinstance(seconds, (int, float))
        ]
        if rendered_timings:
            passes = int(route.get("passes") or 0)
            suffix = f" | {passes} execution pass{'es' if passes != 1 else ''}" if passes else ""
            lines.append("Timing: " + " | ".join(rendered_timings) + suffix)
    candidates = route.get("candidates")
    if isinstance(candidates, list) and candidates:
        rendered = []
        for item in candidates[:5]:
            if not isinstance(item, dict):
                continue
            resident = " / resident" if item.get("resident") else ""
            confidence_note = (
                f" / profile {float(item.get('confidence') or 0) * 100:.0f}%"
                if float(item.get("confidence") or 0) < 0.75
                else ""
            )
            learned = float(item.get("learned_adjustment") or 0)
            learned_note = f" / learned {learned:+.3f}" if learned else ""
            role_evidence = float(item.get("learning_role_evidence") or 0)
            kind_evidence = float(item.get("learning_kind_evidence") or 0)
            kind_weight = float(item.get("learning_kind_weight") or 0)
            if learned and kind_evidence:
                learned_note += f" (role {role_evidence:.1f}, kind {kind_evidence:.1f}, mix {kind_weight * 100:.0f}%)"
            elif learned and role_evidence:
                learned_note += f" (role {role_evidence:.1f})"
            rendered.append(
                f"{item.get('model')} {float(item.get('score') or 0):.3f}{resident}{confidence_note}{learned_note}"
            )
        if rendered:
            lines.append("Ranking: " + " | ".join(rendered))
    evidence = route.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.append("Signals: " + " | ".join(str(item) for item in evidence[:5]))
    return "\n".join(lines)


def recovery_executor(route: dict[str, Any], failed_model: str) -> str:
    """Return the next pre-ranked eligible executor, only in Coordinator mode."""
    if str(route.get("mode") or "") != "orchestrator":
        return ""
    excluded = {failed_model.strip().lower()}
    recoveries = route.get("executor_recoveries")
    if isinstance(recoveries, list):
        for recovery in recoveries:
            if not isinstance(recovery, dict):
                continue
            excluded.add(str(recovery.get("from") or "").strip().lower())
            excluded.add(str(recovery.get("to") or "").strip().lower())
    candidates = route.get("candidates")
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        model = str(candidate.get("model") or "").strip()
        if model and model.lower() not in excluded:
            return model
    return ""


def record_executor_recovery(route: dict[str, Any], failed_model: str, replacement: str, reason: str) -> None:
    recoveries = route.setdefault("executor_recoveries", [])
    if not isinstance(recoveries, list):
        recoveries = []
        route["executor_recoveries"] = recoveries
    recoveries.append(
        {
            "from": failed_model,
            "to": replacement,
            "reason": truncate(reason, 180),
            "at": _runtime().now_iso(),
        }
    )
    route["executor"] = replacement


def executor_recovery_directive(reason: str) -> str:
    return (
        "Continue the original user task from the action evidence already present. The previous executor returned "
        f"no usable continuation ({truncate(reason, 140)}). Complete the task without mentioning executor recovery, "
        "internal routing, or this directive. Request another tool only when the evidence genuinely requires it."
    )


def format_route_history(chat: dict[str, Any], limit: int = 12) -> str:
    history = _runtime().sanitize_route_history(chat.get("route_history"))
    if not history:
        return "No saved route history in this conversation yet."
    rows = ["ROUTE HISTORY"]
    selected = history[-max(1, min(40, limit)) :]
    offset = len(history) - len(selected)
    for index, route in enumerate(selected, offset + 1):
        stamp = str(route.get("created_at") or "").replace("T", " ")[:19]
        executor = str(route.get("executor") or "(none)")
        task = str(route.get("task_kind") or "general")
        strategy = str(route.get("strategy") or "single")
        prompt = re.sub(r"\s+", " ", str(route.get("prompt") or "")).strip()
        rows.append(f"{index:>2}. {stamp}  {executor}  /  {task}  /  {strategy}")
        if prompt:
            rows.append(f"    {truncate(prompt, 120)}")
    return "\n".join(rows)


def action_contract_directive(route: dict[str, Any], retry: bool = False) -> str:
    contract = route.get("action_contract")
    if not isinstance(contract, dict) or not contract.get("capability"):
        return ""
    if retry:
        return "Action correction: the previous response skipped the required tool call. Perform it now."
    capability = str(contract.get("capability") or "")
    tool_steps = max(0, int(route.get("tool_steps") or 0))
    if capability == "runtime_action":
        target = str(contract.get("target") or "").strip()
        preferred_tool = str(contract.get("preferred_tool") or "auto").strip()
        preferred_instruction = (
            f" Preferred tool: {preferred_tool}." if preferred_tool and preferred_tool != "auto" else ""
        )
        target_instruction = (
            f" Exact target: {target!r}; resolve a relative path from the stated working directory." if target else ""
        )
        if tool_steps:
            instruction = (
                "Continue from the action evidence. If complete, report the concrete result; otherwise call one "
                "supplied function tool."
            )
        else:
            instruction = "Call one supplied function tool now."
        return (
            "Coordinator action requirement: "
            + instruction
            + preferred_instruction
            + target_instruction
            + " Return no prose with a tool call. If no tool can safely progress, state one limitation or ask one "
            "precise question."
        )
    if capability != "public_web":
        return ""
    preferred = str(contract.get("preferred_tool") or "web_open")
    target = str(contract.get("target") or "")
    if tool_steps:
        instruction = (
            "Continue from the web evidence. Fetch another page only if needed; otherwise answer with the result "
            "and source URLs."
        )
    elif preferred == "web_search":
        instruction = "Call web_search now; use web_open afterward if page content is needed."
    elif target:
        instruction = (
            f"Call web_open for {target} now; use web_search if the fetch fails or independent evidence is needed."
        )
    else:
        instruction = "Call web_search now, then use web_open for the relevant page."
    return (
        "Coordinator action requirement: "
        + instruction
        + " Public web tools require approval. Do not answer from memory or claim browsing is unavailable."
    )


def routing_control_directive(route: dict[str, Any]) -> str:
    control = route.get("routing_control")
    if not isinstance(control, dict) or not control.get("active"):
        return ""
    preference = str(control.get("preference") or "auto").replace("_", " ")
    instruction = (
        f"The coordinator has already applied the user's {preference} preference to executor selection for this turn. "
        "Do not inspect, list, load, or discuss models, and do not request tools merely to satisfy that preference."
    )
    if control.get("applies_to_previous") and control.get("resolved_task"):
        instruction += (
            " The current utterance is a control instruction for the prior task. Fulfill this standalone task now: "
            + truncate(str(control["resolved_task"]), 1200)
        )
    return "Coordinator routing requirement: " + instruction


def output_constraint_directive(route: dict[str, Any]) -> str:
    prompt = re.sub(r"\s+", " ", str(route.get("prompt") or "")).strip()
    if not prompt or not OUTPUT_ONLY_PATTERN.search(prompt):
        return ""
    return (
        "Final output requirement: Follow the user's literal output shape. Return only the requested value, values, "
        "separators, or fields; add no labels, Markdown, bullets, preamble, explanation, or correction commentary. "
        "This restriction applies to the final answer, not to required tool calls."
    )


def executor_directive(route: dict[str, Any], config: dict[str, Any]) -> str:
    if str(route.get("mode") or "direct") != "orchestrator":
        return "\n\n".join(
            item for item in (action_contract_directive(route), output_constraint_directive(route)) if item
        )
    control_directive = routing_control_directive(route)
    output_directive = output_constraint_directive(route)
    if is_direct_answer_route(route):
        depth = (
            "at the depth the task requests"
            if isinstance(route.get("routing_control"), dict)
            and route["routing_control"].get("preference") in {"quality", "higher_capacity"}
            else "briefly"
        )
        directive = (
            f"Coordinator directive: This is a self-contained conversational turn. Answer directly, {depth}, "
            "and naturally. Tools and specialist delegation are unavailable. Do not mention routing, models, "
            "or this directive."
        )
        additions = [item for item in (control_directive, output_directive) if item]
        return "\n\n".join([directive, *additions])
    policy = str(route.get("policy") or config.get("orchestrator_policy") or "adaptive")
    used = len(route.get("delegations") or []) if isinstance(route.get("delegations"), list) else 0
    limit = delegation_limit(config, route)
    remaining = max(0, limit - used)
    directive = (
        f"Coordinator turn: policy {policy}; specialist budget {remaining}/{limit}. "
        "Retain task ownership across tool calls and returned evidence."
    )
    action_directive = action_contract_directive(route)
    additions = [item for item in (control_directive, action_directive, output_directive) if item]
    return "\n\n".join([directive, *additions])


def status_report(config: dict[str, Any], route: dict[str, Any] | None = None) -> str:
    mode = str(config.get("model_mode") or "direct")
    policy = str(config.get("orchestrator_policy") or "adaptive")
    lines = [
        f"Mode: {'COORDINATOR' if mode == 'orchestrator' else 'DIRECT MODEL'}",
        f"Policy: {policy}",
        f"Fallback direct model: {config.get('model') or '(none)'}",
        f"Planning: {'on' if config.get('orchestrator_planning', True) else 'off'}",
        f"Review: {'on' if config.get('orchestrator_review', True) else 'off'}",
        f"Specialist delegation: {'on' if config.get('orchestrator_delegation', True) else 'off'}",
        f"Semantic arbitration: {'on' if config.get('orchestrator_semantic_routing', True) else 'off'}",
        f"Bounded learning: {'on' if config.get('coordinator_learning', True) else 'off'}",
    ]
    preferences = config.get("coordinator_role_preferences")
    if isinstance(preferences, dict) and preferences:
        lines.append("Role preferences: " + " | ".join(f"{role} > {model}" for role, model in preferences.items()))
    if route:
        lines.extend(["", "Last decision:", format_route_report(route)])
    return "\n".join(lines)


def learning_path() -> Path:
    return _runtime().PATHS.state_dir / "coordinator-learning.json"


def observe_route_outcome(
    config: dict[str, Any],
    route: dict[str, Any] | None,
    reward: float,
    *,
    weight: float,
    source: str,
) -> str:
    if not bool(config.get("coordinator_learning", True)):
        return "Coordinator learning is off."
    if not isinstance(route, dict) or not route.get("executor"):
        return "No route is available to calibrate."
    events = route.setdefault("learning_events", [])
    if not isinstance(events, list):
        events = []
        route["learning_events"] = events
    if any(isinstance(item, dict) and item.get("source") == source for item in events):
        return f"Learning evidence already recorded for {source}."
    role = str(route.get("preference_role") or task_role(route.get("signals") or {}))
    kind = str(route.get("task_kind") or "")
    model = str(route["executor"])
    state_path = learning_path()
    calibration = observe_estimate(
        state_path,
        model,
        role,
        reward,
        weight=weight,
        source=source,
        kind=kind,
    )
    learned = calibration.value
    events.append(
        {
            "source": source,
            "reward": round(float(reward), 3),
            "weight": round(float(weight), 3),
            "adjustment": round(learned, 4),
            "evidence": round(calibration.effective_evidence, 2),
            "role_evidence": round(calibration.role_evidence, 2),
            "kind_evidence": round(calibration.kind_evidence, 2),
            "kind_weight": round(calibration.kind_weight, 3),
        }
    )
    scope = f"{role} / {kind}" if kind else role
    evidence = f"role {calibration.role_evidence:.1f}"
    if kind:
        evidence += f" / kind {calibration.kind_evidence:.1f} / mix {calibration.kind_weight * 100:.0f}%"
    return f"Learning recorded: {model} / {scope} / adjustment {learned:+.3f} / {evidence}"


def record_route_feedback(config: dict[str, Any], route: dict[str, Any] | None, value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"good", "up", "positive", "bad", "down", "negative"}:
        return "usage: /route feedback good|bad"
    reward = 1.0 if normalized in {"good", "up", "positive"} else -1.0
    return observe_route_outcome(config, route, reward, weight=3.0, source="user-feedback")


def collect_response(
    provider: Any,
    model: str,
    messages: list[dict[str, str]],
    config: dict[str, Any],
    cancel_event: threading.Event | None,
    max_tokens: int,
    response_format: str | dict[str, Any] | None = None,
) -> str:
    chunks: list[str] = []
    for chunk in provider.chat_stream(
        model,
        messages,
        think=bool(config.get("think")),
        num_ctx=int(config.get("num_ctx") or 4096),
        num_predict=max_tokens,
        keep_alive=_runtime().executor_keep_alive(config),
        cancel_event=cancel_event,
        extra_options=_runtime().ollama_options(config),
        response_format=response_format,
    ):
        if cancel_event and cancel_event.is_set():
            break
        chunks.append(chunk)
    return "".join(chunks).strip()


def plan(
    provider: Any,
    route: dict[str, Any],
    messages: list[dict[str, str]],
    cwd: Path,
    config: dict[str, Any],
    cancel_event: threading.Event | None = None,
) -> str:
    planner = str(route.get("planner") or "")
    if not planner:
        return ""
    _runtime().require_vision_support(provider, planner, messages)
    runtime = _runtime().runtime_config_for_model(config, planner)
    task = latest_user_task(messages)
    recent = []
    for message in messages[-8:]:
        if message.get("role") == "system":
            continue
        recent.append(f"{str(message.get('role')).upper()}: {truncate(str(message.get('content') or ''), 1800)}")
    grounding = _runtime().retrieved_project_context(cwd, task, config, cwd, provider=provider)
    grounding_block = (
        f"Indexed project context (may be stale; verify with actions):\n{truncate(grounding, 4200)}\n\n"
        if grounding
        else ""
    )
    prompt = (
        f"Working directory: {cwd}\n"
        f"Task type: {route.get('task_kind')}\n"
        f"User task:\n{task}\n\n"
        f"Recent relevant context:\n{truncate(chr(10).join(recent), 7000)}\n\n"
        f"{grounding_block}"
        "Produce a compact execution brief for another compute model. Identify the likely intent, constraints, "
        "verification steps, and failure risks. For code or system work, name the specific files or symbols that "
        "should be inspected before editing, using the indexed context when it is relevant. Do not answer the "
        "user, request tools, invent file contents, or exceed 220 words."
    )
    planning_messages = [
        {
            "role": "system",
            "content": "You are the planning stage of a multi-model coordinator. Be precise and economical.",
        },
        {
            "role": "user",
            "content": prompt,
            **({"image_paths": latest_user_images(messages)} if latest_user_images(messages) else {}),
        },
    ]
    return collect_response(provider, planner, planning_messages, runtime, cancel_event, 360)


def review(
    provider: Any,
    route: dict[str, Any],
    messages: list[dict[str, str]],
    answer: str,
    config: dict[str, Any],
    cancel_event: threading.Event | None = None,
) -> dict[str, str]:
    reviewer = str(route.get("reviewer") or "")
    if not reviewer:
        return {"verdict": "skipped", "feedback": ""}
    _runtime().require_vision_support(provider, reviewer, messages)
    runtime = _runtime().runtime_config_for_model(config, reviewer)
    task = latest_user_task(messages)
    recent_results = _runtime()._recent_action_evidence(messages, limit=7000)
    completion = route.get("action_completion")
    completion_text = repr(completion) if isinstance(completion, dict) else "(not assessed)"
    prompt = (
        f"Original task:\n{task}\n\n"
        f"Candidate answer:\n{truncate(answer, 18000)}\n\n"
        f"Recent tool evidence:\n{recent_results or '(none)'}\n\n"
        f"Runtime completion assessment:\n{truncate(completion_text, 1200)}\n\n"
        "Check correctness, completeness, unsupported claims, task compliance, and whether reported actions are "
        "actually supported by tool evidence. Tool evidence is runtime validation; a concise candidate does not "
        "need to reproduce commands, diffs, or test output unless the user requested those details. Treat a "
        "high-confidence complete runtime assessment as supporting context unless the supplied evidence directly "
        "contradicts it. Reply exactly with VERDICT: PASS when no material correction is needed. Otherwise reply "
        "with VERDICT: REVISE followed by FEEDBACK: and concise, actionable corrections, each grounded in the task "
        "or the tool evidence shown above. Do not request stylistic rewrites."
    )
    review_messages = [
        {"role": "system", "content": "You are the independent quality gate in a multi-model coordinator."},
        {
            "role": "user",
            "content": prompt,
            **({"image_paths": latest_user_images(messages)} if latest_user_images(messages) else {}),
        },
    ]
    raw = collect_response(provider, reviewer, review_messages, runtime, cancel_event, 320)
    upper = raw.upper()
    verdict = "revise" if "VERDICT: REVISE" in upper else "pass"
    feedback = ""
    match = re.search(r"FEEDBACK\s*:\s*(.*)", raw, re.IGNORECASE | re.DOTALL)
    if match:
        feedback = truncate(match.group(1).strip(), 2400)
    elif verdict == "revise":
        feedback = truncate(re.sub(r"^.*?VERDICT\s*:\s*REVISE", "", raw, flags=re.IGNORECASE | re.DOTALL).strip(), 2400)
    if verdict == "revise" and not feedback:
        return {
            "verdict": "pass",
            "feedback": "",
            "note": "reviewer requested a revision without usable feedback; verdict was not applied",
        }
    return {"verdict": verdict, "feedback": feedback}
