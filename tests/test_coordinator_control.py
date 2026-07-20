from __future__ import annotations

import unittest

from dairack.coordinator.control import RoutingControl, materially_larger, model_capacity
from dairack.models import ModelDescriptor


def payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "compute_preference": "auto",
        "control_target": "none",
        "preference_strength": 0.0,
        "control_confidence": 0.95,
        "applies_to_previous": False,
        "resolved_task": "",
    }
    value.update(overrides)
    return value


class RoutingControlTests(unittest.TestCase):
    def test_automatic_classification_is_inert(self) -> None:
        control = RoutingControl.from_semantic_payload(payload(), has_context=True)

        self.assertFalse(control.active)
        self.assertEqual(control.preference, "auto")
        self.assertEqual(control.status, "automatic")

    def test_high_confidence_control_is_active_for_one_route(self) -> None:
        control = RoutingControl.from_semantic_payload(
            payload(
                compute_preference="higher_capacity",
                control_target="compute",
                preference_strength=0.9,
                control_confidence=0.96,
                applies_to_previous=True,
                resolved_task="  Reconsider   the prior architecture.\x00 ",
            ),
            has_context=True,
        )

        self.assertTrue(control.active)
        self.assertEqual(control.resolved_task, "Reconsider the prior architecture.")
        self.assertEqual(control.status, "applied")

    def test_compute_target_separates_control_from_content_style(self) -> None:
        quality = RoutingControl.from_semantic_payload(
            payload(
                compute_preference="quality",
                control_target="compute",
                preference_strength=0.8,
                control_confidence=0.75,
            ),
            has_context=True,
        )
        capacity = RoutingControl.from_semantic_payload(
            payload(
                compute_preference="higher_capacity",
                control_target="content",
                preference_strength=0.8,
                control_confidence=0.98,
            ),
            has_context=True,
        )

        self.assertTrue(quality.active)
        self.assertFalse(capacity.active)
        self.assertEqual(capacity.status, "not a compute directive")

    def test_missing_target_can_be_normalized_only_after_preference_validation(self) -> None:
        control = RoutingControl.from_semantic_payload(
            payload(
                compute_preference="higher_capacity",
                preference_strength=0.9,
                control_confidence=0.8,
            ),
            has_context=True,
        )

        self.assertTrue(control.active)
        self.assertEqual(control.target, "compute")

    def test_uncertain_weak_and_unresolved_controls_fail_closed(self) -> None:
        cases = (
            (
                payload(
                    compute_preference="quality",
                    control_target="compute",
                    preference_strength=0.9,
                    control_confidence=0.6,
                ),
                True,
                "confidence below threshold",
            ),
            (
                payload(
                    compute_preference="quality",
                    control_target="compute",
                    preference_strength=0.2,
                    control_confidence=0.96,
                ),
                True,
                "strength below threshold",
            ),
            (
                payload(
                    compute_preference="quality",
                    control_target="compute",
                    preference_strength=0.9,
                    control_confidence=0.96,
                    applies_to_previous=True,
                    resolved_task="Prior task",
                ),
                False,
                "no prior task available",
            ),
            (
                payload(
                    compute_preference="quality",
                    control_target="compute",
                    preference_strength=0.9,
                    control_confidence=0.96,
                    applies_to_previous=True,
                ),
                True,
                "prior task was not resolved",
            ),
        )
        for raw, has_context, status in cases:
            with self.subTest(status=status):
                control = RoutingControl.from_semantic_payload(raw, has_context=has_context)
                self.assertFalse(control.active)
                self.assertEqual(control.status, status)

    def test_invalid_control_values_fail_schema_validation(self) -> None:
        cases = (
            payload(compute_preference="maximum"),
            payload(control_target="machine"),
            payload(preference_strength=float("nan")),
            payload(control_confidence=True),
            payload(applies_to_previous="yes"),
        )
        for raw in cases:
            with self.subTest(raw=raw):
                control = RoutingControl.from_semantic_payload(raw, has_context=True)
                self.assertFalse(control.active)
                self.assertEqual(control.status, "invalid semantic control")

    def test_capacity_uses_normalized_metadata_without_model_name_rules(self) -> None:
        compact = ModelDescriptor("unknown-a", size=6 * 1024**3, parameter_size="9B")
        larger = ModelDescriptor("unknown-b", size=12 * 1024**3, parameter_size="27B")
        size_only = ModelDescriptor("unknown-c", size=18 * 1024**3)

        self.assertEqual(model_capacity(compact), 9.0)
        self.assertEqual(model_capacity(larger), 27.0)
        self.assertAlmostEqual(model_capacity(size_only), 18.0)
        self.assertTrue(materially_larger(larger, compact))
        self.assertFalse(materially_larger(compact, larger))


if __name__ == "__main__":
    unittest.main()
