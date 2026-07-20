"""Versioned, optional model recommendations kept separate from routing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Iterable, Mapping

from .hardware import GIB, HardwareProfile
from .models import CapabilityProfile, ModelRegistry


class CatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CatalogModel:
    name: str
    label: str
    download_gib: float
    minimum_ram_gib: float
    preferred_vram_gib: float
    capabilities: tuple[str, ...]
    role_scores: dict[str, float]
    source_url: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogModel":
        name = str(raw.get("name") or "").strip()
        if not name:
            raise CatalogError("catalog model is missing a name")
        scores = raw.get("role_scores")
        if not isinstance(scores, Mapping):
            raise CatalogError(f"catalog model {name} has no role scores")
        return cls(
            name=name,
            label=str(raw.get("label") or name),
            download_gib=max(0.0, float(raw.get("download_gib") or 0.0)),
            minimum_ram_gib=max(0.0, float(raw.get("minimum_ram_gib") or 0.0)),
            preferred_vram_gib=max(0.0, float(raw.get("preferred_vram_gib") or 0.0)),
            capabilities=tuple(str(value).lower() for value in raw.get("capabilities", []) if str(value)),
            role_scores={str(key): max(0.0, min(1.0, float(value))) for key, value in scores.items()},
            source_url=str(raw.get("source_url") or ""),
        )

    def supports_hardware(self, hardware: HardwareProfile) -> bool:
        ram_gib = hardware.memory_total_bytes / GIB
        if ram_gib and ram_gib < self.minimum_ram_gib:
            return False
        return True


@dataclass(frozen=True, slots=True)
class CatalogBundle:
    id: str
    label: str
    summary: str
    roles: tuple[str, ...]
    preferences: dict[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogBundle":
        identifier = str(raw.get("id") or "").strip()
        if not identifier:
            raise CatalogError("catalog bundle is missing an id")
        preferences = raw.get("preferences")
        if not isinstance(preferences, Mapping):
            preferences = {}
        return cls(
            id=identifier,
            label=str(raw.get("label") or identifier.title()),
            summary=str(raw.get("summary") or ""),
            roles=tuple(str(role) for role in raw.get("roles", []) if str(role)),
            preferences={
                str(role): tuple(str(name) for name in names if str(name))
                for role, names in preferences.items()
                if isinstance(names, list)
            },
        )


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    schema_version: int
    updated_at: str
    models: dict[str, CatalogModel]
    bundles: dict[str, CatalogBundle]

    def bundle(self, name: str) -> CatalogBundle:
        key = name.strip().lower()
        if key not in self.bundles:
            raise CatalogError(f"unknown setup profile {name}; choose {', '.join(self.bundles)}")
        return self.bundles[key]


@dataclass(frozen=True, slots=True)
class BundleRecommendation:
    bundle: CatalogBundle
    models: tuple[CatalogModel, ...]
    covered_roles: dict[str, str]
    missing_roles: tuple[str, ...]

    @property
    def download_gib(self) -> float:
        return sum(model.download_gib for model in self.models)

    @property
    def complete(self) -> bool:
        return not self.missing_roles


def load_catalog() -> ModelCatalog:
    resource = files("dairack").joinpath("data/model_catalog.json")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"could not load bundled model catalog: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise CatalogError("model catalog must contain an object")
    models = [CatalogModel.from_dict(item) for item in raw.get("models", []) if isinstance(item, Mapping)]
    bundles = [CatalogBundle.from_dict(item) for item in raw.get("bundles", []) if isinstance(item, Mapping)]
    if not models or not bundles:
        raise CatalogError("model catalog is empty")
    return ModelCatalog(
        schema_version=int(raw.get("schema_version") or 1),
        updated_at=str(raw.get("updated_at") or "unknown"),
        models={model.name: model for model in models},
        bundles={bundle.id: bundle for bundle in bundles},
    )


ROLE_CAPABILITIES = {
    "general": "general",
    "coding": "code",
    "agent": "agent",
    "reasoning": "reasoning",
    "vision": "vision",
}
ROLE_THRESHOLDS = {
    "general": 0.72,
    "coding": 0.82,
    "agent": 0.80,
    "reasoning": 0.82,
    "vision": 0.50,
}


def installed_role_coverage(registry: ModelRegistry | None) -> dict[str, str]:
    if not registry:
        return {}
    coverage: dict[str, tuple[str, float]] = {}
    for record in registry.models.values():
        capability = record.effective_capability()
        for role, field in ROLE_CAPABILITIES.items():
            score = float(getattr(capability, field))
            if score >= ROLE_THRESHOLDS[role] and score > coverage.get(role, ("", 0.0))[1]:
                coverage[role] = (record.descriptor.name, score)
    return {role: value[0] for role, value in coverage.items()}


def _candidate_for_role(
    role: str,
    bundle: CatalogBundle,
    catalog: ModelCatalog,
    hardware: HardwareProfile,
) -> CatalogModel | None:
    preferred = bundle.preferences.get(role, ())
    candidates = [catalog.models[name] for name in preferred if name in catalog.models]
    if not candidates:
        candidates = sorted(
            catalog.models.values(),
            key=lambda model: (model.role_scores.get(role, 0.0), -model.download_gib),
            reverse=True,
        )
    return next(
        (
            model
            for model in candidates
            if model.supports_hardware(hardware) and model.role_scores.get(role, 0.0) >= 0.5
        ),
        None,
    )


def recommend_bundle(
    bundle_name: str,
    hardware: HardwareProfile,
    registry: ModelRegistry | None = None,
    catalog: ModelCatalog | None = None,
) -> BundleRecommendation:
    selected_catalog = catalog or load_catalog()
    bundle = selected_catalog.bundle(bundle_name)
    coverage = installed_role_coverage(registry)
    planned: dict[str, CatalogModel] = {}
    planned_roles: dict[str, str] = {}
    missing: list[str] = []
    installed_names = {name.lower() for name in registry.models} if registry else set()
    for role in bundle.roles:
        if role in coverage:
            continue
        candidate = _candidate_for_role(role, bundle, selected_catalog, hardware)
        if not candidate:
            missing.append(role)
            continue
        planned_roles[role] = candidate.name
        if candidate.name.lower() not in installed_names:
            planned[candidate.name] = candidate
    covered = {
        role: coverage.get(role) or planned_roles[role]
        for role in bundle.roles
        if role in coverage or role in planned_roles
    }
    return BundleRecommendation(bundle, tuple(planned.values()), covered, tuple(missing))


def recommendation_set(
    hardware: HardwareProfile,
    registry: ModelRegistry | None = None,
    catalog: ModelCatalog | None = None,
) -> tuple[BundleRecommendation, ...]:
    selected_catalog = catalog or load_catalog()
    return tuple(recommend_bundle(name, hardware, registry, selected_catalog) for name in selected_catalog.bundles)


def catalog_model(
    name: str,
    catalog: ModelCatalog | None = None,
    *,
    allow_family_match: bool = True,
) -> CatalogModel | None:
    selected_catalog = catalog or load_catalog()
    lowered = name.lower()
    exact = next((model for key, model in selected_catalog.models.items() if key.lower() == lowered), None)
    if exact or not allow_family_match:
        return exact
    family = lowered.split(":", 1)[0]
    matches = [model for key, model in selected_catalog.models.items() if key.lower().split(":", 1)[0] == family]
    return matches[0] if len(matches) == 1 else None


def apply_catalog_priors(registry: ModelRegistry, catalog: ModelCatalog | None = None) -> None:
    """Enrich known models without making the catalog a compatibility gate."""
    selected_catalog = catalog or load_catalog()
    field_names = {
        "coding": "code",
        "agent": "agent",
        "reasoning": "reasoning",
        "general": "general",
        "vision": "vision",
    }
    for record in registry.models.values():
        known = catalog_model(record.descriptor.name, selected_catalog, allow_family_match=False)
        if not known:
            continue
        values = record.capability.to_dict()
        for role, score in known.role_scores.items():
            field = field_names.get(role)
            if field:
                values[field] = score
        values["confidence"] = max(float(values.get("confidence") or 0.0), 0.82)
        values["source"] = "curated catalog priors + provider metadata + hardware fit"
        record.capability = CapabilityProfile.from_dict(values)


def total_download_gib(models: Iterable[CatalogModel]) -> float:
    return sum(model.download_gib for model in models)
