"""Compute endpoint identity, credentials, probing, and connection policy."""

from __future__ import annotations

import ipaddress
import json
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from .config import atomic_write_json
from .hardware import HardwareProfile, detect_hardware
from .identity import BRIDGE_SERVICE, LEGACY_BRIDGE_SERVICE, env_value
from .models import ModelDescriptor
from .paths import PATHS, AppPaths
from .providers.ollama import OllamaError, OllamaProvider, normalize_host

LOCAL_OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")
CREDENTIAL_SCHEMA_VERSION = 1


class ComputeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    endpoint: str
    scope: str
    encrypted: bool

    @property
    def local(self) -> bool:
        return self.scope == "local"

    @property
    def safe_transport(self) -> bool:
        return self.encrypted or self.scope in {"local", "tailnet"}


@dataclass(frozen=True, slots=True)
class ComputeProbe:
    endpoint: str
    name: str
    ollama_version: str
    bridge_version: str
    transport: str
    hardware: HardwareProfile
    hardware_verified: bool
    models: tuple[ModelDescriptor, ...]
    latency_ms: int

    @property
    def local(self) -> bool:
        return endpoint_policy(self.endpoint).local


def endpoint_policy(raw: str) -> EndpointPolicy:
    endpoint = normalize_host(raw)
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ComputeError("compute endpoint must be an HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ComputeError("compute endpoint credentials must not be embedded in the URL")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ComputeError("compute endpoint must not contain a path, query, or fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ComputeError(f"invalid compute endpoint port: {exc}") from exc

    hostname = parsed.hostname.lower()
    scope = "public"
    if hostname == "localhost":
        scope = "local"
    else:
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            if address.is_loopback:
                scope = "local"
            elif isinstance(address, ipaddress.IPv4Address) and address in TAILSCALE_IPV4:
                scope = "tailnet"
            elif address.is_private or address.is_link_local:
                scope = "private"
    return EndpointPolicy(endpoint.rstrip("/"), scope, parsed.scheme == "https")


def validate_compute_endpoint(raw: str, *, allow_insecure: bool = False) -> EndpointPolicy:
    policy = endpoint_policy(raw)
    if not policy.safe_transport and not allow_insecure:
        raise ComputeError(
            "remote compute requires HTTPS or a Tailscale address; pass --allow-http only for a trusted private network"
        )
    return policy


def is_local_endpoint(raw: str) -> bool:
    try:
        return endpoint_policy(raw).local
    except ComputeError:
        return False


def _read_credentials(paths: AppPaths) -> dict[str, str]:
    try:
        raw = json.loads(paths.compute_credentials_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ComputeError(f"could not read compute credentials: {exc}") from exc
    if not isinstance(raw, Mapping) or int(raw.get("schema_version") or 0) != CREDENTIAL_SCHEMA_VERSION:
        raise ComputeError("compute credentials use an unsupported format")
    connections = raw.get("connections")
    if not isinstance(connections, Mapping):
        return {}
    return {
        str(endpoint): str(token)
        for endpoint, token in connections.items()
        if isinstance(endpoint, str) and isinstance(token, str) and token
    }


def compute_token(endpoint: str, paths: AppPaths = PATHS) -> str:
    environment = env_value("COMPUTE_TOKEN").strip()
    if environment:
        return environment
    return stored_compute_token(endpoint, paths)


def stored_compute_token(endpoint: str, paths: AppPaths = PATHS) -> str:
    normalized = endpoint_policy(endpoint).endpoint
    return _read_credentials(paths).get(normalized, "")


def save_compute_token(endpoint: str, token: str, paths: AppPaths = PATHS) -> None:
    normalized = endpoint_policy(endpoint).endpoint
    connections = _read_credentials(paths)
    value = token.strip()
    if value:
        connections[normalized] = value
    else:
        connections.pop(normalized, None)
    atomic_write_json(
        paths.compute_credentials_file,
        {"schema_version": CREDENTIAL_SCHEMA_VERSION, "connections": connections},
    )


def provider_for_config(
    config: Mapping[str, Any],
    paths: AppPaths = PATHS,
    *,
    host: str | None = None,
    token: str | None = None,
    provider_factory: Callable[..., OllamaProvider] | None = None,
) -> OllamaProvider:
    endpoint = endpoint_policy(host or str(config.get("ollama_host") or LOCAL_OLLAMA_ENDPOINT)).endpoint
    credential = compute_token(endpoint, paths) if token is None else token.strip()
    factory = provider_factory or OllamaProvider
    return factory(endpoint, credential) if credential else factory(endpoint)


def unknown_remote_hardware(endpoint: str) -> HardwareProfile:
    hostname = urlsplit(endpoint_policy(endpoint).endpoint).hostname or "remote"
    return HardwareProfile(
        os_name="remote",
        architecture="unknown",
        cpu_name=f"Remote compute at {hostname}",
        physical_cores=1,
        logical_cores=1,
        memory_total_bytes=0,
        memory_available_bytes=0,
    )


def probe_compute(
    provider: OllamaProvider,
    *,
    include_models: bool = True,
    local_hardware: HardwareProfile | None = None,
) -> ComputeProbe:
    started = time.monotonic()
    info: dict[str, Any] = {}
    info_request = getattr(provider, "compute_info", None)
    if callable(info_request):
        try:
            candidate = info_request()
            if candidate.get("service") in {BRIDGE_SERVICE, LEGACY_BRIDGE_SERVICE}:
                try:
                    protocol_version = int(candidate.get("protocol_version") or 0)
                except (TypeError, ValueError) as exc:
                    raise ComputeError("compute bridge returned an invalid protocol version") from exc
                if protocol_version != 1:
                    raise ComputeError(
                        f"compute bridge protocol {protocol_version} is not supported by this Dairack build"
                    )
                info = candidate
        except OllamaError as exc:
            if exc.status_code not in {404, 405}:
                raise

    version = provider.version()
    models = tuple(provider.list_models()) if include_models else ()
    endpoint = provider.host
    policy = endpoint_policy(endpoint)
    bridge_hardware = info.get("hardware")
    if isinstance(bridge_hardware, Mapping):
        hardware = HardwareProfile.from_dict(dict(bridge_hardware))
        hardware_verified = True
    elif policy.local:
        hardware = local_hardware or detect_hardware()
        hardware_verified = True
    else:
        hardware = unknown_remote_hardware(endpoint)
        hardware_verified = False

    default_name = "Local Ollama" if policy.local else (urlsplit(endpoint).hostname or "Remote compute")
    name = str(info.get("node_name") or default_name).strip()[:80] or default_name
    return ComputeProbe(
        endpoint=endpoint,
        name=name,
        ollama_version=version,
        bridge_version=str(info.get("dairack_version") or info.get("asusai_version") or ""),
        transport="bridge" if info else "ollama",
        hardware=hardware,
        hardware_verified=hardware_verified,
        models=models,
        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
    )


def apply_compute_probe(config: dict[str, Any], probe: ComputeProbe, *, name: str = "") -> None:
    config["ollama_host"] = probe.endpoint
    config["compute_mode"] = "local" if probe.local else "remote"
    config["compute_name"] = name.strip()[:80] if name.strip() else probe.name
    config["compute_transport"] = probe.transport
    config["compute_hardware_verified"] = probe.hardware_verified
    config["compute_verified_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not probe.local:
        config["remote_ollama_host"] = probe.endpoint


def local_client_name() -> str:
    return platform.node().strip() or "Dairack compute"
