from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from dairack.coordinator.calibration import MAX_ADJUSTMENT, MAX_EVIDENCE, adjustment, load_state, observe, report, reset


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
