"""Pinned-address HTTP transport for untrusted outbound requests."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Mapping

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
IPV4_COMPAT_PREFIX = ipaddress.ip_network("::/96")
SIX_TO_FOUR_PREFIX = ipaddress.ip_network("2002::/16")


class NetworkError(RuntimeError):
    """Raised when a bounded outbound request cannot be completed safely."""


class NetworkCancelled(NetworkError):
    """Raised when the caller interrupts an outbound request."""


@dataclass(frozen=True, slots=True)
class ResolvedURL:
    url: str
    scheme: str
    host: str
    port: int
    request_target: str
    addresses: tuple[tuple[int, int, int, tuple[Any, ...]], ...]


@dataclass(frozen=True, slots=True)
class FetchResult:
    body: bytes
    content_type: str
    final_url: str
    headers: Mapping[str, str]


Resolver = Callable[..., list[tuple[int, int, int, str, tuple[Any, ...]]]]


def _contains_terminal_control(value: str) -> bool:
    return any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value)


def normalize_http_url(
    value: str,
    *,
    require_https: bool = False,
    allow_loopback: bool = False,
) -> str:
    url = str(value or "").strip()
    if not url or _contains_terminal_control(url):
        raise ValueError("URL is empty or contains control characters")
    if len(url) > 8192:
        raise ValueError("URL is too long")
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("URL must start with http:// or https://")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")
    host = str(parsed.hostname or "").rstrip(".")
    if not host or "\\" in parsed.netloc:
        raise ValueError("URL is missing a valid host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("URL host is not valid IDNA") from exc
    else:
        ascii_host = str(literal)
    local = ascii_host in LOCAL_HOSTS or bool(literal and literal.is_loopback)
    if require_https and scheme != "https" and not (allow_loopback and local):
        raise ValueError("URL must use HTTPS")
    if scheme == "http" and require_https and not local:
        raise ValueError("URL must use HTTPS")
    host_text = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    default_port = 443 if scheme == "https" else 80
    netloc = host_text + (f":{port}" if port is not None and port != default_port else "")
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="%/:?@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    packed = address.packed
    if address in NAT64_PREFIX or address in IPV4_COMPAT_PREFIX:
        return ipaddress.IPv4Address(packed[-4:])
    if address in SIX_TO_FOUR_PREFIX:
        return ipaddress.IPv4Address(packed[2:6])
    if address.teredo is not None:
        _server, client = address.teredo
        return client
    return None


def _address_is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None and not embedded.is_global:
            return False
    return True


def resolve_url(
    value: str,
    *,
    require_https: bool = False,
    allow_loopback: bool = False,
    resolver: Resolver | None = None,
) -> ResolvedURL:
    url = normalize_http_url(value, require_https=require_https, allow_loopback=allow_loopback)
    parsed = urllib.parse.urlsplit(url)
    host = str(parsed.hostname or "")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    explicitly_local = host in LOCAL_HOSTS
    try:
        literal_host = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal_host = None
    if literal_host is not None and literal_host.is_loopback:
        explicitly_local = True
    resolver = resolver or socket.getaddrinfo
    try:
        resolved = resolver(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve web host {host}: {exc}") from exc
    if not resolved:
        raise ValueError(f"could not resolve web host {host}")

    addresses: list[tuple[int, int, int, tuple[Any, ...]]] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, kind, protocol, _canonical, sockaddr in resolved:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        raw_ip = str(sockaddr[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise ValueError(f"web host resolved to an invalid address: {raw_ip}") from exc
        if allow_loopback and explicitly_local:
            if not address.is_loopback:
                raise ValueError(f"local web host resolved to non-loopback address {address}")
        elif not _address_is_public(address):
            raise ValueError(f"web requests to non-public address {address} are blocked")
        key = (family, tuple(sockaddr))
        if key not in seen:
            seen.add(key)
            addresses.append((family, kind, protocol, tuple(sockaddr)))
    if not addresses:
        raise ValueError(f"could not resolve a usable address for web host {host}")
    request_target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    return ResolvedURL(url, parsed.scheme, host, port, request_target, tuple(addresses))


def validate_public_url(value: str, *, resolver: Resolver | None = None) -> str:
    return resolve_url(value, resolver=resolver).url


def _dial(target: ResolvedURL, timeout: float) -> socket.socket:
    failures: list[OSError] = []
    for family, kind, protocol, sockaddr in target.addresses:
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(timeout)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            failures.append(exc)
            connection.close()
    if failures:
        raise failures[-1]
    raise OSError("no usable address was available")


def _interrupt_socket(
    connection: socket.socket,
    stop: threading.Event,
    cancel_event: threading.Event | None,
    deadline: float,
    outcome: dict[str, str],
) -> None:
    while not stop.wait(0.05):
        if cancel_event is not None and cancel_event.is_set():
            outcome["reason"] = "cancelled"
        elif time.monotonic() >= deadline:
            outcome["reason"] = "timeout"
        else:
            continue
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return


def _request_once(
    target: ResolvedURL,
    *,
    headers: Mapping[str, str],
    idle_timeout: float,
    deadline: float,
    max_bytes: int,
    cancel_event: threading.Event | None,
) -> tuple[int, str, Mapping[str, str], bytes, str]:
    if cancel_event is not None and cancel_event.is_set():
        raise NetworkCancelled("network request interrupted")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("network request exceeded its total timeout")
    raw = _dial(target, max(0.1, min(idle_timeout, remaining)))
    transport: socket.socket = raw
    connection: http.client.HTTPConnection | None = None
    response: http.client.HTTPResponse | None = None
    stop = threading.Event()
    outcome: dict[str, str] = {}
    watcher: threading.Thread | None = None
    try:
        if target.scheme == "https":
            context = ssl.create_default_context()
            transport = context.wrap_socket(raw, server_hostname=target.host, do_handshake_on_connect=False)
        watcher = threading.Thread(
            target=_interrupt_socket,
            args=(transport, stop, cancel_event, deadline, outcome),
            daemon=True,
            name="dairack-network-deadline",
        )
        watcher.start()
        if isinstance(transport, ssl.SSLSocket):
            transport.do_handshake()
        connection = http.client.HTTPConnection(target.host, target.port, timeout=idle_timeout)
        connection.sock = transport
        connection.request("GET", target.request_target, headers=dict(headers))
        response = connection.getresponse()
        response_headers = {str(key): str(value) for key, value in response.headers.items()}
        location = str(response.headers.get("Location") or "")
        if response.status in REDIRECT_STATUSES:
            return response.status, location, response_headers, b"", ""
        if response.status >= 400:
            raise NetworkError(f"HTTP {response.status} {response.reason}")
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) > max_bytes:
                    raise NetworkError("response is too large")
            except ValueError:
                pass
        body = response.read(max_bytes + 1)
        if outcome.get("reason") == "cancelled" or (cancel_event is not None and cancel_event.is_set()):
            raise NetworkCancelled("network request interrupted")
        if outcome.get("reason") == "timeout" or time.monotonic() >= deadline:
            raise TimeoutError("network request exceeded its total timeout")
        if len(body) > max_bytes:
            raise NetworkError("response is too large")
        return response.status, "", response_headers, body, str(response.headers.get("Content-Type") or "")
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        if outcome.get("reason") == "cancelled" or (cancel_event is not None and cancel_event.is_set()):
            raise NetworkCancelled("network request interrupted") from exc
        if outcome.get("reason") == "timeout" or time.monotonic() >= deadline:
            raise TimeoutError("network request exceeded its total timeout") from exc
        raise NetworkError(str(exc)) from exc
    finally:
        stop.set()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()
        else:
            raw.close()
        if watcher is not None:
            watcher.join(timeout=0.2)


def fetch_public_url(
    value: str,
    *,
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
    idle_timeout: float = 18.0,
    total_timeout: float = 45.0,
    max_redirects: int = 5,
    cancel_event: threading.Event | None = None,
    require_https: bool = False,
    allow_loopback: bool = False,
    resolver: Resolver | None = None,
) -> FetchResult:
    """Fetch a bounded URL while connecting only to the exact addresses that were validated."""
    deadline = time.monotonic() + max(0.1, total_timeout)
    current = value
    previous_scheme = ""
    request_headers = {"User-Agent": "Dairack/1.0"}
    request_headers.update(headers or {})
    for redirect_count in range(max_redirects + 1):
        target = resolve_url(
            current,
            require_https=require_https,
            allow_loopback=allow_loopback,
            resolver=resolver,
        )
        if previous_scheme == "https" and target.scheme != "https":
            raise ValueError("HTTPS downgrade redirect blocked")
        status, location, response_headers, body, content_type = _request_once(
            target,
            headers=request_headers,
            idle_timeout=idle_timeout,
            deadline=deadline,
            max_bytes=max_bytes,
            cancel_event=cancel_event,
        )
        if status not in REDIRECT_STATUSES:
            return FetchResult(body, content_type, target.url, response_headers)
        if not location:
            raise NetworkError(f"HTTP {status} redirect did not include a Location header")
        if redirect_count >= max_redirects:
            raise NetworkError("too many redirects")
        previous_scheme = target.scheme
        current = urllib.parse.urljoin(target.url, location)
    raise NetworkError("too many redirects")
