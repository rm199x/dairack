from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack.bootstrap import initialize
from dairack.config import default_config, load_config, save_config
from dairack.hardware import GIB, Accelerator, HardwareProfile
from dairack.models import ModelDescriptor, load_registry
from dairack.paths import AppPaths
from dairack.providers.ollama import OllamaError


class FakeOllama:
    def __init__(self, host: str) -> None:
        self.host = host

    def version(self) -> str:
        return "test-version"

    def list_models(self) -> list[ModelDescriptor]:
        return [
            ModelDescriptor("fast", 5 * GIB, "8B", "Q4", capabilities=("completion", "tools")),
            ModelDescriptor("large", 18 * GIB, "30B", "Q4", capabilities=("completion", "tools", "thinking")),
        ]


class FakeBridgeOllama(FakeOllama):
    def compute_info(self) -> dict[str, object]:
        hardware = HardwareProfile(
            "linux",
            "x86_64",
            "Remote CPU",
            12,
            24,
            64 * GIB,
            48 * GIB,
            (Accelerator("cuda", "Remote GPU", 16 * GIB),),
        )
        return {
            "service": "dairack-compute",
            "protocol_version": 1,
            "dairack_version": "test",
            "node_name": "Remote Server",
            "hardware": hardware.to_dict(),
        }


class FakePlainRemoteOllama(FakeOllama):
    def compute_info(self) -> dict[str, object]:
        raise OllamaError("not found", 404)


class BootstrapTests(unittest.TestCase):
    @patch("dairack.bootstrap.OllamaProvider", FakeOllama)
    @patch("dairack.bootstrap.detect_hardware")
    def test_initialize_writes_registry_and_migrates_legacy_overrides(self, detect: object) -> None:
        detect.return_value = HardwareProfile(  # type: ignore[attr-defined]
            "linux",
            "x86_64",
            "CPU",
            8,
            16,
            32 * GIB,
            24 * GIB,
            (Accelerator("cuda", "GPU", 8 * GIB),),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            config = default_config()
            config["model"] = "missing"
            config["profile_overrides"] = {"fast": {"num_ctx": 12_288}}
            save_config(config, paths)

            result = initialize(paths)
            registry = load_registry(paths)
            persisted = load_config(paths)

            assert registry is not None
            self.assertEqual(result.ollama_version, "test-version")
            self.assertIn(persisted["model"], registry.models)
            self.assertEqual(registry.models["fast"].effective_runtime()["num_ctx"], 12_288)
            self.assertEqual(persisted["profile_overrides"], {})
            self.assertTrue(paths.hardware_file.exists())
            self.assertIn("Default model:", result.report())

    @patch("dairack.bootstrap.OllamaProvider", FakeOllama)
    @patch("dairack.bootstrap.detect_hardware")
    def test_dry_run_does_not_write(self, detect: object) -> None:
        detect.return_value = HardwareProfile(  # type: ignore[attr-defined]
            "linux", "x86_64", "CPU", 4, 8, 16 * GIB, 12 * GIB
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            result = initialize(paths, write=False)
            self.assertTrue(result.registry.models)
            self.assertFalse(paths.config_file.exists())

    @patch("dairack.bootstrap.OllamaProvider", FakeBridgeOllama)
    @patch(
        "dairack.bootstrap.detect_hardware", side_effect=AssertionError("client hardware must not tune remote models")
    )
    def test_remote_bridge_uses_server_hardware(self, _detect: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            result = initialize(paths, "https://compute.example.test")

        self.assertEqual(result.config["compute_mode"], "remote")
        self.assertEqual(result.config["compute_transport"], "bridge")
        self.assertEqual(result.config["compute_name"], "Remote Server")
        self.assertEqual(result.hardware.primary_accelerator.name, "Remote GPU")
        self.assertTrue(result.registry.hardware_verified)
        self.assertEqual(result.registry.compute_endpoint, "https://compute.example.test")

    @patch("dairack.bootstrap.OllamaProvider", FakePlainRemoteOllama)
    @patch(
        "dairack.bootstrap.detect_hardware", side_effect=AssertionError("client hardware must not tune remote models")
    )
    def test_plain_remote_endpoint_uses_backend_managed_profiles(self, _detect: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            result = initialize(paths, "https://ollama.example.test")

        self.assertFalse(result.registry.hardware_verified)
        self.assertEqual(result.config["compute_transport"], "ollama")
        self.assertEqual(result.registry.models["fast"].runtime.fit, "remote-unverified")
        self.assertEqual(result.registry.models["fast"].runtime.to_profile()["options"], {})
