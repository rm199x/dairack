from __future__ import annotations

import socket
import threading
import unittest
from unittest.mock import Mock, patch

from dairack import network


def resolved(ip: str, port: int = 443) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]


class NetworkTransportTests(unittest.TestCase):
    def test_resolved_address_is_pinned_into_socket_connect(self) -> None:
        resolver = Mock(return_value=resolved("93.184.216.34"))
        target = network.resolve_url("https://example.test/path", resolver=resolver)
        connection = Mock()

        with patch.object(network.socket, "socket", return_value=connection) as socket_factory:
            self.assertIs(network._dial(target, 2.0), connection)

        resolver.assert_called_once_with("example.test", 443, type=socket.SOCK_STREAM)
        socket_factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM, 6)
        connection.connect.assert_called_once_with(("93.184.216.34", 443))

    def test_redirect_target_is_validated_before_another_request(self) -> None:
        answers = {
            "public.example.test": resolved("93.184.216.34"),
            "localhost": resolved("127.0.0.1", 80),
        }

        def resolver(host: str, port: int, **_kwargs: object) -> list[tuple[object, ...]]:
            del port
            return answers[host]

        with (
            patch.object(
                network,
                "_request_once",
                return_value=(302, "http://localhost/admin", {}, b"", ""),
            ) as request_once,
            self.assertRaisesRegex(ValueError, "non-public"),
        ):
            network.fetch_public_url(
                "https://public.example.test/start",
                max_bytes=1024,
                resolver=resolver,
            )
        request_once.assert_called_once()

    def test_deadline_watcher_uses_socket_shutdown(self) -> None:
        connection = Mock()
        stop = threading.Event()
        outcome: dict[str, str] = {}
        network._interrupt_socket(connection, stop, None, 0.0, outcome)
        self.assertEqual(outcome["reason"], "timeout")
        connection.shutdown.assert_called_once_with(socket.SHUT_RDWR)


if __name__ == "__main__":
    unittest.main()
