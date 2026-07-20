from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack import paths as paths_module
from dairack.config import ConfigError, default_config, load_config, save_config
from dairack.paths import AppPaths, migrate_legacy_state


def isolated_paths(root: Path) -> AppPaths:
    return AppPaths(root / "config", root / "data", root / "cache", root / "state")


class PathAndConfigTests(unittest.TestCase):
    def test_portable_home_owns_every_state_class(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DAIRACK_HOME": directory}, clear=False),
        ):
            paths = AppPaths.discover()
            root = Path(directory).resolve()
            self.assertEqual(paths.config_dir, root / "config")
            self.assertEqual(paths.chats_dir, root / "data" / "chats")
            self.assertEqual(paths.index_file, root / "data" / "project-index.sqlite3")

    def test_windows_uses_roaming_config_and_local_data(self) -> None:
        environment = {
            "APPDATA": r"C:\Users\Ryan\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\Ryan\AppData\Local",
            "DAIRACK_HOME": "",
        }
        with (
            patch.object(paths_module, "_is_windows", return_value=True),
            patch.dict(os.environ, environment, clear=False),
        ):
            paths = AppPaths.discover()
        self.assertEqual(paths.config_dir, Path(environment["APPDATA"]) / "Dairack")
        self.assertEqual(paths.data_dir, Path(environment["LOCALAPPDATA"]) / "Dairack" / "Data")

    def test_relative_xdg_directories_are_ignored(self) -> None:
        with (
            patch.object(paths_module, "_is_windows", return_value=False),
            patch.dict(
                os.environ,
                {"DAIRACK_HOME": "", "XDG_CONFIG_HOME": "relative-config"},
                clear=False,
            ),
        ):
            paths = AppPaths.discover()
        self.assertEqual(paths.config_dir, Path.home() / ".config" / "dairack")

    def test_legacy_home_environment_remains_a_compatibility_alias(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"ASUSAI_HOME": directory}, clear=True),
        ):
            paths = AppPaths.discover()
        self.assertEqual(paths.config_dir, Path(directory).resolve() / "config")

    def test_legacy_state_migration_merges_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = isolated_paths(root / "legacy")
            current = isolated_paths(root / "current")
            legacy.config_dir.mkdir(parents=True)
            legacy.data_dir.mkdir(parents=True)
            current.config_dir.mkdir(parents=True)
            legacy.config_file.write_text('{"legacy": true}', encoding="utf-8")
            legacy.chats_dir.mkdir()
            (legacy.chats_dir / "chat.json").write_text("{}", encoding="utf-8")
            (legacy.data_dir / "runtime-venv").mkdir()
            (legacy.data_dir / "runtime-venv" / "python").write_text("managed", encoding="utf-8")
            current.config_file.write_text('{"current": true}', encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                report = migrate_legacy_state(current, legacy_paths=legacy)

            self.assertEqual(current.config_file.read_text(encoding="utf-8"), '{"current": true}')
            self.assertTrue((current.chats_dir / "chat.json").is_file())
            self.assertTrue((legacy.data_dir / "runtime-venv" / "python").is_file())
            self.assertIn(current.config_file, report.conflicts)
            self.assertTrue(report.changed)

    def test_config_round_trip_is_private_and_preserves_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = isolated_paths(Path(directory))
            config = default_config()
            config["extension.example"] = {"enabled": True}
            saved = save_config(config, paths)
            loaded = load_config(paths)

            self.assertEqual(loaded, saved)
            self.assertEqual(loaded["extension.example"], {"enabled": True})
            if os.name == "posix":
                self.assertEqual(paths.config_file.stat().st_mode & 0o777, 0o600)
                self.assertEqual(paths.config_dir.stat().st_mode & 0o777, 0o700)

    def test_invalid_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = isolated_paths(Path(directory))
            paths.config_dir.mkdir(parents=True)
            payload = default_config()
            payload["permission_mode"] = "unrestricted"
            paths.config_file.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "permission_mode"):
                load_config(paths)

    def test_startup_defaults_to_fresh_and_rejects_unknown_modes(self) -> None:
        self.assertEqual(default_config()["startup_chat"], "new")
        self.assertFalse(default_config()["reduced_motion"])
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["startup_chat"] = "surprise"
            with self.assertRaisesRegex(ConfigError, "startup_chat"):
                save_config(config, isolated_paths(Path(directory)))

        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["reduced_motion"] = "sometimes"
            with self.assertRaisesRegex(ConfigError, "reduced_motion"):
                save_config(config, isolated_paths(Path(directory)))

    def test_save_rejects_invalid_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["num_ctx"] = 0
            with self.assertRaisesRegex(ConfigError, "num_ctx"):
                save_config(config, isolated_paths(Path(directory)))

    def test_compute_endpoint_cannot_embed_credentials_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["ollama_host"] = "https://user:secret@compute.example.test"  # pragma: allowlist secret
            with self.assertRaisesRegex(ConfigError, "embedded credentials"):
                save_config(config, isolated_paths(Path(directory)))

            config["ollama_host"] = "https://compute.example.test/api"
            with self.assertRaisesRegex(ConfigError, "must not contain a path"):
                save_config(config, isolated_paths(Path(directory)))

    def test_agent_action_budget_has_a_bounded_default(self) -> None:
        self.assertEqual(default_config()["max_agent_steps"], 12)
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["max_agent_steps"] = 0
            with self.assertRaisesRegex(ConfigError, "max_agent_steps"):
                save_config(config, isolated_paths(Path(directory)))

    def test_update_channel_requires_https_and_interval_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = default_config()
            config["update_index_url"] = "http://updates.example.test/dairack.json"
            with self.assertRaisesRegex(ConfigError, "HTTPS"):
                save_config(config, isolated_paths(Path(directory)))

            config["update_index_url"] = "http://localhost:8000/dairack.json"
            config["update_check_interval_hours"] = 0
            with self.assertRaisesRegex(ConfigError, "update_check_interval_hours"):
                save_config(config, isolated_paths(Path(directory)))

    def test_schema_one_migrates_and_future_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            legacy = default_config()
            legacy["schema_version"] = 1
            legacy.pop("coordinator_role_preferences")
            migrated = save_config(legacy, isolated_paths(Path(directory)))
            self.assertEqual(migrated["schema_version"], 3)
            self.assertEqual(migrated["coordinator_role_preferences"], {})
            self.assertEqual(migrated["compute_mode"], "local")
            self.assertEqual(migrated["compute_transport"], "ollama")

        with tempfile.TemporaryDirectory() as directory:
            future = default_config()
            future["schema_version"] = 999
            with self.assertRaisesRegex(ConfigError, "newer"):
                save_config(future, isolated_paths(Path(directory)))
