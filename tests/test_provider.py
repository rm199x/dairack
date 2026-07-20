from __future__ import annotations

import base64
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dairack.providers.ollama import OllamaError, OllamaProvider, _stream_json


class OllamaProviderTests(unittest.TestCase):
    def test_stream_cancellation_releases_a_blocked_response_read(self) -> None:
        cancelled = threading.Event()
        released = threading.Event()

        class FakeSocket:
            def shutdown(self, _how: int) -> None:
                released.set()

        class FakeResponse:
            fp = type("FP", (), {"raw": type("Raw", (), {"_sock": FakeSocket()})()})()

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self) -> FakeResponse:
                return self

            def __next__(self) -> bytes:
                if not released.wait(1.0):
                    raise AssertionError("cancel watcher did not release the blocked response")
                raise OSError("response closed")

            def close(self) -> None:
                released.set()

        timer = threading.Timer(0.03, cancelled.set)
        timer.start()
        try:
            with patch("dairack.providers.ollama.urllib.request.urlopen", return_value=FakeResponse()):
                self.assertEqual(list(_stream_json("http://ollama.test/api/chat", {}, cancel_event=cancelled)), [])
        finally:
            timer.cancel()

        self.assertTrue(released.is_set())

    @patch("dairack.providers.ollama._request_json")
    def test_bearer_auth_is_applied_to_provider_and_bridge_requests(self, request: object) -> None:
        request.side_effect = [{"version": "test"}, {"service": "dairack-compute"}]  # type: ignore[attr-defined]
        provider = OllamaProvider("https://compute.example.test", "private-token")

        self.assertEqual(provider.version(), "test")
        self.assertEqual(provider.compute_info()["service"], "dairack-compute")

        for call in request.call_args_list:  # type: ignore[attr-defined]
            self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer private-token"})

    def test_provider_builds_a_direct_transport_without_ambient_proxies(self) -> None:
        direct_opener = object()
        with patch(
            "dairack.providers.ollama.urllib.request.build_opener",
            return_value=direct_opener,
        ) as build_opener:
            provider = OllamaProvider("https://compute.example.test", "private-token")

        handler = build_opener.call_args.args[0]
        self.assertEqual(handler.proxies, {})
        self.assertIs(provider._opener, direct_opener)

        with patch("dairack.providers.ollama._request_json", return_value={"version": "test"}) as request:
            self.assertEqual(provider.version(), "test")
        self.assertIs(request.call_args.kwargs["opener"], direct_opener)

    @patch("dairack.providers.ollama._request_json")
    def test_compute_info_falls_back_to_the_legacy_bridge_endpoint(self, request: object) -> None:
        request.side_effect = [  # type: ignore[attr-defined]
            OllamaError("not found", 404),
            {"service": "asusai-compute", "asusai_version": "0.0.9"},
        ]
        provider = OllamaProvider("https://compute.example.test", "private-token")

        self.assertEqual(provider.compute_info()["service"], "asusai-compute")
        self.assertTrue(request.call_args_list[0].args[1].endswith("/dairack/v1/info"))  # type: ignore[attr-defined]
        self.assertTrue(request.call_args_list[1].args[1].endswith("/asusai/v1/info"))  # type: ignore[attr-defined]

    def test_chat_payload_preserves_runtime_controls_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.png"
            image.write_bytes(b"image-bytes")
            provider = OllamaProvider("localhost:11434")
            payload = provider.chat_payload(
                "model",
                [{"role": "user", "content": "inspect", "image_paths": [str(image)]}],
                stream=True,
                think="high",
                num_ctx=8192,
                num_predict=200,
                extra_options={"temperature": 0},
                response_format={"type": "object"},
                keep_alive="5m",
            )

        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertEqual(payload["options"]["num_predict"], 200)
        self.assertEqual(payload["format"], {"type": "object"})
        self.assertEqual(payload["keep_alive"], "5m")
        self.assertEqual(payload["messages"][0]["images"], [base64.b64encode(b"image-bytes").decode("ascii")])

    def test_missing_historical_image_does_not_poison_the_conversation(self) -> None:
        payload = OllamaProvider().chat_payload(
            "model",
            [{"role": "user", "content": "inspect", "image_paths": ["/definitely/missing/image.png"]}],
            stream=True,
            think=False,
            num_ctx=4096,
        )

        self.assertNotIn("images", payload["messages"][0])
        self.assertIn("Unavailable image attachment: image.png", payload["messages"][0]["content"])

    def test_invalid_persisted_image_path_does_not_poison_the_conversation(self) -> None:
        payload = OllamaProvider().chat_payload(
            "model",
            [{"role": "user", "content": "inspect", "image_paths": ["\x00/image.png"]}],
            stream=True,
            think=False,
            num_ctx=4096,
        )

        self.assertNotIn("images", payload["messages"][0])
        self.assertIn("Unavailable image attachment: image.png", payload["messages"][0]["content"])

    def test_chat_payload_preserves_native_tools_and_tool_history(self) -> None:
        call = {
            "type": "function",
            "function": {"name": "list_dir", "arguments": {"path": "/tmp"}},
        }
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List a directory.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        payload = OllamaProvider().chat_payload(
            "model",
            [
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_name": "list_dir", "content": "file.txt"},
            ],
            stream=True,
            think=False,
            num_ctx=4096,
            tools=tools,
        )

        self.assertEqual(payload["tools"], tools)
        self.assertEqual(payload["messages"][0]["tool_calls"], [call])
        self.assertEqual(payload["messages"][1]["tool_name"], "list_dir")

    def test_chat_payload_canonicalizes_all_system_context_before_conversation(self) -> None:
        payload = OllamaProvider().chat_payload(
            "model",
            [
                {"role": "system", "content": "base instructions"},
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "inspect the project"},
                {"role": "system", "content": "transient coordinator directive"},
            ],
            stream=True,
            think=False,
            num_ctx=4096,
        )

        self.assertEqual([message["role"] for message in payload["messages"]], ["system", "user", "assistant", "user"])
        self.assertIn("base instructions", payload["messages"][0]["content"])
        self.assertIn("transient coordinator directive", payload["messages"][0]["content"])

    @patch("dairack.providers.ollama._stream_json")
    def test_stream_reports_each_native_tool_call_once(self, stream: object) -> None:
        call = {
            "type": "function",
            "function": {"name": "list_dir", "arguments": {"path": "/tmp"}},
        }
        stream.return_value = iter(  # type: ignore[attr-defined]
            [
                {"message": {"content": "", "tool_calls": [call]}, "done": False},
                {"message": {"content": "", "tool_calls": [call]}, "done": True},
            ]
        )
        calls: list[dict[str, object]] = []

        provider = OllamaProvider()
        chunks = list(
            provider.chat_stream(
                "model",
                [{"role": "user", "content": "list files"}],
                think=False,
                num_ctx=4096,
                tool_call_sink=calls.append,
            )
        )

        self.assertEqual(chunks, [])
        self.assertEqual(calls, [call])
        self.assertEqual(provider.last_stats["done_reason"], "stop")
        self.assertIs(stream.call_args.kwargs["opener"], provider._opener)  # type: ignore[attr-defined]

    @patch("dairack.providers.ollama._stream_json")
    def test_stream_preserves_length_stop_and_clears_stale_stats(self, stream: object) -> None:
        stream.return_value = iter(  # type: ignore[attr-defined]
            [
                {
                    "message": {"content": "partial,"},
                    "done": True,
                    "done_reason": "length",
                    "eval_count": 2,
                }
            ]
        )
        provider = OllamaProvider()
        provider.last_stats = {"done_reason": "stale"}

        chunks = list(
            provider.chat_stream(
                "model",
                [{"role": "user", "content": "respond"}],
                think=False,
                num_ctx=4096,
            )
        )

        self.assertEqual(chunks, ["partial,"])
        self.assertEqual(provider.last_stats["done_reason"], "length")

    @patch("dairack.providers.ollama._stream_json")
    def test_stream_without_done_marker_is_reported_as_incomplete(self, stream: object) -> None:
        stream.return_value = iter([{"message": {"content": "partial"}, "done": False}])  # type: ignore[attr-defined]
        provider = OllamaProvider()

        self.assertEqual(
            list(
                provider.chat_stream(
                    "model",
                    [{"role": "user", "content": "respond"}],
                    think=False,
                    num_ctx=4096,
                )
            ),
            ["partial"],
        )
        self.assertEqual(provider.last_stats["done_reason"], "stream_ended")

    @patch("dairack.providers.ollama._request_json")
    def test_non_streaming_chat_records_completion_metadata(self, request: object) -> None:
        request.return_value = {  # type: ignore[attr-defined]
            "message": {"content": "partial"},
            "done": True,
            "done_reason": "length",
            "eval_count": 2,
        }
        provider = OllamaProvider()

        response = provider.chat(
            "model",
            [{"role": "user", "content": "respond"}],
            stream=False,
            think=False,
            num_ctx=4096,
        )

        self.assertEqual(response, "partial")
        self.assertEqual(provider.last_stats["done_reason"], "length")

    @patch("dairack.providers.ollama._request_json")
    def test_model_discovery_normalizes_tags_and_show_metadata(self, request: object) -> None:
        request.side_effect = [  # type: ignore[attr-defined]
            {
                "models": [
                    {
                        "name": "example:latest",
                        "size": 5_000_000_000,
                        "digest": "abc",
                        "details": {"parameter_size": "8B", "quantization_level": "Q4_K_M", "family": "test"},
                    }
                ]
            },
            {
                "capabilities": ["completion", "vision", "tools"],
                "model_info": {"general.architecture": "testarch", "testarch.context_length": 131072},
            },
        ]
        model = OllamaProvider().list_models()[0]
        self.assertEqual(model.name, "example:latest")
        self.assertEqual(model.context_length, 131072)
        self.assertEqual(model.architecture, "testarch")
        self.assertIn("vision", model.capabilities)
