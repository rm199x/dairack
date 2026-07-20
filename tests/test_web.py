from __future__ import annotations

import socket
import unittest
import urllib.request
from unittest.mock import patch

from dairack import network, runtime


def resolved(ip: str, port: int = 443) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class WebBoundaryTests(unittest.TestCase):
    def test_duckduckgo_relative_redirects_are_decoded_to_the_destination(self) -> None:
        redirect = "/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&rut=opaque"

        self.assertEqual(runtime.decode_duckduckgo_url(redirect), "https://example.com/docs")

    def test_search_result_exposes_backend_direct_target_and_clean_urls(self) -> None:
        payload = b"""
        <a class="result-link" href="/l/?uddg=https%3A%2F%2Fexample.com%2Freview&amp;rut=x">Review</a>
        <td class="result-snippet">Independent notes.</td>
        """
        with patch.object(
            runtime,
            "fetch_url_bytes",
            return_value=(payload, "text/html; charset=utf-8", "https://lite.duckduckgo.com/lite/?q=example"),
        ):
            code, output = runtime.internet_search("example.com review")

        self.assertEqual(code, 0)
        self.assertIn("backend: DuckDuckGo Lite", output)
        self.assertIn("direct target: https://example.com/", output)
        self.assertIn("https://example.com/review", output)
        self.assertNotIn("duckduckgo.com/l/", output)
        self.assertIn("Use web_open", output)

    def test_url_syntax_rejects_non_http_and_embedded_credentials(self) -> None:
        for value in ("file:///etc/passwd", "ftp://example.test/file", "example.test/path"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.normalize_web_url(value)
        with self.assertRaisesRegex(ValueError, "credentials"):
            runtime.normalize_web_url("https://user:secret@example.test/")  # pragma: allowlist secret

    def test_public_web_validation_blocks_non_public_destinations(self) -> None:
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.2"):
            with (
                self.subTest(address=address),
                patch.object(network.socket, "getaddrinfo", return_value=resolved(address)),
                self.assertRaisesRegex(ValueError, "non-public"),
            ):
                runtime.validate_public_web_url("https://docs.example.test/")

    def test_redirect_handler_revalidates_destination_and_blocks_downgrades(self) -> None:
        request = urllib.request.Request("https://public.example.test/start")
        handler = runtime.PublicWebRedirectHandler()
        with (
            patch.object(network.socket, "getaddrinfo", return_value=resolved("127.0.0.1")),
            self.assertRaisesRegex(ValueError, "non-public"),
        ):
            handler.redirect_request(request, None, 302, "Found", {}, "http://localhost/admin")

        with (
            patch.object(network.socket, "getaddrinfo", return_value=resolved("93.184.216.34", 80)),
            self.assertRaisesRegex(Exception, "downgrade"),
        ):
            handler.redirect_request(request, None, 302, "Found", {}, "http://public.example.test/plain")

    def test_public_web_validation_rejects_private_ipv4_embedded_in_ipv6(self) -> None:
        for address in ("64:ff9b::7f00:1", "64:ff9b::a00:1", "::127.0.0.1"):
            answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0))]
            with (
                self.subTest(address=address),
                patch.object(network.socket, "getaddrinfo", return_value=answer),
                self.assertRaisesRegex(ValueError, "non-public"),
            ):
                runtime.validate_public_web_url("https://docs.example.test/")


if __name__ == "__main__":
    unittest.main()
