"""Versioned configuration with validation and atomic persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .identity import env_value
from .paths import PATHS, AppPaths

CONFIG_SCHEMA_VERSION = 3
POLICIES = {"adaptive", "quality", "efficient"}
PERMISSION_MODES = {"ask", "read-auto", "deny"}
MODEL_MODES = {"orchestrator", "direct"}
STARTUP_CHAT_MODES = {"new", "resume-last"}
COMPUTE_MODES = {"local", "remote"}
COMPUTE_TRANSPORTS = {"ollama", "bridge"}
# Set this to the project's owned release/PyPI JSON endpoint before public distribution.
DEFAULT_UPDATE_INDEX_URL = ""


class ConfigError(ValueError):
    pass


def validate_update_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    secure = parsed.scheme == "https"
    local_http = parsed.scheme == "http" and parsed.hostname in local_hosts
    if not parsed.hostname or parsed.username or parsed.password or not (secure or local_http):
        raise ConfigError("update_index_url must use HTTPS (local HTTP is allowed for development)")
    return url


def default_config() -> dict[str, Any]:
    ollama_host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    parsed_host = urlparse(ollama_host if "://" in ollama_host else "http://" + ollama_host)
    local_compute = (parsed_host.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "provider": "ollama",
        "ollama_host": ollama_host,
        "compute_mode": "local" if local_compute else "remote",
        "compute_name": "Local Ollama" if local_compute else (parsed_host.hostname or "Remote compute"),
        "compute_transport": "ollama",
        "compute_hardware_verified": local_compute,
        "compute_verified_at": "",
        "remote_ollama_host": "" if local_compute else ollama_host,
        "model": "",
        "model_mode": "orchestrator",
        "orchestrator_policy": "adaptive",
        "orchestrator_planning": True,
        "orchestrator_review": True,
        "orchestrator_delegation": True,
        "orchestrator_semantic_routing": True,
        "coordinator_learning": True,
        "coordinator_role_preferences": {},
        "check_updates": True,
        "reduced_motion": False,
        "update_check_interval_hours": 24,
        "update_index_url": env_value("UPDATE_INDEX_URL", DEFAULT_UPDATE_INDEX_URL).strip(),
        "num_ctx": 4096,
        "model_keep_alive": "10m",
        "think": False,
        "agent": True,
        "max_agent_steps": 12,
        "permission_mode": "ask",
        "last_chat": "",
        "startup_chat": "new",
        "context_budget_ratio": 0.82,
        "auto_compact": True,
        "auto_compact_keep_recent": 16,
        "auto_compact_trigger_ratio": 0.88,
        "auto_compact_min_messages": 10,
        "project_retrieval": True,
        "retrieval_results": 5,
        "model_options": {},
        "profile_overrides": {},
    }


def _migrate(raw: Mapping[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(dict(raw))
    try:
        version = int(migrated.get("schema_version") or 1)
    except (TypeError, ValueError) as exc:
        raise ConfigError("schema_version must be an integer") from exc
    if version > CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"configuration schema {version} is newer than this Dairack build supports ({CONFIG_SCHEMA_VERSION})"
        )
    if version < 2:
        migrated.setdefault("coordinator_role_preferences", {})
    if version < 3:
        host = str(migrated.get("ollama_host") or "127.0.0.1:11434")
        parsed = urlparse(host if "://" in host else "http://" + host)
        local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
        migrated.setdefault("compute_mode", "local" if local else "remote")
        migrated.setdefault("compute_name", "Local Ollama" if local else (parsed.hostname or "Remote compute"))
        migrated.setdefault("compute_transport", "ollama")
        migrated.setdefault("compute_hardware_verified", local)
        migrated.setdefault("compute_verified_at", "")
        migrated.setdefault("remote_ollama_host", "" if local else host)
    migrated["schema_version"] = CONFIG_SCHEMA_VERSION
    return migrated


def _merge_defaults(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = default_config()
    config.update(_migrate(raw))
    config["schema_version"] = CONFIG_SCHEMA_VERSION
    return config


def _bounded_int(config: dict[str, Any], key: str, minimum: int, maximum: int) -> None:
    value = config.get(key)
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    config[key] = parsed


def _bounded_float(config: dict[str, Any], key: str, minimum: float, maximum: float) -> None:
    try:
        parsed = float(config.get(key))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    config[key] = parsed


def validate_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    config = _merge_defaults(raw)
    if config["provider"] != "ollama":
        raise ConfigError("only the ollama provider is currently supported")
    if config["model_mode"] not in MODEL_MODES:
        raise ConfigError(f"model_mode must be one of {sorted(MODEL_MODES)}")
    if config["orchestrator_policy"] not in POLICIES:
        raise ConfigError(f"orchestrator_policy must be one of {sorted(POLICIES)}")
    if config["permission_mode"] not in PERMISSION_MODES:
        raise ConfigError(f"permission_mode must be one of {sorted(PERMISSION_MODES)}")
    if config["startup_chat"] not in STARTUP_CHAT_MODES:
        raise ConfigError(f"startup_chat must be one of {sorted(STARTUP_CHAT_MODES)}")
    if config["compute_mode"] not in COMPUTE_MODES:
        raise ConfigError(f"compute_mode must be one of {sorted(COMPUTE_MODES)}")
    if config["compute_transport"] not in COMPUTE_TRANSPORTS:
        raise ConfigError(f"compute_transport must be one of {sorted(COMPUTE_TRANSPORTS)}")
    host = str(config.get("ollama_host") or "").strip()
    if not host:
        raise ConfigError("ollama_host cannot be empty")
    parsed_host = urlparse(host if "://" in host else "http://" + host)
    if not parsed_host.hostname or parsed_host.username or parsed_host.password:
        raise ConfigError("ollama_host must be a host URL without embedded credentials")
    if parsed_host.query or parsed_host.fragment or parsed_host.path not in {"", "/"}:
        raise ConfigError("ollama_host must not contain a path, query, or fragment")
    config["ollama_host"] = host
    hostname = parsed_host.hostname.lower()
    config["compute_mode"] = (
        "local" if hostname == "localhost" or hostname == "::1" or hostname.startswith("127.") else "remote"
    )
    compute_name = str(config.get("compute_name") or "").strip()
    if not compute_name or len(compute_name) > 80:
        raise ConfigError("compute_name must contain between 1 and 80 characters")
    config["compute_name"] = compute_name
    config["compute_verified_at"] = str(config.get("compute_verified_at") or "").strip()
    config["remote_ollama_host"] = str(config.get("remote_ollama_host") or "").strip()
    config["update_index_url"] = validate_update_url(config.get("update_index_url"))
    keep_alive = str(config.get("model_keep_alive") or "").strip()
    if keep_alive and not re.fullmatch(r"(?:0|-1|\d+[smh])", keep_alive):
        raise ConfigError("model_keep_alive must be empty, 0, -1, or a duration like 10m, 45s, or 1h")
    config["model_keep_alive"] = keep_alive
    _bounded_int(config, "num_ctx", 512, 1_048_576)
    _bounded_int(config, "max_agent_steps", 1, 64)
    _bounded_int(config, "update_check_interval_hours", 1, 720)
    _bounded_int(config, "auto_compact_keep_recent", 4, 200)
    _bounded_int(config, "auto_compact_min_messages", 4, 500)
    _bounded_int(config, "retrieval_results", 1, 50)
    _bounded_float(config, "context_budget_ratio", 0.25, 0.98)
    _bounded_float(config, "auto_compact_trigger_ratio", 0.40, 0.99)
    for key in (
        "orchestrator_planning",
        "orchestrator_review",
        "orchestrator_delegation",
        "orchestrator_semantic_routing",
        "coordinator_learning",
        "think",
        "agent",
        "auto_compact",
        "project_retrieval",
        "check_updates",
        "reduced_motion",
        "compute_hardware_verified",
    ):
        if not isinstance(config.get(key), bool):
            raise ConfigError(f"{key} must be true or false")
    for key in ("model_options", "profile_overrides", "coordinator_role_preferences"):
        if not isinstance(config.get(key), dict):
            raise ConfigError(f"{key} must be an object")
    allowed_roles = {"general", "coding", "agent", "reasoning", "research", "vision", "planner", "reviewer"}
    preferences = config["coordinator_role_preferences"]
    for role, model in preferences.items():
        if role not in allowed_roles:
            raise ConfigError(f"unknown coordinator role preference: {role}")
        if not isinstance(model, str) or not model.strip():
            raise ConfigError(f"coordinator role preference {role} must name a model")
        preferences[role] = model.strip()
    return config


def load_config(paths: AppPaths = PATHS) -> dict[str, Any]:
    if not paths.config_file.exists():
        return default_config()
    try:
        raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {paths.config_file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{paths.config_file} must contain a JSON object")
    return validate_config(raw)


def atomic_write_json(path: Path, payload: Mapping[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        temporary_path.replace(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def save_config(config: Mapping[str, Any], paths: AppPaths = PATHS) -> dict[str, Any]:
    validated = validate_config(config)
    atomic_write_json(paths.config_file, validated)
    return validated
