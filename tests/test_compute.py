from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack.compute import (
    ComputeError,
    endpoint_policy,
    probe_compute,
    save_compute_token,
    stored_compute_token,
    validate_compute_endpoint,
)
from dairack.hardware import GIB, Accelerator, HardwareProfile
from dairack.models import ModelDescriptor, ModelRegistry
from dairack.paths import AppPaths
from dairack.providers.ollama import OllamaError


def isolated_paths(root: Path) -> AppPaths:
    return AppPaths(root / "config", root / "data", root / "cache", root / "state")


class FakeProvider:
    def __init__(self, host: str, info: dict[str, object] | None = None) -> None:
        self.host = host
        self.info = info

    def compute_info(self) -> dict[str, object]:
        if self.info is None:
            raise OllamaError("not found", 404)
        return self.info

    def version(self) -> str:
        return "1.2.3"

    def list_models(self) -> list[ModelDescriptor]:
        return [ModelDescriptor("model:latest", 5 * GIB, "9B", "Q4", context_length=32_768)]


class ComputeTests(unittest.TestCase):
    def test_endpoint_policy_distinguishes_local_tailnet_and_insecure_networks(self) -> None:
        self.assertTrue(endpoint_policy("localhost:11434").local)
        self.assertEqual(validate_compute_endpoint("http://100.90.1.2:11435").scope, "tailnet")
        self.assertTrue(validate_compute_endpoint("https://compute.example.test").encrypted)
        with self.assertRaisesRegex(ComputeError, "requires HTTPS"):
            validate_compute_endpoint("http://192.168.1.4:11435")
        self.assertEqual(
            validate_compute_endpoint("http://192.168.1.4:11435", allow_insecure=True).scope,
            "private",
        )
        with self.assertRaisesRegex(ComputeError, "must not be embedded"):
            endpoint_policy("https://user:secret@example.test")  # pragma: allowlist secret

    def test_compute_tokens_are_private_and_keyed_by_normalized_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = isolated_paths(Path(directory))
            save_compute_token("example.test", "secret-value", paths)
            self.assertEqual(stored_compute_token("http://example.test", paths), "secret-value")
            if os.name == "posix":
                self.assertEqual(paths.compute_credentials_file.stat().st_mode & 0o777, 0o600)
            save_compute_token("http://example.test", "", paths)
            self.assertEqual(stored_compute_token("example.test", paths), "")

    def test_plain_remote_uses_unverified_backend_managed_hardware(self) -> None:
        probe = probe_compute(FakeProvider("https://compute.example.test"))  # type: ignore[arg-type]
        self.assertFalse(probe.hardware_verified)
        self.assertEqual(probe.transport, "ollama")
        self.assertEqual(probe.hardware.os_name, "remote")
        registry = ModelRegistry.discover(
            probe.models,
            probe.hardware,
            compute_endpoint=probe.endpoint,
            hardware_verified=False,
        )
        runtime = registry.models["model:latest"].effective_runtime()
        self.assertEqual(runtime["fit"], "remote-unverified")
        self.assertEqual(runtime["options"], {})
        self.assertEqual(runtime["num_ctx"], 8192)

    def test_bridge_metadata_is_the_compute_hardware_authority(self) -> None:
        hardware = HardwareProfile(
            "linux",
            "x86_64",
            "Server CPU",
            12,
            24,
            64 * GIB,
            48 * GIB,
            (Accelerator("cuda", "Server GPU", 16 * GIB),),
        )
        provider = FakeProvider(
            "https://compute.example.test",
            {
                "service": "dairack-compute",
                "protocol_version": 1,
                "dairack_version": "0.1.0",
                "node_name": "Studio Server",
                "hardware": hardware.to_dict(),
            },
        )
        with patch("dairack.compute.detect_hardware", side_effect=AssertionError("client probe must not run")):
            probe = probe_compute(provider)  # type: ignore[arg-type]
        self.assertTrue(probe.hardware_verified)
        self.assertEqual(probe.transport, "bridge")
        self.assertEqual(probe.name, "Studio Server")
        self.assertEqual(probe.hardware.primary_accelerator.name, "Server GPU")

    def test_bridge_protocol_mismatch_fails_closed(self) -> None:
        provider = FakeProvider(
            "https://compute.example.test",
            {"service": "dairack-compute", "protocol_version": 99},
        )
        with self.assertRaisesRegex(ComputeError, "not supported"):
            probe_compute(provider)  # type: ignore[arg-type]

    def test_legacy_bridge_metadata_is_accepted(self) -> None:
        provider = FakeProvider(
            "https://compute.example.test",
            {
                "service": "asusai-compute",
                "protocol_version": 1,
                "asusai_version": "0.0.9",
                "node_name": "Legacy Server",
            },
        )
        probe = probe_compute(provider)  # type: ignore[arg-type]
        self.assertEqual(probe.transport, "bridge")
        self.assertEqual(probe.bridge_version, "0.0.9")
        self.assertEqual(probe.name, "Legacy Server")


if __name__ == "__main__":
    unittest.main()
