"""Cross-platform hardware discovery and conservative Ollama tuning."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

GIB = 1024**3
MIB = 1024**2


def _run(command: Sequence[str], timeout: float = 4.0) -> str:
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _memory_bytes() -> tuple[int, int]:
    if Path("/proc/meminfo").exists():
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, raw = line.split(":", 1)
                match = re.search(r"\d+", raw)
                if match:
                    values[key] = int(match.group(0)) * 1024
        except (OSError, ValueError):
            pass
        if values:
            return values.get("MemTotal", 0), values.get("MemAvailable", values.get("MemFree", 0))
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical), int(status.available_physical)
        except (AttributeError, OSError, TypeError):
            pass
    if platform.system() == "Darwin":
        total_raw = _run(["sysctl", "-n", "hw.memsize"])
        total = int(total_raw) if total_raw.isdigit() else 0
        vm_stat = _run(["vm_stat"])
        page_match = re.search(r"page size of (\d+) bytes", vm_stat)
        page_size = int(page_match.group(1)) if page_match else 4096
        available_pages = 0
        for label in ("Pages free", "Pages inactive", "Pages speculative"):
            match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.", vm_stat, re.MULTILINE)
            if match:
                available_pages += int(match.group(1))
        available = available_pages * page_size
        if total:
            return total, min(total, available or total)
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total = page_size * os.sysconf("SC_PHYS_PAGES")
        available = page_size * os.sysconf("SC_AVPHYS_PAGES")
        return int(total), int(available)
    except (OSError, ValueError):
        return 0, 0


def _cpu_details() -> tuple[str, int, int]:
    logical = os.cpu_count() or 1
    model = platform.processor().strip() or platform.machine()
    physical = 0
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cores: set[tuple[str, str]] = set()
        physical_id = "0"
        core_id = ""
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines() + [""]:
                if not line.strip():
                    if core_id:
                        cores.add((physical_id, core_id))
                    physical_id, core_id = "0", ""
                    continue
                key, _, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if key in {"model name", "Hardware"} and value:
                    model = value
                elif key == "physical id":
                    physical_id = value
                elif key == "core id":
                    core_id = value
            physical = len(cores)
        except OSError:
            pass
    if not physical and platform.system() == "Darwin":
        raw = _run(["sysctl", "-n", "hw.physicalcpu"])
        physical = int(raw) if raw.isdigit() else 0
        model = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or model
    if platform.system() == "Windows":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            raw = _run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,NumberOfCores | "
                        "ConvertTo-Json -Compress"
                    ),
                ]
            )
            try:
                processor = json.loads(raw)
            except json.JSONDecodeError:
                processor = {}
            if isinstance(processor, dict):
                model = str(processor.get("Name") or model).strip()
                try:
                    physical = max(1, int(processor.get("NumberOfCores") or physical))
                except (TypeError, ValueError):
                    pass
    return model, max(1, physical or max(1, logical // 2)), logical


@dataclass(frozen=True, slots=True)
class Accelerator:
    backend: str
    name: str
    memory_bytes: int = 0
    index: int = 0
    driver: str = ""
    compute_capability: str = ""
    unified_memory: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Accelerator":
        return cls(
            backend=str(value.get("backend") or "unknown"),
            name=str(value.get("name") or "unknown accelerator"),
            memory_bytes=int(value.get("memory_bytes") or 0),
            index=int(value.get("index") or 0),
            driver=str(value.get("driver") or ""),
            compute_capability=str(value.get("compute_capability") or ""),
            unified_memory=bool(value.get("unified_memory")),
        )


def _nvidia_accelerators() -> list[Accelerator]:
    if not shutil.which("nvidia-smi"):
        return []
    output = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    devices: list[Accelerator] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 4:
            continue
        try:
            index = int(fields[0])
            memory = int(float(fields[2])) * MIB
        except ValueError:
            continue
        devices.append(
            Accelerator(
                backend="cuda",
                name=fields[1],
                memory_bytes=memory,
                index=index,
                driver=fields[3],
                compute_capability=fields[4] if len(fields) > 4 else "",
            )
        )
    return devices


def _amd_accelerators() -> list[Accelerator]:
    if not shutil.which("rocm-smi"):
        return []
    output = _run(["rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"], timeout=6)
    if not output:
        return []
    try:
        raw = json.loads(output)
    except json.JSONDecodeError:
        return []
    devices: list[Accelerator] = []
    for index, (card, values) in enumerate(raw.items() if isinstance(raw, dict) else []):
        if not isinstance(values, dict):
            continue
        name = next((str(value) for key, value in values.items() if "Card series" in key), str(card))
        memory = 0
        for key, value in values.items():
            if "VRAM Total Memory" not in key:
                continue
            match = re.search(r"\d+", str(value))
            memory = int(match.group(0)) if match else 0
        devices.append(Accelerator("rocm", name, memory, index=index))
    return devices


def _apple_accelerators(total_memory: int) -> list[Accelerator]:
    if platform.system() != "Darwin":
        return []
    output = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=8)
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError:
        payload = {}
    displays = payload.get("SPDisplaysDataType") if isinstance(payload, dict) else []
    devices = []
    for index, display in enumerate(displays if isinstance(displays, list) else []):
        if not isinstance(display, dict):
            continue
        name = str(display.get("sppci_model") or display.get("_name") or "Apple GPU")
        devices.append(Accelerator("metal", name, total_memory, index=index, unified_memory=True))
    return devices or [Accelerator("metal", "Apple integrated GPU", total_memory, unified_memory=True)]


def _windows_accelerators() -> list[Accelerator]:
    if platform.system() != "Windows":
        return []
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        return []
    output = _run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | "
                "ConvertTo-Json -Compress"
            ),
        ],
        timeout=8,
    )
    try:
        raw = json.loads(output) if output else []
    except json.JSONDecodeError:
        return []
    devices = raw if isinstance(raw, list) else [raw]
    result: list[Accelerator] = []
    for index, item in enumerate(devices):
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "Windows GPU")
        lowered = name.lower()
        if not any(vendor in lowered for vendor in ("amd", "radeon", "nvidia", "intel", "arc")):
            continue
        try:
            memory = max(0, int(item.get("AdapterRAM") or 0))
        except (TypeError, ValueError):
            memory = 0
        if memory >= 0xFFF00000:
            memory = 0
        result.append(
            Accelerator(
                backend="windows-gpu",
                name=name,
                memory_bytes=memory,
                index=index,
                driver=str(item.get("DriverVersion") or ""),
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    os_name: str
    architecture: str
    cpu_name: str
    physical_cores: int
    logical_cores: int
    memory_total_bytes: int
    memory_available_bytes: int
    accelerators: tuple[Accelerator, ...] = field(default_factory=tuple)

    @property
    def accelerator_memory_bytes(self) -> int:
        return max((device.memory_bytes for device in self.accelerators), default=0)

    @property
    def primary_accelerator(self) -> Accelerator | None:
        return max(self.accelerators, key=lambda item: item.memory_bytes, default=None)

    @property
    def fingerprint(self) -> str:
        stable = {
            "architecture": self.architecture,
            "cpu": self.cpu_name,
            "cores": self.physical_cores,
            "memory": self.memory_total_bytes,
            "accelerators": [asdict(item) for item in self.accelerators],
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fingerprint"] = self.fingerprint
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "HardwareProfile":
        return cls(
            os_name=str(value.get("os_name") or "unknown"),
            architecture=str(value.get("architecture") or "unknown"),
            cpu_name=str(value.get("cpu_name") or "unknown CPU"),
            physical_cores=max(1, int(value.get("physical_cores") or 1)),
            logical_cores=max(1, int(value.get("logical_cores") or 1)),
            memory_total_bytes=max(0, int(value.get("memory_total_bytes") or 0)),
            memory_available_bytes=max(0, int(value.get("memory_available_bytes") or 0)),
            accelerators=tuple(
                Accelerator.from_dict(item) for item in value.get("accelerators", []) if isinstance(item, dict)
            ),
        )


def detect_hardware() -> HardwareProfile:
    total, available = _memory_bytes()
    cpu_name, physical, logical = _cpu_details()
    accelerators = (
        _nvidia_accelerators() or _amd_accelerators() or _windows_accelerators() or _apple_accelerators(total)
    )
    return HardwareProfile(
        os_name=platform.system().lower(),
        architecture=platform.machine(),
        cpu_name=cpu_name,
        physical_cores=physical,
        logical_cores=logical,
        memory_total_bytes=total,
        memory_available_bytes=available,
        accelerators=tuple(accelerators),
    )


@dataclass(frozen=True, slots=True)
class RuntimeTuning:
    num_ctx: int
    num_batch: int
    num_thread: int
    fit: str
    rationale: str
    recommended: bool = True

    def to_profile(self) -> dict[str, Any]:
        options: dict[str, int] = {}
        if self.num_batch > 0:
            options["num_batch"] = self.num_batch
        if self.num_thread > 0:
            options["num_thread"] = self.num_thread
        return {
            "num_ctx": self.num_ctx,
            "think": False,
            "options": options,
            "fit": self.fit,
            "rationale": self.rationale,
            "recommended": self.recommended,
        }


def suggest_runtime(
    hardware: HardwareProfile,
    model_size_bytes: int,
    model_context: int | None = None,
) -> RuntimeTuning:
    size = max(1, model_size_bytes)
    vram = hardware.accelerator_memory_bytes
    ram = hardware.memory_total_bytes
    context_limit = max(512, int(model_context or 8192))
    safe_capacity = ram * 0.72 + vram * 0.55
    recommended = size <= max(vram * 0.82, safe_capacity)
    if not recommended:
        fit = "constrained"
        context = 2048
        batch = 64
        rationale = "model weights exceed the conservative local memory budget"
    elif vram and size <= vram * 0.82:
        fit = "accelerator"
        context = 16_384 if size <= vram * 0.58 and vram >= 12 * GIB else 8192
        batch = 512 if size <= vram * 0.58 else 384
        rationale = "model fits in accelerator memory with a safety reserve"
    elif vram:
        ratio = size / max(1, vram)
        fit = "hybrid"
        context = 8192 if ratio <= 1.8 and ram >= size * 1.35 else 6144 if ram >= size * 1.25 else 4096
        batch = 256 if ratio <= 1.5 else 192 if ratio <= 2.5 else 128
        rationale = "model requires accelerator offload plus system memory"
    else:
        fit = "cpu"
        context = 6144 if ram >= size * 1.6 else 4096
        batch = 128
        rationale = "no supported accelerator was detected"
    context = max(512, min(context, context_limit))
    threads = max(1, min(hardware.physical_cores, 16))
    return RuntimeTuning(context, batch, threads, fit, rationale, recommended)


def suggest_remote_runtime(model_context: int | None = None) -> RuntimeTuning:
    """Use conservative context while leaving backend-owned execution knobs untouched."""
    context_limit = max(512, int(model_context or 8192))
    return RuntimeTuning(
        num_ctx=min(8192, context_limit),
        num_batch=0,
        num_thread=0,
        fit="remote-unverified",
        rationale="remote hardware was not reported; Ollama owns placement, batching, and thread selection",
        recommended=True,
    )


def format_hardware(profile: HardwareProfile) -> str:
    lines = [
        f"CPU: {profile.cpu_name} | {profile.physical_cores} cores / {profile.logical_cores} threads",
        f"Memory: {profile.memory_total_bytes / GIB:.1f} GiB total | {profile.memory_available_bytes / GIB:.1f} GiB available",
    ]
    if profile.accelerators:
        for device in profile.accelerators:
            memory = f"{device.memory_bytes / GIB:.1f} GiB" if device.memory_bytes else "memory unknown"
            lines.append(f"Accelerator {device.index}: {device.name} | {device.backend} | {memory}")
    else:
        lines.append("Accelerator: none detected")
    lines.append(f"Hardware fingerprint: {profile.fingerprint}")
    return "\n".join(lines)
