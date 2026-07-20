"""Canonical product identity and narrow legacy compatibility helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping

PRODUCT_NAME = "Dairack"
DISPLAY_NAME = "DAIRACK"
APP_NAME = "dairack"
CLI_NAME = "dairack"
ENV_PREFIX = "DAIRACK"

# These names are accepted only at external migration boundaries.
LEGACY_PRODUCT_NAME = "AsusAI"
LEGACY_APP_NAME = "asusai"
LEGACY_ENV_PREFIX = "ASUSAI"

BRIDGE_SERVICE = "dairack-compute"
LEGACY_BRIDGE_SERVICE = "asusai-compute"
BRIDGE_INFO_PATH = "/dairack/v1/info"
LEGACY_BRIDGE_INFO_PATH = "/asusai/v1/info"
BRIDGE_HEALTH_PATH = "/dairack/v1/health"
LEGACY_BRIDGE_HEALTH_PATH = "/asusai/v1/health"


def env_value(suffix: str, default: str = "", *, environ: Mapping[str, str] | None = None) -> str:
    """Read a canonical environment value, falling back to its legacy spelling."""

    values = os.environ if environ is None else environ
    canonical = f"{ENV_PREFIX}_{suffix}"
    legacy = f"{LEGACY_ENV_PREFIX}_{suffix}"
    if canonical in values:
        return str(values[canonical])
    if legacy in values:
        return str(values[legacy])
    return default


def env_enabled(suffix: str, *, environ: Mapping[str, str] | None = None) -> bool:
    value = env_value(suffix, environ=environ).strip().lower()
    return value not in {"", "0", "false", "no", "off"}
