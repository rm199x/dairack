from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack import machine
from dairack.hardware import GIB, Accelerator, HardwareProfile
from dairack.paths import AppPaths


def profile(os_name: str, cpu: str, gpu: str, vram: int) -> HardwareProfile:
    return HardwareProfile(
        os_name,
        "x86_64",
        cpu,
        8,
        16,
        32 * GIB,
        20 * GIB,
        (Accelerator("cuda", gpu, vram * GIB),),
    )


class MachineIdentityTests(unittest.TestCase):
    def test_remote_compute_identity_never_inherits_client_hardware(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            paths.ensure()
            client = profile("windows", "Intel i9 client", "RTX 4080 Laptop", 12)
            compute = profile("linux", "Ryzen server", "RTX 3070 Laptop", 8)
            paths.hardware_file.write_text(json.dumps(compute.to_dict()), encoding="utf-8")
            config = {
                "compute_mode": "remote",
                "compute_name": "Home Server",
                "compute_transport": "bridge",
                "compute_hardware_verified": True,
                "ollama_host": "https://server.example.ts.net",
            }

            mapped = machine.machine_map(config, paths, client=client)
            self.assertEqual(mapped.client.cpu_name, "Intel i9 client")
            self.assertEqual(mapped.compute.cpu_name, "Ryzen server")

            with patch.object(machine, "client_hardware", return_value=client):
                prompt = machine.machine_prompt(config, paths)
                report = machine.hardware_status(config, paths)
            self.assertIn("CLIENT / ACTIONS", report)
            self.assertIn("Intel i9 client", prompt)
            self.assertIn("Ryzen server", prompt)
            self.assertIn("RTX 4080 Laptop / cuda / 12.0 GiB", report)
            self.assertIn("RTX 3070 Laptop / cuda / 8.0 GiB", report)
            self.assertIn("cannot inspect a remote compute machine", report)

    def test_unverified_remote_compute_does_not_use_a_stale_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
            paths.ensure()
            paths.hardware_file.write_text(json.dumps(profile("linux", "Old server", "Old GPU", 8).to_dict()))
            config = {
                "compute_mode": "remote",
                "compute_name": "Plain Ollama",
                "compute_transport": "ollama",
                "compute_hardware_verified": False,
                "ollama_host": "https://ollama.example",
            }

            mapped = machine.machine_map(config, paths, client=profile("windows", "Client", "Client GPU", 12))
            self.assertIsNone(mapped.compute)
            self.assertFalse(mapped.compute_verified)


if __name__ == "__main__":
    unittest.main()
