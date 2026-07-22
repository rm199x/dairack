from __future__ import annotations

import http.client
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from dairack.bridge import BridgeConfig, ComputeBridgeHandler, ComputeBridgeServer
from dairack.compute import ComputeError
from dairack.hardware import GIB, Accelerator, HardwareProfile
from dairack.providers.ollama import OllamaError, OllamaProvider


class FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits: list[tuple[str, str]] = []
    chat_delay = 0.0
    chat_status = 200

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
            if self.chat_status != 200:
                if self.chat_delay:
                    time.sleep(self.chat_delay)
                detail = b'{"error":"test upstream failure"}'
                self.send_response(self.chat_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(detail)))
                self.end_headers()
                self.wfile.write(detail)
                return
            payload = b'{"message":{"content":"hello"},"done":false}\n{"message":{"content":""},"done":true}\n'
            if self.chat_delay:
                time.sleep(self.chat_delay)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(payload)
                    self.wfile.flush()
                    self.close_connection = True
                except OSError:
                    pass
            else:
                self._write(payload, "application/x-ndjson")
        elif self.path == "/api/show":
            self._write(b'{"capabilities":["completion"]}')
        elif self.path == "/api/embed":
            self._write(b'{"embeddings":[[0.25,0.75]]}')
        else:
            self.send_error(404)


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeOllamaHandler.hits = []
        FakeOllamaHandler.chat_delay = 0.0
        FakeOllamaHandler.chat_status = 200
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
        self.assertTrue(info["capabilities"]["embeddings"])
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

    def test_silent_ndjson_generation_receives_protocol_safe_heartbeats(self) -> None:
        FakeOllamaHandler.chat_delay = 0.25
        request = urllib.request.Request(
            self.endpoint + "/api/chat",
            data=json.dumps({"model": "slow", "stream": True}).encode(),
            headers={
                "Authorization": "Bearer test-token-value-that-is-long",
                "Content-Type": "application/json",
            },
        )

        started = time.monotonic()
        with patch("dairack.bridge.STREAM_HEARTBEAT_SECONDS", 0.04):
            with urllib.request.urlopen(request, timeout=1) as response:
                self.assertEqual(response.readline(), b"\n")
                heartbeat_at = time.monotonic() - started
                first_event = b""
                while not first_event.strip():
                    first_event = response.readline()

        self.assertLess(heartbeat_at, 0.20)
        self.assertEqual(json.loads(first_event)["message"]["content"], "hello")

    def test_departed_client_cancels_upstream_before_headers_arrive(self) -> None:
        FakeOllamaHandler.chat_delay = 0.35
        upstream_interrupted = threading.Event()
        interrupt_upstream = ComputeBridgeHandler._interrupt_upstream

        def record_interrupt(
            connection: http.client.HTTPConnection,
            response: http.client.HTTPResponse | None = None,
        ) -> None:
            upstream_interrupted.set()
            interrupt_upstream(connection, response)

        request = urllib.request.Request(
            self.endpoint + "/api/chat",
            data=json.dumps({"model": "slow", "stream": True}).encode(),
            headers={
                "Authorization": "Bearer test-token-value-that-is-long",
                "Content-Type": "application/json",
            },
        )

        with (
            patch("dairack.bridge.STREAM_HEARTBEAT_SECONDS", 0.04),
            patch("dairack.bridge.STREAM_CLIENT_POLL_SECONDS", 0.02),
            patch.object(ComputeBridgeHandler, "_interrupt_upstream", staticmethod(record_interrupt)),
        ):
            response = urllib.request.urlopen(request, timeout=1)
            self.assertEqual(response.readline(), b"\n")
            response.close()
            self.assertTrue(upstream_interrupted.wait(1))

    def test_fast_stream_error_retains_upstream_http_status(self) -> None:
        FakeOllamaHandler.chat_status = 404
        provider = OllamaProvider(self.endpoint, "test-token-value-that-is-long")

        with self.assertRaises(OllamaError) as caught:
            list(provider.chat_stream("missing", [{"role": "user", "content": "hi"}], think=False, num_ctx=4096))

        self.assertEqual(caught.exception.status_code, 404)
        self.assertIn("test upstream failure", str(caught.exception))

    def test_slow_stream_error_after_heartbeat_retains_upstream_status(self) -> None:
        FakeOllamaHandler.chat_delay = 0.12
        FakeOllamaHandler.chat_status = 503
        provider = OllamaProvider(self.endpoint, "test-token-value-that-is-long")

        with (
            patch("dairack.bridge.STREAM_HEARTBEAT_SECONDS", 0.04),
            self.assertRaises(OllamaError) as caught,
        ):
            list(provider.chat_stream("busy", [{"role": "user", "content": "hi"}], think=False, num_ctx=4096))

        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("Ollama returned HTTP 503", str(caught.exception))

    def test_embedding_inference_is_available_through_the_allowlist(self) -> None:
        provider = OllamaProvider(self.endpoint, "test-token-value-that-is-long")

        self.assertEqual(provider.embed("embed-model", ["project text"]), [[0.25, 0.75]])
        self.assertIn(("POST", "/api/embed"), FakeOllamaHandler.hits)

    def test_unauthenticated_non_loopback_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ComputeError, "loopback"):
            ComputeBridgeServer(BridgeConfig(bind="0.0.0.0", port=0, token=""))


if __name__ == "__main__":
    unittest.main()
