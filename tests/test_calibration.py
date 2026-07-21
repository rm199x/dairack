from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from dairack.coordinator.calibration import (
    MAX_ADJUSTMENT,
    MAX_EVIDENCE,
    MAX_TOTAL_ADJUSTMENT,
    adjustment,
    estimate,
    load_state,
    observe,
    report,
    reset,
)


class CoordinatorCalibrationTests(unittest.TestCase):
    def test_learning_requires_evidence_and_remains_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            observe(path, "model:a", "reasoning", 1, source="review")
            observe(path, "model:a", "reasoning", 1, source="review")
            learned, evidence = adjustment(load_state(path), "model:a", "reasoning")
            self.assertEqual(learned, 0.0)
            self.assertEqual(evidence, 2.0)

            observe(path, "model:a", "reasoning", 1, source="feedback")
            learned, evidence = adjustment(load_state(path), "model:a", "reasoning")
            self.assertGreater(learned, 0.0)
            self.assertEqual(evidence, 3.0)

            for _ in range(100):
                observe(path, "model:a", "reasoning", 1, weight=4, source="feedback")
            learned, capped_evidence = adjustment(load_state(path), "model:a", "reasoning")
            self.assertLessEqual(learned, MAX_ADJUSTMENT)
            self.assertLessEqual(capped_evidence, MAX_EVIDENCE)

    def test_kind_evidence_sharpens_the_matching_kind_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            for _ in range(2):
                observe(path, "model:a", "coding", 1, weight=3, source="feedback")
            for _ in range(2):
                observe(path, "model:a", "coding", -1, weight=3, source="feedback", kind="coding agent")
            state = load_state(path)

            coarse, coarse_evidence = adjustment(state, "model:a", "coding")
            matched, matched_evidence = adjustment(state, "model:a", "coding", kind="coding agent")
            unseen, _ = adjustment(state, "model:a", "coding", kind="deep reasoning")

            # Balanced coarse evidence cancels out; the kind that actually failed stays negative.
            self.assertEqual(coarse, 0.0)
            self.assertLess(matched, 0.0)
            self.assertEqual(unseen, coarse)
            self.assertEqual(coarse_evidence, 12.0)
            self.assertEqual(matched_evidence, 6.0)
            self.assertGreaterEqual(matched, -MAX_TOTAL_ADJUSTMENT)

    def test_kind_estimate_interpolates_instead_of_adding_duplicate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            observe(path, "model:a", "agent", 1, weight=3, source="feedback", kind="coding agent")
            state = load_state(path)

            coarse, _ = adjustment(state, "model:a", "agent")
            refined = estimate(state, "model:a", "agent", "coding agent")

            self.assertAlmostEqual(coarse, 0.02)
            self.assertAlmostEqual(refined.value, 0.0244444444)
            self.assertLess(refined.value, 0.0366666667)  # Former additive parent + child result.
            self.assertEqual(refined.role_evidence, 3.0)
            self.assertEqual(refined.kind_evidence, 3.0)
            self.assertAlmostEqual(refined.kind_weight, 1 / 3)

    def test_kind_refinement_does_not_change_an_unseen_sibling(self) -> None:
        state = {
            "records": {
                "model:a|agent": {"positive": 48, "negative": 48},
                "model:a|agent|coding agent": {"positive": 96, "negative": 0},
            }
        }

        matching = estimate(state, "model:a", "agent", "coding agent")
        sibling = estimate(state, "model:a", "agent", "system agent")

        self.assertGreater(matching.value, MAX_ADJUSTMENT)
        self.assertEqual(sibling.value, 0.0)
        self.assertEqual(sibling.kind_evidence, 0.0)

    def test_kind_component_is_bounded_and_needs_minimum_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            for _ in range(100):
                observe(path, "model:a", "reasoning", 1, weight=4, source="feedback", kind="deep reasoning")
            learned, _ = adjustment(load_state(path), "model:a", "reasoning", kind="deep reasoning")
            self.assertGreater(learned, MAX_ADJUSTMENT)
            self.assertLessEqual(learned, MAX_TOTAL_ADJUSTMENT)

            sparse = Path(directory) / "sparse.json"
            for _ in range(2):
                observe(sparse, "model:b", "research", -1, source="review", kind="research")
            learned, evidence = adjustment(load_state(sparse), "model:b", "research", kind="research")
            self.assertEqual(learned, 0.0)
            self.assertEqual(evidence, 2.0)

    def test_report_lists_kind_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            observe(path, "model:a", "coding", -1, weight=3, source="feedback", kind="coding agent")
            rendered = report(path)
            self.assertIn("model:a  /  coding  /  coding agent", rendered)
            self.assertIn("role evidence 3.0", rendered)
            self.assertIn("kind evidence 3.0", rendered)
            self.assertIn("mix 33%", rendered)

    def test_corrupt_record_values_fail_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            path.write_text(
                json.dumps(
                    {
                        "records": {
                            "model:a|general": {"model": "model:a", "role": "general", "positive": "bad"},
                            "model:b|coding": {"model": "model:b", "role": "coding", "negative": math.nan},
                            "ignored": "not an object",
                        }
                    }
                ),
                encoding="utf-8",
            )

            state = load_state(path)
            self.assertEqual(adjustment(state, "model:a", "general"), (0.0, 0.0))
            self.assertEqual(adjustment(state, "model:b", "coding"), (0.0, 0.0))
            learned, evidence = observe(path, "model:a", "general", 1, weight=math.inf)
            self.assertEqual((learned, evidence), (0.0, 0.0))

    def test_roles_are_independent_and_state_is_resettable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            observe(path, "model:a", "coding", -1, weight=3, source="feedback")
            state = load_state(path)
            coding, _ = adjustment(state, "model:a", "coding")
            general, _ = adjustment(state, "model:a", "general")

            self.assertLess(coding, 0.0)
            self.assertEqual(general, 0.0)
            self.assertIn("model:a", report(path))
            reset(path)
            self.assertFalse(path.exists())
            self.assertIn("no evidence", report(path).lower())


if __name__ == "__main__":
    unittest.main()
