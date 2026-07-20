from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack.network import FetchResult
from dairack.paths import AppPaths
from dairack.updates import UpdateError, check_for_update, format_update_command, update_command


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, url: str = "") -> None:
        self.body = body
        self.headers = headers or {}
        self.url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]

    def geturl(self) -> str:
        return self.url or "https://updates.example.test/dairack.json"


def paths_for(root: Path) -> AppPaths:
    return AppPaths(root / "config", root / "data", root / "cache", root / "state")


class UpdateTests(unittest.TestCase):
    def test_custom_manifest_is_cached_and_compared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            calls = 0

            def opener(_request: object, timeout: float) -> FakeResponse:
                nonlocal calls
                calls += 1
                self.assertEqual(timeout, 2.0)
                return FakeResponse(b'{"version":"0.2.0","notes_url":"https://example.test/releases/0.2.0"}')

            first = check_for_update(
                "0.1.0",
                "https://example.test/dairack.json",
                paths=paths,
                timeout=2.0,
                opener=opener,
            )
            second = check_for_update(
                "0.1.0",
                "https://example.test/dairack.json",
                paths=paths,
                opener=opener,
            )

            self.assertTrue(first.available)
            self.assertFalse(first.from_cache)
            self.assertTrue(second.from_cache)
            self.assertEqual(second.latest_version, "0.2.0")
            self.assertEqual(calls, 1)
            self.assertTrue(paths.update_cache_file.exists())

    def test_pypi_json_and_forced_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            bodies = iter(
                (
                    b'{"info":{"version":"0.1.0","project_urls":{"Changelog":"https://example.test/log"}}}',
                    b'{"info":{"version":"0.3.0"}}',
                )
            )

            def opener(_request: object, timeout: float) -> FakeResponse:
                return FakeResponse(next(bodies))

            current = check_for_update("0.1.0", "https://pypi.example.test/dairack/json", paths=paths, opener=opener)
            latest = check_for_update(
                "0.1.0",
                "https://pypi.example.test/dairack/json",
                paths=paths,
                force=True,
                opener=opener,
            )

            self.assertFalse(current.available)
            self.assertEqual(current.notes_url, "https://example.test/log")
            self.assertTrue(latest.available)

    def test_channel_validation_and_response_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            with self.assertRaisesRegex(UpdateError, "HTTPS"):
                check_for_update("0.1.0", "http://updates.example.test/release.json", paths=paths)

            def oversized(_request: object, timeout: float) -> FakeResponse:
                return FakeResponse(b"{}", {"Content-Length": "2000000"})

            with self.assertRaisesRegex(UpdateError, "too large"):
                check_for_update(
                    "0.1.0",
                    "https://updates.example.test/release.json",
                    paths=paths,
                    opener=oversized,
                )

            def downgraded(_request: object, timeout: float) -> FakeResponse:
                return FakeResponse(b'{"version":"0.2.0"}', url="http://updates.example.test/release.json")

            with self.assertRaisesRegex(UpdateError, "redirected"):
                check_for_update(
                    "0.1.0",
                    "https://updates.example.test/release.json",
                    paths=paths,
                    opener=downgraded,
                )

    def test_update_command_matches_install_owner(self) -> None:
        uv = update_command(
            "0.2.0",
            executable="/home/test/.local/share/uv/tools/dairack/bin/python",
            which=lambda name: f"/usr/bin/{name}" if name == "uv" else None,
        )
        pipx = update_command(
            "0.2.0",
            executable="/home/test/.local/share/pipx/venvs/dairack/bin/python",
            which=lambda name: f"/usr/bin/{name}" if name == "pipx" else None,
        )
        managed = update_command(
            "0.2.0",
            executable="/home/test/.local/share/dairack/runtime-venv/bin/python",
            which=lambda _name: None,
        )

        self.assertEqual(uv, ["/usr/bin/uv", "tool", "install", "--force", "dairack==0.2.0"])
        self.assertEqual(pipx, ["/usr/bin/pipx", "install", "--force", "dairack==0.2.0"])
        self.assertEqual(
            managed,
            [
                "/home/test/.local/share/dairack/runtime-venv/bin/python",
                "-m",
                "pip",
                "install",
                "--upgrade",
                "dairack==0.2.0",
            ],
        )
        self.assertIn("dairack==0.2.0", format_update_command(managed))

    def test_untrusted_manifest_fields_and_terminal_controls_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))

            def opener(_request: object, timeout: float) -> FakeResponse:
                return FakeResponse(
                    b'{"version":"0.2.0","command":"rm -rf /","index_url":"https://evil.test",'
                    b'"notes_url":"https://example.test/\\u001b[2J"}'
                )

            info = check_for_update("0.1.0", "https://updates.example.test/release.json", paths=paths, opener=opener)

            self.assertEqual(info.notes_url, "")
            self.assertEqual(update_command(info.latest_version, which=lambda _name: None)[-1], "dairack==0.2.0")

            def c1_opener(_request: object, timeout: float) -> FakeResponse:
                del timeout
                return FakeResponse('{"version":"0.2.0","notes_url":"https://example.test/\\u009b2J"}'.encode())

            c1 = check_for_update(
                "0.1.0",
                "https://updates.example.test/release.json",
                paths=paths,
                force=True,
                opener=c1_opener,
            )
            self.assertEqual(c1.notes_url, "")

    def test_default_update_fetch_uses_the_pinned_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = paths_for(Path(directory))
            result = FetchResult(
                b'{"version":"0.2.0"}',
                "application/json",
                "https://updates.example.test/release.json",
                {},
            )
            with patch("dairack.updates.fetch_public_url", return_value=result) as fetch:
                info = check_for_update(
                    "0.1.0",
                    "https://updates.example.test/release.json",
                    paths=paths,
                    force=True,
                )
            self.assertTrue(info.available)
            self.assertTrue(fetch.call_args.kwargs["require_https"])
            self.assertFalse(fetch.call_args.kwargs["allow_loopback"])

    def test_update_command_uses_normalized_version(self) -> None:
        command = update_command("v0.2.0", executable="/tmp/python", which=lambda _name: None)
        self.assertEqual(command[-1], "dairack==0.2.0")
