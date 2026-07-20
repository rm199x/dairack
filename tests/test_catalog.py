from __future__ import annotations

import unittest

from test_models import hardware

from dairack.catalog import apply_catalog_priors, load_catalog, recommend_bundle
from dairack.hardware import GIB
from dairack.models import ModelDescriptor, ModelRegistry


class CatalogTests(unittest.TestCase):
    def test_catalog_is_packaged_and_balanced_plan_is_hardware_aware(self) -> None:
        catalog = load_catalog()
        recommendation = recommend_bundle("balanced", hardware(), catalog=catalog)

        self.assertEqual(catalog.schema_version, 1)
        self.assertEqual([model.name for model in recommendation.models], ["qwen3.5:9b", "qwen3-coder:30b"])
        self.assertTrue(recommendation.complete)
        self.assertGreater(recommendation.download_gib, 20)

    def test_existing_curated_stack_satisfies_profiles_without_duplicates(self) -> None:
        models = [
            ModelDescriptor(
                "qwen3.5:9b",
                6 * GIB,
                "9B",
                "Q4",
                capabilities=("tools", "thinking", "vision"),
            ),
            ModelDescriptor("qwen3-coder:30b", 18 * GIB, "30B", "Q4", capabilities=("tools",)),
            ModelDescriptor(
                "qwen3.6:27b",
                17 * GIB,
                "27B",
                "Q4",
                capabilities=("tools", "thinking"),
            ),
        ]
        registry = ModelRegistry.discover(models, hardware())

        balanced = recommend_bundle("balanced", hardware(), registry)
        complete = recommend_bundle("complete", hardware(), registry)

        self.assertFalse(balanced.models)
        self.assertFalse(complete.models)
        self.assertEqual(balanced.covered_roles["coding"], "qwen3-coder:30b")
        self.assertEqual(complete.covered_roles["reasoning"], "qwen3.6:27b")

    def test_large_models_are_not_recommended_below_minimum_memory(self) -> None:
        constrained = hardware(ram_gib=12, vram_gib=4)
        recommendation = recommend_bundle("complete", constrained)

        self.assertNotIn("qwen3-coder:30b", [model.name for model in recommendation.models])
        self.assertNotIn("gpt-oss:20b", [model.name for model in recommendation.models])

    def test_family_only_catalog_match_cannot_invent_model_modality(self) -> None:
        descriptor = ModelDescriptor(
            "qwen3.5:custom-build",
            6 * GIB,
            "9B",
            "Q4",
            capabilities=("completion", "tools"),
        )
        registry = ModelRegistry.discover([descriptor], hardware())

        apply_catalog_priors(registry)

        capability = registry.models[descriptor.name].capability
        self.assertLess(capability.vision, 0.5)
        self.assertNotIn("curated catalog", capability.source)


if __name__ == "__main__":
    unittest.main()
