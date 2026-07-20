"""First-run initialization and environment diagnostics."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .catalog import apply_catalog_priors
from .compute import apply_compute_probe, is_local_endpoint, probe_compute, provider_for_config
from .config import ConfigError, atomic_write_json, load_config, save_config
from .hardware import HardwareProfile, detect_hardware, format_hardware
from .models import ModelRegistry, load_registry, save_registry
from .paths import PATHS, AppPaths
from .providers.ollama import OllamaError, OllamaProvider


def _existing_overrides(paths: AppPaths, config: Mapping[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    current = load_registry(paths)
    if current:
        overrides.update({name: dict(record.override) for name, record in current.models.items() if record.override})
    legacy = config.get("profile_overrides")
    if isinstance(legacy, Mapping):
        for name, raw in legacy.items():
            if not isinstance(raw, Mapping):
                continue
            runtime: dict[str, Any] = {}
            if "num_ctx" in raw:
                runtime["num_ctx"] = raw["num_ctx"]
            if "think" in raw:
                runtime["think"] = raw["think"]
            if isinstance(raw.get("model_options"), Mapping):
                runtime["options"] = dict(raw["model_options"])
            target = overrides.setdefault(str(name), {})
            if "runtime" not in target:
                target["runtime"] = runtime
    return overrides


@dataclass(slots=True)
class InitializationResult:
    hardware: HardwareProfile
    registry: ModelRegistry
    config: dict[str, Any]
    ollama_version: str
    paths: AppPaths

    def report(self) -> str:
        verified = "VERIFIED" if self.registry.hardware_verified else "UNVERIFIED / BACKEND MANAGED"
        lines = [
            "DAIRACK INITIALIZATION",
            "",
            f"COMPUTE HARDWARE / {verified}",
            format_hardware(self.hardware),
            "",
            "COMPUTE MODELS",
        ]
        if not self.registry.models:
            lines.append(
                "No models are installed at the active compute endpoint. Use `dairack models install <model>`."
            )
        for record in self.registry.models.values():
            runtime = record.effective_runtime()
            capability = record.effective_capability()
            options = runtime.get("options") if isinstance(runtime.get("options"), Mapping) else {}
            lines.extend(
                [
                    f"{record.descriptor.name}",
                    f"  {record.role} | {runtime.get('fit', 'unknown')} fit",
                    (
                        f"  ctx {runtime.get('num_ctx')} | batch {options.get('num_batch', 'auto')} | "
                        f"threads {options.get('num_thread', 'auto')} | efficiency {capability.efficiency * 100:.0f}%"
                    ),
                ]
            )
        lines.extend(
            [
                "",
                f"Default model: {self.config.get('model') or '(none)'}",
                f"Coordinator: {self.config.get('orchestrator_policy', 'adaptive')}",
                f"Compute: {self.config.get('compute_name')} / {self.config.get('compute_transport')}",
                f"Ollama: {self.ollama_version} at {self.config.get('ollama_host')}",
                f"Configuration: {self.paths.config_file}",
            ]
        )
        return "\n".join(lines)


def initialize(
    paths: AppPaths = PATHS,
    host: str | None = None,
    write: bool = True,
) -> InitializationResult:
    try:
        config = load_config(paths)
    except ConfigError:
        raise
    if host:
        config["ollama_host"] = host
    provider = provider_for_config(config, paths, host=host, provider_factory=OllamaProvider)
    local_hardware = detect_hardware() if is_local_endpoint(provider.host) else None
    probe = probe_compute(provider, include_models=True, local_hardware=local_hardware)
    apply_compute_probe(config, probe)
    version = probe.ollama_version
    hardware = probe.hardware
    registry = ModelRegistry.discover(
        probe.models,
        hardware,
        _existing_overrides(paths, config),
        compute_endpoint=probe.endpoint,
        hardware_verified=probe.hardware_verified,
    )
    apply_catalog_priors(registry)
    config["profile_overrides"] = {}
    installed_names = set(registry.models)
    if not config.get("model") or str(config["model"]) not in installed_names:
        config["model"] = registry.default_model()
    selected = registry.models.get(str(config.get("model") or ""))
    if selected:
        runtime = selected.effective_runtime()
        config["num_ctx"] = int(runtime.get("num_ctx") or config.get("num_ctx") or 4096)
        config["think"] = bool(runtime.get("think", False))
        config["model_options"] = dict(runtime.get("options") or {})
    config["permission_mode"] = str(config.get("permission_mode") or "ask")
    if write:
        paths.ensure()
        atomic_write_json(paths.hardware_file, hardware.to_dict())
        save_registry(registry, paths)
        config = save_config(config, paths)
    return InitializationResult(hardware, registry, config, version, paths)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str
    required: bool = True

    @property
    def passed(self) -> bool:
        return self.status in {"pass", "warn"}


@dataclass(slots=True)
class DoctorReport:
    checks: list[Check]

    @property
    def healthy(self) -> bool:
        return all(check.passed or not check.required for check in self.checks)

    def render(self) -> str:
        labels = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
        lines = ["DAIRACK DOCTOR", ""]
        for check in self.checks:
            lines.append(f"{labels.get(check.status, check.status.upper()):4}  {check.name:18} {check.detail}")
        lines.extend(["", "Ready." if self.healthy else "Required checks failed."])
        return "\n".join(lines)


def _path_check(name: str, path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        return Check(name, "fail", f"not writable: {exc}")
    return Check(name, "pass", str(path))


def doctor(paths: AppPaths = PATHS, host: str | None = None) -> DoctorReport:
    checks: list[Check] = []
    checks.append(
        Check(
            "Python",
            "pass" if sys.version_info >= (3, 11) else "fail",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        )
    )
    for name, path in (
        ("Config path", paths.config_dir),
        ("Data path", paths.data_dir),
        ("Cache path", paths.cache_dir),
    ):
        checks.append(_path_check(name, path))
    try:
        config = load_config(paths)
        checks.append(Check("Configuration", "pass", str(paths.config_file)))
    except ConfigError as exc:
        config = {"ollama_host": host or "127.0.0.1:11434"}
        checks.append(Check("Configuration", "fail", str(exc)))
    provider = provider_for_config(config, paths, host=host, provider_factory=OllamaProvider)
    compute_hardware: HardwareProfile | None = None
    try:
        local_hardware = detect_hardware() if is_local_endpoint(provider.host) else None
        probe = probe_compute(provider, include_models=True, local_hardware=local_hardware)
        compute_hardware = probe.hardware
        endpoint_detail = f"{probe.name} / {probe.transport} / {probe.latency_ms} ms"
        if not probe.hardware_verified:
            endpoint_detail += " / hardware not reported"
        checks.append(Check("Compute endpoint", "pass", endpoint_detail))
        checks.append(Check("Ollama", "pass", f"{probe.ollama_version} at {provider.host}"))
        models = probe.models
        checks.append(
            Check(
                "Compute models",
                "pass" if models else "warn",
                f"{len(models)} installed" if models else "none installed; use `dairack models install <model>`",
                required=False,
            )
        )
    except OllamaError as exc:
        checks.append(Check("Ollama", "fail", str(exc)))
    hardware = detect_hardware()
    accelerator = hardware.primary_accelerator
    checks.append(
        Check(
            "Client hardware",
            "pass" if hardware.memory_total_bytes else "warn",
            (
                f"{hardware.physical_cores} CPU cores, {hardware.memory_total_bytes / 1024**3:.1f} GiB RAM"
                + (f", {accelerator.name}" if accelerator else ", CPU inference")
            ),
            required=False,
        )
    )
    for module in ("textual", "prompt_toolkit"):
        checks.append(
            Check(
                module,
                "pass" if importlib.util.find_spec(module) else "fail",
                "available" if importlib.util.find_spec(module) else "missing Python dependency",
            )
        )
    for command, required in (("git", False), ("rg", False), ("patch", False)):
        location = shutil.which(command)
        checks.append(
            Check(
                command,
                "pass" if location else "warn" if not required else "fail",
                location or "not found on PATH",
                required=required,
            )
        )
    if not shutil.which("patch") and not shutil.which("git"):
        checks.append(
            Check(
                "Edit backend",
                "fail",
                "install Git or a compatible patch utility to apply agent edits",
            )
        )
    else:
        checks.append(
            Check(
                "Edit backend",
                "pass",
                "patch" if shutil.which("patch") else "Git apply fallback",
            )
        )
    if importlib.util.find_spec("ensurepip") is None and shutil.which("pipx") is None and shutil.which("uv") is None:
        checks.append(
            Check(
                "Installer support",
                "warn",
                "no pipx/uv/ensurepip; Debian users need python3-venv for source installs",
                required=False,
            )
        )
    registry = load_registry(paths)
    if (
        registry
        and compute_hardware
        and registry.hardware_verified
        and registry.hardware_fingerprint != compute_hardware.fingerprint
    ):
        checks.append(Check("Model registry", "warn", "compute hardware changed; run `dairack init`", required=False))
    elif registry:
        checks.append(Check("Model registry", "pass", f"{len(registry.models)} configured"))
    else:
        checks.append(Check("Model registry", "warn", "not initialized; run `dairack init`", required=False))
    return DoctorReport(checks)
