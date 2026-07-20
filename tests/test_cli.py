from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_models import hardware

from dairack import cli
from dairack.bootstrap import InitializationResult
from dairack.compute import ComputeProbe
from dairack.hardware import GIB
from dairack.models import ModelDescriptor, ModelRegistry, load_registry, save_registry
from dairack.paths import AppPaths
from dairack.updates import UpdateInfo


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.paths = AppPaths(root / "config", root / "data", root / "cache", root / "state")
        descriptor = ModelDescriptor("example:latest", 5 * GIB, "8B", "Q4", capabilities=("tools",))
        save_registry(ModelRegistry.discover([descriptor], hardware()), self.paths)
        self.paths_patch = patch.object(cli, "PATHS", self.paths)
        self.paths_patch.start()

    def tearDown(self) -> None:
        self.paths_patch.stop()
        self.temporary.cleanup()

    def test_version_and_model_listing(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["--version"]), 0)
            self.assertEqual(cli.main(["models"]), 0)
        self.assertIn("dairack 0.1.0", output.getvalue())
        self.assertIn("example:latest", output.getvalue())

    def test_model_override_set_inspect_and_reset(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["models", "set", "example", "reasoning", "0.96"]), 0)
            self.assertEqual(cli.main(["models", "inspect", "example"]), 0)
        registry = load_registry(self.paths)
        assert registry is not None
        self.assertEqual(registry.models["example:latest"].effective_capability().reasoning, 0.96)
        self.assertIn('"reasoning": 0.96', output.getvalue())

        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["models", "reset", "example"]), 0)
        registry = load_registry(self.paths)
        assert registry is not None
        self.assertFalse(registry.models["example:latest"].override)

    def test_invalid_override_is_rejected(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            code = cli.main(["models", "set", "example", "vision", "1.5"])
        self.assertEqual(code, 2)
        self.assertIn("0.0 to 1.0", error.getvalue())

    def test_coordinator_role_preference_is_optional_and_resettable(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["coordinator", "prefer", "coding", "example"]), 0)
        self.assertIn("coding", output.getvalue())
        config = cli.load_config(self.paths)
        self.assertEqual(config["coordinator_role_preferences"]["coding"], "example:latest")

        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["coordinator", "prefer", "coding", "auto"]), 0)
        config = cli.load_config(self.paths)
        self.assertFalse(config["coordinator_role_preferences"])

    def test_recommendations_are_available_without_a_required_stack(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(["models", "recommend", "--profile", "minimal"]), 0)
        self.assertIn("Recommendations are optional", output.getvalue())

    def test_update_channel_and_available_release_report(self) -> None:
        output = io.StringIO()
        endpoint = "https://updates.example.test/dairack.json"
        with redirect_stdout(output):
            self.assertEqual(cli.main(["update", "channel", endpoint, "--interval", "12"]), 0)
        config = cli.load_config(self.paths)
        self.assertTrue(config["check_updates"])
        self.assertEqual(config["update_index_url"], endpoint)
        self.assertEqual(config["update_check_interval_hours"], 12)

        info = UpdateInfo("0.1.0", "0.2.0", endpoint, "https://example.test/notes")
        output = io.StringIO()
        with patch.object(cli, "check_for_update", return_value=info), redirect_stdout(output):
            self.assertEqual(cli.main(["update"]), 0)
        self.assertIn("DAIRACK UPDATE AVAILABLE", output.getvalue())
        self.assertIn("dairack==0.2.0", output.getvalue())

    def test_update_channel_can_be_disabled(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["update", "channel", "off"]), 0)
        config = cli.load_config(self.paths)
        self.assertFalse(config["check_updates"])
        self.assertEqual(config["update_index_url"], "")

    def test_confirmed_software_update_runs_and_requests_restart(self) -> None:
        endpoint = "https://updates.example.test/dairack.json"
        config = cli.load_config(self.paths)
        config["update_index_url"] = endpoint
        cli.save_config(config, self.paths)
        info = UpdateInfo("0.1.0", "0.2.0", endpoint)
        output = io.StringIO()
        with (
            patch.object(cli, "check_for_update", return_value=info),
            patch.object(cli, "apply_update", return_value=SimpleNamespace(returncode=0)) as apply,
            redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["update", "apply", "--yes"]), 0)
        apply.assert_called_once_with(info)
        self.assertIn("Restart `dairack`", output.getvalue())

    def test_connect_promotes_remote_compute_to_first_class_configuration(self) -> None:
        endpoint = "https://compute.example.test"
        profile = hardware()
        descriptor = ModelDescriptor("remote:latest", 5 * GIB, "8B", "Q4", capabilities=("tools",))
        registry = ModelRegistry.discover(
            [descriptor],
            profile,
            compute_endpoint=endpoint,
            hardware_verified=True,
        )
        probe = ComputeProbe(
            endpoint,
            "Studio Server",
            "test-ollama",
            "0.1.0",
            "bridge",
            profile,
            True,
            (),
            12,
        )

        def initialize_remote(paths: AppPaths, host: str) -> InitializationResult:
            config = cli.load_config(paths)
            config.update(
                {
                    "ollama_host": host,
                    "compute_mode": "remote",
                    "compute_name": "Studio Server",
                    "compute_transport": "bridge",
                    "compute_hardware_verified": True,
                    "remote_ollama_host": host,
                    "model": descriptor.name,
                }
            )
            config = cli.save_config(config, paths)
            return InitializationResult(profile, registry, config, "test-ollama", paths)

        output = io.StringIO()
        with (
            patch.object(cli, "_probe_with_optional_prompt", return_value=(probe, "")),
            patch.object(cli, "initialize", side_effect=initialize_remote),
            redirect_stdout(output),
        ):
            self.assertEqual(cli.main(["connect", endpoint]), 0)

        config = cli.load_config(self.paths)
        self.assertEqual(config["compute_mode"], "remote")
        self.assertEqual(config["ollama_host"], endpoint)
        self.assertIn("Studio Server", output.getvalue())
        self.assertIn("actions      this client", output.getvalue())

    def test_connect_rejects_unencrypted_remote_endpoint_by_default(self) -> None:
        error = io.StringIO()
        with patch.object(cli, "_probe_with_optional_prompt") as probe, redirect_stderr(error):
            self.assertEqual(cli.main(["connect", "http://192.168.1.20:11435"]), 2)
        probe.assert_not_called()
        self.assertIn("requires HTTPS", error.getvalue())
