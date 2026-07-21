from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dairack.hardware import GIB, Accelerator, HardwareProfile, suggest_runtime
from dairack.models import (
    ModelDescriptor,
    ModelRegistry,
    capabilities_for,
    clear_runtime_override,
    load_registry,
    runtime_override_for,
    save_registry,
    save_runtime_override,
)
from dairack.paths import AppPaths


def hardware(vram_gib: int = 8, ram_gib: int = 32) -> HardwareProfile:
    accelerators = (Accelerator("cuda", "Test GPU", vram_gib * GIB),) if vram_gib else ()
    return HardwareProfile("linux", "x86_64", "Test CPU", 8, 16, ram_gib * GIB, 24 * GIB, accelerators)


def paths_at(root: Path) -> AppPaths:
    return AppPaths(root / "config", root / "data", root / "cache", root / "state")


class HardwareAndModelTests(unittest.TestCase):
    def test_runtime_profiles_scale_with_hardware_fit(self) -> None:
        profile = hardware()
        accelerated = suggest_runtime(profile, 5 * GIB, 131_072)
        hybrid = suggest_runtime(profile, 18 * GIB, 131_072)
        constrained = suggest_runtime(profile, 48 * GIB, 131_072)

        self.assertEqual(accelerated.fit, "accelerator")
        self.assertEqual(hybrid.fit, "hybrid")
        self.assertEqual(constrained.fit, "constrained")
        self.assertFalse(constrained.recommended)
        self.assertLess(constrained.num_batch, hybrid.num_batch)

    def test_high_headroom_accelerators_use_larger_declared_context_windows(self) -> None:
        workstation = hardware(vram_gib=48, ram_gib=128)

        roomy = suggest_runtime(workstation, 12 * GIB, 131_072)
        weight_heavy = suggest_runtime(workstation, 32 * GIB, 131_072)
        model_limited = suggest_runtime(workstation, 12 * GIB, 24_576)

        self.assertEqual(roomy.num_ctx, 65_536)
        self.assertLess(weight_heavy.num_ctx, roomy.num_ctx)
        self.assertEqual(model_limited.num_ctx, 24_576)

    def test_declared_features_and_generic_hints_drive_capabilities(self) -> None:
        profile = hardware()
        utility = ModelDescriptor("utility:8b", 5 * GIB, "8B", "Q4", capabilities=("completion",))
        coder = ModelDescriptor("local-coder:30b", 18 * GIB, "30B", "Q4", capabilities=("completion", "tools"))
        visual = ModelDescriptor("visual:9b", 6 * GIB, "9B", "Q4", capabilities=("completion", "vision", "tools"))
        registry = ModelRegistry.discover([utility, coder, visual], profile)

        self.assertGreater(registry.models[coder.name].capability.code, registry.models[utility.name].capability.code)
        self.assertGreater(registry.models[visual.name].capability.vision, 0.7)
        self.assertLess(registry.models[utility.name].capability.vision, 0.1)

    def test_registry_override_round_trip_and_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_at(Path(directory))
            descriptor = ModelDescriptor("model:latest", 6 * GIB, "9B", "Q4", capabilities=("tools",))
            registry = ModelRegistry.discover([descriptor], hardware())
            registry.models[descriptor.name].override = {
                "capabilities": {"reasoning": 0.97},
                "runtime": {"num_ctx": 12_288},
            }
            save_registry(registry, paths)

            loaded = load_registry(paths)
            assert loaded is not None
            self.assertEqual(loaded.models[descriptor.name].effective_capability().reasoning, 0.97)
            self.assertEqual(loaded.models[descriptor.name].effective_runtime()["num_ctx"], 12_288)
            self.assertEqual(capabilities_for(descriptor, paths)["reasoning"], 0.97)

    def test_constrained_model_is_not_default_when_an_alternative_exists(self) -> None:
        models = [
            ModelDescriptor("small", 5 * GIB, "8B", "Q4", capabilities=("tools",)),
            ModelDescriptor("oversized", 60 * GIB, "70B", "Q4", capabilities=("tools", "thinking")),
        ]
        registry = ModelRegistry.discover(models, hardware())
        self.assertEqual(registry.models["oversized"].runtime.fit, "constrained")
        self.assertEqual(registry.default_model(), "small")

    def test_runtime_override_has_one_canonical_registry_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_at(Path(directory))
            descriptor = ModelDescriptor("model:latest", 6 * GIB, "9B", "Q4", capabilities=("tools",))
            save_registry(ModelRegistry.discover([descriptor], hardware()), paths)

            self.assertTrue(save_runtime_override("MODEL:LATEST", {"num_ctx": 10_240}, paths))
            self.assertTrue(save_runtime_override("model:latest", {"model_options": {"num_batch": 160}}, paths))
            self.assertEqual(
                runtime_override_for("model:latest", paths),
                {"num_ctx": 10_240, "model_options": {"num_batch": 160}},
            )
            self.assertTrue(clear_runtime_override("model:latest", paths))
            self.assertEqual(runtime_override_for("model:latest", paths), {})
