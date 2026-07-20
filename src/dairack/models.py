"""Provider-neutral model metadata, capability inference, and user overrides."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import atomic_write_json
from .hardware import HardwareProfile, RuntimeTuning, detect_hardware, suggest_remote_runtime, suggest_runtime
from .paths import PATHS, AppPaths

MODEL_REGISTRY_SCHEMA = 2
CAPABILITY_NAMES = ("code", "agent", "reasoning", "general", "research", "vision", "efficiency")


def parse_parameter_billions(value: str | int | float | None) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([BMK]?)", str(value or ""), re.IGNORECASE)
    if not match:
        return 0.0
    number = float(match.group(1))
    suffix = match.group(2).upper()
    return number * {"": 1.0, "K": 0.000001, "M": 0.001, "B": 1.0}[suffix]


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    name: str
    size: int = 0
    parameter_size: str = ""
    quantization: str = ""
    context_length: int = 0
    family: str = ""
    architecture: str = ""
    digest: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def params(self) -> str:
        return self.parameter_size

    @property
    def quant(self) -> str:
        return self.quantization

    @property
    def context(self) -> int:
        return self.context_length

    @property
    def parameter_billions(self) -> float:
        return parse_parameter_billions(self.parameter_size)

    def label(self) -> str:
        parts = [self.name]
        if self.size:
            parts.append(f"{self.size / 1024**3:.1f} GB")
        if self.parameter_size:
            parts.append(self.parameter_size)
        if self.quantization:
            parts.append(self.quantization)
        if self.context_length:
            parts.append(f"ctx {self.context_length}")
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelDescriptor":
        return cls(
            name=str(value.get("name") or ""),
            size=max(0, int(value.get("size") or 0)),
            parameter_size=str(value.get("parameter_size") or value.get("params") or ""),
            quantization=str(value.get("quantization") or value.get("quant") or ""),
            context_length=max(0, int(value.get("context_length") or value.get("context") or 0)),
            family=str(value.get("family") or ""),
            architecture=str(value.get("architecture") or ""),
            digest=str(value.get("digest") or ""),
            capabilities=tuple(str(item).lower() for item in value.get("capabilities", []) if str(item)),
        )

    @classmethod
    def from_provider_model(cls, model: Any) -> "ModelDescriptor":
        if isinstance(model, cls):
            return model
        details = getattr(model, "details", None)
        details = details if isinstance(details, Mapping) else {}
        return cls(
            name=str(getattr(model, "name", model) or ""),
            size=max(0, int(getattr(model, "size", 0) or 0)),
            parameter_size=str(getattr(model, "params", "") or details.get("parameter_size") or ""),
            quantization=str(getattr(model, "quant", "") or details.get("quantization_level") or ""),
            context_length=max(0, int(getattr(model, "context", 0) or details.get("context_length") or 0)),
            family=str(getattr(model, "family", "") or details.get("family") or ""),
            architecture=str(getattr(model, "architecture", "") or ""),
            digest=str(getattr(model, "digest", "") or ""),
            capabilities=tuple(str(item).lower() for item in (getattr(model, "capabilities", ()) or ()) if str(item)),
        )


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    code: float
    agent: float
    reasoning: float
    general: float
    research: float
    vision: float
    efficiency: float
    confidence: float = 0.55
    source: str = "inferred"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityProfile":
        fields = {key: max(0.0, min(1.0, float(value.get(key, 0.0)))) for key in CAPABILITY_NAMES}
        return cls(
            **fields,
            confidence=max(0.0, min(1.0, float(value.get("confidence", 0.55)))),
            source=str(value.get("source") or "configured"),
        )


def _capacity_scale(parameters_billions: float, size_bytes: int) -> float:
    estimate = parameters_billions or max(1.0, size_bytes / 1024**3 * 1.65)
    return max(0.0, min(1.0, math.log2(estimate + 1.0) / math.log2(72.0)))


def _efficiency_score(model: ModelDescriptor, hardware: HardwareProfile) -> float:
    size = max(1, model.size)
    vram = hardware.accelerator_memory_bytes
    if vram:
        ratio = size / max(1, vram)
        if ratio <= 0.58:
            return 0.98
        if ratio <= 0.84:
            return 0.90
        if ratio <= 1.25:
            return 0.76
        if ratio <= 1.9:
            return 0.62
        if ratio <= 2.7:
            return 0.50
        return 0.38
    ram_ratio = size / max(1, hardware.memory_total_bytes)
    return max(0.22, min(0.72, 0.68 - ram_ratio * 0.45))


def _relative_efficiency_score(model: ModelDescriptor) -> float:
    parameters = model.parameter_billions or max(1.0, model.size / 1024**3 * 1.65)
    return max(0.28, min(0.94, 1.02 - math.log2(parameters + 1.0) * 0.12))


def infer_capabilities(
    model: ModelDescriptor,
    hardware: HardwareProfile,
    *,
    hardware_verified: bool = True,
) -> CapabilityProfile:
    features = {item.lower() for item in model.capabilities}
    scale = _capacity_scale(model.parameter_billions, model.size)
    name_tokens = set(re.findall(r"[a-z0-9]+", f"{model.name} {model.family} {model.architecture}".lower()))
    tool_support = "tools" in features
    thinking_support = "thinking" in features
    vision_support = "vision" in features
    code_hint = bool(name_tokens & {"code", "coder", "coding", "programmer"})
    reasoning_hint = bool(name_tokens & {"reason", "reasoning", "math", "r1"})
    code = 0.46 + scale * 0.32 + (0.10 if tool_support else 0.0) + (0.10 if code_hint else 0.0)
    agent = 0.36 + scale * 0.22 + (0.28 if tool_support else 0.0) + (0.05 if code_hint else 0.0)
    reasoning = 0.48 + scale * 0.39 + (0.08 if thinking_support else 0.0) + (0.08 if reasoning_hint else 0.0)
    general = 0.60 + scale * 0.38
    research = 0.48 + scale * 0.37 + (0.03 if tool_support else 0.0)
    # API capabilities prove modality support, not benchmark leadership. Keep
    # vision competitive while allowing larger general/reasoning models to rank
    # above smaller multimodal models when image quality demand is high.
    vision = 0.72 + scale * 0.16 if vision_support else 0.02
    return CapabilityProfile(
        code=min(0.99, code),
        agent=min(0.99, agent),
        reasoning=min(0.99, reasoning),
        general=min(0.99, general),
        research=min(0.96, research),
        vision=vision,
        efficiency=_efficiency_score(model, hardware) if hardware_verified else _relative_efficiency_score(model),
        confidence=0.72 if model.parameter_size and features else 0.52,
        source="provider metadata + hardware fit" if hardware_verified else "provider metadata + relative model cost",
    )


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass(slots=True)
class ModelRecord:
    descriptor: ModelDescriptor
    capability: CapabilityProfile
    runtime: RuntimeTuning
    role: str
    override: dict[str, Any] = field(default_factory=dict)

    def effective_capability(self) -> CapabilityProfile:
        configured = self.override.get("capabilities")
        if not isinstance(configured, Mapping):
            return self.capability
        return CapabilityProfile.from_dict(_deep_merge(self.capability.to_dict(), configured))

    def effective_runtime(self) -> dict[str, Any]:
        return _deep_merge(self.runtime.to_profile(), self.override.get("runtime", {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.to_dict(),
            "capabilities": self.capability.to_dict(),
            "runtime": self.runtime.to_profile(),
            "role": self.role,
            "override": self.override,
        }


def infer_role(capability: CapabilityProfile) -> str:
    ranked = sorted(
        ((name, getattr(capability, name)) for name in ("code", "agent", "reasoning", "general", "vision")),
        key=lambda item: item[1],
        reverse=True,
    )
    labels = {
        "code": "coding",
        "agent": "agent work",
        "reasoning": "deep reasoning",
        "general": "general intelligence",
        "vision": "visual analysis",
    }
    primary, secondary = ranked[0][0], ranked[1][0]
    return f"{labels[primary]} / {labels[secondary]}"


@dataclass(slots=True)
class ModelRegistry:
    hardware_fingerprint: str
    models: dict[str, ModelRecord]
    generated_at: str
    compute_endpoint: str = ""
    hardware_verified: bool = True
    schema_version: int = MODEL_REGISTRY_SCHEMA

    @classmethod
    def discover(
        cls,
        models: Iterable[Any],
        hardware: HardwareProfile,
        overrides: Mapping[str, Any] | None = None,
        *,
        compute_endpoint: str = "",
        hardware_verified: bool = True,
    ) -> "ModelRegistry":
        configured = overrides if isinstance(overrides, Mapping) else {}
        records: dict[str, ModelRecord] = {}
        for raw in models:
            descriptor = ModelDescriptor.from_provider_model(raw)
            if not descriptor.name:
                continue
            capability = infer_capabilities(descriptor, hardware, hardware_verified=hardware_verified)
            tuning = (
                suggest_runtime(hardware, descriptor.size, descriptor.context_length)
                if hardware_verified
                else suggest_remote_runtime(descriptor.context_length)
            )
            override = configured.get(descriptor.name, {})
            override = dict(override) if isinstance(override, Mapping) else {}
            records[descriptor.name] = ModelRecord(
                descriptor=descriptor,
                capability=capability,
                runtime=tuning,
                role=infer_role(capability),
                override=override,
            )
        return cls(
            hardware_fingerprint=hardware.fingerprint,
            models=records,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            compute_endpoint=compute_endpoint,
            hardware_verified=hardware_verified,
        )

    def default_model(self) -> str:
        if not self.models:
            return ""
        recommended = [record for record in self.models.values() if record.runtime.fit != "constrained"]
        candidates = recommended or list(self.models.values())
        return max(
            candidates,
            key=lambda record: (
                record.effective_capability().general * 0.40
                + record.effective_capability().reasoning * 0.25
                + record.effective_capability().agent * 0.15
                + record.effective_capability().efficiency * 0.20
            ),
        ).descriptor.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "hardware_fingerprint": self.hardware_fingerprint,
            "compute_endpoint": self.compute_endpoint,
            "hardware_verified": self.hardware_verified,
            "models": {name: record.to_dict() for name, record in sorted(self.models.items())},
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelRegistry":
        records: dict[str, ModelRecord] = {}
        raw_models = value.get("models")
        for name, raw in raw_models.items() if isinstance(raw_models, Mapping) else []:
            if not isinstance(raw, Mapping):
                continue
            descriptor = ModelDescriptor.from_dict(raw.get("descriptor", {}))
            if not descriptor.name:
                descriptor = ModelDescriptor(name=str(name))
            capability = CapabilityProfile.from_dict(raw.get("capabilities", {}))
            runtime_raw = raw.get("runtime") if isinstance(raw.get("runtime"), Mapping) else {}
            options = runtime_raw.get("options") if isinstance(runtime_raw.get("options"), Mapping) else {}
            batch = int(options["num_batch"]) if "num_batch" in options else 128
            threads = int(options["num_thread"]) if "num_thread" in options else 1
            tuning = RuntimeTuning(
                num_ctx=max(512, int(runtime_raw.get("num_ctx") or 4096)),
                num_batch=max(0, batch),
                num_thread=max(0, threads),
                fit=str(runtime_raw.get("fit") or "unknown"),
                rationale=str(runtime_raw.get("rationale") or "loaded from registry"),
                recommended=bool(runtime_raw.get("recommended", True)),
            )
            override = raw.get("override") if isinstance(raw.get("override"), Mapping) else {}
            records[str(name)] = ModelRecord(
                descriptor=descriptor,
                capability=capability,
                runtime=tuning,
                role=str(raw.get("role") or infer_role(capability)),
                override=dict(override),
            )
        return cls(
            hardware_fingerprint=str(value.get("hardware_fingerprint") or ""),
            models=records,
            generated_at=str(value.get("generated_at") or ""),
            compute_endpoint=str(value.get("compute_endpoint") or ""),
            hardware_verified=bool(value.get("hardware_verified", True)),
            schema_version=int(value.get("schema_version") or MODEL_REGISTRY_SCHEMA),
        )


_REGISTRY_CACHE: tuple[Path, int, ModelRegistry] | None = None


def load_registry(paths: AppPaths = PATHS) -> ModelRegistry | None:
    global _REGISTRY_CACHE
    path = paths.model_registry_file
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        return None
    if _REGISTRY_CACHE and _REGISTRY_CACHE[0] == path and _REGISTRY_CACHE[1] == modified:
        return _REGISTRY_CACHE[2]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        registry = ModelRegistry.from_dict(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    _REGISTRY_CACHE = (path, modified, registry)
    return registry


def save_registry(registry: ModelRegistry, paths: AppPaths = PATHS) -> None:
    global _REGISTRY_CACHE
    atomic_write_json(paths.model_registry_file, registry.to_dict())
    _REGISTRY_CACHE = None


def set_capability_override(model: str, field: str, value: float, paths: AppPaths = PATHS) -> str:
    normalized = field.lower().replace("-", "_")
    if normalized not in CAPABILITY_NAMES:
        raise ValueError(f"unknown capability {field}; choose {', '.join(CAPABILITY_NAMES)}")
    score = float(value)
    if not 0.0 <= score <= 1.0:
        raise ValueError("capability values must be between 0.0 and 1.0")
    registry = load_registry(paths)
    if not registry:
        raise ValueError("model registry is not initialized")
    matches = [record for name, record in registry.models.items() if name.lower() == model.lower()]
    if len(matches) != 1:
        raise ValueError(f"model not found: {model}")
    record = matches[0]
    configured = record.override.setdefault("capabilities", {})
    configured[normalized] = score
    configured["confidence"] = max(0.85, float(configured.get("confidence") or record.capability.confidence))
    source = str(configured.get("source") or record.capability.source)
    configured["source"] = source if "user calibration" in source else "user calibration + " + source
    save_registry(registry, paths)
    return record.descriptor.name


def clear_capability_overrides(model: str, paths: AppPaths = PATHS) -> bool:
    registry = load_registry(paths)
    if not registry:
        return False
    record = next((item for name, item in registry.models.items() if name.lower() == model.lower()), None)
    if not record or "capabilities" not in record.override:
        return False
    del record.override["capabilities"]
    save_registry(registry, paths)
    return True


def load_hardware(paths: AppPaths = PATHS) -> HardwareProfile:
    try:
        raw = json.loads(paths.hardware_file.read_text(encoding="utf-8"))
        return HardwareProfile.from_dict(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return detect_hardware()


def record_for(model: Any, paths: AppPaths = PATHS) -> ModelRecord:
    descriptor = ModelDescriptor.from_provider_model(model)
    registry = load_registry(paths)
    if registry and descriptor.name in registry.models:
        return registry.models[descriptor.name]
    hardware = load_hardware(paths)
    hardware_verified = registry.hardware_verified if registry else True
    capability = infer_capabilities(descriptor, hardware, hardware_verified=hardware_verified)
    return ModelRecord(
        descriptor=descriptor,
        capability=capability,
        runtime=(
            suggest_runtime(hardware, descriptor.size, descriptor.context_length)
            if hardware_verified
            else suggest_remote_runtime(descriptor.context_length)
        ),
        role=infer_role(capability),
    )


def capabilities_for(model: Any, paths: AppPaths = PATHS) -> dict[str, float]:
    return {name: float(getattr(record_for(model, paths).effective_capability(), name)) for name in CAPABILITY_NAMES}


def capability_metadata_for(model: Any, paths: AppPaths = PATHS) -> dict[str, Any]:
    profile = record_for(model, paths).effective_capability()
    return {
        "confidence": float(profile.confidence),
        "source": str(profile.source),
    }


def runtime_profile_for(model: Any, paths: AppPaths = PATHS) -> dict[str, Any]:
    record = record_for(model, paths)
    profile = record.effective_runtime()
    return {
        "match": record.descriptor.name,
        "name": record.descriptor.name,
        "role": record.role,
        "num_ctx": int(profile.get("num_ctx") or 4096),
        "think": bool(profile.get("think", False)),
        "options": dict(profile.get("options") or {}),
        "fit": str(profile.get("fit") or "unknown"),
        "rationale": str(profile.get("rationale") or "automatic hardware profile"),
    }


def runtime_override_for(model: str, paths: AppPaths = PATHS) -> dict[str, Any]:
    """Return a runtime override in the compatibility config shape."""
    registry = load_registry(paths)
    if not registry:
        return {}
    record = next((item for name, item in registry.models.items() if name.lower() == model.lower()), None)
    if not record:
        return {}
    runtime = record.override.get("runtime")
    if not isinstance(runtime, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("num_ctx", "think"):
        if key in runtime:
            result[key] = runtime[key]
    options = runtime.get("options")
    if isinstance(options, Mapping) and options:
        result["model_options"] = dict(options)
    return result


def save_runtime_override(model: str, value: Mapping[str, Any], paths: AppPaths = PATHS) -> bool:
    registry = load_registry(paths)
    if not registry:
        return False
    record = next((item for name, item in registry.models.items() if name.lower() == model.lower()), None)
    if not record:
        return False
    runtime = record.override.setdefault("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
        record.override["runtime"] = runtime
    for key in ("num_ctx", "think"):
        if key in value:
            runtime[key] = value[key]
    options = value.get("model_options")
    if isinstance(options, Mapping):
        configured = runtime.setdefault("options", {})
        if not isinstance(configured, dict):
            configured = {}
            runtime["options"] = configured
        configured.update(options)
    save_registry(registry, paths)
    return True


def clear_runtime_override(model: str, paths: AppPaths = PATHS) -> bool:
    registry = load_registry(paths)
    if not registry:
        return False
    record = next((item for name, item in registry.models.items() if name.lower() == model.lower()), None)
    if not record or "runtime" not in record.override:
        return False
    del record.override["runtime"]
    save_registry(registry, paths)
    return True
