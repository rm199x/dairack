"""Authoritative client and compute machine identity."""

from __future__ import annotations

import functools
import json
import platform
from dataclasses import dataclass
from typing import Any, Mapping

from .hardware import GIB, HardwareProfile, detect_hardware
from .paths import PATHS, AppPaths


@dataclass(frozen=True, slots=True)
class MachineMap:
    client: HardwareProfile
    compute: HardwareProfile | None
    client_name: str
    compute_name: str
    compute_endpoint: str
    compute_transport: str
    compute_remote: bool
    compute_verified: bool


@functools.lru_cache(maxsize=1)
def client_hardware() -> HardwareProfile:
    """Probe stable client hardware once per process."""
    return detect_hardware()


def _saved_compute_hardware(paths: AppPaths) -> HardwareProfile | None:
    try:
        payload = json.loads(paths.hardware_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return HardwareProfile.from_dict(payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def machine_map(
    config: Mapping[str, Any],
    paths: AppPaths = PATHS,
    *,
    client: HardwareProfile | None = None,
) -> MachineMap:
    local = str(config.get("compute_mode") or "local") == "local"
    client_profile = client or client_hardware()
    verified = bool(config.get("compute_hardware_verified", local))
    compute = client_profile if local else _saved_compute_hardware(paths) if verified else None
    return MachineMap(
        client=client_profile,
        compute=compute,
        client_name=platform.node().strip() or "this client",
        compute_name=str(config.get("compute_name") or ("Local Ollama" if local else "Remote compute")),
        compute_endpoint=str(config.get("ollama_host") or ""),
        compute_transport=str(config.get("compute_transport") or "ollama"),
        compute_remote=not local,
        compute_verified=verified and compute is not None,
    )


def _profile_lines(profile: HardwareProfile, indent: str = "  ") -> list[str]:
    memory = f"{profile.memory_total_bytes / GIB:.1f} GiB" if profile.memory_total_bytes else "unknown"
    lines = [
        f"{indent}OS           {profile.os_name or 'unknown'} / {profile.architecture or 'unknown'}",
        f"{indent}CPU          {profile.cpu_name} / {profile.physical_cores} cores / {profile.logical_cores} threads",
        f"{indent}memory       {memory}",
    ]
    if profile.accelerators:
        for device in profile.accelerators:
            vram = f"{device.memory_bytes / GIB:.1f} GiB" if device.memory_bytes else "memory unknown"
            lines.append(f"{indent}accelerator  {device.name} / {device.backend} / {vram}")
    else:
        lines.append(f"{indent}accelerator  none detected")
    return lines


def hardware_status(config: Mapping[str, Any], paths: AppPaths = PATHS) -> str:
    machines = machine_map(config, paths)
    lines = ["CLIENT / ACTIONS", f"  name         {machines.client_name}", *_profile_lines(machines.client)]
    lines.extend(("", "COMPUTE / INFERENCE"))
    location = "remote" if machines.compute_remote else "local / same machine as client"
    verified = "verified" if machines.compute_verified else "unverified"
    lines.extend(
        (
            f"  name         {machines.compute_name}",
            f"  location     {location}",
            f"  transport    {machines.compute_transport} / {verified}",
            f"  endpoint     {machines.compute_endpoint}",
        )
    )
    if machines.compute is not None:
        lines.extend(_profile_lines(machines.compute))
    else:
        lines.append("  hardware     not reported by the active compute endpoint")
    lines.extend(
        (
            "",
            "BOUNDARY",
            "  Files, shell commands, approvals, chats, and checkpoints belong to CLIENT.",
            "  Model inference belongs to COMPUTE. Client tools cannot inspect a remote compute machine.",
        )
    )
    return "\n".join(lines)


def machine_prompt(config: Mapping[str, Any], paths: AppPaths = PATHS) -> str:
    machines = machine_map(config, paths)
    client = "\n".join(_profile_lines(machines.client, indent="    "))
    if machines.compute is not None:
        compute = "\n".join(_profile_lines(machines.compute, indent="    "))
    else:
        compute = "    hardware     not verified; do not infer it from the client"
    location = "REMOTE" if machines.compute_remote else "LOCAL / SAME MACHINE"
    verification = "VERIFIED" if machines.compute_verified else "UNVERIFIED"
    return (
        "Authoritative runtime machine map:\n"
        f"CLIENT / ACTIONS / {machines.client_name}\n{client}\n"
        f"COMPUTE / INFERENCE / {machines.compute_name} / {location} / "
        f"{machines.compute_transport.upper()} / {verification}\n{compute}\n"
        f"    endpoint     {machines.compute_endpoint}\n"
        "Interpret 'this computer' or 'my computer' as CLIENT unless the user explicitly asks about the server or "
        "compute machine. Interpret 'server', 'compute', or 'the machine running the model' as COMPUTE. Answer "
        "identity questions from these facts without running a command. Client-side shell and filesystem tools can "
        "inspect CLIENT only; they can never inspect a remote COMPUTE machine. Never substitute client results for "
        "server results."
    )
