from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack.coordinator.tuning import DEFAULT_TUNING, TUNING_BOUNDS, candidate_vectors, from_mapping
from dairack.models import ModelDescriptor
from tools import coordinator_lab as lab


class LabProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.model = ModelDescriptor(
            name="small:latest",
            size=4_000_000_000,
            parameter_size="7B",
            capabilities=("completion", "tools"),
            digest="digest-a",
        )

    def list_models(self) -> list[ModelDescriptor]:
        return [self.model]

    def running_models(self) -> list[str]:
        return []

    def chat_stream(self, _model: str, _messages: list[dict[str, object]], **_kwargs: object):
        self.calls += 1
        yield '{"intent":"general","confidence":1}'


class CoordinatorLabTests(unittest.TestCase):
    def test_builtin_dataset_expands_to_unique_cases(self) -> None:
        scenarios = lab.load_scenarios()
        expanded = lab.expand_scenarios(scenarios, 30, 1985)

        self.assertEqual(len(scenarios), 36)
        self.assertEqual(len(expanded), 1116)
        self.assertEqual(len({(item.id.split(".v", 1)[0], item.prompt) for item in expanded}), 1116)
        self.assertEqual(len({item.id for item in expanded}), 1116)

    def test_provider_cache_and_budget_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            source = LabProvider()
            provider = lab.BudgetedCachedProvider(source, cache, 1)
            messages = [{"role": "user", "content": "classify"}]

            first = list(provider.chat_stream("small:latest", messages, think=False))
            second = list(provider.chat_stream("small:latest", messages, think=False))

            self.assertEqual(first, second)
            self.assertEqual(source.calls, 1)
            self.assertEqual(provider.inference_calls, 1)
            self.assertEqual(provider.cache_hits, 1)

            warm = lab.BudgetedCachedProvider(source, cache, 1, resident_models=("small:latest",))
            self.assertEqual(warm.running_models(), ["small:latest"])

            gate_only = lab.BudgetedCachedProvider(source, cache, 0, use_cache=False)
            with self.assertRaises(lab.InferenceBudgetExceeded):
                list(gate_only.chat_stream("small:latest", messages, think=False))
            self.assertEqual(gate_only.cache_hits, 0)

    def test_tuning_vectors_are_bounded_and_invalid_input_is_neutral(self) -> None:
        vectors = candidate_vectors(64, 1985)

        self.assertEqual(vectors[0], DEFAULT_TUNING)
        self.assertEqual(len(vectors), 64)
        for vector in vectors:
            for field, (minimum, maximum) in TUNING_BOUNDS.items():
                self.assertGreaterEqual(getattr(vector, field), minimum)
                self.assertLessEqual(getattr(vector, field), maximum)
        self.assertEqual(from_mapping({"residency_scale": 99}), DEFAULT_TUNING)
        self.assertEqual(from_mapping({"semantic_evidence_scale": "bad"}), DEFAULT_TUNING)
        partial = from_mapping({"residency_scale": 0.7, "semantic_evidence_scale": 99})
        self.assertEqual(partial.residency_scale, 0.7)
        self.assertEqual(partial.semantic_evidence_scale, DEFAULT_TUNING.semantic_evidence_scale)

    def test_loso_retains_a_perfect_baseline(self) -> None:
        scenarios = [
            lab.Scenario("one", "family-a", "Hello", ("general",), "fast"),
            lab.Scenario("two", "family-b", "Thanks", ("general",), "fast"),
        ]

        def perfect_report(
            _provider: object,
            _config: dict[str, object],
            values: object,
            _cwd: Path,
            **_kwargs: object,
        ) -> lab.LabReport:
            cases = tuple(
                lab.CaseResult(
                    scenario_id=scenario.id,
                    family=scenario.family,
                    prompt=scenario.prompt,
                    executor="small:latest",
                    role="general",
                    strategy="single",
                    complexity=0.1,
                    checks=(lab.CheckResult("role", True, False, "ok"),),
                )
                for scenario in values  # type: ignore[union-attr]
            )
            return lab.LabReport("optimization", cases, 0, 0, 0)

        with patch.object(lab, "evaluate", side_effect=perfect_report):
            report = lab.optimize_tuning(object(), {}, scenarios, Path("/tmp"), candidate_count=24, seed=1985)

        self.assertFalse(report.accepted)
        self.assertEqual(report.selected, DEFAULT_TUNING)
        self.assertEqual(report.baseline_loss, 0.0)
        self.assertEqual(len(report.folds), 2)


if __name__ == "__main__":
    unittest.main()
