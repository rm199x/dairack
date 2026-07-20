from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dairack.bridge import BridgeConfig, ComputeBridgeServer
from dairack.compute import ComputeError
from dairack.hardware import GIB, Accelerator, HardwareProfile
from dairack.providers.ollama import OllamaError, OllamaProvider


class FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list[tuple[str, str]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def _write(self, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self.hits.append(("GET", self.path))
        if self.path == "/api/version":
            self._write(b'{"version":"test-ollama"}')
        elif self.path == "/api/tags":
            self._write(b'{"models":[]}')
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        self.hits.append(("POST", self.path))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/api/chat":
            self._write(
                b'{"message":{"content":"hello"},"done":false}\n{"message":{"content":""},"done":true}\n',
                "application/x-ndjson",
            )
        elif self.path == "/api/show":
            self._write(b'{"capabilities":["completion"]}')
        else:
            self.send_error(404)


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOllamaHandler.hits = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        upstream_url = f"http://127.0.0.1:{self.upstream.server_address[1]}"
        hardware = HardwareProfile(
            "linux",
            "x86_64",
            "Compute CPU",
            8,
            16,
            32 * GIB,
            24 * GIB,
            (Accelerator("cuda", "Compute GPU", 8 * GIB),),
        )
        self.bridge = ComputeBridgeServer(
            BridgeConfig(port=0, upstream=upstream_url, token="test-token-value-that-is-long", node_name="Test Server"),
            hardware,
        )
        self.bridge_thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self.bridge_thread.start()
        self.endpoint = f"http://127.0.0.1:{self.bridge.server_address[1]}"

    def tearDown(self) -> None:
        self.bridge.shutdown()
        self.bridge.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.bridge_thread.join(timeout=2)
        self.upstream_thread.join(timeout=2)

    def test_authentication_and_hardware_identity(self) -> None:
        with self.assertRaises(OllamaError) as caught:
            OllamaProvider(self.endpoint).compute_info()
        self.assertEqual(caught.exception.status_code, 401)

        provider = OllamaProvider(self.endpoint, "test-token-value-that-is-long")
        info = provider.compute_info()
        self.assertEqual(info["service"], "dairack-compute")
        self.assertEqual(info["node_name"], "Test Server")
        self.assertEqual(info["hardware"]["cpu_name"], "Compute CPU")
        self.assertEqual(provider.version(), "test-ollama")

    def test_legacy_info_endpoint_remains_available_during_migration(self) -> None:
        request = urllib.request.Request(
            self.endpoint + "/asusai/v1/info",
            headers={"Authorization": "Bearer test-token-value-that-is-long"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            info = json.loads(response.read())
        self.assertEqual(info["service"], "asusai-compute")
        self.assertEqual(info["dairack_version"], info["asusai_version"])

    def test_only_the_inference_surface_is_exposed(self) -> None:
        request = urllib.request.Request(
            self.endpoint + "/api/generate",
            headers={"Authorization": "Bearer test-token-value-that-is-long"},
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 404)
        self.assertNotIn(("GET", "/api/generate"), FakeOllamaHandler.hits)

    def test_chat_stream_passes_through_without_buffering_protocol_changes(self) -> None:
        provider = OllamaProvider(self.endpoint, "test-token-value-that-is-long")
        chunks = list(
            provider.chat_stream(
                "model",
                [{"role": "user", "content": "hi"}],
                think=False,
                num_ctx=4096,
            )
        )
        self.assertEqual(chunks, ["hello"])
        self.assertEqual(provider.last_stats["done_reason"], "stop")
        self.assertIn(("POST", "/api/chat"), FakeOllamaHandler.hits)

    def test_unauthenticated_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ComputeError, "loopback"):
            ComputeBridgeServer(BridgeConfig(bind="0.0.0.0", port=0, token=""))


if __name__ == "__main__":
    unittest.main()
