"""Authenticated, inference-only bridge for remote Dairack clients."""

from __future__ import annotations

import hmac
import http.client
import json
import os
import secrets
import select
import socket
import tempfile
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Thread
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .compute import ComputeError, endpoint_policy, local_client_name
from .hardware import HardwareProfile, detect_hardware
from .identity import (
    BRIDGE_HEALTH_PATH,
    BRIDGE_INFO_PATH,
    BRIDGE_SERVICE,
    LEGACY_BRIDGE_HEALTH_PATH,
    LEGACY_BRIDGE_INFO_PATH,
    LEGACY_BRIDGE_SERVICE,
)

MAX_REQUEST_BYTES = 128 * 1024 * 1024
BRIDGE_PROTOCOL_VERSION = 1
STREAM_HEARTBEAT_SECONDS = 15.0
STREAM_CLIENT_POLL_SECONDS = 0.25
STREAM_RELAY_QUEUE_DEPTH = 16
STREAM_RELAY_EOF = object()
STREAMING_ROUTES = {"/api/chat", "/api/pull"}
ALLOWED_ROUTES = {
    ("GET", "/api/version"),
    ("GET", "/api/tags"),
    ("GET", "/api/ps"),
    ("POST", "/api/show"),
    ("POST", "/api/chat"),
    ("POST", "/api/embed"),
    ("POST", "/api/pull"),
    ("DELETE", "/api/delete"),
}


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    bind: str = "127.0.0.1"
    port: int = 11435
    upstream: str = "http://127.0.0.1:11434"
    token: str = ""
    node_name: str = ""
    max_connections: int = 16


def _atomic_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(value.strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def load_or_create_bridge_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        token = ""
    except OSError as exc:
        raise ComputeError(f"could not read bridge token: {exc}") from exc
    if token:
        if len(token) < 24:
            raise ComputeError(f"bridge token in {path} is too short")
        return token
    token = secrets.token_urlsafe(32)
    _atomic_private_text(path, token)
    return token


class ComputeBridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, config: BridgeConfig, hardware: HardwareProfile | None = None) -> None:
        upstream = endpoint_policy(config.upstream)
        if not upstream.local:
            raise ComputeError("the compute bridge upstream must be a local Ollama endpoint")
        if not 0 <= config.port <= 65535:
            raise ComputeError("bridge port must be between 0 and 65535")
        if not 1 <= config.max_connections <= 128:
            raise ComputeError("max_connections must be between 1 and 128")
        if config.token and len(config.token) < 24:
            raise ComputeError("bridge tokens must contain at least 24 characters")
        if not config.token and config.bind not in {"127.0.0.1", "::1", "localhost"}:
            raise ComputeError("an unauthenticated bridge may only bind to loopback")
        self.bridge_config = BridgeConfig(
            bind=config.bind,
            port=config.port,
            upstream=upstream.endpoint,
            token=config.token,
            node_name=config.node_name or local_client_name(),
            max_connections=config.max_connections,
        )
        self.hardware_profile = hardware or detect_hardware()
        self.request_slots = BoundedSemaphore(config.max_connections)
        self.address_family = socket.AF_INET6 if ":" in config.bind else socket.AF_INET
        super().__init__((config.bind, config.port), ComputeBridgeHandler)


class ComputeBridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Dairack-Compute"
    sys_version = ""

    @property
    def bridge(self) -> ComputeBridgeServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any], *, authenticate: bool = False) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if authenticate:
            self.send_header("WWW-Authenticate", 'Bearer realm="Dairack Compute"')
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = self.bridge.bridge_config.token
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return supplied.startswith(prefix) and hmac.compare_digest(supplied[len(prefix) :], expected)

    def _dispatch(self) -> None:
        if not self.bridge.request_slots.acquire(blocking=False):
            self._send_json(503, {"error": "compute bridge connection limit reached"})
            return
        try:
            self._dispatch_request()
        finally:
            self.bridge.request_slots.release()

    def _dispatch_request(self) -> None:
        path = urlsplit(self.path).path
        if not self._authorized():
            self._send_json(401, {"error": "compute bridge authentication required"}, authenticate=True)
            return
        if self.command == "GET" and path in {BRIDGE_HEALTH_PATH, LEGACY_BRIDGE_HEALTH_PATH}:
            service = LEGACY_BRIDGE_SERVICE if path == LEGACY_BRIDGE_HEALTH_PATH else BRIDGE_SERVICE
            self._send_json(
                200,
                {"service": service, "status": "ready", "protocol_version": BRIDGE_PROTOCOL_VERSION},
            )
            return
        if self.command == "GET" and path in {BRIDGE_INFO_PATH, LEGACY_BRIDGE_INFO_PATH}:
            legacy = path == LEGACY_BRIDGE_INFO_PATH
            payload = {
                "service": LEGACY_BRIDGE_SERVICE if legacy else BRIDGE_SERVICE,
                "protocol_version": BRIDGE_PROTOCOL_VERSION,
                "dairack_version": __version__,
                "node_name": self.bridge.bridge_config.node_name,
                "hardware": self.bridge.hardware_profile.to_dict(),
                "capabilities": {"ollama_proxy": True, "embeddings": True, "hardware_metadata": True},
            }
            if legacy:
                payload["asusai_version"] = __version__
            self._send_json(
                200,
                payload,
            )
            return
        if (self.command, path) not in ALLOWED_ROUTES:
            self._send_json(404, {"error": "route is not exposed by the compute bridge"})
            return
        self._proxy(path)

    def _request_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return None
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ComputeError("invalid Content-Length") from exc
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise ComputeError(f"request exceeds the {MAX_REQUEST_BYTES // 1024**2} MiB bridge limit")
        return self.rfile.read(length)

    @staticmethod
    def _interrupt_upstream(
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse | None = None,
    ) -> None:
        sockets = [
            getattr(connection, "sock", None),
            getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None) if response else None,
        ]
        for upstream_socket in sockets:
            if upstream_socket is None:
                continue
            try:
                upstream_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                pass

    @staticmethod
    def _stream_requested(path: str, body: bytes | None) -> bool:
        if path not in STREAMING_ROUTES:
            return False
        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return True
        return not isinstance(payload, dict) or payload.get("stream", True) is not False

    def _send_stream_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _write_stream_error(self, error: Exception | str, status: int | None = None) -> None:
        message = str(error).strip() or "local Ollama stream failed"
        payload: dict[str, Any] = {"error": message}
        if status is not None:
            payload["status"] = status
        self.wfile.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _downstream_disconnected(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
        except (OSError, ValueError):
            return True
        if not readable:
            return False
        try:
            return not self.connection.recv(1, socket.MSG_PEEK)
        except BlockingIOError:
            return False
        except ValueError:
            # Some wrapped sockets reject MSG_PEEK. Heartbeat writes remain the fallback.
            return False
        except OSError:
            return True

    def _relay_upstream_response(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        *,
        preacknowledged: bool = False,
    ) -> None:
        if preacknowledged and response.status >= 400:
            detail = response.read(64 * 1024).decode(errors="replace").strip()
            self._write_stream_error(
                f"Ollama returned HTTP {response.status}: {detail or response.reason}",
                response.status,
            )
            return
        content_type = response.getheader("Content-Type")
        content_length = response.getheader("Content-Length")
        if not preacknowledged:
            self.send_response(response.status, response.reason)
            if content_type:
                self.send_header("Content-Type", content_type)
            if content_length:
                self.send_header("Content-Length", content_length)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        if content_type and "application/x-ndjson" in content_type.lower() and not content_length:
            self._relay_ndjson_stream(connection, response)
            return
        read = getattr(response, "read1", response.read)
        while True:
            chunk = read(64 * 1024)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

    def _proxy_stream(
        self,
        connection: http.client.HTTPConnection,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        """Open an upstream stream while keeping the downstream connection alive.

        Ollama can delay HTTP response headers until a native tool call is fully
        generated. Once that delay reaches the heartbeat interval, acknowledge
        the downstream NDJSON stream and send a blank line. Fast upstream errors
        retain their original HTTP status; errors after acknowledgement become
        ordinary NDJSON error events understood by the provider.
        """

        opened: Queue[http.client.HTTPResponse | BaseException] = Queue(maxsize=1)
        response_ref: list[http.client.HTTPResponse | None] = [None]

        def open_upstream() -> None:
            try:
                connection.request(self.command, path, body=body, headers=headers)
                response = connection.getresponse()
                response_ref[0] = response
                opened.put(response)
            except (OSError, http.client.HTTPException) as exc:
                opened.put(exc)

        opener = Thread(target=open_upstream, daemon=True, name="dairack-bridge-open-stream")
        opener.start()
        response_started = False
        interrupted = False
        next_heartbeat = time.monotonic() + max(0.01, STREAM_HEARTBEAT_SECONDS)
        try:
            while True:
                try:
                    remaining = max(0.01, next_heartbeat - time.monotonic())
                    event = opened.get(timeout=min(STREAM_CLIENT_POLL_SECONDS, remaining))
                    break
                except Empty:
                    if self._downstream_disconnected():
                        raise ConnectionAbortedError("compute client disconnected") from None
                    if time.monotonic() < next_heartbeat:
                        continue
                    if not response_started:
                        self._send_stream_headers()
                        response_started = True
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                    next_heartbeat = time.monotonic() + max(0.01, STREAM_HEARTBEAT_SECONDS)

            if isinstance(event, BaseException):
                if response_started:
                    self._write_stream_error(f"local Ollama unavailable: {event}")
                else:
                    self._send_json(502, {"error": f"local Ollama unavailable: {event}"})
                return

            response = event
            if response_started:
                self._relay_upstream_response(connection, response, preacknowledged=True)
                return

            response_started = True
            self._relay_upstream_response(connection, response)
        except (OSError, http.client.HTTPException, ValueError):
            interrupted = True
            if not response_started:
                raise
        finally:
            if interrupted or opener.is_alive():
                self._interrupt_upstream(connection, response_ref[0])
            opener.join(timeout=0.5)

    def _relay_ndjson_stream(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
    ) -> None:
        """Relay complete NDJSON records while keeping silent generations alive.

        Ollama may buffer native tool-call arguments until the call is complete.
        A blank NDJSON line is protocol-safe and prevents remote proxies and
        clients from mistaking that healthy generation interval for a dead
        connection. The heartbeat write also detects a departed client, at
        which point closing the upstream socket cancels otherwise orphaned work.
        """

        events: Queue[bytes | BaseException | object] = Queue(maxsize=STREAM_RELAY_QUEUE_DEPTH)
        stopped = Event()

        def enqueue(item: bytes | BaseException | object) -> None:
            while not stopped.is_set():
                try:
                    events.put(item, timeout=0.1)
                    return
                except Full:
                    continue

        def read_upstream() -> None:
            try:
                while not stopped.is_set():
                    line = response.readline()
                    if not line:
                        break
                    enqueue(line)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                enqueue(exc)
            finally:
                enqueue(STREAM_RELAY_EOF)

        reader = Thread(target=read_upstream, daemon=True, name="dairack-bridge-stream")
        reader.start()
        interrupted = False
        next_heartbeat = time.monotonic() + max(0.01, STREAM_HEARTBEAT_SECONDS)
        try:
            while True:
                try:
                    remaining = max(0.01, next_heartbeat - time.monotonic())
                    event = events.get(timeout=min(STREAM_CLIENT_POLL_SECONDS, remaining))
                except Empty:
                    if self._downstream_disconnected():
                        raise ConnectionAbortedError("compute client disconnected") from None
                    if time.monotonic() < next_heartbeat:
                        continue
                    self.wfile.write(b"\n")
                    self.wfile.flush()
                    next_heartbeat = time.monotonic() + max(0.01, STREAM_HEARTBEAT_SECONDS)
                    continue
                if event is STREAM_RELAY_EOF:
                    break
                if isinstance(event, BaseException):
                    raise event
                self.wfile.write(event)
                self.wfile.flush()
        except (OSError, http.client.HTTPException, ValueError):
            interrupted = True
            raise
        finally:
            stopped.set()
            if interrupted or reader.is_alive():
                self._interrupt_upstream(connection, response)
            reader.join(timeout=0.5)

    def _proxy(self, path: str) -> None:
        try:
            body = self._request_body()
        except ComputeError as exc:
            self._send_json(413, {"error": str(exc)})
            return
        upstream = urlsplit(self.bridge.bridge_config.upstream)
        connection_type = http.client.HTTPSConnection if upstream.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(upstream.hostname, upstream.port, timeout=None)
        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "User-Agent": f"Dairack-Compute/{__version__}",
        }
        response_started = False
        try:
            if self._stream_requested(path, body):
                self._proxy_stream(connection, path, body, headers)
                return
            connection.request(self.command, path, body=body, headers=headers)
            response = connection.getresponse()
            response_started = True
            self._relay_upstream_response(connection, response)
        except (OSError, http.client.HTTPException) as exc:
            if not response_started and not self.wfile.closed:
                try:
                    self._send_json(502, {"error": f"local Ollama unavailable: {exc}"})
                except OSError:
                    pass
        finally:
            connection.close()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()
