from __future__ import annotations

import unittest
from unittest.mock import patch

from dairack import hardware


class HardwareProbeTests(unittest.TestCase):
    @unittest.skipUnless(hardware.platform.system() == "Darwin", "live Darwin hardware probe")
    def test_live_darwin_hardware_discovery(self) -> None:
        profile = hardware.detect_hardware()

        self.assertEqual(profile.os_name, "darwin")
        self.assertTrue(profile.cpu_name)
        self.assertGreaterEqual(profile.physical_cores, 1)
        self.assertGreaterEqual(profile.logical_cores, profile.physical_cores)
        self.assertGreater(profile.memory_total_bytes, 0)
        self.assertTrue(profile.accelerators)
        self.assertEqual(profile.accelerators[0].backend, "metal")

    def test_darwin_memory_uses_sysctl_and_vm_stat(self) -> None:
        outputs = {
            ("sysctl", "-n", "hw.memsize"): str(16 * hardware.GIB),
            ("vm_stat",): (
                "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                "Pages free: 100000.\nPages inactive: 200000.\nPages speculative: 50000.\n"
            ),
        }
        with (
            patch.object(hardware.Path, "exists", return_value=False),
            patch.object(hardware.platform, "system", return_value="Darwin"),
            patch.object(hardware, "_run", side_effect=lambda command, timeout=4.0: outputs.get(tuple(command), "")),
        ):
            total, available = hardware._memory_bytes()

        self.assertEqual(total, 16 * hardware.GIB)
        self.assertEqual(available, 350000 * 16384)

    def test_windows_saturated_adapter_ram_is_not_treated_as_four_gib(self) -> None:
        payload = '[{"Name":"AMD Radeon RX","AdapterRAM":4294967295,"DriverVersion":"1"}]'
        with (
            patch.object(hardware.platform, "system", return_value="Windows"),
            patch.object(hardware.shutil, "which", return_value="powershell"),
            patch.object(hardware, "_run", return_value=payload),
        ):
            devices = hardware._windows_accelerators()

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].memory_bytes, 0)


if __name__ == "__main__":
    unittest.main()
