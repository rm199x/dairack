"""Budgeted, action-free evaluation harness for coordinator routing."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from dairack.config import atomic_write_json
from dairack.coordinator.tuning import CoordinatorTuning, candidate_vectors, normalized_distance

SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
SEMANTIC_EXPECTATIONS = {"skip", "required", "allowed"}
SERVICE_CLASSES = {"fast", "balanced", "quality"}
ROLE_CAPABILITY = {
    "general": "general",
    "coding": "code",
    "agent": "agent",
    "reasoning": "reasoning",
    "research": "research",
    "vision": "vision",
}
CHECK_LOSS_WEIGHTS = {
    "executor": 100.0,
    "vision-gate": 100.0,
    "risk-detection": 100.0,
    "role": 4.0,
    "service-class": 3.0,
    "semantic-gate": 2.0,
    "strategy": 1.5,
    "minimum-complexity": 1.0,
    "maximum-complexity": 1.0,
}


class LabError(ValueError):
    pass


class InferenceBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    family: str
    prompt: str
    expected_roles: tuple[str, ...]
    service_class: str
    service_capabilities: tuple[str, ...] = ()
    semantic: str = "allowed"
    strategy: str = ""
    history: tuple[dict[str, str], ...] = ()
    image: bool = False
    min_complexity: float | None = None
    max_complexity: float | None = None
    safety: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Scenario:
        identifier = str(value.get("id") or "").strip()
        family = str(value.get("family") or "").strip()
        prompt = str(value.get("prompt") or "").strip()
        raw_roles = value.get("expected_roles")
        if isinstance(raw_roles, str):
            roles = (raw_roles.strip().lower(),)
        elif isinstance(raw_roles, list):
            roles = tuple(str(role).strip().lower() for role in raw_roles if str(role).strip())
        else:
            roles = ()
        service_class = str(value.get("service_class") or "balanced").strip().lower()
        raw_service_capabilities = value.get("service_capabilities")
        if isinstance(raw_service_capabilities, list):
            service_capabilities = tuple(
                str(capability).strip().lower() for capability in raw_service_capabilities if str(capability).strip()
            )
        else:
            service_capabilities = ()
        semantic = str(value.get("semantic") or "allowed").strip().lower()
        if not identifier or not family or not prompt or not roles:
            raise LabError("each scenario requires id, family, prompt, and expected_roles")
        if any(role not in ROLE_CAPABILITY for role in roles):
            raise LabError(f"scenario {identifier} has an unknown role")
        if service_class not in SERVICE_CLASSES:
            raise LabError(f"scenario {identifier} has an unknown service class")
        if any(capability not in ROLE_CAPABILITY for capability in service_capabilities):
            raise LabError(f"scenario {identifier} has an unknown service capability")
        if semantic not in SEMANTIC_EXPECTATIONS:
            raise LabError(f"scenario {identifier} has an unknown semantic expectation")
        raw_history = value.get("history")
        history: list[dict[str, str]] = []
        if isinstance(raw_history, list):
            for message in raw_history:
                if not isinstance(message, Mapping):
                    continue
                role = str(message.get("role") or "").lower()
                content = str(message.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    history.append({"role": role, "content": content})

        def optional_score(key: str) -> float | None:
            raw = value.get(key)
            if raw is None:
                return None
            try:
                score = float(raw)
            except (TypeError, ValueError) as exc:
                raise LabError(f"scenario {identifier} has an invalid {key}") from exc
            if not 0 <= score <= 1:
                raise LabError(f"scenario {identifier} {key} must be between zero and one")
            return score

        return cls(
            id=identifier,
            family=family,
            prompt=prompt,
            expected_roles=roles,
            service_class=service_class,
            service_capabilities=service_capabilities,
            semantic=semantic,
            strategy=str(value.get("strategy") or "").strip().lower(),
            history=tuple(history),
            image=bool(value.get("image", False)),
            min_complexity=optional_score("min_complexity"),
            max_complexity=optional_score("max_complexity"),
            safety=bool(value.get("safety", False)),
        )


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    hard: bool
    detail: str


@dataclass(frozen=True, slots=True)
class CaseResult:
    scenario_id: str
    family: str
    prompt: str
    executor: str
    role: str
    strategy: str
    complexity: float
    checks: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def hard_failures(self) -> int:
        return sum(not check.passed and check.hard for check in self.checks)


@dataclass(frozen=True, slots=True)
class LabReport:
    profile: str
    cases: tuple[CaseResult, ...]
    inference_calls: int
    cache_hits: int
    inference_budget: int

    @property
    def passed(self) -> bool:
        if not self.cases:
            return False
        hard_failures = sum(case.hard_failures for case in self.cases)
        return hard_failures == 0 and sum(case.passed for case in self.cases) / len(self.cases) >= 0.90

    def to_dict(self) -> dict[str, Any]:
        total_checks = sum(len(case.checks) for case in self.cases)
        passed_checks = sum(check.passed for case in self.cases for check in case.checks)
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": self.profile,
            "passed": self.passed,
            "archetype_count": len({case.scenario_id.split(".v", 1)[0] for case in self.cases}),
            "case_count": len(self.cases),
            "case_pass_rate": _ratio(sum(case.passed for case in self.cases), len(self.cases)),
            "check_pass_rate": _ratio(passed_checks, total_checks),
            "hard_failures": sum(case.hard_failures for case in self.cases),
            "inference": {
                "calls": self.inference_calls,
                "cache_hits": self.cache_hits,
                "budget": self.inference_budget,
            },
            "families": _family_payload(self.cases),
            "cases": [
                {
                    **{key: value for key, value in asdict(case).items() if key != "checks"},
                    "passed": case.passed,
                    "checks": [asdict(check) for check in case.checks],
                }
                for case in self.cases
            ],
        }

    def render(self, failure_limit: int = 20) -> str:
        payload = self.to_dict()
        lines = [
            f"COORDINATOR LAB / {self.profile.upper()}",
            (
                f"Archetypes {payload['archetype_count']}  /  cases {payload['case_count']}"
                f"  /  case pass {payload['case_pass_rate'] * 100:.1f}%"
                f"  /  checks {payload['check_pass_rate'] * 100:.1f}%  /  hard failures {payload['hard_failures']}"
            ),
            (
                f"Inference {self.inference_calls}/{self.inference_budget}"
                f"  /  cache hits {self.cache_hits}  /  result {'PASS' if self.passed else 'NEEDS WORK'}"
            ),
            "",
            "FAMILIES",
        ]
        for family, values in sorted(payload["families"].items()):
            lines.append(
                f"  {family:<24} {values['passed']:>3}/{values['cases']:<3}"
                f"  {values['pass_rate'] * 100:>5.1f}%  /  hard {values['hard_failures']}"
            )
        failures = [case for case in self.cases if not case.passed]
        if failures:
            lines.extend(["", "FAILURES"])
            for case in failures[: max(1, failure_limit)]:
                lines.append(f"  {case.scenario_id}  /  {case.executor}  /  {case.role}  /  {case.strategy}")
                for check in case.checks:
                    if not check.passed:
                        marker = "HARD" if check.hard else "MISS"
                        lines.append(f"    {marker} {check.name}: {check.detail}")
            if len(failures) > failure_limit:
                lines.append(f"  ... {len(failures) - failure_limit} more; use --json for the full report")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class FoldResult:
    held_out_family: str
    selected_tuning: CoordinatorTuning
    training_loss: float
    held_out_loss: float
    baseline_held_out_loss: float


@dataclass(frozen=True, slots=True)
class OptimizationReport:
    candidate_count: int
    baseline: CoordinatorTuning
    selected: CoordinatorTuning
    baseline_loss: float
    selected_loss: float
    accepted: bool
    folds: tuple[FoldResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_count": self.candidate_count,
            "baseline": self.baseline.to_dict(),
            "selected": self.selected.to_dict(),
            "baseline_loss": round(self.baseline_loss, 6),
            "selected_loss": round(self.selected_loss, 6),
            "accepted": self.accepted,
            "folds": [
                {
                    "held_out_family": fold.held_out_family,
                    "selected_tuning": fold.selected_tuning.to_dict(),
                    "training_loss": round(fold.training_loss, 6),
                    "held_out_loss": round(fold.held_out_loss, 6),
                    "baseline_held_out_loss": round(fold.baseline_held_out_loss, 6),
                }
                for fold in self.folds
            ],
        }

    def render(self) -> str:
        outcome = "PROMOTABLE" if self.accepted else "BASELINE RETAINED"
        lines = [
            "COORDINATOR TUNING / GROUPED LOSO",
            (
                f"Candidates {self.candidate_count}  /  baseline loss {self.baseline_loss:.4f}"
                f"  /  selected loss {self.selected_loss:.4f}  /  {outcome}"
            ),
            "Selected: "
            + "  /  ".join(
                f"{name}={value:.4f}" for name, value in self.selected.to_dict().items() if name != "schema_version"
            ),
            "",
            "HELD-OUT FAMILIES",
        ]
        for fold in self.folds:
            lines.append(
                f"  {fold.held_out_family:<24} loss {fold.held_out_loss:.4f}"
                f"  /  baseline {fold.baseline_held_out_loss:.4f}"
            )
        return "\n".join(lines)


class BudgetedCachedProvider:
    """Provider facade that prevents unbounded lab inference and caches classifier output."""

    def __init__(
        self,
        provider: Any,
        cache_path: Path,
        inference_budget: int,
        *,
        use_cache: bool = True,
        resident_models: Iterable[str] | None = None,
    ) -> None:
        self.provider = provider
        self.cache_path = cache_path
        self.inference_budget = max(0, inference_budget)
        self.inference_calls = 0
        self.cache_hits = 0
        self.use_cache = use_cache
        self._models = list(provider.list_models())
        if resident_models is None:
            try:
                self._resident = list(provider.running_models())
            except Exception:
                self._resident = []
        else:
            self._resident = [str(model) for model in resident_models if str(model).strip()]
        self._digests = {str(model.name): str(getattr(model, "digest", "") or "") for model in self._models}
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": CACHE_SCHEMA_VERSION, "responses": {}}
        responses = raw.get("responses") if isinstance(raw, dict) else None
        if not isinstance(responses, dict):
            return {"schema_version": CACHE_SCHEMA_VERSION, "responses": {}}
        return {"schema_version": CACHE_SCHEMA_VERSION, "responses": dict(list(responses.items())[-1000:])}

    def _save_cache(self) -> None:
        responses = self._cache.get("responses")
        if isinstance(responses, dict) and len(responses) > 1000:
            self._cache["responses"] = dict(list(responses.items())[-1000:])
        atomic_write_json(self.cache_path, self._cache)

    def list_models(self) -> list[Any]:
        return list(self._models)

    def running_models(self) -> list[str]:
        return list(self._resident)

    def supports(self, model: str, capability: str) -> bool:
        selected = next((item for item in self._models if str(item.name).lower() == model.lower()), None)
        return bool(selected and capability.lower() in {str(value).lower() for value in selected.capabilities})

    def chat_stream(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Iterable[str]:
        cache_input = {
            "model": model,
            "digest": self._digests.get(model, ""),
            "messages": messages,
            "think": kwargs.get("think"),
            "num_ctx": kwargs.get("num_ctx"),
            "num_predict": kwargs.get("num_predict"),
            "extra_options": kwargs.get("extra_options"),
            "response_format": kwargs.get("response_format"),
        }
        serialized = json.dumps(cache_input, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        key = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        responses = self._cache["responses"]
        cached = responses.get(key) if self.use_cache else None
        if isinstance(cached, dict) and isinstance(cached.get("response"), str):
            self.cache_hits += 1
            yield cached["response"]
            return
        if self.inference_calls >= self.inference_budget:
            raise InferenceBudgetExceeded(f"semantic inference budget exhausted ({self.inference_budget})")
        self.inference_calls += 1
        chunks = list(self.provider.chat_stream(model, messages, **kwargs))
        response = "".join(chunks)
        if self.use_cache:
            responses[key] = {"model": model, "response": response}
            self._save_cache()
        yield response


def load_scenarios(path: Path | None = None) -> list[Scenario]:
    try:
        if path is None:
            raw_text = (Path(__file__).parent / "data" / "coordinator-scenarios.json").read_text(encoding="utf-8")
        else:
            raw_text = path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError(f"could not load coordinator scenarios: {exc}") from exc
    values = raw.get("scenarios") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise LabError("coordinator scenario data must contain a scenarios array")
    scenarios = [Scenario.from_mapping(value) for value in values if isinstance(value, Mapping)]
    identifiers = [scenario.id for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise LabError("coordinator scenario ids must be unique")
    if not scenarios:
        raise LabError("coordinator scenario data is empty")
    return scenarios


def expand_scenarios(scenarios: Iterable[Scenario], variants: int, seed: int) -> list[Scenario]:
    expanded: list[Scenario] = []
    for scenario in scenarios:
        expanded.append(scenario)
        seen_prompts = {scenario.prompt}
        created = 0
        attempt = 0
        target = max(0, variants)
        while created < target and attempt < max(32, target * 10):
            prompt, relaxed_semantic = _prompt_variant(scenario.prompt, scenario.id, attempt, seed)
            attempt += 1
            if prompt in seen_prompts:
                continue
            seen_prompts.add(prompt)
            semantic = scenario.semantic
            if semantic == "skip" and relaxed_semantic:
                semantic = "allowed"
            created += 1
            expanded.append(
                Scenario(
                    **{
                        **asdict(scenario),
                        "id": f"{scenario.id}.v{created}",
                        "prompt": prompt,
                        "semantic": semantic,
                    }
                )
            )
    return expanded


def evaluate(
    provider: Any,
    config: dict[str, Any],
    scenarios: Iterable[Scenario],
    cwd: Path,
    *,
    profile: str,
    semantic_checks: bool,
) -> LabReport:
    from dairack import runtime

    models = list(provider.list_models())
    capabilities = {str(model.name): runtime.model_capabilities(model) for model in models}
    results: list[CaseResult] = []
    for scenario in scenarios:
        messages: list[dict[str, Any]] = [dict(message) for message in scenario.history]
        user_message: dict[str, Any] = {"role": "user", "content": scenario.prompt}
        if scenario.image:
            user_message["image_paths"] = [f"/coordinator-lab/{scenario.id}.png"]
        messages.append(user_message)
        route = runtime.select_orchestrator_route(provider, config, messages, cwd)
        checks = _evaluate_route(route, scenario, capabilities, semantic_checks, profile == "quick")
        results.append(
            CaseResult(
                scenario_id=scenario.id,
                family=scenario.family,
                prompt=scenario.prompt,
                executor=str(route.get("executor") or ""),
                role=str(route.get("preference_role") or "general"),
                strategy=str(route.get("strategy") or "single"),
                complexity=float(route.get("complexity") or 0),
                checks=tuple(checks),
            )
        )
    return LabReport(
        profile=profile,
        cases=tuple(results),
        inference_calls=int(getattr(provider, "inference_calls", 0)),
        cache_hits=int(getattr(provider, "cache_hits", 0)),
        inference_budget=int(getattr(provider, "inference_budget", 0)),
    )


def optimize_tuning(
    provider: Any,
    config: dict[str, Any],
    scenarios: Iterable[Scenario],
    cwd: Path,
    *,
    candidate_count: int,
    seed: int,
) -> OptimizationReport:
    scenario_list = list(scenarios)
    if len({scenario.family for scenario in scenario_list}) < 2:
        raise LabError("grouped LOSO requires at least two scenario families")
    candidates = candidate_vectors(candidate_count, seed)
    evaluations: list[tuple[CoordinatorTuning, LabReport, dict[str, float], float]] = []
    for candidate in candidates:
        candidate_config = dict(config)
        candidate_config["_coordinator_tuning"] = candidate.to_dict()
        report = evaluate(
            provider,
            candidate_config,
            scenario_list,
            cwd,
            profile="optimization",
            semantic_checks=True,
        )
        family_losses = _family_losses(report.cases)
        total_loss = _mean_case_loss(report.cases)
        evaluations.append((candidate, report, family_losses, total_loss))
    eligible = [item for item in evaluations if not any(case.hard_failures for case in item[1].cases)]
    if not eligible:
        raise LabError("every tuning candidate violated a hard invariant")
    baseline_item = evaluations[0]
    baseline = baseline_item[0]
    baseline_loss = baseline_item[3]

    def objective(item: tuple[CoordinatorTuning, LabReport, dict[str, float], float]) -> tuple[float, float]:
        return item[3] + normalized_distance(item[0], baseline) * 0.02, normalized_distance(item[0], baseline)

    selected_item = min(eligible, key=objective)
    families = sorted({scenario.family for scenario in scenario_list})
    folds: list[FoldResult] = []
    for held_out in families:
        training_families = tuple(family for family in families if family != held_out)

        def fold_objective(
            item: tuple[CoordinatorTuning, LabReport, dict[str, float], float],
            family_names: tuple[str, ...] = training_families,
        ) -> tuple[float, float]:
            losses = [item[2].get(family, 0.0) for family in family_names]
            training_loss = sum(losses) / len(losses)
            distance = normalized_distance(item[0], baseline)
            return training_loss + distance * 0.02, distance

        fold_item = min(eligible, key=fold_objective)
        training_loss = sum(fold_item[2].get(family, 0.0) for family in training_families) / len(training_families)
        folds.append(
            FoldResult(
                held_out_family=held_out,
                selected_tuning=fold_item[0],
                training_loss=training_loss,
                held_out_loss=fold_item[2].get(held_out, 0.0),
                baseline_held_out_loss=baseline_item[2].get(held_out, 0.0),
            )
        )
    selected_family_losses = selected_item[2]
    baseline_family_losses = baseline_item[2]
    no_family_regression = all(
        selected_family_losses.get(family, 0.0) <= baseline_family_losses.get(family, 0.0) + 1e-9 for family in families
    )
    accepted = selected_item[3] < baseline_loss - 1e-9 and no_family_regression
    if not accepted:
        selected_item = baseline_item
    return OptimizationReport(
        candidate_count=len(candidates),
        baseline=baseline,
        selected=selected_item[0],
        baseline_loss=baseline_loss,
        selected_loss=selected_item[3],
        accepted=accepted,
        folds=tuple(folds),
    )


def _evaluate_route(
    route: Mapping[str, Any],
    scenario: Scenario,
    capabilities: Mapping[str, Mapping[str, float]],
    semantic_checks: bool,
    gate_checks: bool,
) -> list[CheckResult]:
    executor = str(route.get("executor") or "")
    role = str(route.get("preference_role") or "general")
    strategy = str(route.get("strategy") or "single")
    complexity = float(route.get("complexity") or 0)
    selected = capabilities.get(executor, {})
    evaluate_semantic_outcome = semantic_checks or scenario.semantic != "required"
    evaluate_role = evaluate_semantic_outcome or scenario.image
    checks = [CheckResult("executor", bool(executor and selected), True, executor or "no executor selected")]
    if evaluate_role:
        checks.append(
            CheckResult(
                "role",
                role in scenario.expected_roles,
                False,
                f"expected {' or '.join(scenario.expected_roles)}, received {role}",
            )
        )
    if scenario.image:
        vision = float(selected.get("vision") or 0)
        checks.append(CheckResult("vision-gate", vision >= 0.50, True, f"selected vision capability {vision:.3f}"))
    if executor and selected and evaluate_semantic_outcome:
        target, selected_utility, target_utility = _service_target(scenario, capabilities, executor)
        tolerance = {"fast": 0.055, "balanced": 0.045, "quality": 0.030}[scenario.service_class]
        checks.append(
            CheckResult(
                "service-class",
                selected_utility + tolerance >= target_utility,
                False,
                (
                    f"{scenario.service_class} utility {selected_utility:.3f}; "
                    f"target {target} {target_utility:.3f}; tolerance {tolerance:.3f}"
                ),
            )
        )
    if (semantic_checks or gate_checks) and scenario.semantic != "allowed":
        assessment = route.get("semantic_assessment")
        triggered = (
            isinstance(assessment, Mapping) and bool(assessment.get("model")) and bool(assessment.get("trigger"))
        )
        assessed = triggered and not assessment.get("error")
        expected = scenario.semantic == "required"
        observed = triggered if gate_checks else assessed
        checks.append(
            CheckResult(
                "semantic-gate",
                observed == expected,
                False,
                f"expected {scenario.semantic}, received {'trigger' if observed else 'fast path'}",
            )
        )
    if scenario.strategy and evaluate_semantic_outcome:
        checks.append(
            CheckResult(
                "strategy",
                strategy == scenario.strategy,
                False,
                f"expected {scenario.strategy}, received {strategy}",
            )
        )
    if scenario.min_complexity is not None and evaluate_semantic_outcome:
        checks.append(
            CheckResult(
                "minimum-complexity",
                complexity >= scenario.min_complexity,
                False,
                f"expected >= {scenario.min_complexity:.2f}, received {complexity:.2f}",
            )
        )
    if scenario.max_complexity is not None and evaluate_semantic_outcome:
        checks.append(
            CheckResult(
                "maximum-complexity",
                complexity <= scenario.max_complexity,
                False,
                f"expected <= {scenario.max_complexity:.2f}, received {complexity:.2f}",
            )
        )
    if scenario.safety:
        risk = float((route.get("signals") or {}).get("risk") or 0)
        checks.append(CheckResult("risk-detection", risk >= 0.20, True, f"risk signal {risk:.3f}"))
    return checks


def _service_target(
    scenario: Scenario,
    capabilities: Mapping[str, Mapping[str, float]],
    selected_model: str,
) -> tuple[str, float, float]:
    service_roles = scenario.service_capabilities or (scenario.expected_roles[0],)
    capability_names = tuple(ROLE_CAPABILITY[role] for role in service_roles)

    def utility(values: Mapping[str, float]) -> float:
        role_score = sum(float(values.get(name) or 0) for name in capability_names) / len(capability_names)
        general = float(values.get("general") or 0)
        efficiency = float(values.get("efficiency") or 0)
        if scenario.service_class == "fast":
            return efficiency * 0.64 + role_score * 0.24 + general * 0.12
        if scenario.service_class == "quality":
            return role_score * 0.72 + general * 0.22 + efficiency * 0.06
        return role_score * 0.52 + general * 0.20 + efficiency * 0.28

    eligible = {
        name: values
        for name, values in capabilities.items()
        if not scenario.image or float(values.get("vision") or 0) >= 0.50
    }
    ranked = sorted(((utility(values), name) for name, values in eligible.items()), reverse=True)
    if not ranked:
        return "(none)", 0.0, 1.0
    target_utility, target = ranked[0]
    return target, utility(capabilities.get(selected_model, {})), target_utility


def _prompt_variant(prompt: str, identifier: str, index: int, seed: int) -> tuple[str, bool]:
    local = random.Random(f"{seed}:{identifier}:{index}")
    number = index + 1
    case_styles = (
        lambda value: value,
        str.lower,
        str.upper,
        str.title,
    )
    punctuation = ("", ".", "?", "!")[(number // 4) % 4]
    variant = case_styles[number % len(case_styles)](prompt).rstrip(".?!") + punctuation
    spacing = " " * (1 + (number // 16) % 4)
    variant = re.sub(r"\s+", spacing, variant)
    relaxed_semantic = False
    if number % 5 == 0:
        words = variant.split()
        candidates = [position for position, word in enumerate(words) if len(re.sub(r"\W", "", word)) >= 4]
        if candidates:
            position = local.choice(candidates)
            word = words[position]
            swap = local.randrange(1, max(2, len(word) - 1))
            swap = min(swap, len(word) - 2)
            words[position] = word[:swap] + word[swap + 1] + word[swap] + word[swap + 2 :]
            variant = spacing.join(words)
            relaxed_semantic = True
    if number % 7 == 0:
        prefixes = ("Please, ", "Could you please ", "When ready, ", "Briefly, ")
        variant = prefixes[(number // 7) % len(prefixes)] + variant[0].lower() + variant[1:]
        relaxed_semantic = True
    if number % 11 == 0:
        suffixes = (" please", " if possible", " when ready", " for me")
        variant = variant.rstrip(".?!") + suffixes[(number // 11) % len(suffixes)]
        relaxed_semantic = True
    return variant, relaxed_semantic


def _case_loss(case: CaseResult) -> float:
    return sum(CHECK_LOSS_WEIGHTS.get(check.name, 1.0) for check in case.checks if not check.passed)


def _mean_case_loss(cases: Iterable[CaseResult]) -> float:
    values = [_case_loss(case) for case in cases]
    return sum(values) / len(values) if values else float("inf")


def _family_losses(cases: Iterable[CaseResult]) -> dict[str, float]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for case in cases:
        grouped[case.family].append(case)
    return {family: _mean_case_loss(values) for family, values in grouped.items()}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _family_payload(cases: Iterable[CaseResult]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for case in cases:
        grouped[case.family].append(case)
    return {
        family: {
            "cases": len(values),
            "passed": sum(case.passed for case in values),
            "pass_rate": _ratio(sum(case.passed for case in values), len(values)),
            "hard_failures": sum(case.hard_failures for case in values),
            "failed_checks": dict(Counter(check.name for case in values for check in case.checks if not check.passed)),
        }
        for family, values in grouped.items()
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the action-free Dairack coordinator evaluation lab.")
    parser.add_argument("--profile", choices=("quick", "semantic", "full"), default="quick")
    parser.add_argument("--dataset", type=Path, help="optional JSON scenario dataset")
    parser.add_argument("--variants", type=int, help="deterministic unique prompt variants per archetype")
    parser.add_argument("--budget", type=int, help="maximum uncached semantic classifier calls")
    parser.add_argument("--max-cases", type=int, default=0, help="cap evaluated cases after expansion")
    parser.add_argument("--seed", type=int, default=1985, help="deterministic mutation/search seed")
    parser.add_argument("--clear-cache", action="store_true", help="discard cached semantic assessments first")
    parser.add_argument(
        "--resident-model",
        action="append",
        default=[],
        help="simulate an already-loaded model (repeatable; default is a reproducible cold pool)",
    )
    parser.add_argument("--optimize", action="store_true", help="search the bounded tuning vector with grouped LOSO")
    parser.add_argument("--candidates", type=int, default=96, help="bounded tuning vectors to evaluate (1-512)")
    parser.add_argument("--json", action="store_true", help="emit the complete machine-readable report")
    return parser


def main(argv: list[str] | None = None) -> int:
    from dairack import runtime
    from dairack.config import ConfigError, load_config
    from dairack.paths import PATHS
    from dairack.providers.ollama import OllamaError

    options = _parser().parse_args(argv)
    defaults = {
        "quick": {"variants": 30, "budget": 0},
        "semantic": {"variants": 0, "budget": 32},
        "full": {"variants": 2, "budget": 80},
    }[options.profile]
    variants = defaults["variants"] if options.variants is None else options.variants
    budget = defaults["budget"] if options.budget is None else options.budget
    if not 0 <= variants <= 100:
        print("--variants must be between 0 and 100", file=sys.stderr)
        return 2
    if not 0 <= budget <= 1000:
        print("--budget must be between 0 and 1000", file=sys.stderr)
        return 2
    if options.max_cases < 0:
        print("--max-cases cannot be negative", file=sys.stderr)
        return 2
    if options.optimize and options.profile == "quick":
        print("--optimize requires the semantic or full profile", file=sys.stderr)
        return 2
    if not 1 <= options.candidates <= 512:
        print("--candidates must be between 1 and 512", file=sys.stderr)
        return 2
    cache_path = PATHS.cache_dir / "coordinator-lab-responses.json"
    if options.clear_cache:
        cache_path.unlink(missing_ok=True)
    try:
        config = load_config(PATHS)
        scenarios = expand_scenarios(load_scenarios(options.dataset), variants, options.seed)
        if options.max_cases:
            scenarios = scenarios[: options.max_cases]
        config.update(
            {
                "model_mode": "orchestrator",
                "orchestrator_policy": "adaptive",
                "orchestrator_semantic_routing": True,
                "coordinator_learning": False,
                "coordinator_role_preferences": {},
            }
        )
        provider = BudgetedCachedProvider(
            runtime.provider_from_config(config),
            cache_path,
            budget,
            use_cache=options.profile != "quick",
            resident_models=options.resident_model,
        )
        report = evaluate(
            provider,
            config,
            scenarios,
            Path.cwd(),
            profile=options.profile,
            semantic_checks=options.profile != "quick",
        )
        optimization = (
            optimize_tuning(
                provider,
                config,
                scenarios,
                Path.cwd(),
                candidate_count=options.candidates,
                seed=options.seed,
            )
            if options.optimize
            else None
        )
    except (ConfigError, LabError, OllamaError) as exc:
        print(f"coordinator lab failed: {exc}", file=sys.stderr)
        return 1
    if options.json:
        payload: dict[str, Any] = {"evaluation": report.to_dict()}
        if optimization:
            payload["optimization"] = optimization.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(report.render())
        if optimization:
            print(f"\n{optimization.render()}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
