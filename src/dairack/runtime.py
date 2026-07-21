#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import difflib
import functools
import hashlib
import html
import json
import math
import nturl2path
import os
import platform
import re
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from copy import deepcopy
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from typing import Any

from . import network as secure_network
from .bootstrap import initialize as initialize_app
from .compute import provider_for_config as configured_compute_provider
from .config import (
    atomic_write_json,
    default_config,
)
from .config import (
    load_config as load_app_config,
)
from .config import (
    save_config as save_app_config,
)
from .coordinator.analysis import (
    analyze_task as analyze_orchestrator_task,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    execution_scope as coordinator_execution_scope,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    extract_public_web_targets,
    is_direct_answer_route,
    public_web_action_contract,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    merge_semantic_assessment as _merge_semantic_assessment,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    referenced_task_analysis as coordinator_referenced_task_analysis,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    semantic_context as coordinator_semantic_context,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    semantic_gate as coordinator_semantic_gate,  # noqa: F401  (re-export)
)
from .coordinator.analysis import (
    signal_hits as _signal_hits,  # noqa: F401  (compatibility export)
)
from .coordinator.analysis import (
    task_kind as _coordinator_task_kind,  # noqa: F401  (re-export)
)
from .coordinator.calibration import estimate as calibration_estimate  # noqa: F401  (re-export)
from .coordinator.calibration import load_state as load_calibration_state  # noqa: F401  (late-bound by selection)
from .coordinator.calibration import report as calibration_report
from .coordinator.calibration import reset as reset_calibration
from .coordinator.control import (
    RoutingControl,  # noqa: F401  (compatibility export)
    materially_larger,  # noqa: F401  (re-export)
)
from .coordinator.delegation import (  # noqa: F401  (re-exported coordinator constants)
    COORDINATOR_DELEGATION_LIMITS,
    COORDINATOR_SPECIALTIES,
    COORDINATOR_TOKEN_BUDGETS,
)
from .coordinator.delegation import admits_delegation as coordinator_admits_delegation  # noqa: F401  (re-export)
from .coordinator.delegation import capability_score as _specialist_capability_score  # noqa: F401  (re-export)
from .coordinator.delegation import delegation_limit as coordinator_delegation_limit
from .coordinator.delegation import execute_delegation as execute_coordinator_delegation
from .coordinator.delegation import quality_demand as coordinator_quality_demand  # noqa: F401  (re-export)
from .coordinator.delegation import select_specialist as select_coordinator_specialist
from .coordinator.delegation import specialty as coordinator_specialty
from .coordinator.oversight import (
    action_contract_directive,
    executor_recovery_directive,
    format_route_history,
    format_route_report,
    observe_route_outcome,
    record_executor_recovery,
    record_route_feedback,
    routing_control_directive,  # noqa: F401  (re-export)
)
from .coordinator.oversight import collect_response as _collect_orchestrator_response
from .coordinator.oversight import executor_directive as coordinator_executor_directive
from .coordinator.oversight import learning_path as coordinator_learning_path
from .coordinator.oversight import plan as orchestrator_plan
from .coordinator.oversight import recovery_executor as coordinator_recovery_executor
from .coordinator.oversight import review as orchestrator_review
from .coordinator.oversight import status_report as orchestrator_status
from .coordinator.policy import POLICIES as COORDINATOR_POLICY_DEFINITIONS  # noqa: F401  (re-export)
from .coordinator.policy import policy_for  # noqa: F401  (re-export)
from .coordinator.ranking import (
    candidate_score as coordinator_candidate_score,  # noqa: F401  (re-export)
)
from .coordinator.ranking import (
    effective_learning_adjustment as coordinator_learning_adjustment,
)
from .coordinator.ranking import (
    role_preference as coordinator_role_preference,  # noqa: F401  (re-export)
)
from .coordinator.ranking import (
    semantic_router_model as select_semantic_router_model,  # noqa: F401  (re-export)
)
from .coordinator.ranking import (
    stage_model as select_coordinator_stage_model,  # noqa: F401  (re-export)
)
from .coordinator.selection import candidate_score_for as _orchestrator_candidate_score  # noqa: F401  (re-export)
from .coordinator.selection import direct_route  # noqa: F401  (re-export)
from .coordinator.selection import executor_continuity as _executor_continuity  # noqa: F401  (re-export)
from .coordinator.selection import role_preference as _coordinator_role_preference  # noqa: F401  (re-export)
from .coordinator.selection import select_route as select_orchestrator_route
from .coordinator.selection import semantic_router_model as _semantic_router_model  # noqa: F401  (re-export)
from .coordinator.selection import specialist_model as _specialist_model  # noqa: F401  (re-export)
from .coordinator.semantic import assessment as coordinator_semantic_assessment  # noqa: F401  (re-export)
from .coordinator.semantic import reset_assessment_cache as reset_semantic_assessment_cache  # noqa: F401  (re-export)
from .coordinator.tuning import DEFAULT_TUNING  # noqa: F401  (re-export)
from .coordinator.tuning import for_config as coordinator_tuning_for_config  # noqa: F401  (re-export)
from .file_discovery import find_paths as discover_paths
from .identity import APP_NAME, env_enabled
from .machine import hardware_status as format_hardware_status
from .machine import machine_prompt
from .messages import (
    IMAGE_EXTENSIONS,
    MAX_ATTACHED_IMAGES,
    SYSTEM_PARTS_KEY,
    TOOL_RESULT_PREFIXES,
    canonicalize_messages,
    depends_on_conversation_context,  # noqa: F401  (compatibility export)
    expand_system_messages,
    latest_user_images,
    latest_user_message,  # noqa: F401  (compatibility export)
    latest_user_task,
    message_image_paths,  # noqa: F401  (compatibility export)
)
from .models import (
    ModelDescriptor,
    capabilities_for,
    capability_metadata_for,
    clear_runtime_override,
    load_registry,
    runtime_override_for,
    runtime_profile_for,
    save_runtime_override,
)
from .paths import PATHS
from .permissions import (
    argv_needs_interactive_tty,
    command_needs_interactive_tty,
    detect_project_root,
    is_auto_approvable_tool_call,
    is_internal_coordinator_call,
    path_within,
    read_only_shell_argv,
    resolve_user_path,
)
from .permissions import (
    is_read_only_shell_command as _is_read_only_shell_command,
)
from .permissions import (
    is_read_only_tool_call as _is_read_only_tool_call,
)
from .providers.ollama import OllamaError, OllamaProvider
from .search import RG_EXCLUSION_GLOBS
from .text import MAX_TEXT_OUTPUT, truncate, truncate_middle
from .tool_protocol import TOOL_REGISTRY, decode_text_tool_call, strip_tool_protocol
from .turn import (
    CompletionOutcome,
    ResponseFacts,
    ReviewOutcome,
    RouteFacts,
    TurnAction,
    TurnState,
    completion_arbiter_outcome,
    finalizing,
    next_action,
    review_outcome,
    synthesis_exhausted,
)

# Compatibility exports for the Textual layer and existing integrations.
is_read_only_shell_command = _is_read_only_shell_command
is_read_only_tool_call = _is_read_only_tool_call

APP = APP_NAME
CONFIG_PATH = PATHS.config_file
HISTORY_PATH = PATHS.history_file
CHAT_DIR = PATHS.chats_dir
INDEX_DB_PATH = PATHS.index_file
CHECKPOINT_DIR = PATHS.checkpoints_dir
APP_DATA_DIR = PATHS.data_dir
DEFAULT_TIMEOUT = 120
SEARCH_TIMEOUT = 30
SENSITIVE_CHILD_ENV_KEYS = {"DAIRACK_COMPUTE_TOKEN", "ASUSAI_COMPUTE_TOKEN"}
NATIVE_TOOL_DIRECTIVE = (
    "Function tools are active through the provider API for this request. Return actions through the native "
    "tool_calls field; return no ordinary response text alongside a tool call, and do not print function names, "
    "arguments, or fallback markup as response content."
)
MAX_TOOL_OUTPUT = MAX_TEXT_OUTPUT
DEFAULT_AGENT_ACTION_LIMIT = 12
MAX_AGENT_ACTION_LIMIT = 64
MAX_INDEX_FILE_BYTES = 512 * 1024
MAX_INDEX_FILES = 6000
MAX_RETRIEVAL_CHARS = 9000
GROUNDED_MEMORY_FORMAT = "grounded-ledger-v1"
WEB_TIMEOUT = 18
WEB_TOTAL_TIMEOUT = 45
WEB_MAX_BYTES = 1_200_000
WEB_USER_AGENT = "Mozilla/5.0 (compatible; Dairack/1.0; +local-terminal-agent)"
ORCHESTRATOR_MODEL_ID = "dairack:coordinator"
LEGACY_ORCHESTRATOR_MODEL_ID = "asusai:orchestrator"
ORCHESTRATOR_POLICIES = {"adaptive", "quality", "efficient"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SEARCH_GLOBS = RG_EXCLUSION_GLOBS
SLASH_COMMANDS = [
    "/help",
    "/exit",
    "/quit",
    "/models",
    "/library",
    "/profiles",
    "/profile",
    "/model",
    "/coordinator",
    "/orchestrator",
    "/route",
    "/compute",
    "/hardware",
    "/image",
    "/images",
    "/detach",
    "/pull",
    "/ctx",
    "/think",
    "/agent",
    "/permissions",
    "/allow",
    "/deny",
    "/chats",
    "/resume",
    "/new",
    "/save",
    "/context",
    "/compact",
    "/autocompact",
    "/reset",
    "/pwd",
    "/cd",
    "/index",
    "/find",
    "/symbols",
    "/deps",
    "/repo",
    "/tests",
    "/test",
    "/read",
    "/ls",
    "/diff",
    "/undo",
    "/checkpoints",
    "/search",
    "/open",
    "/web",
    "/url",
    "/run",
    "/copy",
    "/config",
]

PRIMARY_HELP_TEXT = """
DAIRACK COMMANDS

CONVERSATION
  /new [title]          start a clean conversation
  /chats                open saved conversations
  /save [title]         save or rename this conversation
  /context              inspect context pressure and memory

INTELLIGENCE
  /model [name]         choose Coordinator or a direct model
  /library              install, update, remove, or inspect models
  /coordinator [mode]   set adaptive, quality, efficient, or off
  /route                inspect the latest routing decision
  /compute              inspect or change the inference server
  /hardware             distinguish client and compute hardware

WORKSPACE
  /image [path]         attach visual input
  /index [path]         build or refresh project memory
  /find <query>         search indexed project memory
  /diff                 review current repository changes

ACTIONS
  /permissions <mode>   set ask, read-auto, or deny
  /run <command>        run an explicit shell command
  /web <query>          search the public internet

Use /help all for the complete command and profile reference.
""".strip()

HELP_TEXT = """
COMPLETE COMMAND REFERENCE
  /help [all]           show primary or complete help
  /exit, /quit          leave Dairack
  /library              open the model library
  /models               compatibility alias for /library in the TUI
  /profiles             show Dairack hardware profiles for known models
  /profile              show or tune the selected model profile
  /model [name]         switch model, or open model selector
  /coordinator [MODE]   coordinate models; MODE: adaptive, quality, efficient, off
  /orchestrator [MODE]  compatibility alias for /coordinator
  /route [history|feedback good|bad]
                         inspect routes or calibrate the latest decision
  /compute              inspect or change the model compute server
  /hardware             show authoritative client and compute hardware
  /image [path]         attach an image, or open the project image selector
  /images               show images staged for the next prompt
  /detach [N|all]       remove a staged image
  /pull <model>         pull an Ollama model
  /ctx [tokens]         show or set context tokens for requests
  /think on|off         show/use model thinking where supported
  /agent [on|off|budget N]
                         configure model actions and the per-task action limit
  /permissions MODE     set action policy: ask, read-auto, deny
  /allow, /deny         approve or reject a pending agent action
  /chats                list saved chats
  /resume [id|number]   resume a saved chat, latest when omitted
  /new [title]          start a new saved chat
  /save [title]         save now, optionally rename current chat
  /context              show active context budget and saved history size
  /compact [keep]       summarize older turns for long-chat context
  /autocompact on|off   configure automatic long-chat compaction
  /reset                clear chat history
  /pwd                  show current working directory
  /cd <path>            change working directory
  /index [path]         build/update local project memory index
  /find <query>         search indexed project memory
  /symbols [query]      search indexed symbols
  /deps [query]         inspect indexed imports/dependencies
  /repo                 show indexed repo profile
  /tests                show detected test commands
  /test [number|cmd]    run a detected or explicit test command
  /read <file> [line]   read a file with line numbers
  /ls [path]            list a directory safely
  /diff                 show git diff for current project
  /undo [id|latest]     restore files from an edit checkpoint
  /checkpoints          list edit checkpoints
  /search <pattern>     search files from the current directory
  /open <file> [line]   open a text file, optionally around a line
  /web <query>          search the internet
  /url <url>            fetch readable text from a web page
  /run <command>        run a shell command yourself
  /copy                 copy transcript to terminal clipboard
  /config               show current config

Profile tuning:
  /profile set ctx N     override context tokens for selected model
  /profile set batch N   override Ollama num_batch for selected model
  /profile set gpu N     override Ollama num_gpu/GPU layers for selected model
  /profile set threads N override Ollama num_thread for selected model
  /profile set think on|off override thinking mode for selected model
  /profile reset         remove selected-model overrides

One-shot:
  dairack "explain this server"
  dairack -m model:tag "write a Python FFT example"
  dairack --plain
  dairack --resume latest
  dairack --no-color
""".strip()


def help_text(args: list[str] | None = None) -> str:
    return HELP_TEXT if args and args[0].lower() in {"all", "full", "advanced"} else PRIMARY_HELP_TEXT


def unknown_command_display(command: str) -> str:
    entered = "/" + command.strip().lstrip("/")
    candidates = [value for value in SLASH_COMMANDS if value not in {"/orchestrator", "/quit"}]
    matches = difflib.get_close_matches(entered, candidates, n=1, cutoff=0.48)
    lines = [f"UNKNOWN COMMAND  {entered}"]
    if matches:
        match = matches[0]
        suffix = " or F6 Model Library" if match == "/library" else ""
        lines.append(f"Did you mean {match}{suffix}?")
    else:
        lines.append("Use /help for the primary command reference.")
    return "\n".join(lines)


DEFAULT_CONFIG: dict[str, Any] = default_config()


def model_profile_for(model: str) -> dict[str, Any] | None:
    if not model:
        return None
    registry = load_registry(PATHS)
    if registry:
        lowered = model.lower()
        for name in registry.models:
            if name.lower() == lowered:
                return runtime_profile_for(name, PATHS)
    return None


def model_override_key(config: dict[str, Any], model: str) -> str:
    overrides = config.get("profile_overrides")
    if isinstance(overrides, dict):
        for key in overrides:
            if str(key).lower() == model.lower():
                return str(key)
    return model


def model_profile_override(config: dict[str, Any], model: str) -> dict[str, Any]:
    overrides = config.get("profile_overrides")
    if isinstance(overrides, dict):
        value = overrides.get(model_override_key(config, model))
        if isinstance(value, dict):
            return dict(value)
    return runtime_override_for(model, PATHS)


def ensure_model_profile_override(config: dict[str, Any], model: str) -> dict[str, Any]:
    overrides = config.setdefault("profile_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
        config["profile_overrides"] = overrides
    key = model_override_key(config, model)
    value = overrides.get(key)
    if not isinstance(value, dict):
        value = {}
        overrides[key] = value
    return value


def override_summary(override: dict[str, Any]) -> str:
    if not override:
        return "overrides none"
    parts = []
    if "num_ctx" in override:
        parts.append(f"ctx {override['num_ctx']}")
    if "think" in override:
        parts.append(f"think {'on' if override['think'] else 'off'}")
    options = override.get("model_options")
    if isinstance(options, dict):
        if "num_thread" in options:
            parts.append(f"threads {options['num_thread']}")
        if "num_batch" in options:
            parts.append(f"batch {options['num_batch']}")
        if "num_gpu" in options:
            parts.append(f"gpu {options['num_gpu']}")
    return "overrides " + (", ".join(parts) if parts else json.dumps(override, sort_keys=True))


def apply_model_profile(config: dict[str, Any], model: str) -> str:
    config["model"] = model
    profile = model_profile_for(model)
    if not profile:
        config["model_options"] = {}
        override = model_profile_override(config, model)
        if "num_ctx" in override:
            config["num_ctx"] = int(override["num_ctx"])
        if "think" in override:
            config["think"] = bool(override["think"])
        options = override.get("model_options")
        if isinstance(options, dict):
            config["model_options"] = dict(options)
        return "no generated hardware profile; run `dairack init`; " + override_summary(override)
    config["num_ctx"] = int(profile["num_ctx"])
    config["think"] = bool(profile["think"])
    config["model_options"] = dict(profile.get("options") or {})
    override = model_profile_override(config, model)
    if "num_ctx" in override:
        config["num_ctx"] = int(override["num_ctx"])
    if "think" in override:
        config["think"] = bool(override["think"])
    options = override.get("model_options")
    if isinstance(options, dict):
        config["model_options"].update(options)
    return (
        f"{profile['name']} profile: {profile['role']}; "
        f"ctx {config['num_ctx']}; think {'on' if config['think'] else 'off'}; "
        f"options {config['model_options']}; {override_summary(override)}"
    )


def runtime_config_for_model(config: dict[str, Any], model: str) -> dict[str, Any]:
    """Build an effective per-request profile without changing the selected mode."""
    runtime = dict(config)
    overrides = config.get("profile_overrides")
    runtime["profile_overrides"] = dict(overrides) if isinstance(overrides, dict) else {}
    apply_model_profile(runtime, model)
    return runtime


def ollama_options(config: dict[str, Any]) -> dict[str, Any]:
    options = config.get("model_options")
    return dict(options) if isinstance(options, dict) else {}


def model_capabilities(model: Any) -> dict[str, float]:
    """Resolve capabilities against this runtime's active state tree."""
    return capabilities_for(model, PATHS)


def model_capability_metadata(model: Any) -> dict[str, Any]:
    return capability_metadata_for(model, PATHS)


def model_supports_vision(provider: Any, model: str) -> bool:
    supports = getattr(provider, "supports", None)
    if callable(supports):
        try:
            return bool(supports(model, "vision"))
        except Exception:
            pass
    return model_capabilities(model).get("vision", 0.0) >= 0.50


def require_vision_support(provider: Any, model: str, messages: list[dict[str, Any]]) -> None:
    if latest_user_images(messages) and not model_supports_vision(provider, model):
        raise RuntimeError(f"{model} cannot accept images. Select COORDINATOR or a model marked VISION with F2.")


def runtime_failure_message(error: Exception | str, phase: str = "model response") -> dict[str, str]:
    """Create model-visible, task-neutral history for a failed runtime turn."""
    raw = re.sub(r"\s+", " ", str(error)).strip()
    overflow = re.search(
        r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)",
        raw,
        re.IGNORECASE,
    )
    if overflow:
        detail = f"context limit exceeded: request {overflow.group(1)} tokens; available {overflow.group(2)} tokens"
    else:
        detail = truncate(raw, 1200)
    return {
        "role": "user",
        "content": (
            "Runtime event:\n"
            f"phase: {phase}\n"
            "status: failed\n"
            f"detail: {detail or 'unknown runtime failure'}\n"
            "effect: the preceding assistant response did not complete"
        ),
    }


def _effective_learning_adjustment(
    signals: dict[str, float],
    task_complexity: float,
    learned_adjustment: float,
) -> float:
    return coordinator_learning_adjustment(signals, task_complexity, learned_adjustment)


COORDINATOR_SEMANTIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["conversation", "general", "research", "reasoning", "coding", "system_action", "visual", "mixed"],
        },
        "code": {"type": "number", "minimum": 0, "maximum": 1},
        "agent": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "number", "minimum": 0, "maximum": 1},
        "general": {"type": "number", "minimum": 0, "maximum": 1},
        "research": {"type": "number", "minimum": 0, "maximum": 1},
        "vision": {"type": "number", "minimum": 0, "maximum": 1},
        "risk": {"type": "number", "minimum": 0, "maximum": 1},
        "complexity": {"type": "number", "minimum": 0, "maximum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "needs_plan": {"type": "boolean"},
        "needs_review": {"type": "boolean"},
        "requires_action": {"type": "boolean"},
        "compute_preference": {
            "type": "string",
            "enum": ["auto", "quality", "higher_capacity", "efficiency"],
        },
        "control_target": {
            "type": "string",
            "enum": ["none", "compute", "content", "discussion"],
        },
        "preference_strength": {"type": "number", "minimum": 0, "maximum": 1},
        "control_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "applies_to_previous": {"type": "boolean"},
        "resolved_task": {"type": "string", "maxLength": 1200},
        "reason": {"type": "string", "maxLength": 160},
    },
    "required": [
        "intent",
        "code",
        "agent",
        "reasoning",
        "general",
        "research",
        "vision",
        "risk",
        "complexity",
        "confidence",
        "needs_plan",
        "needs_review",
        "requires_action",
        "compute_preference",
        "control_target",
        "preference_strength",
        "control_confidence",
        "applies_to_previous",
        "resolved_task",
        "reason",
    ],
    "additionalProperties": False,
}


def configure_orchestrator(config: dict[str, Any], args: list[str]) -> str:
    if not args or args[0].lower() in {"status", "show"}:
        return orchestrator_status(config)
    action = args[0].lower()
    if action in {"on", "adaptive", "quality", "efficient"}:
        config["model_mode"] = "orchestrator"
        if action in ORCHESTRATOR_POLICIES:
            config["orchestrator_policy"] = action
        return f"coordinator enabled\npolicy: {config.get('orchestrator_policy', 'adaptive')}"
    if action in {"off", "direct"}:
        config["model_mode"] = "direct"
        return f"direct model mode\nmodel: {config.get('model') or '(none)'}"
    if action == "policy" and len(args) > 1 and args[1].lower() in ORCHESTRATOR_POLICIES:
        config["model_mode"] = "orchestrator"
        config["orchestrator_policy"] = args[1].lower()
        return f"coordinator enabled\npolicy: {args[1].lower()}"
    setting_names = {
        "planning": "planning",
        "review": "review",
        "delegation": "delegation",
        "semantic": "semantic_routing",
    }
    if action in setting_names and len(args) > 1 and args[1].lower() in {"on", "off"}:
        config[f"orchestrator_{setting_names[action]}"] = args[1].lower() == "on"
        return f"coordinator {action}: {args[1].lower()}"
    if action == "learning":
        operation = args[1].lower() if len(args) > 1 else "status"
        if operation in {"on", "off"}:
            config["coordinator_learning"] = operation == "on"
            return f"coordinator learning: {operation}"
        if operation == "reset":
            reset_calibration(coordinator_learning_path())
            return "coordinator learning reset; capability priors remain active"
        if operation in {"status", "show"}:
            enabled = "on" if config.get("coordinator_learning", True) else "off"
            return f"Coordinator learning: {enabled}\n{calibration_report(coordinator_learning_path())}"
        raise ValueError("usage: /coordinator learning on|off|status|reset")
    if action == "prefer" and len(args) > 2:
        role = args[1].lower()
        allowed_roles = {"general", "coding", "agent", "reasoning", "research", "vision", "planner", "reviewer"}
        if role not in allowed_roles:
            raise ValueError(f"unknown coordinator role: {role}; choose {', '.join(sorted(allowed_roles))}")
        preferences = config.setdefault("coordinator_role_preferences", {})
        if args[2].lower() in {"auto", "none", "off"}:
            preferences.pop(role, None)
            return f"coordinator {role}: automatic"
        registry = load_registry()
        names = list(registry.models) if registry else []
        matches = [name for name in names if name.lower() == args[2].lower()]
        if not matches:
            matches = [name for name in names if name.lower().startswith(args[2].lower())]
        if len(matches) != 1:
            detail = "not installed" if not matches else f"ambiguous: {', '.join(matches)}"
            raise ValueError(f"model preference {args[2]} is {detail}")
        preferences[role] = matches[0]
        return f"coordinator {role}: prefer {matches[0]} (soft)"
    raise ValueError(
        "usage: /coordinator on|off|adaptive|quality|efficient|status|"
        "policy MODE|planning on|off|review on|off|delegation on|off|semantic on|off|learning on|off|status|reset|"
        "prefer ROLE MODEL|auto "
        "(/orchestrator remains an alias)"
    )


ACTION_COMPLETION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "complete": {"type": "boolean"},
        "needs_action": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 180},
    },
    "required": ["complete", "needs_action", "confidence", "reason"],
    "additionalProperties": False,
}


def _recent_action_evidence(messages: list[dict[str, Any]], limit: int = 9000) -> str:
    evidence: list[str] = []
    size = 0
    for message in reversed(messages):
        content = str(message.get("content") or "").strip()
        role = str(message.get("role") or "")
        if not content:
            continue
        if role == "tool" or content.startswith(TOOL_RESULT_PREFIXES):
            excerpt = truncate(content, 2800)
            if size + len(excerpt) > limit and evidence:
                break
            evidence.append(excerpt)
            size += len(excerpt)
        if len(evidence) >= 6:
            break
    return "\n\n".join(reversed(evidence))


def assess_action_completion(
    provider: Any,
    config: dict[str, Any],
    route: dict[str, Any],
    messages: list[dict[str, Any]],
    candidate: str,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    contract = route.get("action_contract")
    if not candidate.strip():
        return {}
    if not isinstance(contract, dict) or not contract.get("capability"):
        contract = {
            "capability": "agent_followthrough",
            "reason": "the executor already initiated runtime actions and must resolve their outcome",
        }
    semantic = route.get("semantic_assessment")
    model = str(semantic.get("model") or "") if isinstance(semantic, dict) else ""
    model = model or str(route.get("executor") or config.get("model") or "")
    if not model:
        return {}
    runtime = runtime_config_for_model(config, model)
    runtime["think"] = False
    options = runtime.get("model_options")
    runtime["model_options"] = dict(options) if isinstance(options, dict) else {}
    runtime["model_options"]["temperature"] = 0
    prompt = (
        "Judge whether a local agent's candidate response has actually completed the user's request. Return strict "
        "JSON only. Use only the supplied action evidence. complete is false when the response merely announces "
        "future work, asks the user to wait, prints commands that were not executed, searches the wrong target, or "
        "leaves failed actions unresolved. An accurate result, an evidence-backed not-found result, a clear safety "
        "denial, or a precise limitation can be complete. needs_action is true only when another supplied runtime "
        "tool could materially advance the request now. Do not solve the task yourself.\n\n"
        f"USER REQUEST\n{truncate(latest_user_task(messages), 2400)}\n\n"
        f"ACTION CONTRACT\n{json.dumps(contract, sort_keys=True)}\n\n"
        f"ACTION EVIDENCE\n{_recent_action_evidence(messages) or '(none)'}\n\n"
        f"CANDIDATE RESPONSE\n{truncate(candidate, 5000)}"
    )
    provider_state = {
        name: getattr(provider, name)
        for name in ("last_stats", "current_stats", "current_model", "stream_phase")
        if hasattr(provider, name)
    }
    try:
        raw = _collect_orchestrator_response(
            provider,
            model,
            [
                {
                    "role": "system",
                    "content": "You are a completion arbiter inside an agent runtime. Output strict JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            runtime,
            cancel_event,
            140,
            ACTION_COMPLETION_SCHEMA,
        )
    finally:
        for name, value in provider_state.items():
            try:
                setattr(provider, name, value)
            except (AttributeError, TypeError):
                pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError, RecursionError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    complete = parsed.get("complete")
    needs_action = parsed.get("needs_action")
    confidence = parsed.get("confidence")
    if not isinstance(complete, bool) or not isinstance(needs_action, bool) or isinstance(confidence, bool):
        return {}
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
        return {}
    return {
        "complete": complete,
        "needs_action": needs_action,
        "confidence": round(confidence_value, 3),
        "reason": truncate(str(parsed.get("reason") or "completion unclear"), 180),
        "model": model,
    }


def action_completion_required(route: dict[str, Any], action_steps: int, agent_enabled: bool) -> bool:
    if not agent_enabled or str(route.get("mode") or "") != "orchestrator":
        return False
    contract = route.get("action_contract")
    return action_steps > 0 or bool(isinstance(contract, dict) and contract.get("capability"))


def action_completion_directive(assessment: dict[str, Any]) -> str:
    reason = str(assessment.get("reason") or "the result did not complete the requested action")
    if assessment.get("needs_action"):
        next_step = "Request exactly one appropriate supplied function tool now and continue from its result."
    else:
        next_step = "Return an honest final result, limitation, or one precise blocking question now."
    return (
        "Coordinator completion requirement: The previous candidate was not complete: "
        + reason
        + ". "
        + next_step
        + " Do not ask the user to wait, print an unexecuted command, or promise future work."
    )


def format_model_profiles() -> str:
    registry = load_registry(PATHS)
    if not registry:
        return "Model registry is not initialized. Run `dairack init`."
    lines = ["Generated hardware profiles:"]
    for record in registry.models.values():
        profile = runtime_profile_for(record.descriptor.name, PATHS)
        lines.append(
            f"- {record.descriptor.name}: {profile['role']} | {profile['fit']} fit | "
            f"ctx {profile['num_ctx']} | think {'on' if profile['think'] else 'off'} | "
            f"options {profile.get('options') or {}}"
        )
    lines.append("")
    lines.append("Profiles are derived from Ollama metadata and local hardware. Refresh with `dairack init`.")
    return "\n".join(lines)


def format_current_profile(config: dict[str, Any]) -> str:
    model = str(config.get("model") or "")
    profile = model_profile_for(model)
    override = model_profile_override(config, model) if model else {}
    lines = [
        f"Model: {model or '(none)'}",
        f"Automatic: {profile['name'] + ' - ' + profile['role'] if profile else 'not initialized'}",
        f"Effective ctx: {config.get('num_ctx')}",
        f"Effective think: {'on' if config.get('think') else 'off'}",
        f"Effective options: {json.dumps(ollama_options(config), sort_keys=True)}",
        override_summary(override),
        "",
        "Tune with /profile set ctx N, /profile set batch N, /profile set threads N, /profile set gpu N, /profile set think on|off.",
        "Use /profile reset to remove overrides for this selected model.",
    ]
    return "\n".join(lines)


def set_profile_override(config: dict[str, Any], model: str, field: str, raw: str) -> str:
    override = ensure_model_profile_override(config, model)
    field = field.lower().replace("-", "_")
    if field in {"ctx", "context", "num_ctx"}:
        value = max(512, min(262144, int(raw)))
        override["num_ctx"] = value
        return f"ctx override set to {value}"
    if field in {"threads", "thread", "num_thread"}:
        value = max(1, min(64, int(raw)))
        options = override.setdefault("model_options", {})
        if not isinstance(options, dict):
            options = {}
            override["model_options"] = options
        options["num_thread"] = value
        return f"thread override set to {value}"
    if field in {"batch", "num_batch"}:
        value = max(1, min(4096, int(raw)))
        options = override.setdefault("model_options", {})
        if not isinstance(options, dict):
            options = {}
            override["model_options"] = options
        options["num_batch"] = value
        return f"batch override set to {value}"
    if field in {"gpu", "num_gpu", "gpu_layers", "layers"}:
        value = max(0, min(999, int(raw)))
        options = override.setdefault("model_options", {})
        if not isinstance(options, dict):
            options = {}
            override["model_options"] = options
        options["num_gpu"] = value
        return f"gpu override set to {value}"
    if field == "think":
        if raw.lower() not in {"on", "off", "true", "false", "1", "0"}:
            raise ValueError("think must be on or off")
        override["think"] = raw.lower() in {"on", "true", "1"}
        return f"think override set to {'on' if override['think'] else 'off'}"
    raise ValueError("supported fields: ctx, batch, threads, gpu, think")


def persist_profile_override(config: dict[str, Any], model: str) -> bool:
    overrides = config.get("profile_overrides")
    if not isinstance(overrides, dict):
        return False
    key = model_override_key(config, model)
    value = overrides.get(key)
    if not isinstance(value, dict) or not save_runtime_override(model, value, PATHS):
        return False
    del overrides[key]
    return True


def reset_profile_override(config: dict[str, Any], model: str) -> bool:
    overrides = config.get("profile_overrides")
    if not isinstance(overrides, dict):
        config["profile_overrides"] = {}
        return clear_runtime_override(model, PATHS)
    key = model_override_key(config, model)
    removed = overrides.pop(key, None) is not None
    return clear_runtime_override(model, PATHS) or removed


def ansi(text: str, code: str) -> str:
    if not sys.stdout.isatty() or env_enabled("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.lexers import Lexer
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Box, Frame, TextArea

    HAS_PROMPT_TOOLKIT = True
except Exception:
    HAS_PROMPT_TOOLKIT = False


if HAS_PROMPT_TOOLKIT:

    class DairackTranscriptLexer(Lexer):  # type: ignore[misc]
        def lex_document(self, document: Document) -> Any:
            def get_line(lineno: int) -> list[tuple[str, str]]:
                line = document.lines[lineno]
                stripped = line.strip()
                if stripped in {"you", "dairack", "coordinator", "system", "action", "diff"}:
                    return [(f"class:role.{stripped if stripped != 'dairack' else 'ai'}", line)]
                if line.startswith("+") and not line.startswith("+++"):
                    return [("class:diff.add", line)]
                if line.startswith("-") and not line.startswith("---"):
                    return [("class:diff.del", line)]
                if line.startswith("@@"):
                    return [("class:diff.hunk", line)]
                if line.startswith("diff --git") or line.startswith("+++ ") or line.startswith("--- "):
                    return [("class:diff.file", line)]
                if line.startswith("$ "):
                    return [("class:command", line)]
                return [("class:transcript", line)]

            return get_line


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_config() -> dict[str, Any]:
    try:
        return load_app_config(PATHS)
    except Exception as exc:
        raise SystemExit(
            f"refusing to overwrite unreadable configuration at {CONFIG_PATH}: {exc}\n"
            "Repair or move that file, then restart Dairack."
        ) from exc


def save_config(config: dict[str, Any]) -> None:
    save_app_config(config, PATHS)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def chat_path(chat_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", chat_id.strip())
    return CHAT_DIR / f"{safe_id}.json"


def new_chat_id() -> str:
    base = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
    candidate = base
    suffix = 2
    while chat_path(candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def clean_chat_title(value: str, fallback: str = "new chat") -> str:
    title = re.sub(r"\s+", " ", value).strip()
    if not title:
        title = fallback
    return title[:80]


def message_count(messages: list[dict[str, str]]) -> int:
    return sum(1 for message in messages if message.get("role") in {"user", "assistant"})


def infer_chat_title(messages: list[dict[str, str]], current: str = "") -> str:
    current = clean_chat_title(current, "")
    if current and current != "new chat":
        return current
    ignored_prefixes = (
        "Shell tool result:",
        "Patch tool result:",
        "Tool result:",
        "Tool request denied",
    )
    for message in messages:
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if not content or content.startswith(ignored_prefixes):
            continue
        return clean_chat_title(content)
    return current or "new chat"


def config_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def config_int(config: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def config_float(config: dict[str, Any], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def agent_action_limit(config: dict[str, Any]) -> int:
    return config_int(
        config,
        "max_agent_steps",
        DEFAULT_AGENT_ACTION_LIMIT,
        1,
        MAX_AGENT_ACTION_LIMIT,
    )


def agent_synthesis_directive(used: int, limit: int, retry: bool = False) -> str:
    lead = "Your previous response still attempted an action, but actions are unavailable. " if retry else ""
    return (
        f"{lead}The task action budget is exhausted after {used} of {limit} executed actions. "
        "Do not request or describe another tool action. Produce the best complete final answer now from the "
        "evidence already collected. State any unfinished verification briefly and concretely."
    )


def chat_summary_upto(chat: dict[str, Any]) -> int:
    try:
        value = int(chat.get("summary_upto") or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


def new_chat_state(cwd: Path, config: dict[str, Any], title: str = "") -> dict[str, Any]:
    stamp = now_iso()
    return {
        "id": new_chat_id(),
        "title": clean_chat_title(title, "new chat"),
        "created_at": stamp,
        "updated_at": stamp,
        "cwd": str(cwd),
        "project_root": "",
        "model": str(config.get("model") or ""),
        "model_mode": str(config.get("model_mode") or "direct"),
        "orchestrator_policy": str(config.get("orchestrator_policy") or "adaptive"),
        "last_route": {},
        "route_history": [],
        "summary": "",
        "summary_upto": 1,
        "summary_format": GROUNDED_MEMORY_FORMAT,
        "last_compacted_at": "",
    }


def sanitize_messages(
    raw: Any,
    cwd: Path,
    agent: bool,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            message: dict[str, Any] = {"role": role, "content": str(item.get("content") or "")}
            image_paths = item.get("image_paths")
            if role == "user" and isinstance(image_paths, list):
                cleaned = [str(value) for value in image_paths if str(value).strip()]
                if cleaned:
                    message["image_paths"] = cleaned[:MAX_ATTACHED_IMAGES]
            if role == "assistant" and isinstance(item.get("tool_calls"), list):
                tool_calls: list[dict[str, Any]] = []
                for raw_call in item["tool_calls"]:
                    if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
                        continue
                    function = raw_call["function"]
                    name = str(function.get("name") or "").strip()
                    arguments = function.get("arguments")
                    if not name or not isinstance(arguments, (dict, str)):
                        continue
                    cleaned_function: dict[str, Any] = {"name": name, "arguments": arguments}
                    if function.get("index") is not None:
                        cleaned_function["index"] = function["index"]
                    cleaned_call: dict[str, Any] = {"type": "function", "function": cleaned_function}
                    call_id = str(raw_call.get("id") or "").strip()
                    if call_id:
                        cleaned_call["id"] = call_id
                    tool_calls.append(cleaned_call)
                if tool_calls:
                    message["tool_calls"] = tool_calls
            if role == "tool":
                tool_name = str(item.get("tool_name") or "").strip()
                if not tool_name:
                    continue
                message["tool_name"] = tool_name
            messages.append(message)
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": system_prompt(cwd, agent, config)})
    else:
        messages[0] = {"role": "system", "content": system_prompt(cwd, agent, config)}
    return messages


def sanitize_blocks(raw: Any) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return blocks
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role in {"asusai", "dairack"}:
            role = "assistant"
        if role not in {"you", "assistant", "coordinator", "system", "action", "diff"}:
            continue
        block = {"role": role, "text": str(item.get("text") or "")}
        severity = str(item.get("severity") or "info")
        if role == "system" and severity in {"success", "warning", "error"}:
            block["severity"] = severity
        if role == "system" and str(item.get("kind") or "") == "reference":
            block["kind"] = "reference"
        blocks.append(block)
    return blocks


def blocks_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    system_like = (
        "Shell tool result:",
        "Patch tool result:",
        "Structured tool result:",
        "Tool result:",
        "Tool request denied",
        "Runtime event:",
    )
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if not content or role == "system":
            continue
        if role == "user":
            if content.startswith("Coordinator specialist result:"):
                block_role = "coordinator"
            else:
                block_role = "system" if content.startswith(system_like) else "you"
        elif role == "assistant":
            block_role = "assistant"
        elif role == "tool":
            block_role = "system"
        else:
            continue
        image_paths = message.get("image_paths")
        if block_role == "you" and isinstance(image_paths, list) and image_paths:
            labels = "  ".join(f"[IMAGE {Path(str(path)).name}]" for path in image_paths)
            content = labels + ("\n\n" + content if content else "")
        blocks.append({"role": block_role, "text": content})
    return blocks


def load_chat_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["id"] = str(data.get("id") or path.stem)
    return data


def list_chat_sessions(limit: int = 30) -> list[dict[str, Any]]:
    if not CHAT_DIR.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for path in CHAT_DIR.glob("*.json"):
        data = load_chat_file(path)
        if data is not None:
            sessions.append(data)
    sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return sessions[:limit]


def resolve_chat_session(ref: str = "latest") -> dict[str, Any]:
    sessions = list_chat_sessions(limit=200)
    if not sessions:
        raise ValueError("no saved chats yet")
    ref = (ref or "latest").strip()
    if ref in {"latest", "last"}:
        return sessions[0]
    if ref.isdigit():
        index = int(ref) - 1
        if 0 <= index < len(sessions):
            return sessions[index]
        raise ValueError(f"chat number out of range: {ref}")
    exact = [session for session in sessions if str(session.get("id") or "") == ref]
    if exact:
        return exact[0]
    matches = [session for session in sessions if str(session.get("id") or "").startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(str(session.get("id")) for session in matches[:5])
        raise ValueError(f"ambiguous chat id prefix; matches: {ids}")
    raise ValueError(f"chat not found: {ref}")


def sanitize_route_history(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)][-80:]


def saved_project_root(session: dict[str, Any]) -> str:
    configured = str(session.get("project_root") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            return str(candidate.resolve())
    return ""


def chat_runtime_state(
    session: dict[str, Any],
    fallback_cwd: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], Path, list[dict[str, str]], list[dict[str, str]]]:
    cwd = fallback_cwd
    saved_cwd = str(session.get("cwd") or "").strip()
    if saved_cwd:
        candidate = Path(saved_cwd).expanduser()
        if candidate.is_dir():
            cwd = candidate.resolve()

    project_root = ""
    requested_project_root = saved_project_root(session)
    if requested_project_root:
        validated_scope = project_scope_for_chat({"project_root": requested_project_root}, cwd)
        if validated_scope.resolve() == Path(requested_project_root).resolve():
            project_root = requested_project_root
    fallback_mode = str(config.get("model_mode") or "direct")
    if fallback_mode not in {"direct", "orchestrator"}:
        fallback_mode = "direct"
    model_mode = str(session.get("model_mode") or fallback_mode)
    if model_mode not in {"direct", "orchestrator"}:
        model_mode = fallback_mode
    fallback_policy = str(config.get("orchestrator_policy") or "adaptive")
    if fallback_policy not in ORCHESTRATOR_POLICIES:
        fallback_policy = "adaptive"
    orchestrator_policy = str(session.get("orchestrator_policy") or fallback_policy)
    if orchestrator_policy not in ORCHESTRATOR_POLICIES:
        orchestrator_policy = fallback_policy

    chat = {
        "id": str(session.get("id") or new_chat_id()),
        "title": clean_chat_title(str(session.get("title") or ""), "new chat"),
        "created_at": str(session.get("created_at") or now_iso()),
        "updated_at": str(session.get("updated_at") or now_iso()),
        "cwd": str(cwd),
        "project_root": project_root,
        "model": str(session.get("model") or config.get("model") or ""),
        "model_mode": model_mode,
        "orchestrator_policy": orchestrator_policy,
        "last_route": dict(session.get("last_route") or {}) if isinstance(session.get("last_route"), dict) else {},
        "route_history": sanitize_route_history(session.get("route_history")),
        "summary": str(session.get("summary") or ""),
        "summary_upto": chat_summary_upto(session),
        "summary_format": str(session.get("summary_format") or ""),
        "last_compacted_at": str(session.get("last_compacted_at") or ""),
    }
    messages = sanitize_messages(session.get("messages"), cwd, bool(config.get("agent")), config)
    blocks = sanitize_blocks(session.get("blocks"))
    if not blocks:
        blocks = blocks_from_messages(messages)
    return chat, cwd, messages, blocks


def save_chat_session(
    chat: dict[str, Any],
    cwd: Path,
    config: dict[str, Any],
    messages: list[dict[str, str]],
    blocks: list[dict[str, str]],
) -> Path:
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    chat.setdefault("id", new_chat_id())
    chat.setdefault("created_at", now_iso())
    chat["updated_at"] = now_iso()
    chat["cwd"] = str(cwd)
    chat["model"] = str(config.get("model") or "")
    chat["model_mode"] = str(config.get("model_mode") or "direct")
    chat["orchestrator_policy"] = str(config.get("orchestrator_policy") or "adaptive")
    chat["title"] = infer_chat_title(messages, chat.get("title", ""))
    last_route = dict(chat.get("last_route") or {}) if isinstance(chat.get("last_route"), dict) else {}
    route_history = sanitize_route_history(chat.get("route_history"))
    if last_route:
        route_key = str(last_route.get("created_at") or "")
        matched = next(
            (
                index
                for index, item in enumerate(route_history)
                if route_key and str(item.get("created_at") or "") == route_key
            ),
            -1,
        )
        if matched >= 0:
            route_history[matched] = last_route
        else:
            route_history.append(last_route)
        route_history = route_history[-80:]
    chat["route_history"] = route_history
    path = chat_path(chat["id"])
    payload = {
        "id": chat["id"],
        "title": chat["title"],
        "created_at": chat["created_at"],
        "updated_at": chat["updated_at"],
        "cwd": str(cwd),
        "project_root": str(chat.get("project_root") or ""),
        "model": str(config.get("model") or ""),
        "model_mode": str(config.get("model_mode") or "direct"),
        "orchestrator_policy": str(config.get("orchestrator_policy") or "adaptive"),
        "last_route": last_route,
        "route_history": route_history,
        "provider": str(config.get("provider") or "ollama"),
        "num_ctx": int(config.get("num_ctx") or 4096),
        "summary": str(chat.get("summary") or ""),
        "summary_upto": chat_summary_upto(chat),
        "summary_format": str(chat.get("summary_format") or ""),
        "last_compacted_at": str(chat.get("last_compacted_at") or ""),
        "messages": messages,
        "blocks": blocks,
    }
    atomic_write_json(path, payload)
    return path


def format_chat_list(limit: int = 20) -> str:
    sessions = list_chat_sessions(limit=limit)
    if not sessions:
        return "No saved chats yet."
    lines = ["Saved chats:"]
    for index, session in enumerate(sessions, 1):
        messages = sanitize_messages(session.get("messages"), Path.cwd(), False)
        updated = str(session.get("updated_at") or "").replace("T", " ")[:16] or "unknown"
        chat_id = str(session.get("id") or "")
        title = clean_chat_title(str(session.get("title") or ""), "new chat")
        lines.append(f"{index:>2}. {updated:<16}  {chat_id:<22}  {message_count(messages):>3} msgs  {title}")
    lines.append("")
    lines.append("Resume with /resume <number> or /resume <id>.")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    images = message.get("image_paths")
    image_cost = len(images) * 1200 if isinstance(images, list) else 0
    tool_cost = (
        estimate_tokens(json.dumps(message.get("tool_calls"), sort_keys=True)) if message.get("tool_calls") else 0
    )
    return (
        6
        + estimate_tokens(str(message.get("role") or ""))
        + estimate_tokens(str(message.get("content") or ""))
        + image_cost
        + tool_cost
    )


def chat_executor(config: dict[str, Any], chat: dict[str, Any] | None = None) -> str:
    if chat is not None:
        route = chat.get("last_route")
        if isinstance(route, dict) and route.get("executor"):
            return str(route["executor"])
    return str(config.get("model") or "")


def context_runtime_config(
    config: dict[str, Any],
    chat: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    executor = chat_executor(config, chat)
    if not executor:
        return config, ""
    return runtime_config_for_model(config, executor), executor


def context_budget(config: dict[str, Any]) -> int:
    num_ctx = int(config.get("num_ctx") or 4096)
    try:
        ratio = float(config.get("context_budget_ratio") or 0.82)
    except (TypeError, ValueError):
        ratio = 0.82
    ratio = min(0.95, max(0.45, ratio))
    return max(512, int(num_ctx * ratio))


def _context_message_groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Keep native tool requests and their results indivisible during context fitting."""
    groups: list[list[dict[str, Any]]] = []
    for message in messages:
        if (
            str(message.get("role") or "") == "tool"
            and groups
            and str(groups[-1][0].get("role") or "") == "assistant"
            and bool(groups[-1][0].get("tool_calls"))
        ):
            groups[-1].append(message)
        else:
            groups.append([message])
    return groups


def _context_tool_evidence(content: str) -> str:
    """Reduce one structured result to evidence that survives micro-context shedding."""
    metadata, separator, output = content.partition("\noutput:\n")
    fields: dict[str, str] = {}
    for line in metadata.splitlines()[1:]:
        key, marker, value = line.partition(":")
        if marker:
            fields[key.strip().lower()] = " ".join(value.split())
    metadata_lines = metadata.splitlines()
    label = fields.get("summary") or fields.get("tool") or (metadata_lines[0] if metadata_lines else "tool result")
    if fields.get("exit_code"):
        label += f" / exit {fields['exit_code']}"
    if not separator:
        return label[:360]
    evidence = [" ".join(line.split()) for line in output.splitlines() if line.strip()]
    selected = evidence[:2]
    if len(evidence) > 2:
        selected.extend(line for line in evidence[-2:] if line not in selected)
    suffix = " | " + " | ".join(selected) if selected else ""
    return (label + suffix)[:520]


def _omitted_tool_ledger(
    groups: list[list[dict[str, Any]]],
    kept_indices: set[int],
    budget: int,
) -> str:
    entries: list[str] = []
    for index, group in enumerate(groups):
        if index in kept_indices:
            continue
        for message in group:
            content = str(message.get("content") or "")
            if message.get("role") == "tool" or content.startswith(TOOL_RESULT_PREFIXES):
                entries.append("- " + _context_tool_evidence(content))
    if not entries:
        return ""
    max_chars = max(480, min(1800, int(budget * 0.22)))
    rendered = "Compressed evidence from omitted active tool exchanges:\n" + "\n".join(entries[-8:])
    return truncate_middle(rendered, max_chars)


def active_context_messages(
    messages: list[dict[str, Any]],
    summary: str,
    config: dict[str, Any],
    reserve_tokens: int = 0,
    summary_required: bool = False,
) -> list[dict[str, Any]]:
    if not messages:
        return []
    budget = max(512, context_budget(config) - max(0, int(reserve_tokens)))
    if (
        summary.strip()
        and not summary_required
        and sum(estimate_message_tokens(message) for message in messages) <= budget
    ):
        return list(messages)
    system = messages[0]
    running = estimate_message_tokens(system)

    summary_message: dict[str, Any] | None = None
    if summary.strip():
        summary_text = truncate(summary.strip(), max(1200, budget * 2))
        summary_message = {
            "role": "system",
            "content": "Persistent summary of earlier conversation:\n" + summary_text,
        }
        running += estimate_message_tokens(summary_message)

    groups = _context_message_groups(messages[1:])
    task_group = -1
    for index, group in enumerate(groups):
        if any(
            message.get("role") == "user"
            and str(message.get("content") or "").strip()
            and not str(message.get("content") or "").startswith(TOOL_RESULT_PREFIXES)
            for message in group
        ):
            task_group = index
    required = {index for index in (len(groups) - 1, task_group) if index >= 0}
    optional_system_prefixes = (
        "Compressed evidence from omitted active tool exchanges:",
        "Retrieved local project memory.",
        "Project retrieval is inactive because",
    )
    required.update(
        index
        for index, group in enumerate(groups)
        if group
        and group[0].get("role") == "system"
        and not str(group[0].get("content") or "").startswith(optional_system_prefixes)
        and not re.match(
            r"^\d+ earlier saved messages? (?:are|is) omitted from this active context window\.",
            str(group[0].get("content") or ""),
        )
    )
    priority = [
        *sorted(required, reverse=True),
        *(index for index in range(len(groups) - 1, -1, -1) if index not in required),
    ]
    kept_indices: set[int] = set()
    for index in priority:
        group = groups[index]
        cost = sum(estimate_message_tokens(message) for message in group)
        if index in required or running + cost <= budget:
            kept_indices.add(index)
            running += cost

    omitted = max(0, len(messages) - 1 - sum(len(groups[index]) for index in kept_indices))

    include_context_notices = True

    def build_selected() -> list[dict[str, Any]]:
        selected = [system]
        if summary_message:
            selected.append(summary_message)
        ledger = _omitted_tool_ledger(groups, kept_indices, budget) if include_context_notices else ""
        if ledger:
            selected.append({"role": "system", "content": ledger})
        if omitted and include_context_notices:
            selected.append(
                {
                    "role": "system",
                    "content": (
                        f"{omitted} earlier saved messages are omitted from this active context window. "
                        "Use the persistent summary and ask for /open or /search when exact old details are needed."
                    ),
                }
            )
        for index in sorted(kept_indices):
            selected.extend(groups[index])
        return selected

    selected = build_selected()
    removable = sorted(kept_indices - required)
    while removable and sum(estimate_message_tokens(message) for message in selected) > budget:
        index = removable.pop(0)
        kept_indices.remove(index)
        omitted += len(groups[index])
        selected = build_selected()

    if sum(estimate_message_tokens(message) for message in selected) > budget:
        include_context_notices = False
        selected = build_selected()

    # Current task evidence has priority over macro memory. A large summary is
    # retained in saved state and can return on the next turn; it must not make
    # an otherwise valid current tool exchange impossible to continue.
    if sum(estimate_message_tokens(message) for message in selected) > budget and summary_message:
        selected_without_summary = [message for message in selected if message is not summary_message]
        fixed_tokens = sum(estimate_message_tokens(message) for message in selected_without_summary)
        available_tokens = max(0, budget - fixed_tokens - 12)
        if available_tokens >= 48:
            prefix = "Persistent summary of earlier conversation:\n"
            char_limit = max(120, (available_tokens - estimate_tokens(prefix) - 8) * 4)
            summary_message["content"] = prefix + truncate(summary.strip(), char_limit)
        elif summary_required:
            summary_message["content"] = (
                "Persistent summary of earlier conversation temporarily omitted while current action evidence "
                "uses the request window. The grounded memory remains saved."
            )
        else:
            summary_message = None
        selected = build_selected()

    # A caller may pass an already-materialized macro-memory message back
    # through the final provider fitter. Shrink that copy too; otherwise its
    # durable marker makes it indivisible on the second fitting pass.
    if sum(estimate_message_tokens(message) for message in selected) > budget:
        for index, message in enumerate(selected):
            content = str(message.get("content") or "")
            prefix = "Persistent summary of earlier conversation:\n"
            if not content.startswith(prefix):
                continue
            fixed_tokens = sum(
                estimate_message_tokens(candidate)
                for candidate_index, candidate in enumerate(selected)
                if candidate_index != index
            )
            available_tokens = max(0, budget - fixed_tokens - 12)
            if available_tokens >= 48:
                char_limit = max(120, (available_tokens - estimate_tokens(prefix) - 8) * 4)
                replacement = prefix + truncate(content[len(prefix) :], char_limit)
            else:
                replacement = (
                    "Persistent summary of earlier conversation temporarily omitted while current action evidence "
                    "uses the request window. The grounded memory remains saved."
                )
            selected[index] = {**message, "content": replacement}
            break
    return selected


def summarized_context_source(
    messages: list[dict[str, Any]],
    chat: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return saved history not already represented by grounded memory."""
    if not messages:
        return []
    summary = str(chat.get("summary") or "").strip()
    if not summary:
        return list(messages)
    upto = min(max(1, chat_summary_upto(chat)), len(messages))
    return [messages[0], *messages[upto:]]


class RequestContextError(ValueError):
    pass


def fit_request_context_messages(
    messages: list[dict[str, Any]],
    config: dict[str, Any],
    tools: list[dict[str, Any]] | None = None,
    finalizing: bool = False,
) -> list[dict[str, Any]]:
    """Fit the final provider payload, including tool schemas, tokenizer uncertainty, and generation room.

    Routing headroom is deliberately excluded here: coordinator stages run as
    separate capped requests and do not share the executor window. The
    compaction estimator includes it so memory compacts slightly before the
    fitter would ever fail — intentional hysteresis, not drift.
    """
    canonical = canonicalize_messages(messages)
    sections = expand_system_messages(canonical)
    tool_tokens = estimate_tokens(json.dumps(tools, sort_keys=True)) if tools else 0
    tokenizer_headroom, _routing_headroom = context_request_headroom(config)
    reserve_tokens = tool_tokens + tokenizer_headroom + response_token_reserve(config, finalizing)
    fitted = active_context_messages(
        sections,
        "",
        config,
        reserve_tokens=reserve_tokens,
    )
    fitted = canonicalize_messages(fitted)
    estimated_payload = sum(estimate_message_tokens(message) for message in fitted) + reserve_tokens
    if estimated_payload > context_budget(config):
        raise RequestContextError(
            "the current request cannot fit safely in this model's context window; "
            "shorten the prompt, detach images, increase /ctx, or start /new"
        )
    return fitted


def strip_tool_catalog_for_native(content: str) -> str:
    """Drop the prose tool catalog when native schemas accompany the request.

    The catalog exists for the compatibility text protocol; with native tools the
    schemas are authoritative and the duplicate listing wastes context budget.
    Unmatched markers leave the content unchanged.
    """
    start = content.find("Available tools:")
    end = content.find("Compatibility fallback only:")
    if start == -1 or end == -1 or end <= start:
        return content
    return content[:start] + content[end:]


def strip_native_system_catalog(message: dict[str, Any]) -> dict[str, Any]:
    """Strip compatibility prose without losing budget-aware system sections."""
    parts = message.get(SYSTEM_PARTS_KEY)
    if isinstance(parts, list):
        stripped_parts = [strip_tool_catalog_for_native(str(part)) for part in parts]
        return {
            **message,
            "content": "\n\n".join(part for part in stripped_parts if part.strip()),
            SYSTEM_PARTS_KEY: stripped_parts,
        }
    return {
        **message,
        "content": strip_tool_catalog_for_native(str(message.get("content") or "")),
    }


def compatibility_tool_history_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert native tool history into the text protocol before schemas are shed.

    A provider request without native schemas must not contain native assistant
    calls or ``tool`` roles. Structured results remain model-visible as user
    messages, preserving evidence without violating the provider protocol.
    """
    compatible: list[dict[str, Any]] = []
    for message in canonicalize_messages(messages):
        role = str(message.get("role") or "")
        if role == "assistant" and message.get("tool_calls"):
            content = str(message.get("content") or "").strip()
            if content:
                compatible.append({key: value for key, value in message.items() if key != "tool_calls"})
            continue
        if role == "tool":
            compatible.append(
                {
                    key: value
                    for key, value in {**message, "role": "user"}.items()
                    if key not in {"tool_name", "tool_call_id", "name"}
                }
            )
            continue
        compatible.append(dict(message))
    return compatible


def fit_agent_request_context_messages(
    messages: list[dict[str, Any]],
    config: dict[str, Any],
    tools: list[dict[str, Any]] | None,
    finalizing: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fit a request, preferring native tools but retaining the compatibility protocol when space is tight."""
    posture = context_posture_directive(config)
    posture_directives = [posture] if posture else []
    native_tools = list(tools or [])
    if native_tools:
        native_request = canonicalize_messages(messages, [NATIVE_TOOL_DIRECTIVE, *posture_directives])
        if native_request and native_request[0].get("role") == "system":
            native_request[0] = strip_native_system_catalog(native_request[0])
        try:
            return (
                fit_request_context_messages(native_request, config, native_tools, finalizing=finalizing),
                native_tools,
            )
        except RequestContextError:
            pass
    compat = canonicalize_messages(compatibility_tool_history_messages(messages), posture_directives)
    return fit_request_context_messages(compat, config, finalizing=finalizing), []


def context_report(
    messages: list[dict[str, str]],
    summary: str,
    config: dict[str, Any],
    chat: dict[str, Any] | None = None,
) -> str:
    state = context_state(messages, summary, config, chat)
    runtime = state["runtime"]
    executor = str(state["executor"])
    saved_tokens = sum(estimate_message_tokens(message) for message in messages)
    saved_chat_messages = sum(1 for message in messages[1:] if message.get("role") in {"user", "assistant"})
    active_chat_messages = sum(1 for message in state["messages"] if message.get("role") in {"user", "assistant"})
    omitted = max(0, saved_chat_messages - active_chat_messages)
    lines = [
        f"saved messages: {len(messages)} ({saved_tokens} est tokens)",
        f"request window: {int(runtime.get('num_ctx') or 4096)} tokens" + (f" / {executor}" if executor else ""),
        f"next request: {state['mode']} {state['request_tokens']}/{state['budget']} est tokens "
        f"({state['ratio'] * 100:.0f}%)",
        f"macro memory: {state['macro_tokens']} est tokens",
        f"live working set: {state['micro_tokens']} est tokens / {active_chat_messages} messages",
        f"runtime foundation: {state['foundation_tokens']} est tokens",
        f"tool interface: {state['tool_tokens']} est tokens",
        f"safety headroom: {state['headroom_tokens']} est tokens",
        f"answer reserve: {state['answer_reserve']} tokens",
        f"omitted from active request: {omitted} messages",
        f"summary: {'yes' if summary.strip() else 'no'}",
    ]
    if chat is not None:
        _should, reason = should_auto_compact(messages, chat, runtime)
        lines.append(f"auto compact: {'on' if config_bool(runtime, 'auto_compact', True) else 'off'} ({reason})")
        lines.append(f"summary covers messages before index: {chat_summary_upto(chat)}")
    return "\n".join(lines)


def compact_candidate_range(
    messages: list[dict[str, str]],
    chat: dict[str, Any],
    keep_recent: int,
) -> tuple[int, int]:
    legacy_summary = bool(str(chat.get("summary") or "").strip()) and (
        str(chat.get("summary_format") or "") != GROUNDED_MEMORY_FORMAT
    )
    start = 1 if legacy_summary else min(max(1, chat_summary_upto(chat)), len(messages))
    end = max(1, len(messages) - keep_recent) if keep_recent > 0 else len(messages)
    if legacy_summary:
        end = max(end, min(chat_summary_upto(chat), len(messages)))
    end = min(max(start, end), len(messages))
    return start, end


def _latest_task_message_index(messages: list[dict[str, Any]]) -> int:
    for index in range(len(messages) - 1, 0, -1):
        message = messages[index]
        content = str(message.get("content") or "").strip()
        if message.get("role") == "user" and content and not content.startswith(TOOL_RESULT_PREFIXES):
            return index
    return len(messages)


def _coherent_compaction_end(messages: list[dict[str, Any]], end: int, maximum: int) -> int:
    """Do not split an assistant native call from its contiguous tool results."""
    bounded = min(max(1, end), maximum)
    if bounded < maximum and str(messages[bounded].get("role") or "") == "tool":
        while bounded < maximum and str(messages[bounded].get("role") or "") == "tool":
            bounded += 1
    return bounded


def _summary_context_message(summary: str, config: dict[str, Any]) -> dict[str, Any]:
    budget = context_budget(config)
    return {
        "role": "system",
        "content": "Persistent summary of earlier conversation:\n" + truncate(summary.strip(), max(1200, budget * 2)),
    }


def _request_payload_estimates(
    source: list[dict[str, Any]],
    summary: str,
    config: dict[str, Any],
) -> tuple[int, int]:
    """Estimate native and compatibility payloads with per-turn routing headroom."""
    summary_message = _summary_context_message(summary, config) if summary.strip() else None
    compatibility = compatibility_tool_history_messages(source)
    if summary_message:
        compatibility.insert(min(1, len(compatibility)), summary_message)

    native = canonicalize_messages(source, [NATIVE_TOOL_DIRECTIVE])
    if native and native[0].get("role") == "system":
        native[0] = strip_native_system_catalog(native[0])
    if summary_message:
        native.insert(min(1, len(native)), summary_message)

    tokenizer_headroom, routing_headroom = context_request_headroom(config)
    common_reserve = tokenizer_headroom + routing_headroom + response_token_reserve(config)
    compatibility_estimate = sum(estimate_message_tokens(message) for message in compatibility) + common_reserve
    native_estimate = compatibility_estimate
    if config_bool(config, "agent", True):
        native_estimate = (
            sum(estimate_message_tokens(message) for message in native)
            + estimate_tokens(json.dumps(agent_tool_schemas(), sort_keys=True))
            + common_reserve
        )
    return native_estimate, compatibility_estimate


def context_request_headroom(config: dict[str, Any]) -> tuple[int, int]:
    """Return tokenizer uncertainty and per-turn routing reserve for one model profile."""
    num_ctx = int(config.get("num_ctx") or 4096)
    return max(256, int(num_ctx * 0.04)), max(160, int(num_ctx * 0.03))


def response_token_reserve(config: dict[str, Any], finalizing: bool = False) -> int:
    """Extra prompt-budget reserve that guarantees generation room beyond the ratio's residual.

    The budget ratio implicitly leaves ``num_ctx - budget`` for the answer. That
    residual is adequate for ordinary action turns at default settings, so this
    returns zero there; it binds when a raised budget ratio, a small window,
    thinking mode, or final synthesis would otherwise let a packed prompt starve
    generation into a truncated or empty response.
    """
    num_ctx = int(config.get("num_ctx") or 4096)
    if finalizing:
        floor = max(512, min(2048, int(num_ctx * 0.24)))
    else:
        floor = max(320, min(1024, int(num_ctx * 0.14)))
    if config_bool(config, "think", False):
        floor += max(256, int(num_ctx * 0.08))
    floor = min(floor, int(num_ctx * 0.45))
    implicit = max(0, num_ctx - context_budget(config))
    return max(0, floor - implicit)


def executor_response_allowance(
    request_messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    config: dict[str, Any],
) -> int:
    """Generation cap for one executor request: the true window residual.

    Passed as num_predict so the provider stops at the boundary instead of
    context-shifting the prompt away; truncation stays honest and recoverable
    through the turn ladder rather than silently corrupting the request.
    """
    tokenizer_headroom, _routing_headroom = context_request_headroom(config)
    prompt_tokens = sum(estimate_message_tokens(message) for message in request_messages)
    if tools:
        prompt_tokens += estimate_tokens(json.dumps(tools, sort_keys=True))
    num_ctx = int(config.get("num_ctx") or 4096)
    return max(256, num_ctx - prompt_tokens - tokenizer_headroom)


def executor_keep_alive(config: dict[str, Any]) -> str | None:
    """Residency hint for executor requests; empty configuration defers to the provider default."""
    value = str(config.get("model_keep_alive") or "").strip()
    return value or None


def context_posture_directive(config: dict[str, Any]) -> str:
    """One-line working posture matched to the executor's context tier.

    Small windows get incremental-work guidance (windowed reads, narrow tool
    requests, section-by-section audits); generous windows get whole-file
    latitude. The standard tier adds nothing, keeping default prompts lean.
    """
    num_ctx = int(config.get("num_ctx") or 4096)
    if num_ctx < 6144:
        return (
            "Context posture: TIGHT. The request window is small: read files in windows and continue with "
            "start_line, keep each tool request narrow, avoid re-reading unchanged content, and for large "
            "files work section by section, carrying forward only concise conclusions."
        )
    if num_ctx >= 16384:
        return (
            "Context posture: ROOMY. The window is generous: prefer whole-file reads and gather related "
            "evidence together before concluding."
        )
    return ""


def context_state(
    messages: list[dict[str, Any]],
    summary: str,
    config: dict[str, Any],
    chat: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    *,
    resolve_runtime: bool = True,
) -> dict[str, Any]:
    """Describe the next safe provider payload using the selected model's runtime profile."""
    runtime, executor = (
        context_runtime_config(config, chat) if resolve_runtime else (config, chat_executor(config, chat))
    )
    source = summarized_context_source(messages, chat or {})
    active = active_context_messages(source, summary, runtime, summary_required=bool(summary.strip()))
    requested_tools = (
        list(tools) if tools is not None else agent_tool_schemas() if config_bool(runtime, "agent", True) else []
    )
    try:
        fitted, fitted_tools = fit_agent_request_context_messages(active, runtime, requested_tools)
    except RequestContextError:
        fitted = active
        fitted_tools = []

    tokenizer_headroom, routing_headroom = context_request_headroom(runtime)
    response_reserve = response_token_reserve(runtime)
    headroom_tokens = tokenizer_headroom + routing_headroom + response_reserve
    tool_tokens = estimate_tokens(json.dumps(fitted_tools, sort_keys=True)) if fitted_tools else 0
    message_tokens = sum(estimate_message_tokens(message) for message in fitted)
    macro_tokens = sum(
        estimate_message_tokens(message)
        for message in fitted
        if str(message.get("content") or "").startswith("Persistent summary of earlier conversation:")
    )
    foundation_tokens = estimate_message_tokens(fitted[0]) if fitted else 0
    micro_tokens = max(0, message_tokens - foundation_tokens - macro_tokens)
    budget = context_budget(runtime)
    request_tokens = message_tokens + tool_tokens + headroom_tokens
    mode = "native" if fitted_tools else "compatibility" if config_bool(runtime, "agent", True) else "conversation"
    num_ctx = int(runtime.get("num_ctx") or 4096)
    return {
        "runtime": runtime,
        "executor": executor,
        "messages": fitted,
        "mode": mode,
        "budget": budget,
        "request_tokens": request_tokens,
        "ratio": min(1.0, request_tokens / max(1, budget)),
        "macro_tokens": macro_tokens,
        "micro_tokens": micro_tokens,
        "foundation_tokens": foundation_tokens,
        "tool_tokens": tool_tokens,
        "headroom_tokens": headroom_tokens,
        "answer_reserve": max(0, num_ctx - budget) + response_reserve,
    }


def token_aware_compaction_range(
    messages: list[dict[str, Any]],
    chat: dict[str, Any],
    config: dict[str, Any],
    keep_recent: int,
) -> tuple[int, int, str, str, int]:
    """Choose the deepest safe summary boundary while preserving the active user task."""
    start, initial_end = compact_candidate_range(messages, chat, keep_recent)
    maximum = max(start, _latest_task_message_index(messages))
    end = _coherent_compaction_end(messages, initial_end, maximum)
    if end <= start and maximum > start:
        end = _coherent_compaction_end(messages, start + 1, maximum)
    budget = context_budget(config)
    trigger_ratio = config_float(config, "auto_compact_trigger_ratio", 0.88, 0.50, 1.50)
    target = min(budget, int(budget * trigger_ratio))
    selected_summary = ""
    selected_mode = "constrained"
    selected_estimate = 0

    while end > start:
        summary = grounded_memory_summary(messages, end, config, chat)
        source = [messages[0], *messages[end:]]
        native_estimate, compatibility_estimate = _request_payload_estimates(source, summary, config)
        selected_summary = summary
        if not config_bool(config, "agent", True) or native_estimate <= target:
            selected_mode = "native" if config_bool(config, "agent", True) else "conversation"
            selected_estimate = native_estimate if config_bool(config, "agent", True) else compatibility_estimate
            break
        selected_mode = "compatibility" if compatibility_estimate <= target else "constrained"
        selected_estimate = compatibility_estimate
        if end >= maximum:
            break
        end = _coherent_compaction_end(messages, end + 1, maximum)

    return start, end, selected_summary, selected_mode, selected_estimate


def compact_range_text(messages: list[dict[str, str]], start: int, end: int) -> str:
    old = messages[start:end]
    parts: list[str] = []
    for index, message in enumerate(old, start):
        role = message.get("role") or "unknown"
        content = str(message.get("content") or "").strip()
        if content:
            parts.append(f"[{index}] {role.upper()}:\n{content}")
    return truncate("\n\n".join(parts), 70000)


def should_auto_compact(
    messages: list[dict[str, str]],
    chat: dict[str, Any],
    config: dict[str, Any],
) -> tuple[bool, str]:
    if not config_bool(config, "auto_compact", True):
        return False, "disabled"
    keep_recent = config_int(config, "auto_compact_keep_recent", 16, 4, 120)
    min_messages = config_int(config, "auto_compact_min_messages", 10, 4, 80)
    start, end = compact_candidate_range(messages, chat, keep_recent)
    compactable = end - start
    pressure_compactable = max(0, _latest_task_message_index(messages) - start)
    legacy_summary = bool(str(chat.get("summary") or "").strip()) and (
        str(chat.get("summary_format") or "") != GROUNDED_MEMORY_FORMAT
    )
    if legacy_summary and end > 1:
        return True, "upgrading model-written memory to grounded memory"

    source = summarized_context_source(messages, chat)
    summary = str(chat.get("summary") or "")
    native_estimate, compatibility_estimate = _request_payload_estimates(source, summary, config)
    budget = context_budget(config)
    request_tokens = native_estimate if config_bool(config, "agent", True) else compatibility_estimate
    trigger_ratio = config_float(config, "auto_compact_trigger_ratio", 0.88, 0.50, 1.50)
    trigger_tokens = int(budget * trigger_ratio)
    if pressure_compactable > 0 and request_tokens >= trigger_tokens:
        return True, f"active request {request_tokens}/{trigger_tokens} est tokens"
    if compactable < min_messages:
        return False, f"waiting for {min_messages - compactable} more compactable messages"

    active = active_context_messages(source, summary, config, summary_required=bool(summary.strip()))
    saved_chat_messages = sum(1 for message in source[1:] if message.get("role") in {"user", "assistant"})
    active_chat_messages = sum(1 for message in active if message.get("role") in {"user", "assistant"})
    omitted = max(0, saved_chat_messages - active_chat_messages)
    if omitted > 0:
        return True, f"{omitted} saved messages outside active context"
    return False, f"below trigger {request_tokens}/{trigger_tokens} est tokens"


def _grounded_excerpt(value: Any, limit: int) -> str:
    return truncate(collapse_ws(str(value or "")), limit)


def _tool_result_memory(index: int, content: str) -> str:
    metadata, separator, output = content.partition("\noutput:\n")
    fields: dict[str, str] = {}
    for line in metadata.splitlines()[1:]:
        key, marker, value = line.partition(":")
        if marker:
            fields[key.strip().lower()] = value.strip()
    action = fields.get("summary") or fields.get("command") or metadata.splitlines()[0]
    status = fields.get("exit_code")
    lead = f"Action result [{index}]: {_grounded_excerpt(action, 320)}"
    if status:
        lead += f" -> exit {status}"
    if not separator:
        return lead
    evidence = [_grounded_excerpt(line, 320) for line in output.splitlines() if line.strip()][:3]
    return lead + (" | output: " + " | ".join(evidence) if evidence else "")


def _requested_action_summary(messages: list[dict[str, Any]], index: int) -> str:
    if index <= 0:
        return "unknown action"
    previous = messages[index - 1]
    if previous.get("role") != "assistant":
        return "unknown action"
    calls = previous.get("tool_calls")
    if isinstance(calls, list):
        for raw_call in calls:
            if not isinstance(raw_call, dict):
                continue
            call, _error = TOOL_REGISTRY.validate(raw_call)
            if call:
                return tool_summary(call)
    call, _error, _recognized = decode_text_tool_call(str(previous.get("content") or ""))
    return tool_summary(call) if call else "unknown action"


def grounded_memory_summary(
    messages: list[dict[str, Any]],
    end: int,
    config: dict[str, Any],
    chat: dict[str, Any],
) -> str:
    """Build bounded memory from attributed transcript excerpts, without model inference."""
    bounded_end = max(1, min(end, len(messages)))
    events: list[str] = []
    for index, message in enumerate(messages[1:bounded_end], 1):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if content.startswith(TOOL_RESULT_PREFIXES):
            if content.startswith("Runtime event:"):
                events.append(f"Runtime event [{index}]: {_grounded_excerpt(content, 600)}")
            elif content.startswith("Tool request denied"):
                events.append(f"Denied action [{index}]: {_grounded_excerpt(content, 600)}")
            else:
                events.append(_tool_result_memory(index, content))
            continue
        if role == "tool" and content.startswith("Action was not executed:"):
            action = _requested_action_summary(messages, index)
            events.append(
                f"Denied action [{index}]: {_grounded_excerpt(action, 320)} | {_grounded_excerpt(content, 500)}"
            )
            continue
        if role == "user":
            images = message.get("image_paths")
            image_note = ""
            if isinstance(images, list) and images:
                image_note = " | images: " + ", ".join(_grounded_excerpt(path, 160) for path in images[:4])
            events.append(f"User [{index}]: {_grounded_excerpt(content, 900)}{image_note}")
            continue
        if role != "assistant":
            continue
        if message.get("tool_calls"):
            continue
        call, _error, recognized = decode_text_tool_call(content)
        if call or recognized:
            continue
        events.append(f"Assistant response [{index}]: {_grounded_excerpt(content, 1100)}")

    project = str(chat.get("project_root") or chat.get("cwd") or "").strip() or "not set"
    header = [
        "# Grounded conversation memory",
        "This is an extractive event ledger, not an independent claim that prior assistant statements are correct.",
        f"Active project: {project}",
        f"Covers saved messages 1-{max(0, bounded_end - 1)}.",
    ]
    max_chars = max(3000, min(12000, int(context_budget(config) * 1.2)))
    fixed_size = len("\n".join(header)) + len("\n\n## Events\n")
    available = max(800, max_chars - fixed_size)
    selected = list(events)
    omitted = 0
    if sum(len(event) + 1 for event in events) > available and events:
        selected_indices = {0}
        used = len(events[0]) + 1
        critical = {
            index
            for index, event in enumerate(events[1:], 1)
            if event.startswith(("Action result", "Denied action", "Runtime event"))
        }
        for index in sorted(critical, reverse=True):
            cost = len(events[index]) + 1
            if used + cost <= available:
                selected_indices.add(index)
                used += cost
        for index in range(len(events) - 1, 0, -1):
            if index in selected_indices:
                continue
            cost = len(events[index]) + 1
            if used + cost <= available:
                selected_indices.add(index)
                used += cost
        selected = [events[index] for index in sorted(selected_indices)]
        omitted = len(events) - len(selected)
    body: list[str] = []
    if omitted:
        body.append(f"[{omitted} intermediate events omitted to fit the active context budget]")
    body.extend(selected or ["[No attributable events in this range]"])
    return "\n".join([*header, "", "## Events", *body])


def compact_chat_memory(
    provider: Any,
    model: str,
    messages: list[dict[str, str]],
    chat: dict[str, Any],
    config: dict[str, Any],
    *,
    keep_recent: int | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    del provider, model
    if cancel_event and cancel_event.is_set():
        return False, "memory rebuild interrupted"
    if keep_recent is None:
        keep_recent = config_int(config, "auto_compact_keep_recent", 16, 4, 120)
    keep_recent = max(4, min(120, keep_recent))
    start, end, summary, request_mode, request_tokens = token_aware_compaction_range(
        messages,
        chat,
        config,
        keep_recent,
    )
    if end <= start:
        return False, "nothing new enough to compact"
    chat["summary"] = summary
    chat["summary_upto"] = end
    chat["summary_format"] = GROUNDED_MEMORY_FORMAT
    chat["last_compacted_at"] = now_iso()
    return True, (
        f"rebuilt grounded memory through message {end - 1}; "
        f"kept recent {len(messages) - end}; "
        f"summary {estimate_tokens(summary)} est tokens; "
        f"{request_mode} request {request_tokens}/{context_budget(config)} est tokens"
    )


def auto_compact_if_needed(
    provider: Any,
    model: str,
    messages: list[dict[str, str]],
    chat: dict[str, Any],
    config: dict[str, Any],
    *,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    should, reason = should_auto_compact(messages, chat, config)
    if not should:
        return False, reason
    changed, detail = compact_chat_memory(
        provider,
        model,
        messages,
        chat,
        config,
        keep_recent=config_int(config, "auto_compact_keep_recent", 16, 4, 120),
        cancel_event=cancel_event,
    )
    if changed:
        return True, f"{reason}; {detail}"
    return False, detail


def size_human(num: int | None) -> str:
    if not num:
        return "unknown"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def file_url_path(value: str, *, windows: bool | None = None) -> str:
    parsed = urllib.parse.urlparse(value)
    is_windows = os.name == "nt" if windows is None else windows
    path = nturl2path.url2pathname(parsed.path) if is_windows else urllib.parse.unquote(parsed.path)
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path = f"//{parsed.netloc}{path}"
    elif is_windows and re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    return path


def resolve_image_path(cwd: Path, raw: str) -> Path:
    value = raw.strip().strip("\"'")
    if value.startswith("file://"):
        value = file_url_path(value)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        candidate = candidate.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"image not found: {raw}") from exc
    if not candidate.is_file():
        raise ValueError(f"not an image file: {candidate}")
    if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"unsupported image type {candidate.suffix or '(none)'}; use {supported}")
    size = candidate.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"image is {size_human(size)}; maximum is {size_human(MAX_IMAGE_BYTES)}")
    if size == 0:
        raise ValueError(f"image is empty: {candidate}")
    return candidate


def discover_image_files(
    cwd: Path,
    limit: int = 100,
    cancel_event: threading.Event | None = None,
) -> list[Path]:
    if cancel_event and cancel_event.is_set():
        return []
    patterns: list[str] = []
    for extension in sorted(IMAGE_EXTENSIONS):
        patterns.extend(["-g", f"*{extension}", "-g", f"*{extension.upper()}"])
    cmd = ["rg", "--files", *patterns, *sum((["-g", pattern] for pattern in SEARCH_GLOBS), [])]
    code, output = run_argv(cmd, cwd, timeout=8, cancel_event=cancel_event) if shutil.which("rg") else (127, "")
    if cancel_event and cancel_event.is_set():
        return []
    paths: list[Path] = []
    if code in {0, 1}:
        for raw in output.splitlines():
            try:
                path = resolve_image_path(cwd, raw)
            except ValueError:
                continue
            paths.append(path)
            if len(paths) >= limit:
                break
    else:
        excluded = {".git", ".cache", ".codex", ".venv", "venv", "node_modules", "__pycache__"}
        scanned = 0
        for root, directories, filenames in os.walk(cwd):
            if cancel_event and cancel_event.is_set():
                return []
            directories[:] = [name for name in directories if name not in excluded]
            for filename in filenames:
                if cancel_event and cancel_event.is_set():
                    return []
                scanned += 1
                if scanned > 50_000:
                    break
                candidate = Path(root) / filename
                if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                try:
                    paths.append(resolve_image_path(cwd, str(candidate)))
                except ValueError:
                    continue
            if scanned > 50_000:
                break
    paths.sort(key=lambda path: (-path.stat().st_mtime, str(path).lower()))
    return paths[:limit]


def collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sanitize_terminal_text(text: Any) -> str:
    """Remove terminal controls from untrusted text while preserving readable layout."""
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(character for character in normalized if character in {"\n", "\t"} or character.isprintable())


def normalize_web_url(url: str) -> str:
    return secure_network.normalize_http_url(url)


def validate_public_web_url(url: str) -> str:
    return secure_network.validate_public_url(url)


class PublicWebRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        target = validate_public_web_url(urllib.parse.urljoin(request.full_url, new_url))
        if (
            urllib.parse.urlparse(request.full_url).scheme == "https"
            and urllib.parse.urlparse(target).scheme != "https"
        ):
            raise urllib.error.HTTPError(target, code, "HTTPS downgrade redirect blocked", headers, response)
        return super().redirect_request(request, response, code, message, headers, target)


def decode_duckduckgo_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    is_duckduckgo_redirect = parsed.path.startswith("/l/") and (
        not parsed.netloc or parsed.netloc.endswith("duckduckgo.com")
    )
    if is_duckduckgo_redirect:
        query = urllib.parse.parse_qs(parsed.query)
        if query.get("uddg"):
            return query["uddg"][0]
    return urllib.parse.urljoin("https://duckduckgo.com", href)


class DuckDuckGoLiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.current: dict[str, Any] | None = None
        self.capture_title = False
        self.capture_snippet = False

    def finish_current(self) -> None:
        if not self.current:
            return
        title = collapse_ws(sanitize_terminal_text(" ".join(self.current.get("title_parts", []))))
        url = sanitize_terminal_text(self.current.get("url") or "")
        snippet = collapse_ws(sanitize_terminal_text(" ".join(self.current.get("snippet_parts", []))))
        try:
            url = normalize_web_url(url)
        except ValueError:
            url = ""
        if title and url:
            self.results.append({"title": title, "url": url, "snippet": snippet})
        self.current = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and "result-link" in classes:
            self.finish_current()
            self.current = {
                "url": decode_duckduckgo_url(attrs_dict.get("href", "")),
                "title_parts": [],
                "snippet_parts": [],
            }
            self.capture_title = True
            return
        if tag == "td" and "result-snippet" in classes and self.current is not None:
            self.capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.capture_title:
            self.capture_title = False
        elif tag == "td" and self.capture_snippet:
            self.capture_snippet = False

    def handle_data(self, data: str) -> None:
        if not self.current:
            return
        if self.capture_title:
            self.current["title_parts"].append(data)
        elif self.capture_snippet:
            self.current["snippet_parts"].append(data)

    def close(self) -> None:
        super().close()
        self.finish_current()


class ReadableHTMLParser(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_chars = max_chars
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.capture_title = False

    SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "canvas", "nav", "aside", "form", "button", "footer"})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.capture_title = True
        if tag in {
            "p",
            "div",
            "section",
            "article",
            "header",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "pre",
            "blockquote",
        }:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "title":
            self.capture_title = False
        if not self.skip_depth and tag in {"p", "li", "tr", "h1", "h2", "h3", "h4", "pre", "blockquote"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.capture_title:
            self.title_parts.append(data)
            return
        if sum(len(part) for part in self.text_parts) < self.max_chars:
            self.text_parts.append(data)

    def title(self) -> str:
        return collapse_ws(" ".join(self.title_parts))

    def text(self) -> str:
        raw = "".join(self.text_parts)
        lines = [collapse_ws(line) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


class ActionCancelled(RuntimeError):
    pass


def fetch_url_bytes(
    url: str,
    max_bytes: int = WEB_MAX_BYTES,
    cancel_event: threading.Event | None = None,
) -> tuple[bytes, str, str]:
    if cancel_event and cancel_event.is_set():
        raise ActionCancelled("interrupted before the network request started")
    try:
        result = secure_network.fetch_public_url(
            url,
            max_bytes=max_bytes,
            headers={"User-Agent": WEB_USER_AGENT},
            idle_timeout=WEB_TIMEOUT,
            total_timeout=WEB_TOTAL_TIMEOUT,
            cancel_event=cancel_event,
        )
    except secure_network.NetworkCancelled as exc:
        raise ActionCancelled(str(exc)) from exc
    return result.body, result.content_type, result.final_url


def internet_search(
    query: str,
    limit: int = 6,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    query = query.strip()
    if not query:
        return 2, "usage: /web <query>"
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    try:
        raw, _content_type, final_url = fetch_url_bytes(url, cancel_event=cancel_event)
        parser = DuckDuckGoLiteParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        parser.close()
    except ActionCancelled as exc:
        return 130, sanitize_terminal_text(exc)
    except Exception as exc:
        return 1, f"web search failed: {sanitize_terminal_text(exc)}"
    results = parser.results[: max(1, min(limit, 12))]
    display_query = sanitize_terminal_text(query)
    display_url = sanitize_terminal_text(final_url)
    if not results:
        body_text = raw.decode("utf-8", errors="replace").lower()
        if "no result" not in body_text:
            # The backend answered with a page we could not parse: a rate limit, challenge
            # page, or changed markup — materially different from a genuine empty result set.
            return 1, (
                f"search backend unavailable: the response for {display_query} contained no parseable "
                "results. The backend may be rate-limiting or its format may have changed; retry "
                "later or use web_open on a known URL."
            )
        return 1, f"no web results for: {display_query}\nsource: {display_url}"
    lines = [
        f"Internet search: {display_query}",
        "backend: DuckDuckGo Lite",
        f"source: {display_url}",
    ]
    targets = extract_public_web_targets(query)
    if targets:
        lines.append(f"direct target: {targets[0]}")
    lines.append("")
    for idx, item in enumerate(results, 1):
        lines.append(f"{idx}. {item['title']}")
        lines.append(f"   {item['url']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
    lines.append("")
    lines.append("Use web_open on a relevant URL when page content is required.")
    return 0, "\n".join(lines)


def web_open_url(
    url: str,
    max_chars: int = 16000,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    try:
        raw, content_type, final_url = fetch_url_bytes(url, cancel_event=cancel_event)
    except ActionCancelled as exc:
        return 130, sanitize_terminal_text(exc)
    except Exception as exc:
        return 1, f"web open failed: {sanitize_terminal_text(exc)}"
    lowered = content_type.lower()
    if lowered and not any(kind in lowered for kind in ("text/", "html", "xml", "json")):
        return 1, f"not a readable text page: {content_type}\n{final_url}"
    charset = "utf-8"
    match = re.search(r"charset=([^;]+)", content_type, re.I)
    if match:
        charset = match.group(1).strip("\"' ")
    text = sanitize_terminal_text(raw.decode(charset, errors="replace"))
    title = ""
    if "html" in lowered or "<html" in text[:1000].lower():
        parser = ReadableHTMLParser(max_chars=max_chars)
        parser.feed(text)
        parser.close()
        title = sanitize_terminal_text(parser.title())
        text = sanitize_terminal_text(parser.text())
    else:
        text = "\n".join(collapse_ws(line) for line in text.splitlines() if collapse_ws(line))
    text = truncate(text, max_chars)
    lines = [f"URL: {sanitize_terminal_text(final_url)}"]
    if title:
        lines.append(f"Title: {title}")
    lines.extend(["", text if text else "(no readable text extracted)"])
    return 0, "\n".join(lines)


ModelInfo = ModelDescriptor


def provider_from_config(config: dict[str, Any]) -> OllamaProvider:
    provider = config.get("provider", "ollama")
    if provider != "ollama":
        raise SystemExit(f"unsupported provider {provider!r}; only ollama is wired up")
    return configured_compute_provider(config, PATHS)


@functools.lru_cache(maxsize=1)
def application_implementation_path() -> Path:
    package_path = Path(__file__).resolve().parent
    source_root = detect_project_root(package_path)
    if source_root != package_path:
        return source_root

    try:
        direct_url = metadata.distribution("dairack").read_text("direct_url.json")
        payload = json.loads(direct_url or "{}")
        parsed = urllib.parse.urlsplit(str(payload.get("url") or ""))
        installed_from = Path(urllib.request.url2pathname(parsed.path)).resolve() if parsed.scheme == "file" else None
    except (metadata.PackageNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
        installed_from = None

    candidates: list[Path] = []
    if installed_from is not None:
        candidates.append(installed_from if installed_from.is_dir() else installed_from.parent)
        if candidates[0].name == "dist":
            candidates.append(candidates[0].parent)
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "dairack").is_dir():
            return candidate
    return package_path


def system_prompt(cwd: Path, agent: bool, config: dict[str, Any] | None = None) -> str:
    host_system = platform.system() or "Unknown"
    implementation_path = application_implementation_path()
    active_config = config if isinstance(config, dict) else default_config()
    base = textwrap.dedent(
        f"""
        You are Dairack, a local terminal assistant running on the user's machine.
        Be concise, direct, and pragmatic. Prefer concrete commands and file paths.
        Handle ordinary conversation and general questions naturally. Treat coding and
        system work as capabilities, not as the assumed subject of every request.
        Current working directory: {cwd}
        Client home directory: {Path.home()}
        Dairack implementation path: {implementation_path}
        Client operating system: {host_system}
        Your agent runtime, tools, approvals, and working directory live on this client.
        Model inference comes from the configured Ollama compute endpoint, which may be
        local or remote. The compute server cannot access client paths except through
        content deliberately included in a model request or an approved client-side tool result.
        """
    ).strip()
    base += "\n\n" + machine_prompt(active_config, PATHS)
    if not agent:
        base += (
            "\n\n"
            + textwrap.dedent(
                """
            You cannot execute terminal commands automatically. The user can run slash
            commands such as /search, /open, /run, /cd, /model, and /help from the
            terminal UI.
            """
            ).strip()
        )
    else:
        tool_catalog = textwrap.indent(TOOL_REGISTRY.prompt_catalog(), "            ")
        base += (
            "\n\n"
            + textwrap.dedent(
                f"""
            Agent mode is enabled. Use the supplied function tools whenever they are
            available. Request exactly one action at a time, then wait for its result.
            Do not print commands or function calls as ordinary prose. Never say that
            an action is running, ask the user to wait, or promise to inspect something
            unless the same response contains a real function tool call.
            When requesting a tool, return no narration alongside it; Dairack's action
            surface communicates what is happening. Keep ordinary prose for final results.

            Available tools:
{tool_catalog}

            Compatibility fallback only: when native function tools are unavailable,
            return one <tool> JSON envelope that conforms to the same tool schema.
            Never mix an action request with ordinary response text.

            Use hardware_status only when the authoritative machine map needs to be
            restated; do not run shell commands for ordinary hardware identity questions.
            To locate a named file, directory, or project outside the active workspace,
            use find_paths with the exact name and an explicit likely root such as the
            client home directory. Do not silently search the current directory when the
            requested object is expected elsewhere. Treat user-supplied names as entities
            before interpreting them as error types, licenses, or generic concepts.
            For recent facts, prices, releases, docs, schedules, or anything likely
            to have changed, use web_search first and include source URLs in your answer.
            When the user asks you to inspect a public website, URL, link, or domain,
            use web_open on the supplied or contextually referenced target first. Use
            web_search for discovery, independent verification, or as fallback when a
            direct fetch fails. Continue the workflow yourself after each result; never
            claim web access is unavailable when these tools are supplied or ask the
            user to invoke an internal web tool for you.
            For code work, prefer grep for exact text or symbols, search_project
            for concepts, plus read_file, list_dir, and git diff before editing. For targeted changes, request edit_file with the exact
            current text and its replacement; use patch with a unified diff only for
            multi-file or large rewrites. Do not write files through
            shell redirection, sed -i, Python scripts, or install commands unless the
            user explicitly asked for that action. Prefer read-only inspection
            commands first. Never request destructive commands unless the user
            explicitly asked for that change.

            Preserve the user's requested scope. Inspection, explanation, review, and
            audit requests are read-only unless the user explicitly asks to fix, edit,
            or implement changes. For a read-only task, gather evidence and report
            findings or recommendations; do not request a patch or modifying command.

            In COORDINATOR mode, use consult_specialist only when a distinct capability
            or an independent check is materially useful. Delegate one narrow question,
            include the minimum evidence needed, declare quality and risk from the task
            semantics, and continue the original task after the coordinator returns the
            result. Do not choose a model yourself. When a raster image must be inspected,
            request consult_specialist with specialty vision and its path instead of
            read_file. Routine work should stay on the current executor to avoid needless
            model loads.
            """
            ).strip()
        )
        if host_system == "Windows":
            base += (
                "\n\n"
                + textwrap.dedent(
                    """
                The shell tool runs PowerShell on Windows. Use PowerShell syntax only;
                do not mix cmd.exe redirection or Unix commands into it. Prefer modern
                commands such as Get-CimInstance, Get-ChildItem, Get-Content,
                Get-Command, systeminfo, and nvidia-smi. Do not use deprecated wmic.
                """
                ).strip()
            )
    return base


def print_help() -> None:
    print(PRIMARY_HELP_TEXT)


def select_model(provider: OllamaProvider, current: str = "") -> str:
    try:
        models = provider.list_models()
    except Exception as exc:
        raise SystemExit(f"could not list Ollama models at {provider.host}: {exc}") from exc

    if not models:
        print("No Ollama models are installed yet.")
        name = input("Model to use/pull: ").strip()
        if not name:
            raise SystemExit("no model selected")
        return name

    while True:
        print("Select operating mode or direct model:")
        print("  1.   COORDINATOR | adaptive task routing and specialist delegation")
        for i, model in enumerate(models, 2):
            marker = "*" if model.name == current else " "
            print(f"  {i}. {marker} {model.label()}")
        default = current if current else models[0].name
        choice = input(f"model [{default}]: ").strip()
        if not choice:
            return default
        if choice.startswith("/"):
            if choice in {"/help", "/h"}:
                print_help()
            else:
                print("Select a model first; slash commands are available at the you> prompt.")
            continue
        if choice == "1":
            return ORCHESTRATOR_MODEL_ID
        if choice.isdigit() and 2 <= int(choice) <= len(models) + 1:
            return models[int(choice) - 2].name
        return choice


def command_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for key in SENSITIVE_CHILD_ENV_KEYS:
        environment.pop(key, None)
    return environment


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1.5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_process(
    command: str | list[str],
    cwd: Path,
    timeout: int,
    cancel_event: threading.Event | None = None,
    *,
    shell: bool,
) -> tuple[int, str]:
    if cancel_event and cancel_event.is_set():
        return 130, "command interrupted before execution"
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            shell=shell,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            env=command_environment(),
        )
    except OSError as exc:
        return 127, f"command could not start: {exc}"
    output_parts: list[str] = []
    output_size = 0
    output_discarded = False
    output_lock = threading.Lock()

    def drain(stream: Any) -> None:
        nonlocal output_size, output_discarded
        try:
            while chunk := stream.read(4096):
                with output_lock:
                    remaining = max(0, MAX_TOOL_OUTPUT - output_size)
                    if remaining:
                        output_parts.append(chunk[:remaining])
                        output_size += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        output_discarded = True
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=drain, args=(process.stdout,), daemon=True, name="dairack-stdout"),
        threading.Thread(target=drain, args=(process.stderr,), daemon=True, name="dairack-stderr"),
    ]
    for reader in readers:
        reader.start()

    def captured_output() -> str:
        for reader in readers:
            reader.join(timeout=1.0)
        with output_lock:
            output = "".join(output_parts)
            discarded = output_discarded
        if discarded:
            output = output.rstrip() + f"\n[output capped at {MAX_TOOL_OUTPUT} characters]"
        return output

    deadline = time.monotonic() + timeout
    while True:
        if cancel_event and cancel_event.is_set():
            _stop_process_tree(process)
            output = captured_output()
            return 130, "command interrupted" + (f"\n{output}" if output else "")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process_tree(process)
            output = captured_output()
            return 124, f"command timed out after {timeout}s" + (f"\n{output}" if output else "")
        code = process.poll()
        if code is not None:
            return code, captured_output()
        time.sleep(min(0.05, remaining))


def shell_invocation(cmd: str) -> tuple[str | list[str], bool]:
    """Return the user's platform shell invocation without relying on shell=True on Windows."""
    if os.name != "nt":
        return cmd, True
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell:
        return [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", cmd], False
    command_processor = os.environ.get("COMSPEC") or "cmd.exe"
    return [command_processor, "/d", "/s", "/c", cmd], False


def run_shell(
    cmd: str,
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    if command_needs_interactive_tty(cmd):
        return (
            126,
            "interactive command blocked\n"
            "This command appears to require a terminal password prompt or direct TTY input.\n"
            "Run sudo authentication in a normal terminal first, or use a non-interactive form such as sudo -n when credentials are already cached.",
        )
    command, use_shell = shell_invocation(cmd)
    return _run_process(command, cwd, timeout, cancel_event, shell=use_shell)


def run_argv(
    cmd: list[str],
    cwd: Path,
    timeout: int = DEFAULT_TIMEOUT,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    if argv_needs_interactive_tty(cmd):
        return (
            126,
            "interactive command blocked\n"
            "This command appears to require a terminal password prompt or direct TTY input.",
        )
    return _run_process(cmd, cwd, timeout, cancel_event, shell=False)


def tool_result_char_budget(
    messages: list[dict[str, Any]],
    chat: dict[str, Any],
    config: dict[str, Any],
) -> int:
    """Scale one tool result to the active model while preserving room for continuation."""
    state = context_state(
        messages,
        str(chat.get("summary") or ""),
        config,
        chat,
        resolve_runtime=False,
    )
    budget = int(state["budget"])
    free_tokens = max(0, budget - int(state["request_tokens"]))
    target_tokens = max(384, min(MAX_TOOL_OUTPUT // 4, int(budget * 0.18)))
    if free_tokens:
        target_tokens = min(target_tokens, max(384, free_tokens - max(96, int(budget * 0.02))))
    return max(1536, min(MAX_TOOL_OUTPUT, target_tokens * 4))


def bounded_tool_output(output: str, limit: int) -> str:
    if not output:
        return "(no output)"
    return truncate_middle(output, max(512, min(MAX_TOOL_OUTPUT, limit)))


def fit_tool_result_for_context(
    messages: list[dict[str, Any]],
    chat: dict[str, Any],
    config: dict[str, Any],
    call: dict[str, str],
    code: int,
    result: str,
    cwd: Path | None = None,
) -> str:
    """Bound one completed action so the next model continuation still fits."""

    def fits(candidate: str) -> bool:
        history = [*messages, tool_history_message(call, code, candidate)]
        if cwd is not None:
            active = request_context_messages(history, chat, config, cwd, include_retrieval=False)
        else:
            source = summarized_context_source(history, chat)
            summary = str(chat.get("summary") or "")
            active = active_context_messages(source, summary, config, summary_required=bool(summary.strip()))
        tools = agent_tool_schemas() if config_bool(config, "agent", True) else []
        try:
            fit_agent_request_context_messages(active, config, tools)
        except RequestContextError:
            return False
        return True

    if fits(result):
        return result
    low = 128
    high = max(low, len(result) - 1)
    best = ""
    while low <= high:
        midpoint = (low + high) // 2
        candidate = truncate_middle(result, midpoint)
        if fits(candidate):
            best = candidate
            low = midpoint + 1
        else:
            high = midpoint - 1
    if best:
        return best
    fallback = "Action completed; detailed output was omitted to preserve the active context. Request a narrower range."
    return fallback if fits(fallback) else truncate(fallback, 96)


REPEATABLE_READ_TOOLS = frozenset(
    {"read_file", "list_dir", "find_paths", "grep", "hardware_status", "search_project", "web_search", "web_open"}
)
STATE_PRESERVING_TOOLS = REPEATABLE_READ_TOOLS | {"consult_specialist", "analyze_image"}


def tool_call_signature(call: dict[str, str]) -> str:
    payload = {key: value for key, value in call.items() if key not in {"name", "reason", "_protocol"}}
    return str(call.get("name") or "") + "\x00" + json.dumps(payload, sort_keys=True, ensure_ascii=False)


class ActionLoopGuard:
    """Detect no-progress repetition of identical read actions within one task turn."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._digests: dict[str, str] = {}
        self.repeat_stops = 0

    def reset(self) -> None:
        self._counts.clear()
        self._digests.clear()
        self.repeat_stops = 0

    @property
    def force_synthesis(self) -> bool:
        return self.repeat_stops >= 2

    def refusal(self, call: dict[str, str]) -> str:
        """Return a refusal reason when this exact read already ran twice with no state change since."""
        name = str(call.get("name") or "")
        if name not in REPEATABLE_READ_TOOLS:
            return ""
        if self._counts.get(tool_call_signature(call), 0) < 2:
            return ""
        self.repeat_stops += 1
        return (
            "because this exact action already ran twice in this task with identical parameters and no "
            "state change since; its result is unchanged and shown above. Use that result or take a "
            "different action."
        )

    def record(self, call: dict[str, str], result: str) -> str:
        """Record an executed action; return a note when a repeated read returned identical output."""
        name = str(call.get("name") or "")
        if name not in STATE_PRESERVING_TOOLS:
            self.reset()
            return ""
        self.repeat_stops = 0
        if name not in REPEATABLE_READ_TOOLS:
            return ""
        signature = tool_call_signature(call)
        digest = hashlib.sha256(result.encode("utf-8", "replace")).hexdigest()
        previous_digest = self._digests.get(signature)
        self._counts[signature] = self._counts.get(signature, 0) + 1 if previous_digest == digest else 1
        note = ""
        if self._counts[signature] >= 2:
            note = "\n[unchanged from the previous run of this exact action]"
        self._digests[signature] = digest
        return note


def transient_stream_error(exc: BaseException) -> bool:
    """Recognize connection hiccups worth one silent in-turn retry, as opposed to real request errors."""
    if isinstance(exc, OllamaError):
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "stalled",
                "disconnect",
                "could not reach",
                "malformed stream",
                "connection reset",
                "timed out",
                "http 502",
                "http 503",
                "http 504",
                "xml syntax error",
            )
        )
    return isinstance(exc, (TimeoutError, ConnectionError))


def recoverable_model_protocol_error(exc: BaseException) -> bool:
    """Recognize malformed model tool output that may recover on another executor."""
    if not isinstance(exc, OllamaError):
        return False
    text = str(exc).lower()
    return bool(
        "http 500" in text
        and (
            "xml syntax error" in text
            or "tool call" in text
            and any(marker in text for marker in ("parse", "malformed", "unexpected eof"))
        )
    )


def strip_tool_markup(text: str) -> str:
    return strip_tool_protocol(text)


def response_incomplete_reason(text: str, stats: dict[str, Any] | None = None) -> str:
    """Return structural evidence that a model response ended before completion."""
    done_reason = str((stats or {}).get("done_reason") or "").strip().lower().replace("-", "_")
    if done_reason in {"length", "max_tokens", "token_limit"}:
        return f"provider stopped at its token limit ({done_reason})"
    if done_reason == "stream_ended":
        return "provider stream ended without a completion marker"

    visible = strip_tool_markup(text).strip()
    if not visible:
        return "empty response"
    fence_markers = re.findall(r"(?m)^\s*(?:```|~~~)", visible)
    if len(fence_markers) % 2:
        return "response ended with an unclosed Markdown code fence"
    # Trailing commas, semicolons, and colons are common in legitimate complete answers
    # ("Here are the steps:"), so only unambiguous structural danglers force a retry.
    dangling = {
        "\\": "response ended after an escape character",
        "(": "response ended after an opening parenthesis",
        "[": "response ended after an opening bracket",
        "{": "response ended after an opening brace",
    }
    return dangling.get(visible[-1], "")


def completion_retry_directive(reason: str) -> str:
    return (
        f"The previous output was structurally incomplete: {reason}. "
        "Produce one complete replacement response from the same evidence. Start from the beginning, finish all "
        "sentences and code fences, and do not mention this retry."
    )


def osc52_sequence(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\033]52;c;{encoded}\a"


def _validated_tool_call(data: dict[str, Any], body: str = "") -> tuple[dict[str, str] | None, str]:
    call, error = TOOL_REGISTRY.validate(data, body)
    return (normalize_coordinator_tool_call(call), "") if call else (None, error)


def parse_tool_request(text: str) -> tuple[dict[str, str] | None, str]:
    """Parse one validated action request and distinguish absence from malformed markup."""
    call, error, _recognized = decode_text_tool_call(text)
    return (normalize_coordinator_tool_call(call), "") if call else (None, error)


def resolve_tool_request(
    text: str,
    native_calls: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str] | None, str]:
    calls = list(native_calls or [])
    if len(calls) > 1:
        return None, "multiple native action requests were returned; exactly one is required"
    if calls:
        call, error = _validated_tool_call(calls[0])
        if call:
            call["_protocol"] = "native"
        return call, error
    call, error = parse_tool_request(text)
    if call:
        call["_protocol"] = "compat"
    return call, error


def read_only_batch(
    native_calls: list[dict[str, Any]],
    cwd: Path,
    project_root: Path | None = None,
) -> list[dict[str, str]]:
    """Return validated calls when a response is a batch of independently auto-approvable reads.

    A non-empty result is only returned when there is more than one native call and every one of
    them would auto-run on its own as an in-scope, read-only action. Any write, network,
    non-read shell, coordinator, or out-of-scope call — or any parse failure — yields an empty
    list, so the caller falls back to strict single-action handling. This never widens what may
    run without approval.
    """
    if len(native_calls) < 2:
        return []
    validated: list[dict[str, str]] = []
    for raw in native_calls:
        call, _error = _validated_tool_call(raw)
        if not call:
            return []
        call = normalize_coordinator_tool_call(call)
        if is_internal_coordinator_call(call):
            return []
        if not is_read_only_tool_call(call):
            return []
        if not is_auto_approvable_tool_call(call, cwd, project_root):
            return []
        call["_protocol"] = "native"
        validated.append(call)
    return validated


def agent_tool_schemas() -> list[dict[str, Any]]:
    return TOOL_REGISTRY.schemas()


def native_tools_for(
    provider: Any,
    model: str,
    enabled: bool,
    route: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    if is_direct_answer_route(route):
        return []
    try:
        if not provider.supports(model, "tools"):
            return []
    except Exception as exc:
        raise RuntimeError(f"could not verify native tool support for {model}: {exc}") from exc
    return agent_tool_schemas()


def normalize_coordinator_tool_call(call: dict[str, str]) -> dict[str, str]:
    normalized = dict(call)
    name = str(normalized.get("name") or "").lower()
    path = str(normalized.get("path") or "").strip()
    if name == "analyze_image" or (name == "read_file" and Path(path).suffix.lower() in IMAGE_EXTENSIONS):
        normalized["name"] = "consult_specialist"
        normalized["specialty"] = "vision"
        normalized["task"] = str(
            normalized.get("task")
            or normalized.get("query")
            or normalized.get("reason")
            or "Inspect this image and report details relevant to the current task."
        )
        normalized.pop("line", None)
        normalized.pop("start_line", None)
        normalized.pop("max_lines", None)
        normalized.pop("query", None)
    return normalized


def patch_stats(patch_text: str) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    files: set[str] = set()
    for line in patch_text.splitlines():
        if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            files.add(line[4:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return additions, deletions, len(files)


def tool_presentation(call: dict[str, str]) -> dict[str, Any]:
    presentation = TOOL_REGISTRY.presentation(call.get("name"))
    target_field = str(presentation.get("target_field") or "")
    target = str(call.get(target_field) or "").strip() if target_field else ""
    if call.get("name") in {"list_dir", "index_project", "grep"} and not target:
        target = "."
    if call.get("name") == "read_file":
        if call.get("line"):
            target += f":{call['line']}"
        elif call.get("start_line"):
            target += f":{call['start_line']}+"
    if call.get("name") == "patch":
        additions, deletions, files = patch_stats(call.get("patch", ""))
        noun = "file" if files == 1 else "files"
        target = f"{files} {noun}, +{additions} -{deletions}"
    presentation["target"] = target
    presentation["details"] = [
        (str(label), str(call.get(str(field)) or "").strip())
        for field, label in presentation.get("detail_fields") or ()
        if str(call.get(str(field)) or "").strip()
    ]
    return presentation


def _append_presentation_details(lines: list[str], presentation: dict[str, Any]) -> None:
    for label, value in presentation.get("details") or ():
        lines.append(f"{label}  {value}")


def tool_activity_label(call: dict[str, str], step_label: str = "") -> str:
    presentation = tool_presentation(call)
    label = str(presentation["activity"])
    target = collapse_ws(str(presentation.get("target") or ""))
    if target:
        label += " / " + truncate(target, 88).splitlines()[0]
    if step_label:
        label += f" / {step_label}"
    return label


def tool_request_display(call: dict[str, str]) -> str:
    presentation = tool_presentation(call)
    title = str(presentation["display_name"]).upper()
    risk = str(presentation["risk"]).upper()
    target = str(presentation.get("target") or "")
    lines = ["ACTION REQUEST", f"{title}  /  {risk}"]
    if call.get("name") == "shell":
        lines.append("$ " + target)
    elif target:
        lines.append(f"{presentation['target_label']}  {target}")
    _append_presentation_details(lines, presentation)
    return "\n".join(lines)


def _tool_access_label(approved_by: str) -> str:
    normalized = approved_by.strip().lower()
    if normalized in {"user", "direct"}:
        return "USER"
    if normalized == "read-auto":
        return "READ-AUTO"
    if normalized == "coordinator":
        return "COORDINATOR"
    if "trusted" in normalized:
        return "APPROVED / READ-AUTO ENABLED"
    if normalized.startswith("approved"):
        return "APPROVED ONCE"
    return approved_by.strip().upper() or "APPROVED"


def tool_result_display(
    call: dict[str, str],
    code: int,
    result: str,
    approved_by: str,
    elapsed: float,
) -> str:
    presentation = tool_presentation(call)
    state = "INTERRUPTED" if code == 130 else "TIMED OUT" if code == 124 else "COMPLETE" if code == 0 else "FAILED"
    if call.get("name") == "patch" and code == 0:
        state = "APPLIED"
    title = str(presentation["display_name"]).upper()
    lines = [f"{title}  {state}  /  EXIT {code}  /  {elapsed:.1f}s"]
    target = str(presentation.get("target") or "")
    if call.get("name") == "shell":
        lines.append("$ " + target)
    elif target:
        lines.append(f"{presentation['target_label']}  {target}")
    _append_presentation_details(lines, presentation)
    lines.extend((f"ACCESS  {_tool_access_label(approved_by)}", "RESULT", result or "(no output)"))
    return "\n".join(lines)


def tool_denied_display(call: dict[str, str], reason: str, state: str = "DENIED") -> str:
    presentation = tool_presentation(call)
    lines = [f"{str(presentation['display_name']).upper()}  {state.upper()}"]
    target = str(presentation.get("target") or "")
    if call.get("name") == "shell":
        lines.append("$ " + target)
    elif target:
        lines.append(f"{presentation['target_label']}  {target}")
    _append_presentation_details(lines, presentation)
    lines.append(f"REASON  {reason}")
    return "\n".join(lines)


def tool_summary(call: dict[str, str]) -> str:
    if call.get("name") == "shell":
        cmd = call.get("cmd", "")
        return f"$ {cmd[:160]}"
    if call.get("name") == "patch":
        return "patch " + str(tool_presentation(call)["target"])
    if call.get("name") == "consult_specialist":
        specialty = coordinator_specialty(call).replace("_", " ")
        target = f" | {call.get('path')}" if call.get("path") else ""
        return f"coordinator delegate {specialty}{target} | {call.get('task', '')[:100]}"
    if call.get("name") == "find_paths":
        return f"find {call.get('query', '')[:80]} under {call.get('path', '')[:100]}"
    if call.get("name") == "grep":
        return f"grep {call.get('query', '')[:80]} under {call.get('path') or '.'}"
    presentation = tool_presentation(call)
    target = truncate(collapse_ws(str(presentation.get("target") or "")), 140).splitlines()[0]
    return f"{call.get('name') or 'tool'} {target}".rstrip()


def open_file_preview(
    cwd: Path,
    value: str,
    line: int | None = None,
    max_lines: int = 260,
    start_line: int | None = None,
) -> tuple[int, str]:
    path = resolve_user_path(cwd, value)
    if not path.exists():
        return 1, f"not found: {path}"
    if path.is_dir():
        return 1, f"is a directory: {path}"
    try:
        probe = path.read_bytes()[:4096]
        if b"\x00" in probe:
            return 1, f"binary file not displayed: {path}"
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return 1, f"could not read {path}: {exc}"

    max_lines = max(1, min(260, max_lines))
    lines = text.splitlines()
    total = len(lines)
    if line is not None:
        if line < 1:
            return 1, "line must be >= 1"
        half = max_lines // 2
        start = max(1, line - half)
        end = min(total, start + max_lines - 1)
        start = max(1, end - max_lines + 1)
    elif start_line is not None:
        if start_line < 1:
            return 1, "start_line must be >= 1"
        if total and start_line > total:
            return 1, f"start_line {start_line} exceeds file length {total}"
        start = start_line
        end = min(total, start + max_lines - 1)
    else:
        start = 1
        end = min(total, max_lines)

    rendered = [f"{path}"]
    if total:
        rendered.append(f"lines {start}-{end} of {total}")
        rendered.extend(f"{idx:>5}  {lines[idx - 1]}" for idx in range(start, end + 1))
    else:
        rendered.append("(empty file)")
    if end < total:
        rendered.append(f"...[{total - end} lines remain; continue with start_line={end + 1}, max_lines={max_lines}]")
    return 0, "\n".join(rendered)


def search_files(
    cwd: Path,
    pattern: str,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    return grep_target(cwd, pattern, cancel_event=cancel_event)


def grep_target(
    target: Path,
    pattern: str,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Search a file or directory through one bounded, cancellable backend contract."""
    target = target.resolve()
    if "\x00" in pattern:
        return 2, "invalid search expression: null bytes are not supported"
    if not target.exists() or not (target.is_file() or target.is_dir()):
        return 1, f"search root not found: {target}"
    ripgrep = shutil.which("rg")
    if ripgrep:
        command = [ripgrep, "-n", "--with-filename", "--hidden"]
        for glob in SEARCH_GLOBS:
            command.extend(["--glob", glob])
        workdir = target if target.is_dir() else target.parent
        search_path = "." if target.is_dir() else target.name
        command.extend(["--", pattern, search_path])
    else:
        workdir = target if target.is_dir() else target.parent
        command = [
            sys.executable,
            "-m",
            "dairack.search",
            "--max-output",
            str(MAX_TOOL_OUTPUT),
            "--max-file-bytes",
            str(MAX_INDEX_FILE_BYTES),
            "--max-files",
            str(MAX_INDEX_FILES),
            "--",
            pattern,
            str(target),
        ]

    code, output = run_argv(
        command,
        workdir,
        timeout=SEARCH_TIMEOUT,
        cancel_event=cancel_event,
    )
    if code == 1 and not output:
        return 1, "no matches"
    lowered = output.lower()
    if code == 2 and "regex" in lowered and "invalid search expression" not in lowered:
        return 2, "invalid search expression:\n" + output
    return code, output


def list_directory(cwd: Path, value: str = ".") -> tuple[int, str]:
    path = resolve_user_path(cwd, value or ".")
    if not path.exists():
        return 1, f"not found: {path}"
    if not path.is_dir():
        return 1, f"not a directory: {path}"
    try:
        entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except Exception as exc:
        return 1, f"could not list {path}: {exc}"
    lines = [str(path)]
    for entry in entries[:240]:
        marker = "/" if entry.is_dir() else ""
        try:
            size = "" if entry.is_dir() else size_human(entry.stat().st_size)
        except OSError:
            size = "unknown"
        lines.append(f"{entry.name}{marker:<1} {size}")
    if len(entries) > 240:
        lines.append(f"...[{len(entries) - 240} entries not shown]")
    return 0, "\n".join(lines)


def git_diff(cwd: Path) -> tuple[int, str]:
    root = detect_project_root(cwd)
    if not (root / ".git").exists():
        return 1, f"not a git repository: {cwd}"
    stat = subprocess.run(
        ["git", "-C", str(root), "diff", "--stat"], text=True, capture_output=True, timeout=DEFAULT_TIMEOUT
    )
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", "--"], text=True, capture_output=True, timeout=DEFAULT_TIMEOUT
    )
    output = (stat.stdout or "") + (stat.stderr or "")
    if output.strip():
        output += "\n"
    output += (diff.stdout or "") + (diff.stderr or "")
    code = diff.returncode if diff.returncode else stat.returncode
    return code, output or "(no diff)"


INDEX_SKIP_NAMES = {
    ".cache",
    ".codex",
    ".git",
    ".hg",
    ".idea",
    ".local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
    "__pycache__",
}


def should_skip_index_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in INDEX_SKIP_NAMES for part in rel.parts)


def read_indexable_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_INDEX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return None


def file_language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".h": "c",
        ".cpp": "cpp",
        ".hpp": "cpp",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
    }
    return mapping.get(suffix, suffix[1:] if suffix else "text")


def extract_symbols(rel: str, content: str) -> list[tuple[str, int, str, str, str]]:
    patterns = [
        (r"^\s*(async\s+def|def)\s+([A-Za-z_][\w]*)\s*(\([^)]*\))?", "function"),
        (r"^\s*class\s+([A-Za-z_][\w]*)\s*(\([^)]*\))?", "class"),
        (r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(\([^)]*\))?", "function"),
        (r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*?\)?\s*=>", "function"),
        (r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", "class"),
        (r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)", "interface"),
        (r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)", "type"),
        (r"^\s*func\s+(?:\([^)]+\)\s*)?([A-Za-z_][\w]*)\s*(\([^)]*\))?", "function"),
        (r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)\s*(\([^)]*\))?", "function"),
        (r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_][\w]*)", "type"),
        (r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+([A-Za-z_][\w]*)", "type"),
    ]
    results: list[tuple[str, int, str, str, str]] = []
    seen: set[tuple[int, str, str]] = set()
    for line_no, line in enumerate(content.splitlines(), 1):
        if len(line) > 500:
            continue
        for pattern, kind in patterns:
            match = re.match(pattern, line)
            if not match:
                continue
            groups = [group for group in match.groups() if group]
            if not groups:
                continue
            name = groups[1] if groups[0] in {"def", "async def"} and len(groups) > 1 else groups[0]
            if name in {"export", "public", "private", "protected", "static"}:
                continue
            key = (line_no, kind, name)
            if key in seen:
                continue
            seen.add(key)
            results.append((rel, line_no, kind, name, line.strip()[:300]))
            break
    return results


def extract_imports(rel: str, content: str) -> list[tuple[str, int, str, str]]:
    patterns = [
        (r"^\s*import\s+([A-Za-z_][\w.]*)(?:\s+as\s+\w+)?\s*$", "python"),
        (r"^\s*import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", "javascript"),
        (r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+", "python"),
        (r"require\(['\"]([^'\"]+)['\"]\)", "javascript"),
        (r"^\s*use\s+([A-Za-z_][\w:]*);", "rust"),
        (r"^\s*import\s+['\"]([^'\"]+)['\"]", "javascript"),
        (r"^\s*import\s+\"([^\"]+)\"", "go"),
    ]
    imports: list[tuple[str, int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for line_no, line in enumerate(content.splitlines(), 1):
        if len(line) > 500:
            continue
        for pattern, kind in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            name = (
                match.group(2) if kind == "javascript" and match.lastindex and match.lastindex >= 2 else match.group(1)
            )
            if kind == "python" and line.strip().startswith("import ") and "," in name:
                names = [part.strip().split()[0] for part in name.split(",")]
            else:
                names = [name.strip()]
            for item in names:
                if not item:
                    continue
                key = (line_no, item)
                if key not in seen:
                    imports.append((rel, line_no, kind, item[:220]))
                    seen.add(key)
            break
    return imports


def detect_test_commands(root: Path, rows: list[tuple[str, str, float, int, str]]) -> list[dict[str, str]]:
    by_path = {row[1]: row[4] for row in rows}
    commands: list[dict[str, str]] = []

    def add(name: str, cmd: str, reason: str) -> None:
        if not any(item["cmd"] == cmd for item in commands):
            commands.append({"name": name, "cmd": cmd, "reason": reason})

    package = by_path.get("package.json")
    if package:
        try:
            data = json.loads(package)
            scripts = data.get("scripts") if isinstance(data, dict) else {}
            if isinstance(scripts, dict):
                for name in ("test", "test:unit", "test:ci", "lint", "typecheck"):
                    if name in scripts:
                        add(
                            f"npm {name}",
                            f"npm run {name}" if name != "test" else "npm test",
                            f"package.json script: {scripts[name]}",
                        )
        except Exception:
            pass
    if "pnpm-lock.yaml" in by_path and any(item["cmd"].startswith("npm ") for item in commands):
        commands = [
            {
                **item,
                "name": item["name"].replace("npm", "pnpm", 1),
                "cmd": item["cmd"].replace("npm run", "pnpm", 1).replace("npm test", "pnpm test", 1),
            }
            for item in commands
        ]
    elif "yarn.lock" in by_path and any(item["cmd"].startswith("npm ") for item in commands):
        commands = [
            {
                **item,
                "name": item["name"].replace("npm", "yarn", 1),
                "cmd": item["cmd"].replace("npm run", "yarn", 1).replace("npm test", "yarn test", 1),
            }
            for item in commands
        ]
    if "Cargo.toml" in by_path:
        add("cargo test", "cargo test", "Cargo.toml present")
    if "go.mod" in by_path:
        add("go test", "go test ./...", "go.mod present")
    if any(path.startswith("tests/") for path in by_path) or any(
        path in by_path for path in ("pytest.ini", "conftest.py")
    ):
        add("pytest", "python3 -m pytest", "Python test files detected")
    if "pyproject.toml" in by_path and (
        "pytest" in by_path["pyproject.toml"] or "[tool.pytest" in by_path["pyproject.toml"]
    ):
        add("pytest", "python3 -m pytest", "pyproject pytest config detected")
    if "Makefile" in by_path and re.search(r"^test\s*:", by_path["Makefile"], re.MULTILINE):
        add("make test", "make test", "Makefile test target detected")
    return commands[:12]


def repo_profile(
    root: Path,
    rows: list[tuple[str, str, float, int, str]],
    symbols: list[tuple[str, int, str, str, str]],
    imports: list[tuple[str, int, str, str]],
) -> dict[str, Any]:
    language_counts: dict[str, int] = {}
    for _root, rel, _mtime, _size, _content in rows:
        language = file_language(rel)
        language_counts[language] = language_counts.get(language, 0) + 1
    top_languages = sorted(language_counts.items(), key=lambda item: item[1], reverse=True)[:8]
    test_commands = detect_test_commands(root, rows)
    package_files = [
        rel
        for _root, rel, _mtime, _size, _content in rows
        if Path(rel).name in {"package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Makefile"}
    ]
    top_imports: dict[str, int] = {}
    for _path, _line, _kind, name in imports:
        top_imports[name] = top_imports.get(name, 0) + 1
    common_imports = sorted(top_imports.items(), key=lambda item: item[1], reverse=True)[:10]
    summary_lines = [
        f"Root: {root}",
        f"Files indexed: {len(rows)}",
        "Languages: " + (", ".join(f"{name} {count}" for name, count in top_languages) or "unknown"),
        "Package/config files: " + (", ".join(package_files[:12]) or "none detected"),
        "Detected tests: " + (", ".join(item["cmd"] for item in test_commands) or "none detected"),
        "Common imports: " + (", ".join(f"{name} ({count})" for name, count in common_imports) or "none detected"),
        "Top symbols: "
        + (
            ", ".join(f"{name} ({kind}, {path}:{line})" for path, line, kind, name, _sig in symbols[:20])
            or "none detected"
        ),
    ]
    return {
        "root": str(root),
        "languages": dict(top_languages),
        "test_commands": test_commands,
        "package_files": package_files,
        "common_imports": dict(common_imports),
        "summary": "\n".join(summary_lines),
    }


def _open_index_db_once() -> sqlite3.Connection:
    INDEX_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        INDEX_DB_PATH.parent.chmod(0o700)
    except OSError:
        pass
    conn = sqlite3.connect(str(INDEX_DB_PATH))
    try:
        try:
            INDEX_DB_PATH.chmod(0o600)
        except OSError:
            pass
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            "root TEXT PRIMARY KEY, updated_at TEXT NOT NULL, file_count INTEGER NOT NULL, byte_count INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            "root TEXT NOT NULL, path TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, content TEXT NOT NULL, "
            "PRIMARY KEY(root, path))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS symbols ("
            "root TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, kind TEXT NOT NULL, "
            "name TEXT NOT NULL, signature TEXT NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS symbols_root_name ON symbols(root, name)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS imports ("
            "root TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS imports_root_name ON imports(root, name)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS project_profiles ("
            "root TEXT PRIMARY KEY, updated_at TEXT NOT NULL, profile_json TEXT NOT NULL, summary TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS file_vectors ("
            "root TEXT NOT NULL, path TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, "
            "model TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL, "
            "PRIMARY KEY(root, path))"
        )
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(root UNINDEXED, path, content)")
        except sqlite3.OperationalError:
            pass
    except Exception:
        conn.close()
        raise
    return conn


_INDEX_CORRUPTION_MARKERS = ("malformed", "not a database", "file is encrypted", "disk image is malformed")
_INDEX_REPAIR_LOCK = threading.Lock()


def is_index_corruption_error(error: sqlite3.DatabaseError) -> bool:
    return any(marker in str(error).lower() for marker in _INDEX_CORRUPTION_MARKERS)


def quarantine_corrupt_index(error: sqlite3.DatabaseError) -> None:
    if not is_index_corruption_error(error):
        raise error
    with _INDEX_REPAIR_LOCK:
        if INDEX_DB_PATH.exists():
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
            quarantine = INDEX_DB_PATH.with_name(f"{INDEX_DB_PATH.name}.corrupt-{stamp}")
            try:
                INDEX_DB_PATH.replace(quarantine)
            except OSError as move_error:
                raise sqlite3.DatabaseError(
                    f"project index is corrupt and could not be quarantined: {move_error}"
                ) from error
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(INDEX_DB_PATH) + suffix).unlink(missing_ok=True)
            except OSError as cleanup_error:
                raise sqlite3.DatabaseError(f"project index sidecar could not be removed: {cleanup_error}") from error


def recover_corrupt_index(error: sqlite3.DatabaseError, operation: str) -> str:
    if not is_index_corruption_error(error):
        return f"{operation} failed: {error}"
    try:
        quarantine_corrupt_index(error)
        connection = _open_index_db_once()
        connection.close()
    except sqlite3.DatabaseError as repair_error:
        return f"{operation} failed; project index repair failed: {repair_error}"
    return "Project index was corrupt and has been reset. Run /index to rebuild it."


def open_index_db(*, repair: bool = False) -> sqlite3.Connection:
    try:
        return _open_index_db_once()
    except sqlite3.DatabaseError as exc:
        if not repair or not is_index_corruption_error(exc):
            raise
        quarantine_corrupt_index(exc)
        return _open_index_db_once()


def index_has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_fts'").fetchone()
    return bool(row)


def index_project_with_vectors(
    provider: Any | None,
    target: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Build the lexical index, then refresh semantic vectors best effort."""
    code, output = build_project_index(target, cancel_event)
    if code == 0 and provider is not None:
        _vector_code, vector_output = embed_project_vectors(provider, target, cancel_event)
        output = f"{output}\n{vector_output}"
    return code, output


def build_project_index(
    cwd: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    cwd = cwd.expanduser().resolve()
    if not cwd.is_dir():
        return 1, f"not a directory: {cwd}"
    root = detect_project_root(cwd)
    conn = open_index_db(repair=True)
    database_error: sqlite3.DatabaseError | None = None
    try:
        return _build_project_index_connected(root, conn, cancel_event)
    except sqlite3.DatabaseError as exc:
        database_error = exc
    finally:
        conn.close()
    if database_error is None or not is_index_corruption_error(database_error):
        if database_error is not None:
            raise database_error
        raise sqlite3.DatabaseError("project index failed without an error")
    quarantine_corrupt_index(database_error)
    conn = open_index_db(repair=True)
    try:
        return _build_project_index_connected(root, conn, cancel_event)
    finally:
        conn.close()


def _build_project_index_connected(
    root: Path,
    conn: sqlite3.Connection,
    cancel_event: threading.Event | None,
) -> tuple[int, str]:
    fts = index_has_fts(conn)
    rows: list[tuple[str, str, float, int, str]] = []
    symbols: list[tuple[str, int, str, str, str]] = []
    imports: list[tuple[str, int, str, str]] = []
    skipped = 0
    total_bytes = 0
    for current, dirs, files in os.walk(root):
        if cancel_event and cancel_event.is_set():
            conn.close()
            return 130, "project indexing interrupted before the index was replaced"
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if name not in INDEX_SKIP_NAMES and not should_skip_index_path(current_path / name, root)
        ]
        for name in files:
            if cancel_event and cancel_event.is_set():
                conn.close()
                return 130, "project indexing interrupted before the index was replaced"
            path = current_path / name
            if should_skip_index_path(path, root):
                skipped += 1
                continue
            try:
                stat = path.stat()
            except OSError:
                skipped += 1
                continue
            text = read_indexable_text(path)
            if text is None:
                skipped += 1
                continue
            rel = str(path.relative_to(root))
            rows.append((str(root), rel, stat.st_mtime, stat.st_size, text))
            symbols.extend(extract_symbols(rel, text))
            imports.extend(extract_imports(rel, text))
            total_bytes += stat.st_size
            if len(rows) >= MAX_INDEX_FILES:
                skipped += 1
                break
        if len(rows) >= MAX_INDEX_FILES:
            break

    if cancel_event and cancel_event.is_set():
        conn.close()
        return 130, "project indexing interrupted before the index was replaced"
    with conn:
        conn.execute("DELETE FROM files WHERE root = ?", (str(root),))
        conn.executemany("INSERT OR REPLACE INTO files(root, path, mtime, size, content) VALUES (?, ?, ?, ?, ?)", rows)
        if fts:
            conn.execute("DELETE FROM file_fts WHERE root = ?", (str(root),))
            conn.executemany(
                "INSERT INTO file_fts(root, path, content) VALUES (?, ?, ?)", [(row[0], row[1], row[4]) for row in rows]
            )
        conn.execute("DELETE FROM symbols WHERE root = ?", (str(root),))
        conn.executemany(
            "INSERT INTO symbols(root, path, line, kind, name, signature) VALUES (?, ?, ?, ?, ?, ?)",
            [(str(root), path, line, kind, name, signature) for path, line, kind, name, signature in symbols],
        )
        conn.execute("DELETE FROM imports WHERE root = ?", (str(root),))
        conn.executemany(
            "INSERT INTO imports(root, path, line, kind, name) VALUES (?, ?, ?, ?, ?)",
            [(str(root), path, line, kind, name) for path, line, kind, name in imports],
        )
        profile = repo_profile(root, rows, symbols, imports)
        conn.execute(
            "INSERT OR REPLACE INTO project_profiles(root, updated_at, profile_json, summary) VALUES (?, ?, ?, ?)",
            (str(root), now_iso(), json.dumps(profile, sort_keys=True), profile["summary"]),
        )
        conn.execute(
            "INSERT OR REPLACE INTO projects(root, updated_at, file_count, byte_count) VALUES (?, ?, ?, ?)",
            (str(root), now_iso(), len(rows), total_bytes),
        )
    conn.close()
    tests = detect_test_commands(root, rows)
    languages = ", ".join(f"{name} {count}" for name, count in profile["languages"].items()) or "unknown"
    test_text = ", ".join(item["cmd"] for item in tests) or "none detected"
    return 0, (
        f"indexed {len(rows)} files ({size_human(total_bytes)})\n"
        f"root: {root}\n"
        f"symbols: {len(symbols)}\n"
        f"imports: {len(imports)}\n"
        f"languages: {languages}\n"
        f"tests: {test_text}\n"
        f"skipped: {skipped}"
    )


def indexed_project_for_cwd(cwd: Path) -> Path | None:
    if not INDEX_DB_PATH.exists():
        return None
    try:
        conn = open_index_db()
        try:
            roots = [Path(row[0]) for row in conn.execute("SELECT root FROM projects").fetchall()]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        recover_corrupt_index(exc, "project lookup")
        return None
    cwd = cwd.resolve()
    matches = [root for root in roots if path_within(cwd, root)]
    if not matches:
        return None
    return sorted(matches, key=lambda item: len(str(item)), reverse=True)[0]


def project_scope_for_chat(chat: dict[str, Any], cwd: Path) -> Path:
    saved = str(chat.get("project_root") or "").strip()
    if saved:
        candidate = Path(saved).expanduser().resolve()
        cwd = cwd.resolve()
        if candidate.is_dir() and candidate != Path(candidate.anchor):
            local_root = detect_project_root(cwd)
            indexed_root = indexed_project_for_cwd(candidate)
            if candidate == local_root or (
                indexed_root is not None
                and indexed_root.resolve() == candidate
                and detect_project_root(candidate) == candidate
            ):
                return candidate
    return detect_project_root(cwd)


def remember_indexed_project(
    chat: dict[str, Any],
    cwd: Path,
    call: dict[str, str],
    exit_code: int,
    project_root: Path | None = None,
) -> None:
    if exit_code != 0 or call.get("name") != "index_project":
        return
    target = resolve_user_path(project_root or cwd, call.get("path", ".") or ".")
    if target.is_dir():
        chat["project_root"] = str(detect_project_root(target))


def update_project_scope_after_cd(chat: dict[str, Any], cwd: Path) -> None:
    saved = str(chat.get("project_root") or "").strip()
    if saved and not path_within(cwd, Path(saved).expanduser()):
        chat["project_root"] = ""


def fts_query(raw: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]{2,}", raw)
    if not terms:
        return ""
    return " OR ".join(f"{term}*" for term in terms[:8])


def make_file_snippet(path: str, content: str, query: str, max_lines: int = 10) -> str:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", query)]
    lines = content.splitlines()
    hit = 0
    for i, line in enumerate(lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            hit = i
            break
    start = max(0, hit - max_lines // 2)
    end = min(len(lines), start + max_lines)
    start = max(0, end - max_lines)
    rendered = [f"{path}:{start + 1}-{end}"]
    rendered.extend(f"{idx + 1:>5}  {lines[idx]}" for idx in range(start, end))
    return "\n".join(rendered)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fresh_index_rows(root: Path, rows: list[tuple[Any, ...]]) -> tuple[list[tuple[Any, ...]], int]:
    fresh: list[tuple[Any, ...]] = []
    stale = 0
    for row in rows:
        if len(row) < 4:
            stale += 1
            continue
        try:
            stat = (root / str(row[0])).stat()
            expected_mtime = float(row[-2])
            expected_size = int(row[-1])
        except (OSError, TypeError, ValueError):
            stale += 1
            continue
        if stat.st_size != expected_size or abs(stat.st_mtime - expected_mtime) > 1e-6:
            stale += 1
            continue
        fresh.append(row[:-2])
    return fresh, stale


def embedding_model_for(provider: Any) -> str:
    """Pick the smallest installed embedding-capable model; cached per provider instance."""
    cached = getattr(provider, "_dairack_embedding_model", None)
    if cached is not None:
        return str(cached)
    name = ""
    try:
        models = provider.list_models()
    except Exception:
        return ""
    candidates = [model for model in models if "embedding" in tuple(getattr(model, "capabilities", ()) or ())]
    if candidates:
        name = str(min(candidates, key=lambda model: int(getattr(model, "size", 0) or 0)).name)
    provider._dairack_embedding_model = name
    return name


def _unit_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _vector_dot(packed: bytes, dim: int, query: list[float]) -> float:
    if dim != len(query) or len(packed) != dim * 4:
        return -1.0
    return sum(a * b for a, b in zip(struct.unpack(f"{dim}f", packed), query, strict=True))


def _embed_document_text(path: str, content: str) -> str:
    return f"{path}\n{content[:1600]}"


def embed_project_vectors(
    provider: Any,
    cwd: Path,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Build or refresh per-file semantic vectors for the indexed project, best effort."""
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return 1, "No project index found for this directory. Run /index first."
    model = embedding_model_for(provider)
    if not model:
        return 1, "no embedding-capable model installed; retrieval stays lexical"
    conn = open_index_db()
    embedded = kept = 0
    try:
        files = conn.execute("SELECT path, mtime, size, content FROM files WHERE root = ?", (str(root),)).fetchall()
        current = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT path, mtime, size, model FROM file_vectors WHERE root = ?", (str(root),)
            ).fetchall()
        }
        live_paths = {str(path) for path, _mtime, _size, _content in files}
        removed = [path for path in current if path not in live_paths]
        for path in removed:
            conn.execute("DELETE FROM file_vectors WHERE root = ? AND path = ?", (str(root), path))
        pending: list[tuple[str, float, int, str]] = []
        for path, mtime, size, content in files:
            if current.get(str(path)) == (mtime, size, model):
                kept += 1
                continue
            pending.append((str(path), float(mtime), int(size), str(content or "")))
        for start in range(0, len(pending), 16):
            if cancel_event and cancel_event.is_set():
                conn.commit()
                return 130, "semantic indexing interrupted"
            batch = pending[start : start + 16]
            vectors = provider.embed(model, [_embed_document_text(path, content) for path, _m, _s, content in batch])
            if len(vectors) != len(batch):
                return 1, "embedding response count mismatch; semantic vectors unchanged"
            for (path, mtime, size, _content), vector in zip(batch, vectors, strict=True):
                unit = _unit_vector(vector)
                conn.execute(
                    "INSERT OR REPLACE INTO file_vectors (root, path, mtime, size, model, dim, vector) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (str(root), path, mtime, size, model, len(unit), _pack_vector(unit)),
                )
                embedded += 1
        conn.commit()
    except sqlite3.DatabaseError as exc:
        return 1, recover_corrupt_index(exc, "semantic indexing")
    except Exception as exc:
        return 1, f"semantic indexing unavailable: {exc}"
    finally:
        conn.close()
    return 0, f"semantic vectors: {embedded} embedded, {kept} current, {len(removed)} removed ({model})"


def hybrid_project_search(
    provider: Any,
    cwd: Path,
    query: str,
    limit: int = 8,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Fuse lexical bm25 hits with semantic vector hits via reciprocal-rank fusion.

    Falls back to the lexical result whenever a provider, embedding model, or
    vector table is unavailable, so retrieval never degrades below today's FTS5
    behavior.
    """
    lexical_code, lexical_output = search_project_index(cwd, query, limit=limit, cancel_event=cancel_event)
    if provider is None:
        return lexical_code, lexical_output
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return lexical_code, lexical_output
    model = embedding_model_for(provider)
    if not model:
        return lexical_code, lexical_output
    try:
        conn = open_index_db()
    except sqlite3.DatabaseError:
        return lexical_code, lexical_output
    try:
        vector_rows = conn.execute(
            "SELECT path, dim, vector FROM file_vectors WHERE root = ? AND model = ?",
            (str(root), model),
        ).fetchall()
        if not vector_rows:
            return lexical_code, lexical_output
        try:
            query_vector = _unit_vector(provider.embed(model, [query])[0])
        except Exception:
            return lexical_code, lexical_output
        scored = sorted(
            ((_vector_dot(bytes(vector), int(dim), query_vector), str(path)) for path, dim, vector in vector_rows),
            reverse=True,
        )
        semantic_ranked = [path for score, path in scored[: max(limit * 3, 12)] if score > 0.0]
        candidates = max(limit * 3, 12)
        lexical_ranked: list[str] = []
        if index_has_fts(conn):
            fts = fts_query(query)
            if fts:
                try:
                    lexical_ranked = [
                        str(row[0])
                        for row in conn.execute(
                            "SELECT path FROM file_fts WHERE root = ? AND file_fts MATCH ? "
                            "ORDER BY bm25(file_fts) LIMIT ?",
                            (str(root), fts, candidates),
                        ).fetchall()
                    ]
                except sqlite3.OperationalError:
                    lexical_ranked = []
        fused: dict[str, float] = {}
        for ranked in (lexical_ranked, semantic_ranked):
            for rank, path in enumerate(ranked):
                fused[path] = fused.get(path, 0.0) + 1.0 / (60.0 + rank)
        if not fused:
            return lexical_code, lexical_output
        ordered = [path for path, _score in sorted(fused.items(), key=lambda item: -item[1])][:limit]
        placeholders = ",".join("?" for _ in ordered)
        rows = conn.execute(
            f"SELECT path, content, mtime, size FROM files WHERE root = ? AND path IN ({placeholders})",
            (str(root), *ordered),
        ).fetchall()
    except sqlite3.DatabaseError:
        return lexical_code, lexical_output
    finally:
        conn.close()
    by_path = {str(row[0]): row for row in rows}
    ordered_rows = [by_path[path] for path in ordered if path in by_path]
    fresh, stale = fresh_index_rows(root, ordered_rows)
    if not fresh:
        return lexical_code, lexical_output
    snippets = [make_file_snippet(path, content, query) for path, content in fresh]
    suffix = f"\n\n{stale} stale match(es) omitted; run /index to refresh." if stale else ""
    return 0, f"root: {root}\n\n" + "\n\n---\n\n".join(snippets) + suffix


def search_project_index(
    cwd: Path,
    query: str,
    limit: int = 8,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    if cancel_event and cancel_event.is_set():
        return 130, "project search interrupted"
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return 1, "No project index found for this directory. Run /index first."
    conn = open_index_db()
    if cancel_event is not None:
        conn.set_progress_handler(lambda: 1 if cancel_event.is_set() else 0, 1000)
    rows: list[tuple[Any, ...]] = []
    database_error: sqlite3.DatabaseError | None = None
    try:
        if index_has_fts(conn):
            q = fts_query(query)
            if q:
                try:
                    rows = conn.execute(
                        "SELECT files.path, files.content, files.mtime, files.size FROM file_fts "
                        "JOIN files ON files.root = file_fts.root AND files.path = file_fts.path "
                        "WHERE file_fts.root = ? AND file_fts MATCH ? "
                        "ORDER BY bm25(file_fts) LIMIT ?",
                        (str(root), q, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    if cancel_event and cancel_event.is_set():
                        return 130, "project search interrupted"
                    rows = []
        if not rows:
            like = f"%{escape_like(query)}%"
            rows = conn.execute(
                "SELECT path, content, mtime, size FROM files "
                "WHERE root = ? AND (path LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\') LIMIT ?",
                (str(root), like, like, limit),
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        if cancel_event and cancel_event.is_set():
            return 130, "project search interrupted"
        database_error = exc
    finally:
        conn.close()
    if database_error is not None:
        return 1, recover_corrupt_index(database_error, "project search")
    if cancel_event and cancel_event.is_set():
        return 130, "project search interrupted"
    rows, stale = fresh_index_rows(root, rows)
    if not rows:
        if stale:
            return 1, f"indexed matches are stale ({stale}); run /index to refresh\nroot: {root}"
        return 1, f"no indexed matches for {query!r}\nroot: {root}"
    snippets = [make_file_snippet(path, content, query) for path, content in rows]
    suffix = f"\n\n{stale} stale match(es) omitted; run /index to refresh." if stale else ""
    return 0, f"root: {root}\n\n" + "\n\n---\n\n".join(snippets) + suffix


def search_symbols(cwd: Path, query: str = "", limit: int = 40) -> tuple[int, str]:
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return 1, "No project index found for this directory. Run /index first."
    conn = open_index_db()
    database_error: sqlite3.DatabaseError | None = None
    rows: list[tuple[Any, ...]] = []
    try:
        if query.strip():
            like = f"%{escape_like(query)}%"
            rows = conn.execute(
                "SELECT symbols.path, symbols.line, symbols.kind, symbols.name, symbols.signature, "
                "files.mtime, files.size FROM symbols JOIN files "
                "ON files.root = symbols.root AND files.path = symbols.path "
                "WHERE symbols.root = ? AND (symbols.name LIKE ? ESCAPE '\\' "
                "OR symbols.signature LIKE ? ESCAPE '\\' OR symbols.path LIKE ? ESCAPE '\\') "
                "ORDER BY symbols.path, symbols.line LIMIT ?",
                (str(root), like, like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT symbols.path, symbols.line, symbols.kind, symbols.name, symbols.signature, "
                "files.mtime, files.size FROM symbols JOIN files "
                "ON files.root = symbols.root AND files.path = symbols.path "
                "WHERE symbols.root = ? ORDER BY symbols.path, symbols.line LIMIT ?",
                (str(root), limit),
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        database_error = exc
    finally:
        conn.close()
    if database_error is not None:
        return 1, recover_corrupt_index(database_error, "symbol search")
    rows, _stale = fresh_index_rows(root, rows)
    if not rows:
        return 1, f"no indexed symbols for {query!r}\nroot: {root}"
    lines = [f"root: {root}", "Symbols:"]
    lines.extend(f"{path}:{line:<5} {kind:<10} {name:<32} {signature}" for path, line, kind, name, signature in rows)
    return 0, "\n".join(lines)


def search_imports(cwd: Path, query: str = "", limit: int = 60) -> tuple[int, str]:
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return 1, "No project index found for this directory. Run /index first."
    conn = open_index_db()
    database_error: sqlite3.DatabaseError | None = None
    rows: list[tuple[Any, ...]] = []
    try:
        if query.strip():
            like = f"%{escape_like(query)}%"
            rows = conn.execute(
                "SELECT imports.path, imports.line, imports.kind, imports.name, files.mtime, files.size "
                "FROM imports JOIN files ON files.root = imports.root AND files.path = imports.path "
                "WHERE imports.root = ? AND (imports.name LIKE ? ESCAPE '\\' "
                "OR imports.path LIKE ? ESCAPE '\\') ORDER BY imports.name, imports.path LIMIT ?",
                (str(root), like, like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT imports.path, imports.line, imports.kind, imports.name, files.mtime, files.size "
                "FROM imports JOIN files ON files.root = imports.root AND files.path = imports.path "
                "WHERE imports.root = ? ORDER BY imports.name, imports.path LIMIT ?",
                (str(root), limit),
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        database_error = exc
    finally:
        conn.close()
    if database_error is not None:
        return 1, recover_corrupt_index(database_error, "import search")
    rows, _stale = fresh_index_rows(root, rows)
    if not rows:
        return 1, f"no indexed imports for {query!r}\nroot: {root}"
    lines = [f"root: {root}", "Imports/dependencies:"]
    lines.extend(f"{name:<36} {kind:<12} {path}:{line}" for path, line, kind, name in rows)
    return 0, "\n".join(lines)


def load_repo_profile(cwd: Path) -> tuple[int, dict[str, Any] | None, str]:
    root = indexed_project_for_cwd(cwd)
    if root is None:
        return 1, None, "No project index found for this directory. Run /index first."
    conn = open_index_db()
    database_error: sqlite3.DatabaseError | None = None
    row: tuple[Any, ...] | None = None
    try:
        row = conn.execute("SELECT profile_json, summary FROM project_profiles WHERE root = ?", (str(root),)).fetchone()
    except sqlite3.DatabaseError as exc:
        database_error = exc
    finally:
        conn.close()
    if database_error is not None:
        return 1, None, recover_corrupt_index(database_error, "repo profile")
    if not row:
        return 1, None, "No repo profile found. Run /index again."
    try:
        profile = json.loads(row[0])
    except Exception:
        profile = {"root": str(root), "summary": row[1], "test_commands": []}
    return 0, profile, str(profile.get("summary") or row[1])


def repo_profile_text(cwd: Path) -> tuple[int, str]:
    code, _profile, summary = load_repo_profile(cwd)
    return code, summary


def test_commands_for_cwd(cwd: Path) -> tuple[int, list[dict[str, str]], str]:
    code, profile, message = load_repo_profile(cwd)
    if code != 0 or profile is None:
        return code, [], message
    commands = profile.get("test_commands")
    if not isinstance(commands, list):
        commands = []
    cleaned = []
    for item in commands:
        if isinstance(item, dict) and item.get("cmd"):
            cleaned.append(
                {
                    "name": str(item.get("name") or item["cmd"]),
                    "cmd": str(item["cmd"]),
                    "reason": str(item.get("reason") or ""),
                }
            )
    return 0, cleaned, message


def format_test_commands(cwd: Path) -> tuple[int, str]:
    code, commands, message = test_commands_for_cwd(cwd)
    if code != 0:
        return code, message
    if not commands:
        return 1, "No test commands detected. Run /index after adding project config, or run /test <command>."
    lines = ["Detected test commands:"]
    for index, item in enumerate(commands, 1):
        reason = f"  ({item['reason']})" if item.get("reason") else ""
        lines.append(f"{index}. {item['cmd']}{reason}")
    lines.append("")
    lines.append("Run with /test <number> or /test <command>.")
    return 0, "\n".join(lines)


def resolve_test_command(cwd: Path, args: list[str]) -> tuple[int, str, str]:
    if not args:
        return 1, "", "usage: /test <number|command>"
    if len(args) == 1 and args[0].isdigit():
        code, commands, message = test_commands_for_cwd(cwd)
        if code != 0:
            return code, "", message
        index = int(args[0]) - 1
        if not 0 <= index < len(commands):
            return 1, "", f"test number out of range: {args[0]}"
        return 0, commands[index]["cmd"], commands[index].get("reason", "")
    return 0, " ".join(args), "explicit command"


def retrieved_project_context(
    cwd: Path,
    prompt: str,
    config: dict[str, Any],
    project_root: Path | None = None,
    provider: Any | None = None,
) -> str:
    if not config_bool(config, "project_retrieval", True):
        return ""
    if not prompt.strip():
        return ""
    limit = config_int(config, "retrieval_results", 5, 1, 12)
    scope = project_root or cwd
    if provider is not None and config_bool(config, "retrieval_embeddings", True):
        code, output = hybrid_project_search(provider, scope, prompt, limit=limit)
    else:
        code, output = search_project_index(scope, prompt, limit=limit)
    parts: list[str] = []
    profile_code, profile_text = repo_profile_text(scope)
    if profile_code == 0:
        parts.append("Repo profile:\n" + profile_text)
    sym_code, sym_output = search_symbols(scope, prompt, limit=12)
    if sym_code == 0:
        parts.append(sym_output)
    if code == 0:
        parts.append(output)
    if not parts:
        return ""
    return truncate("\n\n====\n\n".join(parts), MAX_RETRIEVAL_CHARS)


def latest_user_prompt(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content and not content.startswith(TOOL_RESULT_PREFIXES):
            return content
    return ""


def request_context_messages(
    messages: list[dict[str, str]],
    chat: dict[str, Any],
    config: dict[str, Any],
    cwd: Path,
    include_retrieval: bool = True,
    provider: Any | None = None,
) -> list[dict[str, str]]:
    source = summarized_context_source(messages, chat)
    summary = str(chat.get("summary") or "")
    active = active_context_messages(source, summary, config, summary_required=bool(summary.strip()))
    project_root = project_scope_for_chat(chat, cwd)
    retrieved = (
        retrieved_project_context(
            cwd,
            latest_user_prompt(messages),
            config,
            project_root,
            provider=provider,
        )
        if include_retrieval
        else ""
    )
    context_parts: list[str] = []
    if project_root != cwd.resolve():
        context_parts.append(
            f"Active project root: {project_root}\n"
            "Resolve relative file paths, patches, shell actions, and project-memory operations against this root."
        )
    if retrieved:
        context_parts.append(
            "Retrieved local project memory. Treat these snippets as potentially relevant context; "
            "open files or search again when exact current contents matter.\n\n"
            f"{retrieved}"
        )
    elif (
        include_retrieval
        and config_bool(config, "project_retrieval", True)
        and (project_root / ".git").exists()
        and indexed_project_for_cwd(cwd) is None
    ):
        context_parts.append(
            "Project retrieval is inactive because no local index exists for this project yet. File contents "
            "must be read with actions; the user can enable automatic retrieval with /index."
        )
    if not context_parts:
        return active
    retrieval_message = {
        "role": "system",
        "content": "\n\n".join(context_parts),
    }
    insert_at = min(len(active), 1)
    while insert_at < len(active) and active[insert_at].get("role") == "system":
        insert_at += 1
    return active[:insert_at] + [retrieval_message] + active[insert_at:]


_DIFF_C_ESCAPES = {
    "a": b"\a",
    "b": b"\b",
    "f": b"\f",
    "n": b"\n",
    "r": b"\r",
    "t": b"\t",
    "v": b"\v",
    "\\": b"\\",
    '"': b'"',
}


def _validated_diff_header_path(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("patch file header contains a control character")
    return value


def diff_header_path(line: str) -> str:
    """Decode one unified-diff path using Git/GNU patch C-quoting rules."""
    raw = line[4:].strip()
    if not raw:
        raise ValueError("patch contains an empty file header")
    if not raw.startswith('"'):
        value = raw.split("\t", 1)[0]
        return _validated_diff_header_path(value)

    decoded = bytearray()
    index = 1
    while index < len(raw):
        character = raw[index]
        if character == '"':
            suffix = raw[index + 1 :]
            if suffix and not suffix.startswith("\t"):
                raise ValueError("patch contains trailing data after a quoted file header")
            value = os.fsdecode(bytes(decoded))
            return _validated_diff_header_path(value)
        if character != "\\":
            decoded.extend(os.fsencode(character))
            index += 1
            continue
        index += 1
        if index >= len(raw):
            raise ValueError("patch contains an unterminated filename escape")
        escaped = raw[index]
        if escaped in _DIFF_C_ESCAPES:
            decoded.extend(_DIFF_C_ESCAPES[escaped])
            index += 1
            continue
        if escaped in "01234567":
            end = index
            while end < min(len(raw), index + 3) and raw[end] in "01234567":
                end += 1
            decoded.append(int(raw[index:end], 8))
            index = end
            continue
        raise ValueError(f"patch contains unsupported filename escape: \\{escaped}")
    raise ValueError("patch contains an unterminated quoted file header")


def patch_paths_for_strip(patch_text: str, strip: int) -> list[str]:
    paths: set[str] = set()
    for line in patch_text.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        value = diff_header_path(line)
        if value == "/dev/null":
            continue
        parts = Path(value).parts
        if strip:
            parts = parts[strip:]
        if not parts:
            continue
        paths.add(str(Path(*parts)))
    return sorted(paths)


def unified_patch_error(patch_text: str) -> str:
    lines = patch_text.splitlines()
    if any(line.startswith("***************") or line.startswith("*** ") for line in lines):
        return "context-format diffs are not supported; provide a unified diff"
    old_headers = sum(line.startswith("--- ") for line in lines)
    new_headers = sum(line.startswith("+++ ") for line in lines)
    if not old_headers or old_headers != new_headers or not any(line.startswith("@@ ") for line in lines):
        return "patch must be a complete unified diff with paired file headers and hunk markers"
    try:
        for line in lines:
            if line.startswith(("--- ", "+++ ")):
                diff_header_path(line)
    except ValueError as exc:
        return str(exc)
    return ""


def new_checkpoint_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    mode: int = 0o600,
    *,
    private_parent: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if private_parent:
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
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


def create_checkpoint(cwd: Path, paths: list[str], reason: str = "") -> tuple[str, str]:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_id = new_checkpoint_id()
    root = CHECKPOINT_DIR / checkpoint_id
    files_dir = root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    try:
        files_dir.chmod(0o700)
    except OSError:
        pass
    entries: list[dict[str, Any]] = []
    for index, rel in enumerate(paths):
        target = (cwd / rel).resolve()
        if not path_within(target, cwd.resolve()):
            raise ValueError(f"refusing checkpoint outside cwd: {rel}")
        backup_name = f"{index:04d}.bak"
        existed = target.exists()
        target_mode = target.stat().st_mode & 0o777 if existed and target.is_file() else 0
        if existed and target.is_file():
            backup_path = files_dir / backup_name
            atomic_write_bytes(backup_path, target.read_bytes(), private_parent=True)
        entries.append(
            {
                "path": rel,
                "existed": existed,
                "backup": backup_name if existed and target.is_file() else "",
                "mode": target_mode,
            }
        )
    metadata = {
        "id": checkpoint_id,
        "created_at": now_iso(),
        "cwd": str(cwd.resolve()),
        "reason": reason,
        "files": entries,
    }
    atomic_write_json(root / "checkpoint.json", metadata)
    return checkpoint_id, str(root)


def list_checkpoints(limit: int = 20) -> str:
    if not CHECKPOINT_DIR.exists():
        return "No checkpoints yet."
    rows = []
    for path in CHECKPOINT_DIR.iterdir():
        meta_path = path / "checkpoint.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text())
        except Exception:
            continue
        rows.append(data)
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    if not rows:
        return "No checkpoints yet."
    lines = ["Checkpoints:"]
    for item in rows[:limit]:
        lines.append(
            f"{item.get('id')}  {str(item.get('created_at') or '').replace('T', ' ')[:16]}  "
            f"{len(item.get('files') or [])} files  {item.get('reason') or ''}"
        )
    lines.append("")
    lines.append("Restore with /undo <id> or /undo latest.")
    return "\n".join(lines)


def resolve_checkpoint(ref: str = "latest") -> Path:
    if not CHECKPOINT_DIR.exists():
        raise ValueError("No checkpoints yet.")
    checkpoints = sorted(
        [path for path in CHECKPOINT_DIR.iterdir() if (path / "checkpoint.json").exists()],
        key=lambda path: path.name,
        reverse=True,
    )
    if not checkpoints:
        raise ValueError("No checkpoints yet.")
    ref = (ref or "latest").strip()
    if ref in {"latest", "last"}:
        return checkpoints[0]
    exact = [path for path in checkpoints if path.name == ref]
    if exact:
        return exact[0]
    matches = [path for path in checkpoints if path.name.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError("ambiguous checkpoint id prefix")
    raise ValueError(f"checkpoint not found: {ref}")


def undo_checkpoint(ref: str = "latest") -> tuple[int, str]:
    root = resolve_checkpoint(ref)
    meta = json.loads((root / "checkpoint.json").read_text())
    cwd = Path(meta["cwd"]).resolve()
    restored = 0
    removed = 0
    for entry in meta.get("files", []):
        rel = str(entry.get("path") or "")
        target = (cwd / rel).resolve()
        if not path_within(target, cwd):
            return 1, f"checkpoint contains unsafe path: {rel}"
        backup = str(entry.get("backup") or "")
        if entry.get("existed") and backup:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = int(entry.get("mode") or 0o600)
            atomic_write_bytes(target, (root / "files" / backup).read_bytes(), mode=mode)
            restored += 1
        elif not entry.get("existed") and target.exists() and target.is_file():
            target.unlink()
            removed += 1
    return 0, f"restored checkpoint {meta.get('id')}\nrestored files: {restored}\nremoved new files: {removed}"


def apply_unified_patch(patch_text: str, cwd: Path) -> tuple[int, str]:
    if not patch_text.strip():
        return 2, "empty patch"
    format_error = unified_patch_error(patch_text)
    if format_error:
        return 2, format_error
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(patch_text.rstrip() + "\n")
            temp_path = handle.name

        git_binary = shutil.which("git")
        patch_binary = shutil.which("patch")
        if not patch_binary and not git_binary:
            return 127, "applying edits requires `patch` or `git` on PATH"

        backends: list[tuple[str, str]] = []
        if git_binary:
            backends.append(("git", git_binary))
        if patch_binary:
            backends.append(("patch", patch_binary))
        attempts: list[str] = []
        for strip in (1, 0):
            for backend, binary in backends:
                if backend == "git":
                    dry_cmd = [binary, "apply", "--check", f"-p{strip}", temp_path]
                    apply_cmd = [binary, "apply", f"-p{strip}", temp_path]
                else:
                    dry_cmd = [binary, "--batch", "--forward", "--dry-run", f"-p{strip}", "-i", temp_path]
                    apply_cmd = [binary, "--batch", "--forward", f"-p{strip}", "-i", temp_path]
                dry = subprocess.run(
                    dry_cmd,
                    cwd=str(cwd),
                    text=True,
                    capture_output=True,
                    timeout=DEFAULT_TIMEOUT,
                )
                dry_output = (dry.stdout or "") + (dry.stderr or "")
                attempts.append(f"$ {' '.join(shlex.quote(part) for part in dry_cmd[:-1])} <patch>\n{dry_output}")
                if dry.returncode != 0:
                    continue

                targets = patch_paths_for_strip(patch_text, strip)
                if not targets:
                    return 1, "patch dry-run succeeded but no target files were detected"
                try:
                    checkpoint_id, checkpoint_path = create_checkpoint(cwd, targets, reason="patch tool")
                except ValueError as exc:
                    return 1, str(exc)

                applied = subprocess.run(
                    apply_cmd,
                    cwd=str(cwd),
                    text=True,
                    capture_output=True,
                    timeout=DEFAULT_TIMEOUT,
                )
                apply_output = (applied.stdout or "") + (applied.stderr or "")
                output = (
                    f"checkpoint: {checkpoint_id}\n{checkpoint_path}\n"
                    f"$ {' '.join(shlex.quote(part) for part in dry_cmd[:-1])} <patch>\n{dry_output}"
                    f"$ {' '.join(shlex.quote(part) for part in apply_cmd[:-1])} <patch>\n{apply_output}"
                )
                return applied.returncode, output
        return 1, "patch did not apply with available backends at -p1 or -p0\n" + "".join(attempts)
    except FileNotFoundError:
        return 127, "applying edits requires `patch` or `git` on PATH"
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return 124, f"patch timed out after {DEFAULT_TIMEOUT}s\n{output}"
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except OSError:
                pass


def _exact_edit_content(target: Path, old_string: str, new_string: str) -> tuple[str, str] | tuple[None, str]:
    """Return (updated_content, "") for a valid exact edit, or (None, error)."""
    if not old_string:
        return None, "edit_file requires the exact existing text in old_string"
    if old_string == new_string:
        return None, "old_string and new_string are identical; nothing to change"
    if not target.exists():
        return None, f"file not found: {target}"
    if not target.is_file():
        return None, f"not a file: {target}"
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, f"not a UTF-8 text file: {target}"
    except OSError as exc:
        return None, f"could not read {target}: {exc}"
    occurrences = content.count(old_string)
    if occurrences == 0:
        return None, (
            "old_string was not found in the file. Re-read the file and copy the exact "
            "current text, including whitespace and indentation."
        )
    if occurrences > 1:
        return None, (
            f"old_string matches {occurrences} places in the file. Include more surrounding "
            "lines so it matches exactly once."
        )
    return content.replace(old_string, new_string, 1), ""


def apply_exact_edit(call: dict[str, str], cwd: Path) -> tuple[int, str]:
    target = resolve_user_path(cwd, call.get("path", ""))
    updated, error = _exact_edit_content(target, call.get("old_string", ""), call.get("new_string", ""))
    if updated is None:
        return 1, error
    try:
        rel = str(target.resolve().relative_to(cwd.resolve()))
    except ValueError:
        return 1, f"refusing to edit outside the working directory: {target}"
    try:
        checkpoint_id, checkpoint_path = create_checkpoint(cwd, [rel], reason="edit tool")
    except ValueError as exc:
        return 1, str(exc)
    original = target.read_text(encoding="utf-8")
    mode = target.stat().st_mode & 0o777
    atomic_write_bytes(target, updated.encode("utf-8"), mode=mode)
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return 0, (
        f"checkpoint: {checkpoint_id}\n{checkpoint_path}\n"
        f"edited {rel}: +{added} -{removed} lines\n" + truncate_middle("".join(diff_lines), 12000)
    )


def edit_preview_diff(call: dict[str, str], cwd: Path) -> str:
    """Render the diff an edit_file request would apply, for permission review."""
    target = resolve_user_path(cwd, call.get("path", ""))
    updated, error = _exact_edit_content(target, call.get("old_string", ""), call.get("new_string", ""))
    if updated is None:
        return f"edit_file would fail: {error}"
    original = target.read_text(encoding="utf-8")
    rel = call.get("path", "")
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def tool_approval_diff(call: dict[str, str], cwd: Path) -> str:
    """Return the change preview shown during permission review for write actions."""
    if call.get("name") == "patch":
        return call.get("patch", "")
    if call.get("name") == "edit_file":
        try:
            return edit_preview_diff(call, cwd)
        except OSError as exc:
            return f"edit_file preview unavailable: {exc}"
    return ""


def execute_tool_call(
    call: dict[str, str],
    cwd: Path,
    project_root: Path | None = None,
    cancel_event: threading.Event | None = None,
    enforce_project_scope: bool = False,
    output_limit: int | None = None,
) -> tuple[int, str]:
    scope = project_root or cwd
    if cancel_event and cancel_event.is_set():
        return 130, "action interrupted before execution"
    if call.get("name") == "shell":
        if enforce_project_scope:
            argv = read_only_shell_argv(call.get("cmd", ""))
            if argv is None:
                return 1, "read-auto command validation failed; explicit approval is required"
            return run_argv(argv, scope, cancel_event=cancel_event)
        return run_shell(call.get("cmd", ""), scope, cancel_event=cancel_event)
    if call.get("name") == "patch":
        return apply_unified_patch(call.get("patch", ""), scope)
    if call.get("name") == "edit_file":
        return apply_exact_edit(call, scope)
    if call.get("name") == "read_file":
        line = None
        if call.get("line"):
            try:
                line = int(call["line"])
            except ValueError:
                return 1, "line must be an integer"
        start_line = None
        if call.get("start_line"):
            try:
                start_line = int(call["start_line"])
            except ValueError:
                return 1, "start_line must be an integer"
        max_lines = 260
        if call.get("max_lines"):
            try:
                max_lines = int(call["max_lines"])
            except ValueError:
                return 1, "max_lines must be an integer"
        if output_limit is not None:
            context_lines = max(20, min(260, (max(512, output_limit) - 320) // 72))
            max_lines = min(max_lines, context_lines)
        target = resolve_user_path(scope, call.get("path", "."))
        if enforce_project_scope and not path_within(target, scope):
            return 1, f"read-auto scope blocked: {target}"
        return open_file_preview(scope, str(target), line=line, max_lines=max_lines, start_line=start_line)
    if call.get("name") == "list_dir":
        target = resolve_user_path(scope, call.get("path", "."))
        if enforce_project_scope and not path_within(target, scope):
            return 1, f"read-auto scope blocked: {target}"
        return list_directory(scope, str(target))
    if call.get("name") == "find_paths":
        target = resolve_user_path(scope, call.get("path", "."))
        if enforce_project_scope and not path_within(target, scope):
            return 1, f"read-auto scope blocked: {target}"
        return discover_paths(target, call.get("query", ""), cancel_event=cancel_event)
    if call.get("name") == "grep":
        target = resolve_user_path(scope, call.get("path", "."))
        if enforce_project_scope and not path_within(target, scope):
            return 1, f"read-auto scope blocked: {target}"
        return grep_target(target, call.get("query", ""), cancel_event=cancel_event)
    if call.get("name") == "hardware_status":
        return 0, format_hardware_status(load_config(), PATHS)
    if call.get("name") == "search_project":
        requested_root = call.get("path", "").strip()
        search_scope = resolve_user_path(scope, requested_root) if requested_root else scope
        if enforce_project_scope and not path_within(search_scope, scope):
            return 1, f"read-auto scope blocked: {search_scope}"
        return search_project_index(search_scope, call.get("query", ""), limit=8, cancel_event=cancel_event)
    if call.get("name") == "index_project":
        return build_project_index(resolve_user_path(scope, call.get("path", ".")), cancel_event)
    if call.get("name") == "web_search":
        return internet_search(call.get("query", ""), limit=8, cancel_event=cancel_event)
    if call.get("name") == "web_open":
        return web_open_url(call.get("url", ""), cancel_event=cancel_event)
    if call.get("name") == "consult_specialist":
        return 2, "specialist delegation must be executed by the coordinator runtime"
    return 2, f"unknown tool: {call.get('name')}"


def tool_result_message(call: dict[str, str], code: int, result: str) -> str:
    if call.get("name") == "shell":
        return f"Shell tool result:\ncommand: {call.get('cmd', '')}\nexit_code: {code}\noutput:\n{result}"
    if call.get("name") in {"patch", "edit_file"}:
        return f"Patch tool result:\nsummary: {tool_summary(call)}\nexit_code: {code}\noutput:\n{result}"
    if call.get("name") == "consult_specialist":
        return f"Coordinator specialist result:\nsummary: {tool_summary(call)}\nexit_code: {code}\noutput:\n{result}"
    if call.get("name") in {
        "read_file",
        "list_dir",
        "find_paths",
        "grep",
        "hardware_status",
        "search_project",
        "index_project",
        "web_search",
        "web_open",
    }:
        return (
            f"Structured tool result:\n"
            f"tool: {call.get('name')}\n"
            f"summary: {tool_summary(call)}\n"
            f"exit_code: {code}\n"
            f"output:\n{result}"
        )
    return f"Tool result:\nexit_code: {code}\noutput:\n{result}"


def tool_history_message(call: dict[str, str], code: int, result: str) -> dict[str, Any]:
    content = tool_result_message(call, code, result)
    if call.get("_protocol") == "native":
        return {"role": "tool", "tool_name": str(call.get("name") or "tool"), "content": content}
    return {"role": "user", "content": content}


def denied_tool_history_message(call: dict[str, str], detail: str) -> dict[str, Any]:
    if call.get("_protocol") == "native":
        return {
            "role": "tool",
            "tool_name": str(call.get("name") or "tool"),
            "content": f"Action was not executed: {detail}",
        }
    return {"role": "user", "content": f"Tool request denied {detail}."}


def diff_style_for_line(line: str) -> str:
    if line.startswith("+") and not line.startswith("+++"):
        return "class:diff.add"
    if line.startswith("-") and not line.startswith("---"):
        return "class:diff.del"
    if line.startswith("@@"):
        return "class:diff.hunk"
    if line.startswith("---") or line.startswith("+++"):
        return "class:diff.file"
    return "class:diff.ctx"


def approve_tool(call: dict[str, str], cwd: Path | None = None) -> bool:
    print()
    print("Tool request:")
    if call.get("reason"):
        print(f"  reason: {call['reason']}")
    print(f"  {tool_summary(call)}")
    preview = tool_approval_diff(call, cwd or Path.cwd())
    if preview:
        print()
        for line in preview.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                print(ansi(line, "32"))
            elif line.startswith("-") and not line.startswith("---"):
                print(ansi(line, "31"))
            elif line.startswith("@@"):
                print(ansi(line, "36"))
            else:
                print(line)
    answer = input("Allow this action? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def setup_readline() -> None:
    try:
        import readline  # type: ignore

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if HISTORY_PATH.exists():
            readline.read_history_file(str(HISTORY_PATH))
        import atexit

        atexit.register(readline.write_history_file, str(HISTORY_PATH))
    except Exception:
        pass


def fancy_enabled(args: argparse.Namespace) -> bool:
    return (
        HAS_PROMPT_TOOLKIT
        and not args.plain
        and not args.no_color
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and not env_enabled("PLAIN")
    )


def textual_enabled(args: argparse.Namespace) -> bool:
    return (
        not args.plain
        and not args.no_color
        and not getattr(args, "legacy_ui", False)
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and not env_enabled("PLAIN")
        and not env_enabled("LEGACY_UI")
    )


def configured_mode_label(config: dict[str, Any]) -> str:
    if str(config.get("model_mode") or "direct") == "orchestrator":
        return f"coordinator/{config.get('orchestrator_policy', 'adaptive')}"
    return str(config.get("model") or "none")


def print_header(config: dict[str, Any], version: str, fancy: bool) -> None:
    mode_label = configured_mode_label(config)
    if not fancy:
        print(f"{APP} | type here; /help for commands, /exit to quit")
        print(f"mode: {mode_label} | provider: ollama {version}")
        return

    width = max(64, os.get_terminal_size().columns)
    title = f" {APP} "
    rule = "-" * max(1, width - len(title) - 1)
    print(ansi(f"{title}{rule}", "36;1"))
    print(
        f"{ansi('mode', '2')}: {ansi(mode_label, '35;1')}  "
        f"{ansi('provider', '2')}: {ansi('ollama ' + version, '34;1')}  "
        f"{ansi('ctx', '2')}: {ansi(str(config.get('num_ctx')), '33;1')}  "
        f"{ansi('agent', '2')}: {ansi('on' if config.get('agent') else 'off', '32;1' if config.get('agent') else '2')}"
    )
    print(ansi("type below. /help commands, /model switch, /exit quit", "2"))


def toolbar_html(config: dict[str, Any], cwd: Path) -> Any:
    model = html.escape(configured_mode_label(config))
    ctx = html.escape(str(config.get("num_ctx") or ""))
    agent = "on" if config.get("agent") else "off"
    cwd_text = html.escape(str(cwd))
    if len(cwd_text) > 42:
        cwd_text = "..." + cwd_text[-39:]
    return HTML(
        f'<style bg="ansiblack"> '
        f'<style fg="ansimagenta" bold="true">mode {model}</style>'
        f' <style fg="ansibrightblack">|</style> ctx {ctx}'
        f' <style fg="ansibrightblack">|</style> agent {agent}'
        f' <style fg="ansibrightblack">|</style> {cwd_text}'
        f' <style fg="ansibrightblack">|</style> '
        f'<style fg="ansicyan" bold="true">/model</style> switch '
        f'<style fg="ansicyan" bold="true">/help</style> help '
        f"</style>"
    )


def read_line(session: Any, config: dict[str, Any], cwd: Path, fancy: bool) -> str:
    if not fancy:
        return input("you> ").strip()
    model = html.escape(configured_mode_label(config))
    return session.prompt(
        HTML(
            f'<style fg="ansimagenta" bold="true">{model}</style> '
            f'<style fg="ansibrightblack">|</style> '
            f'<style fg="ansicyan" bold="true">you</style>'
            f'<style fg="ansibrightblack"> &gt; </style>'
        ),
        bottom_toolbar=lambda: toolbar_html(config, cwd),
    ).strip()


class SynchronizedMessages(list[dict[str, Any]]):
    """List whose structural mutations share the chat snapshot lock."""

    def __init__(self, values: list[dict[str, Any]], lock: threading.RLock) -> None:
        super().__init__(values)
        self._lock = lock

    def __deepcopy__(self, memo: dict[int, Any]) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(list(self), memo)

    def append(self, value: dict[str, Any]) -> None:
        with self._lock:
            super().append(value)

    def extend(self, values: Any) -> None:
        with self._lock:
            super().extend(values)

    def insert(self, index: int, value: dict[str, Any]) -> None:
        with self._lock:
            super().insert(index, value)

    def pop(self, index: int = -1) -> dict[str, Any]:
        with self._lock:
            return super().pop(index)

    def remove(self, value: dict[str, Any]) -> None:
        with self._lock:
            super().remove(value)

    def clear(self) -> None:
        with self._lock:
            super().clear()

    def copy(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self)

    def reverse(self) -> None:
        with self._lock:
            super().reverse()

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        with self._lock:
            super().sort(key=key, reverse=reverse)

    def __setitem__(self, key: Any, value: Any) -> None:
        with self._lock:
            super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        with self._lock:
            super().__delitem__(key)

    def __iadd__(self, values: Any) -> SynchronizedMessages:
        with self._lock:
            super().__iadd__(values)
        return self

    def __imul__(self, value: int) -> SynchronizedMessages:
        with self._lock:
            super().__imul__(value)
        return self


class DairackTui:
    def __init__(
        self,
        provider: OllamaProvider,
        version: str,
        config: dict[str, Any],
        cwd: Path,
        chat: dict[str, Any] | None = None,
        messages: list[dict[str, str]] | None = None,
        blocks: list[dict[str, str]] | None = None,
    ) -> None:
        self.provider = provider
        self.version = version
        self.config = config
        self.cwd = cwd
        self.chat = chat or new_chat_state(cwd, config)
        self.lock = threading.RLock()
        self.messages = SynchronizedMessages(
            messages or [{"role": "system", "content": system_prompt(cwd, bool(config.get("agent")), config)}],
            self.lock,
        )
        self._worker_threads: set[threading.Thread] = set()
        self.busy = False
        self.busy_label = ""
        self.busy_started = 0.0
        self._busy_interruptible = True
        self.interrupt_requested = False
        self.cancel_event = threading.Event()
        self._active_tool_call: dict[str, str] | None = None
        self.blocks: list[dict[str, str]] = blocks or []
        self.pending_tool: dict[str, str] | None = None
        self.pending_images: list[Path] = []
        self._queued_prompts: list[str] = []
        self.model_picker_active = False
        self.model_picker_items: list[ModelInfo] = []
        self.model_picker_index = 0
        self._active_route: dict[str, Any] | None = None
        self._route_config: dict[str, Any] | None = None
        self._route_plan = ""
        self._route_review_rounds = 0
        self._agent_steps_used = 0
        self._loop_guard = ActionLoopGuard()

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
        if not env_enabled("NO_COLOR"):
            os.environ.pop("NO_COLOR", None)

        self.transcript = TextArea(
            text="",
            multiline=True,
            wrap_lines=True,
            read_only=True,
            focusable=True,
            focus_on_click=True,
            scrollbar=True,
            lexer=DairackTranscriptLexer(),
            style="class:transcript",
        )
        self.transcript.buffer.set_document(
            Document(self.render_transcript_text(), cursor_position=len(self.render_transcript_text())),
            bypass_readonly=True,
        )
        self.approval_panel = Window(
            FormattedTextControl(
                self.approval_fragments,
                show_cursor=False,
            ),
            height=7,
            wrap_lines=False,
            always_hide_cursor=True,
            style="class:approval",
        )
        self.model_picker_panel = Window(
            FormattedTextControl(self.model_picker_fragments),
            height=8,
            wrap_lines=False,
            style="class:picker",
        )
        self.input = TextArea(
            height=1,
            multiline=False,
            wrap_lines=False,
            completer=WordCompleter(SLASH_COMMANDS, ignore_case=True, sentence=True),
            history=FileHistory(str(HISTORY_PATH)),
            prompt=[("class:input.prompt", "  > ")],
            style="class:input",
        )

        kb = KeyBindings()

        @kb.add("enter")
        def _(event: Any) -> None:
            if self.model_picker_active:
                self.select_model_picker()
                return
            if self.pending_tool:
                self.approve_pending_tool()
                return
            self.submit()

        @kb.add("down", filter=Condition(lambda: self.model_picker_active))
        @kb.add("c-n", filter=Condition(lambda: self.model_picker_active))
        @kb.add("j", filter=Condition(lambda: self.model_picker_active))
        def _(event: Any) -> None:
            self.move_model_picker(1)

        @kb.add("up", filter=Condition(lambda: self.model_picker_active))
        @kb.add("c-p", filter=Condition(lambda: self.model_picker_active))
        @kb.add("k", filter=Condition(lambda: self.model_picker_active))
        def _(event: Any) -> None:
            self.move_model_picker(-1)

        @kb.add("c-q")
        def _(event: Any) -> None:
            event.app.exit()

        @kb.add("c-c")
        def _(event: Any) -> None:
            if self.busy:
                self.request_interrupt()
                return
            event.app.exit()

        @kb.add("c-l")
        def _(event: Any) -> None:
            self.clear_transcript()

        @kb.add("c-t")
        def _(event: Any) -> None:
            self.focus_transcript()

        @kb.add("pageup")
        def _(event: Any) -> None:
            self.scroll_transcript(-12)

        @kb.add("pagedown")
        def _(event: Any) -> None:
            self.scroll_transcript(12)

        @kb.add("home")
        def _(event: Any) -> None:
            if event.app.current_buffer is self.transcript.buffer:
                self.scroll_transcript_to(0)
            else:
                self.input.buffer.cursor_position = 0

        @kb.add("end")
        def _(event: Any) -> None:
            if event.app.current_buffer is self.transcript.buffer:
                self.scroll_transcript_to(len(self.transcript.text.splitlines()) - 1)
            else:
                self.input.buffer.cursor_position = len(self.input.text)

        @kb.add("escape")
        def _(event: Any) -> None:
            if self.model_picker_active:
                self.close_model_picker()
                return
            if self.pending_tool:
                self.deny_pending_tool()
                return
            if event.app.current_buffer is self.transcript.buffer:
                event.app.layout.focus(self.input)
                return
            self.request_interrupt()

        root = HSplit(
            [
                Window(FormattedTextControl(self.header_fragments), height=1),
                Window(FormattedTextControl(self.subheader_fragments), height=1),
                Window(height=1, char=" ", style="class:gutter"),
                Box(
                    self.transcript,
                    padding_left=3,
                    padding_right=3,
                    padding_top=1,
                    padding_bottom=1,
                    style="class:transcript.box",
                ),
                Window(height=1, char=" ", style="class:gutter"),
                ConditionalContainer(
                    Frame(
                        Box(self.approval_panel, padding_left=1, padding_right=1),
                        title=[("class:approval.title", " action request ")],
                        style="class:approval",
                    ),
                    filter=Condition(lambda: self.pending_tool is not None),
                ),
                Frame(
                    Box(self.input, padding_left=1, padding_right=1),
                    title=self.input_title_fragments,
                    height=3,
                    style="class:input.frame",
                ),
                ConditionalContainer(
                    self.model_picker_panel,
                    filter=Condition(lambda: self.model_picker_active),
                ),
                Window(FormattedTextControl(self.status_fragments), height=1),
                Window(FormattedTextControl(self.footer_fragments), height=1),
            ]
        )
        self.app = Application(
            layout=Layout(root, focused_element=self.input),
            key_bindings=kb,
            style=self.style(),
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.15,
        )

    def style(self) -> Any:
        return Style.from_dict(
            {
                "brand": "#7dd3fc bold",
                "header": "bg:#0f172a #e5e7eb",
                "header.dim": "bg:#0f172a #94a3b8",
                "subtle": "#64748b",
                "model": "#d8b4fe bold",
                "provider": "#93c5fd bold",
                "ctx": "#fde68a bold",
                "ok": "#86efac bold",
                "warn": "#fbbf24 bold",
                "busy": "#67e8f9 bold",
                "busy.alt": "#c4b5fd bold",
                "transcript": "bg:#090e1a #d6d3d1",
                "transcript.box": "bg:#090e1a",
                "empty": "bg:#090e1a #cbd5e1",
                "empty.dim": "bg:#090e1a #64748b",
                "role.you": "bg:#090e1a #38bdf8 bold",
                "role.ai": "bg:#090e1a #d8b4fe bold",
                "role.coordinator": "bg:#090e1a #6faaa3 bold",
                "role.action": "bg:#090e1a #c7a96b bold",
                "role.system": "bg:#090e1a #fbbf24 bold",
                "role.diff": "bg:#090e1a #86efac bold",
                "role.dim": "bg:#090e1a #334155",
                "msg.you": "bg:#090e1a #e2e8f0",
                "msg.ai": "bg:#090e1a #d6d3d1",
                "msg.action": "bg:#090e1a #b9b5aa",
                "msg.system": "bg:#090e1a #cbd5e1",
                "diff.add": "bg:#071a12 #86efac",
                "diff.del": "bg:#1f0a0a #fca5a5",
                "diff.hunk": "bg:#09111f #67e8f9 bold",
                "diff.file": "bg:#111827 #fde68a bold",
                "diff.ctx": "bg:#090e1a #cbd5e1",
                "command": "bg:#090e1a #93c5fd bold",
                "gutter": "bg:#090e1a",
                "frame": "#334155",
                "input.frame": "#38bdf8",
                "input.title": "#38bdf8 bold",
                "input": "bg:#111827 #f8fafc",
                "input.prompt": "#38bdf8 bold",
                "picker": "bg:#0b1220 #cbd5e1",
                "picker.title": "bg:#111827 #7dd3fc bold",
                "picker.hint": "bg:#111827 #64748b",
                "picker.row": "bg:#0b1220 #cbd5e1",
                "picker.current": "bg:#172554 #f8fafc bold",
                "picker.active": "bg:#4c1d95 #f8fafc bold",
                "picker.dim": "bg:#0b1220 #64748b",
                "picker.profile": "bg:#0b1220 #fde68a",
                "approval": "bg:#111827 #e5e7eb",
                "approval.title": "bg:#172554 #f8fafc bold",
                "approval.cmd": "bg:#111827 #93c5fd bold",
                "approval.reason": "bg:#111827 #cbd5e1",
                "approval.warn": "bg:#111827 #fbbf24 bold",
                "footer": "bg:#0f172a #94a3b8",
                "footer.hotkey": "#7dd3fc bold",
                "status": "bg:#020617 #d1d5db",
                "status.dim": "bg:#020617 #64748b",
            }
        )

    def run(self) -> None:
        self.app.run()

    def header_fragments(self) -> list[tuple[str, str]]:
        return [
            ("class:header", " "),
            ("class:brand", "dairack"),
            ("class:header", "  "),
            ("class:header.dim", "local terminal agent"),
            ("class:header", " " * 80),
        ]

    def subheader_fragments(self) -> list[tuple[str, str]]:
        mode = str(self.config.get("model_mode") or "direct")
        model_label = (
            f"coordinator/{self.config.get('orchestrator_policy', 'adaptive')}"
            if mode == "orchestrator"
            else str(self.config.get("model") or "none")
        )
        return [
            ("class:gutter", " "),
            ("class:subtle", "model "),
            ("class:model", model_label),
            ("class:subtle", "  provider "),
            ("class:provider", f"ollama {self.version}"),
            ("class:subtle", "  ctx "),
            ("class:ctx", str(self.config.get("num_ctx") or "")),
            ("class:subtle", "  agent "),
            ("class:ok" if self.config.get("agent") else "class:subtle", "on" if self.config.get("agent") else "off"),
            ("class:subtle", "  permissions "),
            (
                "class:warn"
                if self.config.get("permission_mode") == "ask"
                else "class:ok"
                if self.config.get("permission_mode") == "read-auto"
                else "class:subtle",
                str(self.config.get("permission_mode") or "ask"),
            ),
            ("class:subtle", "  chat "),
            ("class:provider", clean_chat_title(self.chat.get("title", ""), "new chat")),
        ]

    def footer_fragments(self) -> list[tuple[str, str]]:
        return [
            ("class:footer", " "),
            ("class:footer.hotkey", "Ctrl-L"),
            ("class:footer", " clear  "),
            ("class:footer.hotkey", "Ctrl-T"),
            ("class:footer", " chat  "),
            ("class:footer.hotkey", "PgUp/PgDn"),
            ("class:footer", " scroll  "),
            ("class:footer.hotkey", "Esc"),
            ("class:footer", " back/stop  "),
            ("class:footer.hotkey", "Ctrl-Q"),
            ("class:footer", " quit  "),
            ("class:footer.hotkey", "/chats"),
            ("class:footer", " saved  "),
            ("class:footer.hotkey", "/run"),
            ("class:footer", " shell  "),
            ("class:footer.hotkey", "/search"),
            ("class:footer", " files  "),
            ("class:footer.hotkey", "/permissions"),
            ("class:footer", " actions"),
        ]

    def input_title_fragments(self) -> list[tuple[str, str]]:
        model_label = (
            f"coordinator/{self.config.get('orchestrator_policy', 'adaptive')}"
            if str(self.config.get("model_mode") or "direct") == "orchestrator"
            else str(self.config.get("model") or "model")
        )
        return [
            ("class:input.title", " "),
            ("class:model", model_label),
            ("class:input.title", " "),
        ]

    def model_picker_fragments(self) -> list[tuple[str, str]]:
        if not self.model_picker_items:
            return [
                ("class:picker.title", "  model selector  "),
                ("class:picker.hint", " no installed models. Use /pull <model>."),
            ]
        current = str(self.config.get("model") or "")
        count = len(self.model_picker_items)
        window_size = 6
        start = max(0, min(self.model_picker_index - window_size // 2, max(0, count - window_size)))
        visible = self.model_picker_items[start : start + window_size]
        fragments: list[tuple[str, str]] = [
            ("class:picker.title", "  model selector  "),
            ("class:picker.hint", " up/down j/k move  enter select  esc cancel\n"),
        ]
        for offset, model in enumerate(visible, start):
            selected = offset == self.model_picker_index
            orchestrator = model.name == ORCHESTRATOR_MODEL_ID
            active = (
                str(self.config.get("model_mode") or "direct") == "orchestrator"
                if orchestrator
                else str(self.config.get("model_mode") or "direct") != "orchestrator" and model.name == current
            )
            profile = model_profile_for(model.name)
            role = (
                f"task coordination / {self.config.get('orchestrator_policy', 'adaptive')}"
                if orchestrator
                else profile["role"]
                if profile
                else "custom model"
            )
            display_name = "COORDINATOR" if orchestrator else model.name
            name = display_name if len(display_name) <= 28 else display_name[:25] + "..."
            role_text = role if len(role) <= 34 else role[:31] + "..."
            marker = ">" if selected else " "
            active_marker = "*" if active else " "
            row_style = "class:picker.active" if selected else "class:picker.current" if active else "class:picker.row"
            fragments.append((row_style, f" {marker} {active_marker} {offset + 1:>2}. {name:<28} "))
            fragments.append(("class:picker.profile", f"{role_text:<34} "))
            fragments.append(("class:picker.dim", f"{size_human(model.size):>9}\n"))
        if count > window_size:
            fragments.append(("class:picker.hint", f"  showing {start + 1}-{start + len(visible)} of {count}\n"))
        return fragments

    def approval_fragments(self) -> list[tuple[str, str]]:
        call = self.pending_tool
        if not call:
            return []
        name = call.get("name") or "unknown"
        summary = tool_summary(call)
        reason = call.get("reason") or "(not provided)"
        fragments: list[tuple[str, str]] = [
            ("class:approval.title", "  pending action  "),
            ("class:approval", f"{name}\n"),
            ("class:approval.reason", "  reason  "),
            ("class:approval", f"{reason[:110]}\n"),
        ]
        if name == "shell":
            fragments.extend(
                [
                    ("class:approval.reason", "  command "),
                    ("class:approval.cmd", f"$ {call.get('cmd', '')[:130]}\n"),
                ]
            )
        elif name == "patch":
            adds, dels, files = patch_stats(call.get("patch", ""))
            fragments.extend(
                [
                    ("class:approval.reason", "  patch   "),
                    ("class:approval.cmd", f"{files} files, +{adds} -{dels}\n"),
                ]
            )
        else:
            fragments.extend(
                [
                    ("class:approval.reason", "  target  "),
                    ("class:approval.cmd", summary[:130] + "\n"),
                ]
            )
        fragments.extend(
            [
                ("class:approval.warn", "  Enter"),
                ("class:approval", " approve  "),
                ("class:approval.warn", "Esc"),
                ("class:approval", " deny  "),
                ("class:approval.warn", "/permissions"),
                ("class:approval", " change policy"),
            ]
        )
        return fragments

    def render_transcript_text(self) -> str:
        if not self.blocks:
            return (
                "\n"
                "Dairack is ready.\n"
                f"Chat: {clean_chat_title(self.chat.get('title', ''), 'new chat')}. Use /help, /chats, /resume, or /model.\n"
            )

        chunks: list[str] = []
        for block in self.blocks[-140:]:
            role = block["role"]
            text = block["text"].rstrip("\n")
            chunks.append("dairack" if role == "assistant" else role)
            chunks.append(text if text else " ")
            chunks.append("")
        return "\n".join(chunks).rstrip() + "\n"

    def sync_transcript(self) -> None:
        text = self.render_transcript_text()
        focused = hasattr(self, "app") and self.app.current_buffer is self.transcript.buffer
        cursor = min(self.transcript.buffer.cursor_position, len(text)) if focused else len(text)
        self.transcript.buffer.set_document(Document(text, cursor_position=cursor), bypass_readonly=True)

    def focus_transcript(self) -> None:
        self.app.layout.focus(self.transcript)
        self.app.invalidate()

    def scroll_transcript_to(self, row: int) -> None:
        lines = self.transcript.text.splitlines() or [""]
        row = max(0, min(row, len(lines) - 1))
        position = self.transcript.buffer.document.translate_row_col_to_index(row, 0)
        self.transcript.buffer.cursor_position = position
        self.app.layout.focus(self.transcript)
        self.app.invalidate()

    def scroll_transcript(self, delta: int) -> None:
        row = self.transcript.buffer.document.cursor_position_row + delta
        self.scroll_transcript_to(row)

    def copy_transcript(self) -> None:
        text = self.render_transcript_text()
        try:
            self.app.output.write_raw(osc52_sequence(text))
            self.app.output.flush()
            self.append_system("Transcript copied to terminal clipboard.")
        except Exception as exc:
            self.append_error(f"copy failed: {exc}")

    def status_fragments(self) -> list[tuple[str, str]]:
        if self.model_picker_active:
            return [
                ("class:status", " model selector "),
                ("class:footer.hotkey", "Enter"),
                ("class:status.dim", " select  "),
                ("class:footer.hotkey", "Esc"),
                ("class:status.dim", " cancel  "),
                ("class:footer.hotkey", "/profile"),
                ("class:status.dim", " tune selected model after switching"),
            ]
        if self.pending_tool:
            return [
                ("class:status", " action request "),
                ("class:footer.hotkey", "Enter"),
                ("class:status.dim", " approve  "),
                ("class:footer.hotkey", "Esc"),
                ("class:status.dim", " deny  "),
                ("class:footer.hotkey", "/permissions"),
                ("class:status.dim", " policy"),
            ]
        if self.busy:
            elapsed = max(0.0, time.monotonic() - self.busy_started)
            dots = "." * ((int(elapsed * 3) % 4) + 1)
            dots = f"{dots:<4}"
            wave = ["|", "/", "-", "\\"][int(elapsed * 8) % 4]
            color = "class:busy" if int(elapsed * 5) % 2 == 0 else "class:busy.alt"
            interruptible = (
                bool(tool_presentation(self._active_tool_call).get("interruptible"))
                if self._active_tool_call
                else self._busy_interruptible
            )
            return [
                ("class:status", " "),
                (color, wave),
                ("class:status", " "),
                (color, dots),
                ("class:status", f" {self.busy_label:<18} "),
                ("class:warn", f"{elapsed:0.1f}s"),
                ("class:status.dim", "  esc stop" if interruptible else "  finishing safely"),
                ("class:status.dim", "  model "),
                ("class:model", str(self.config.get("model") or "")),
            ]

        cwd = str(self.cwd)
        if len(cwd) > 52:
            cwd = "..." + cwd[-49:]
        return [
            ("class:status", " ready "),
            ("class:status.dim", " enter sends  "),
            ("class:footer.hotkey", "/model"),
            ("class:status.dim", " switch  "),
            ("class:footer.hotkey", "Ctrl-T"),
            ("class:status.dim", " chat  "),
            ("class:footer.hotkey", "/web"),
            ("class:status.dim", " internet  "),
            ("class:footer.hotkey", "/help"),
            ("class:status.dim", " commands  "),
            ("class:subtle", cwd),
        ]

    def transcript_fragments(self) -> list[tuple[str, str]]:
        if not self.blocks:
            return [
                ("class:empty", "\n"),
                ("class:empty", "Dairack is ready.\n"),
                (
                    "class:empty.dim",
                    f"Chat: {clean_chat_title(self.chat.get('title', ''), 'new chat')}. Use /help, /chats, /resume, or /model.\n",
                ),
            ]

        fragments: list[tuple[str, str]] = []
        for block in self.blocks[-80:]:
            role = block["role"]
            text = block["text"].rstrip("\n")
            if role == "diff":
                fragments.append(("class:role.diff", "diff"))
                fragments.append(("class:role.dim", "\n"))
                for line in text.splitlines():
                    fragments.append((diff_style_for_line(line), line + "\n"))
                if not text:
                    fragments.append(("class:diff.ctx", " \n"))
                fragments.append(("class:role.dim", "\n"))
                continue
            if role == "you":
                fragments.append(("class:role.you", "you"))
            elif role == "assistant":
                fragments.append(("class:role.ai", "dairack"))
            elif role == "coordinator":
                fragments.append(("class:role.coordinator", "coordinator"))
            elif role == "action":
                fragments.append(("class:role.action", "action"))
            else:
                fragments.append(("class:role.system", "system"))
            fragments.append(("class:role.dim", "\n"))
            style = (
                "class:msg.you"
                if role == "you"
                else "class:msg.ai"
                if role == "assistant"
                else "class:msg.action"
                if role == "action"
                else "class:msg.system"
            )
            fragments.append((style, text if text else " "))
            fragments.append(("class:role.dim", "\n\n"))
        return fragments

    def transcript_cursor(self) -> Point:
        lines = 0
        for _, text in self.transcript_fragments():
            lines += text.count("\n")
        return Point(x=0, y=max(0, lines))

    def submit(self) -> None:
        text = self.input.text.strip()
        if not text and not self.pending_images:
            self.input.text = ""
            return
        if not text:
            text = "Analyze the attached image carefully and report the relevant details."
        if self.busy:
            if text.startswith("/"):
                self.append_system("Still processing. Press Esc to interrupt before running commands.")
                return
            self.input.text = ""
            self._queued_prompts.append(text)
            self.append_system(
                f"Queued ({len(self._queued_prompts)}). Sends when the current response completes; Esc stops it."
            )
            return
        if self.pending_tool:
            command = text.split(maxsplit=1)[0].lower() if text.startswith("/") else ""
            if command in {"/allow", "/approve", "/deny"}:
                self.input.text = ""
                self.handle_command(text)
                return
            self.append_system("Approve or reject the pending action first with /allow or /deny.")
            return
        if text.startswith("/"):
            self.input.text = ""
            self.handle_command(text)
            return
        self.input.text = ""
        if self._queued_prompts:
            self._queued_prompts.append(text)
            text = self._queued_prompts.pop(0)
        self.dispatch_prompt(text)

    def dispatch_prompt(self, text: str) -> None:
        message: dict[str, Any] = {"role": "user", "content": text}
        if self.pending_images:
            message["image_paths"] = [str(path) for path in self.pending_images]
        self.messages.append(message)
        if self.pending_images:
            labels = "  ".join(f"[IMAGE {path.name}]" for path in self.pending_images)
            self.append_user(labels + "\n\n" + text)
            self.pending_images = []
        else:
            self.append_user(text)
        self.save_current_chat()
        self.start_generation()

    def restore_draft(self, text: str) -> None:
        existing = self.input.text
        combined = text if not existing.strip() else text + "\n" + existing
        self.input.text = combined.replace("\n", " ")
        self.app.invalidate()

    def flush_queued_prompt(self, interrupted: bool) -> None:
        """After a turn ends, send the next queued prompt, or hand queued text back after an interrupt.

        A pending approval holds the queue; the flush after the approval's own
        continuation worker delivers it instead.
        """
        if not self._queued_prompts:
            return
        if self.pending_tool:
            return
        if interrupted:
            drained = "\n".join(self._queued_prompts)
            self._queued_prompts.clear()
            self.restore_draft(drained)
            self.append_system("Interrupted; queued input was returned to the input area.")
            return
        self.dispatch_prompt(self._queued_prompts.pop(0))

    def redraw_transcript(self) -> None:
        if len(self.blocks) > 120:
            self.blocks = [{"role": "system", "text": "older transcript trimmed"}] + self.blocks[-100:]
        self.sync_transcript()
        self.app.invalidate()

    def append_block(self, role: str, text: str, severity: str = "info", kind: str = "message") -> None:
        with self.lock:
            block = {"role": role, "text": text}
            if role == "system" and severity in {"success", "warning", "error"}:
                block["severity"] = severity
            if role == "system" and kind == "reference":
                block["kind"] = kind
            self.blocks.append(block)
            self.redraw_transcript()

    def append_to_last(self, text: str) -> None:
        with self.lock:
            if not self.blocks:
                self.blocks.append({"role": "assistant", "text": ""})
            self.blocks[-1]["text"] += text
            self.redraw_transcript()

    def replace_last_assistant_text(self, text: str) -> None:
        with self.lock:
            for block in reversed(self.blocks):
                if block["role"] == "assistant":
                    block["text"] = text
                    self.redraw_transcript()
                    return

    def append_user(self, text: str) -> None:
        self.append_block("you", text)

    def append_assistant_start(self) -> None:
        self.append_block("assistant", "")

    def append_assistant_chunk(self, text: str) -> None:
        self.append_to_last(text)

    def append_assistant_end(self) -> None:
        self.redraw_transcript()

    def discard_last_assistant_entry(self) -> None:
        with self.lock:
            if self.blocks and self.blocks[-1].get("role") == "assistant":
                self.blocks.pop()
                self.redraw_transcript()

    def append_system(self, text: str, severity: str = "info", kind: str = "message") -> None:
        self.append_block("system", text.strip(), severity, kind)

    def append_reference(self, text: str) -> None:
        self.append_system(text, kind="reference")

    def append_error(self, text: str) -> None:
        self.append_system(text, "error")

    def record_runtime_failure(self, error: Exception | str, phase: str = "model response") -> None:
        self.append_error(str(error))
        self.messages.append(runtime_failure_message(error, phase))
        self.save_current_chat()

    def append_warning(self, text: str) -> None:
        self.append_system(text, "warning")

    def append_success(self, text: str) -> None:
        self.append_system(text, "success")

    def append_action(self, text: str) -> None:
        self.append_block("action", text.strip())

    def append_diff(self, text: str) -> None:
        self.append_block("diff", truncate(text, 40000).strip())

    def save_current_chat(self, title: str | None = None, announce: bool = False) -> None:
        with self.lock:
            if title is not None:
                self.chat["title"] = clean_chat_title(title, "new chat")
            if (
                self.chat.get("_transient")
                and title is None
                and not announce
                and not any(
                    message.get("role") == "user"
                    and str(message.get("content") or "").strip()
                    and not str(message.get("content") or "").startswith(TOOL_RESULT_PREFIXES)
                    for message in self.messages
                )
            ):
                return
            self.chat.pop("_transient", None)
            chat_snapshot = deepcopy(self.chat)
            config_snapshot = deepcopy(self.config)
            messages_snapshot = deepcopy(self.messages)
            blocks_snapshot = deepcopy(self.blocks)
            cwd_snapshot = self.cwd
        path = save_chat_session(
            chat_snapshot,
            cwd_snapshot,
            config_snapshot,
            messages_snapshot,
            blocks_snapshot,
        )
        config_snapshot["last_chat"] = chat_snapshot["id"]
        save_config(config_snapshot)
        with self.lock:
            for key in ("id", "title", "created_at", "updated_at", "cwd", "route_history"):
                if key in chat_snapshot:
                    self.chat[key] = chat_snapshot[key]
            self.config["last_chat"] = chat_snapshot["id"]
        if announce:
            self.append_system(f"saved chat: {chat_snapshot['title']}\n{path}")

    def load_chat(self, ref: str = "latest") -> None:
        session = resolve_chat_session(ref)
        chat, cwd, messages, blocks = chat_runtime_state(session, self.cwd, self.config)
        self.chat = chat
        self.cwd = cwd
        self.messages = SynchronizedMessages(messages, self.lock)
        self.blocks = blocks
        if chat.get("model"):
            self.config["model"] = chat["model"]
        self.config["model_mode"] = str(chat.get("model_mode") or self.config.get("model_mode") or "direct")
        self.config["orchestrator_policy"] = str(
            chat.get("orchestrator_policy") or self.config.get("orchestrator_policy") or "adaptive"
        )
        self._active_route = None
        self._route_config = None
        self.pending_images = []
        self.config["last_chat"] = chat["id"]
        save_config(self.config)
        with self.lock:
            self.messages[0]["content"] = system_prompt(self.cwd, bool(self.config.get("agent")), self.config)
        self.append_system(f"resumed chat: {self.chat['title']}")
        self.app.invalidate()

    def start_new_chat(self, title: str = "") -> None:
        self.chat = new_chat_state(self.cwd, self.config, title)
        self.chat["_transient"] = True
        self.messages = SynchronizedMessages(
            [{"role": "system", "content": system_prompt(self.cwd, bool(self.config.get("agent")), self.config)}],
            self.lock,
        )
        self.blocks = []
        self.pending_tool = None
        self.pending_images = []
        self.save_current_chat()
        self.append_system(f"new chat: {self.chat['title']}")
        self.app.invalidate()

    def clear_transcript(self) -> None:
        with self.lock:
            self.blocks = []
            self.redraw_transcript()

    def set_busy(self, value: bool, label: str = "", *, interruptible: bool | None = None) -> None:
        was_busy = self.busy
        self.busy = value
        self.busy_label = label
        if value and not was_busy:
            self.busy_started = time.monotonic()
            self._busy_interruptible = True if interruptible is None else interruptible
        elif value and interruptible is not None:
            self._busy_interruptible = interruptible
        elif not value:
            self.busy_started = 0.0
            self._busy_interruptible = True
        self.app.invalidate()

    def begin_tool_action(self, call: dict[str, str], step_label: str = "") -> None:
        self._active_tool_call = dict(call)
        self.set_busy(True, tool_activity_label(call, step_label))

    def finish_tool_action(self) -> None:
        self._active_tool_call = None
        self.app.invalidate()

    def request_interrupt(self) -> None:
        if not self.busy:
            return
        action_interruptible = not self._active_tool_call or bool(
            tool_presentation(self._active_tool_call).get("interruptible")
        )
        if not action_interruptible or (not self._active_tool_call and not self._busy_interruptible):
            self.append_system("This action is finishing atomically and cannot be stopped halfway.")
            return
        if not self.interrupt_requested:
            self.interrupt_requested = True
            self.cancel_event.set()
            self.busy_label = "interrupting"
            self.append_system("Interrupt requested. Stopping after the current model chunk.")
        self.app.invalidate()

    def maybe_auto_compact(
        self,
        runtime: dict[str, Any] | None = None,
        executor: str = "",
    ) -> bool:
        if self.cancel_event.is_set():
            return False
        effective = runtime or context_runtime_config(self.config, self.chat)[0]
        model = executor or chat_executor(self.config, self.chat)
        should, reason = should_auto_compact(self.messages, self.chat, effective)
        if not should:
            return True
        self.set_busy(True, "auto compacting")
        try:
            changed, detail = compact_chat_memory(
                self.provider,
                model,
                self.messages,
                self.chat,
                effective,
                keep_recent=config_int(effective, "auto_compact_keep_recent", 16, 4, 120),
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                self.append_system("auto compact interrupted")
                self.save_current_chat()
                return False
            if changed:
                self.chat["last_compaction"] = {"at": now_iso(), "reason": reason, "detail": detail}
                notice = getattr(self, "set_notice", None)
                if callable(notice):
                    notice("Context compacted")
                else:
                    self.append_system("context compacted\n" + detail)
            self.save_current_chat()
            return True
        except Exception as exc:
            self.append_error(f"auto compact failed: {exc}")
            self.save_current_chat()
            return True

    def start_generation(self, label: str = "loading model") -> None:
        self._active_route = None
        self._route_config = None
        self._route_plan = ""
        self._route_review_rounds = 0
        self._agent_steps_used = 0
        self._loop_guard = ActionLoopGuard()
        if str(self.config.get("model_mode") or "direct") == "orchestrator":
            label = f"routing / {self.config.get('orchestrator_policy', 'adaptive')}"
        self.interrupt_requested = False
        self.cancel_event.clear()
        self.set_busy(True, label)
        self.start_worker(self.generate_worker, "dairack-generate")

    def start_worker(self, target: Callable[[], None], name: str) -> threading.Thread:
        def managed() -> None:
            try:
                target()
            finally:
                with self.lock:
                    self._worker_threads.discard(thread)

        thread = threading.Thread(target=managed, daemon=True, name=name)
        with self.lock:
            self._worker_threads.add(thread)
        thread.start()
        return thread

    def stop_workers(self, timeout: float = 2.0) -> None:
        self.cancel_event.set()
        deadline = time.monotonic() + max(0.0, timeout)
        current = threading.current_thread()
        with self.lock:
            workers = [worker for worker in self._worker_threads if worker is not current]
        critical_names = ("dairack-generate", "dairack-action", "dairack-continue", "dairack-tool", "dairack-compact")
        workers.sort(key=lambda worker: (not worker.name.startswith(critical_names), worker.name))
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)

    def reserve_agent_action(self) -> bool:
        limit = agent_action_limit(self.config)
        if self._agent_steps_used >= limit:
            return False
        self._agent_steps_used += 1
        if self._active_route is not None:
            self._active_route["tool_steps"] = self._agent_steps_used
            self._active_route["tool_limit"] = limit
            self.chat["last_route"] = self._active_route
        return True

    def generate_worker(self) -> None:
        try:
            if self._active_route is None:
                self.set_busy(True, "routing request")
                project_root = project_scope_for_chat(self.chat, self.cwd)
                self._active_route = select_orchestrator_route(
                    self.provider,
                    self.config,
                    self.messages,
                    project_root,
                    self.cancel_event,
                    previous_route=self.chat.get("last_route"),
                )
                self._active_route["tool_steps"] = self._agent_steps_used
                self._active_route["tool_limit"] = agent_action_limit(self.config)
                executor = str(self._active_route.get("executor") or self.config.get("model") or "")
                if not executor:
                    raise RuntimeError("no compute model is available for this route")
                require_vision_support(self.provider, executor, self.messages)
                self._route_config = runtime_config_for_model(self.config, executor)
                self.chat["last_route"] = self._active_route
                if self._active_route.get("planner"):
                    self.set_busy(True, f"planning / {self._active_route['planner']}")
                    self._route_plan = orchestrator_plan(
                        self.provider,
                        self._active_route,
                        self.messages,
                        project_root,
                        self.config,
                        self.cancel_event,
                    )
                self.save_current_chat()
            route = self._active_route
            runtime = self._route_config
            if route is None or runtime is None:
                raise RuntimeError("route initialization failed")
            repairing_action = False
            action_repair_attempted = False
            action_contract_repair_attempted = False
            action_completion_repairs = 0
            completion_repair_attempted = False
            executor_recovery_attempted = False
            completion_feedback = ""
            action_feedback = ""
            revision_feedback = ""
            synthesis_attempts = 0
            while True:
                if self.cancel_event.is_set():
                    self.append_system("Interrupted.")
                    self.save_current_chat()
                    return
                if not self.maybe_auto_compact(runtime, str(route["executor"])):
                    return
                action_limit = agent_action_limit(self.config)
                finalizing = self._agent_steps_used >= action_limit or self._loop_guard.force_synthesis
                if finalizing:
                    synthesis_attempts += 1
                    if synthesis_attempts > 2:
                        self.append_system(
                            "Task action budget reached; final synthesis could not be completed. "
                            "All action results remain in this chat."
                        )
                        self.save_current_chat()
                        return
                assistant_text = ""
                first_chunk = True
                if repairing_action:
                    repairing_action = False
                    self.replace_last_assistant_text("")
                else:
                    self.append_assistant_start()
                request_messages = request_context_messages(
                    self.messages,
                    self.chat,
                    runtime,
                    self.cwd,
                    provider=self.provider,
                )
                directives: list[str] = []
                directive = coordinator_executor_directive(route, self.config)
                if directive:
                    directives.append(directive)
                if self._route_plan:
                    directives.append(
                        "Internal advisory execution brief; verify it and do not mention it:\n" + self._route_plan
                    )
                if completion_feedback:
                    directives.append(completion_feedback)
                    completion_feedback = ""
                if action_feedback:
                    directives.append(action_feedback)
                    action_feedback = ""
                if revision_feedback:
                    directives.append(
                        "Produce a complete corrected replacement answer. Do not discuss internal review or retry "
                        "state, and do not call the answer a revision. Required corrections:\n" + revision_feedback
                    )
                    revision_feedback = ""
                if finalizing:
                    directives.append(
                        agent_synthesis_directive(
                            self._agent_steps_used,
                            action_limit,
                            retry=synthesis_attempts > 1,
                        )
                    )
                request_messages = canonicalize_messages(request_messages, directives)
                executor = str(route["executor"])
                native_calls: list[dict[str, Any]] = []
                native_tools = (
                    []
                    if finalizing
                    else native_tools_for(self.provider, executor, bool(self.config.get("agent")), route)
                )
                try:
                    request_messages, native_tools = fit_agent_request_context_messages(
                        request_messages,
                        runtime,
                        native_tools,
                    )
                except RequestContextError:
                    reduced = request_context_messages(
                        self.messages,
                        self.chat,
                        runtime,
                        self.cwd,
                        include_retrieval=False,
                    )
                    request_messages, native_tools = fit_agent_request_context_messages(
                        canonicalize_messages(reduced, directives),
                        runtime,
                        native_tools,
                    )
                    route["context_degraded"] = "project retrieval omitted to fit the context window"
                if finalizing:
                    self.set_busy(True, f"synthesizing / {executor}")
                stream_retry_used = False
                response_allowance = executor_response_allowance(request_messages, native_tools, runtime)
                generation_error: Exception | None = None
                while True:
                    try:
                        for chunk in self.provider.chat_stream(
                            executor,
                            request_messages,
                            think=bool(runtime.get("think")),
                            num_ctx=int(runtime.get("num_ctx") or 4096),
                            num_predict=response_allowance,
                            keep_alive=executor_keep_alive(runtime),
                            cancel_event=self.cancel_event,
                            extra_options=ollama_options(runtime),
                            tools=native_tools or None,
                            tool_call_sink=native_calls.append,
                        ):
                            if self.cancel_event.is_set():
                                break
                            if first_chunk:
                                self.set_busy(True, "synthesizing response" if finalizing else "streaming response")
                                first_chunk = False
                            assistant_text += chunk
                            self.append_assistant_chunk(chunk)
                        break
                    except Exception as exc:
                        if stream_retry_used or self.cancel_event.is_set() or not transient_stream_error(exc):
                            generation_error = exc
                            break
                        stream_retry_used = True
                        assistant_text = ""
                        native_calls.clear()
                        first_chunk = True
                        self.replace_last_assistant_text("")
                        self.set_busy(True, f"reconnecting / {executor}")
                if generation_error is not None:
                    replacement = (
                        coordinator_recovery_executor(route, executor)
                        if not executor_recovery_attempted and recoverable_model_protocol_error(generation_error)
                        else ""
                    )
                    if replacement:
                        executor_recovery_attempted = True
                        reason = str(generation_error)
                        record_executor_recovery(route, executor, replacement, reason)
                        self._route_config = runtime_config_for_model(self.config, replacement)
                        runtime = self._route_config
                        completion_repair_attempted = False
                        completion_feedback = executor_recovery_directive(reason)
                        repairing_action = True
                        self.replace_last_assistant_text("")
                        self.set_busy(True, f"recovering / {replacement}")
                        self.chat["last_route"] = route
                        self.save_current_chat()
                        continue
                    raise generation_error
                if not self.cancel_event.is_set() and self.maybe_run_read_batch(
                    native_calls,
                    assistant_text,
                    finalizing=finalizing,
                ):
                    self.set_busy(True, "continuing")
                    continue
                call, parse_error = resolve_tool_request(assistant_text, native_calls)
                visible_text = strip_tool_markup(assistant_text)
                internal_call = bool(call and is_internal_coordinator_call(normalize_coordinator_tool_call(call)))
                if call and not internal_call:
                    action_text = tool_request_display(call)
                    rendered_text = action_text
                elif call:
                    rendered_text = visible_text
                elif parse_error:
                    rendered_text = visible_text or "Correcting action request format..."
                else:
                    rendered_text = visible_text or "No response was returned."
                if internal_call and not rendered_text:
                    self.discard_last_assistant_entry()
                else:
                    self.replace_last_assistant_text(rendered_text)
                    self.append_assistant_end()
                if self.cancel_event.is_set():
                    if assistant_text:
                        self.messages.append({"role": "assistant", "content": assistant_text + "\n\n[interrupted]"})
                    self.append_system("Interrupted.")
                    self.save_current_chat()
                    return
                assistant_message: dict[str, Any] = {"role": "assistant", "content": assistant_text}
                if native_calls:
                    assistant_message["tool_calls"] = native_calls
                self.messages.append(assistant_message)
                self.save_current_chat()
                stats = dict(getattr(self.provider, "last_stats", {}) or {})
                incomplete_reason = ""
                if not call and not parse_error:
                    incomplete_reason = response_incomplete_reason(assistant_text, stats)
                agent_enabled = bool(self.config.get("agent"))
                state = TurnState(
                    action_limit=action_limit,
                    action_steps=self._agent_steps_used,
                    synthesis_attempts=synthesis_attempts,
                    review_rounds=self._route_review_rounds,
                    contract_repair_attempted=action_contract_repair_attempted,
                    completion_repair_attempted=completion_repair_attempted,
                    executor_recovery_attempted=executor_recovery_attempted,
                    parse_repair_attempted=action_repair_attempted,
                    action_completion_repairs=action_completion_repairs,
                )
                facts = ResponseFacts(
                    has_call=bool(call),
                    parse_error=parse_error or "",
                    incomplete_reason=incomplete_reason,
                    response_blank=not assistant_text.strip(),
                )
                recovery_executor = (
                    coordinator_recovery_executor(route, executor)
                    if incomplete_reason and not finalizing and not executor_recovery_attempted
                    else ""
                )
                route_facts = RouteFacts(
                    has_reviewer=bool(route.get("reviewer")) and bool(assistant_text.strip()),
                    action_requirement=(action_contract_directive(route, retry=True) or "") if agent_enabled else "",
                    contract_capability=action_completion_required(
                        route,
                        self._agent_steps_used,
                        agent_enabled,
                    ),
                    has_recovery_executor=bool(recovery_executor),
                )
                if completion_repair_attempted and not incomplete_reason and not call and not parse_error:
                    retry_record = route.get("completion_retry")
                    if isinstance(retry_record, dict):
                        retry_record["recovered"] = True
                action = next_action(state, facts, route_facts, finalizing)

                if action is TurnAction.REPAIR_CONTRACT:
                    action_contract_repair_attempted = True
                    if self.messages and self.messages[-1].get("role") == "assistant":
                        self.messages.pop()
                    action_feedback = route_facts.action_requirement
                    repairing_action = True
                    self.replace_last_assistant_text("")
                    self.set_busy(True, "preparing requested action")
                    self.save_current_chat()
                    continue
                if action is TurnAction.RETRY_COMPLETION:
                    completion_repair_attempted = True
                    if self.messages and self.messages[-1].get("role") == "assistant":
                        self.messages.pop()
                    route["completion_retry"] = {
                        "attempted": True,
                        "reason": incomplete_reason,
                        "recovered": False,
                    }
                    completion_feedback = completion_retry_directive(incomplete_reason)
                    repairing_action = True
                    self.replace_last_assistant_text("Completing response...")
                    self.set_busy(True, f"completing / {executor}")
                    self.save_current_chat()
                    continue
                if action is TurnAction.RECOVER_EXECUTOR:
                    executor_recovery_attempted = True
                    if self.messages and self.messages[-1].get("role") == "assistant":
                        self.messages.pop()
                    reason = incomplete_reason or "unusable continuation"
                    record_executor_recovery(route, executor, recovery_executor, reason)
                    self._route_config = runtime_config_for_model(self.config, recovery_executor)
                    runtime = self._route_config
                    completion_repair_attempted = False
                    completion_feedback = executor_recovery_directive(reason)
                    repairing_action = True
                    self.replace_last_assistant_text("")
                    self.set_busy(True, f"recovering / {recovery_executor}")
                    self.chat["last_route"] = route
                    self.save_current_chat()
                    continue
                if action is TurnAction.STOP_INCOMPLETE:
                    self.append_system(
                        "Response remained structurally incomplete after one retry.\n" + incomplete_reason
                    )
                    self.save_current_chat()
                    return
                if action is TurnAction.CHECK_COMPLETION:
                    self.set_busy(True, "verifying action result")
                    try:
                        completion = assess_action_completion(
                            self.provider,
                            self.config,
                            route,
                            self.messages,
                            assistant_text,
                            self.cancel_event,
                        )
                    except Exception as exc:
                        completion = {"error": str(exc)}
                    if completion:
                        route["action_completion"] = completion
                    enforce_completion = bool(
                        completion
                        and not completion.get("error")
                        and not completion.get("complete")
                        and float(completion.get("confidence") or 0) >= 0.65
                    )
                    outcome = completion_arbiter_outcome(state, enforce_completion)
                    if outcome is CompletionOutcome.REPAIR:
                        action_completion_repairs += 1
                        if self.messages and self.messages[-1].get("role") == "assistant":
                            self.messages.pop()
                        action_feedback = action_completion_directive(completion)
                        repairing_action = True
                        self.replace_last_assistant_text("")
                        self.set_busy(True, "continuing requested action")
                        self.save_current_chat()
                        continue
                    if outcome is CompletionOutcome.STOP:
                        reason = str(completion.get("reason") or "completion could not be verified")
                        self.replace_last_assistant_text("The requested action did not complete.")
                        self.append_error(f"Agent stopped after two completion corrections.\n{reason}")
                        self.save_current_chat()
                        return
                    action = next_action(state, facts, route_facts, finalizing, completion_checked=True)
                if action is TurnAction.SYNTHESIZE_RETRY:
                    if call:
                        self.messages.append(
                            denied_tool_history_message(call, "because the task action budget is exhausted")
                        )
                    else:
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Tool actions are unavailable for this task now. Return a concise final "
                                    "answer using the action results already present."
                                ),
                            }
                        )
                    repairing_action = True
                    self.replace_last_assistant_text("Concluding from collected evidence...")
                    self.set_busy(True, f"synthesizing / {executor}")
                    self.save_current_chat()
                    continue
                if action is TurnAction.FINALIZE_FAIL:
                    self.append_system(
                        "Task action budget reached; the executor did not return a usable final synthesis. "
                        "All action results remain in this chat."
                    )
                    self.save_current_chat()
                    return
                if action is TurnAction.FINALIZE_DELIVER:
                    return
                if action is TurnAction.REPAIR_PARSE:
                    action_repair_attempted = True
                    repairing_action = True
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Action request rejected by the runtime: "
                                f"{parse_error}. Return exactly one corrected tool request and no other text."
                            ),
                        }
                    )
                    self.set_busy(True, "correcting action request")
                    self.save_current_chat()
                    continue
                if action is TurnAction.BLOCK_PARSE:
                    route["action_parse_error"] = parse_error
                    self.replace_last_assistant_text(
                        "I could not format that action correctly, so nothing was run. Rephrasing the request may help."
                    )
                    self.append_system(
                        "The requested action stayed malformed after a correction attempt; no action was run. "
                        "Details are available with /route."
                    )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "No action was run because the action request stayed malformed. Do not describe "
                                "results of actions that did not run."
                            ),
                        }
                    )
                    observe_route_outcome(
                        self.config,
                        route,
                        -1.0,
                        weight=1.0,
                        source="tool-protocol",
                    )
                    self.save_current_chat()
                    return
                if action is TurnAction.REVIEW:
                    reviewer = str(route.get("reviewer") or "")
                    self._route_review_rounds += 1
                    state.review_rounds = self._route_review_rounds
                    review_round = self._route_review_rounds
                    self.set_busy(True, f"reviewing / {reviewer}")
                    try:
                        review = orchestrator_review(
                            self.provider,
                            route,
                            self.messages,
                            assistant_text,
                            self.config,
                            self.cancel_event,
                        )
                    except Exception as exc:
                        review = {"verdict": "error", "feedback": "", "error": str(exc)}
                    review["round"] = review_round
                    route["review"] = review
                    if review.get("verdict") in {"pass", "revise"}:
                        observe_route_outcome(
                            self.config,
                            route,
                            1.0 if review["verdict"] == "pass" else -1.0,
                            weight=0.75,
                            source="independent-review",
                        )
                    self.chat["last_route"] = route
                    if self.cancel_event.is_set():
                        self.append_system("Quality review interrupted; retained the completed response.")
                        self.save_current_chat()
                        return
                    review_result = review_outcome(
                        state,
                        str(review.get("verdict") or ""),
                        bool(review.get("feedback")),
                    )
                    if review_result is ReviewOutcome.REVISE:
                        if self.messages and self.messages[-1].get("role") == "assistant":
                            self.messages.pop()
                        revision_feedback = str(review["feedback"])
                        repairing_action = True
                        self.append_system("Independent review requested corrections; revising.")
                        self.set_busy(True, f"revising / {executor}")
                        self.save_current_chat()
                        continue
                    if review_result is ReviewOutcome.UNRESOLVED:
                        review["unresolved"] = True
                        self.append_system(
                            "Independent review still requests changes after one revision; kept the "
                            "revised answer. Details are available with /route."
                        )
                        self.save_current_chat()
                        return
                    self.save_current_chat()
                    return
                if action is TurnAction.DELIVER:
                    return
                if not agent_enabled:
                    self.append_system(
                        "Agent mode is off. Enable it with /agent on before approving model-requested actions."
                    )
                    return
                outcome = self.handle_tool_request(call)
                if outcome == "continue":
                    self.set_busy(True, "continuing")
                    continue
                return
        except Exception as exc:
            self.record_runtime_failure(exc)
        finally:
            self.interrupt_requested = False
            interrupted = self.cancel_event.is_set()
            self.cancel_event.clear()
            self.set_busy(False)
            self.flush_queued_prompt(interrupted)

    def handle_tool_request(self, call: dict[str, str]) -> str:
        call = normalize_coordinator_tool_call(call)
        if is_internal_coordinator_call(call):
            if coordinator_delegation_limit(self.config, self._active_route) == 0:
                self.messages.append(
                    denied_tool_history_message(call, "because this route requires a direct conversational answer")
                )
                self.save_current_chat()
                return "continue"
            self.run_tool_call(call, approved_by="coordinator")
            return "continue"
        mode = str(self.config.get("permission_mode") or "ask")
        if mode == "deny":
            self.messages.append(denied_tool_history_message(call, "by permissions policy"))
            display = tool_denied_display(call, "permissions policy", "BLOCKED")
            append_action = getattr(self, "append_action", None)
            if callable(append_action):
                append_action(display)
            else:
                self.append_system(display)
            return "stop"
        if mode == "read-auto" and is_auto_approvable_tool_call(
            call,
            self.cwd,
            project_scope_for_chat(self.chat, self.cwd),
        ):
            self.run_tool_call(call, approved_by="read-auto")
            return "continue"

        self.pending_tool = call
        approval_diff = tool_approval_diff(call, project_scope_for_chat(self.chat, self.cwd))
        if approval_diff:
            self.append_diff(approval_diff)
        self.save_current_chat()
        self.app.invalidate()
        return "pending"

    def maybe_run_read_batch(
        self,
        native_calls: list[dict[str, Any]],
        assistant_text: str,
        *,
        finalizing: bool = False,
    ) -> bool:
        """Execute a response's calls together when they are all auto-approvable reads.

        Returns True when a batch ran, so the caller continues to the next generation instead
        of the single-action path. Each call still passes through run_tool_call, so the loop
        guard, action budget, and read-auto scope checks apply exactly as for one call. Gated to
        read-auto while agent mode is active and the turn still accepts actions. In ask/deny,
        agent-off, or final-synthesis states this returns False and strict handling applies.
        """
        if (
            not bool(self.config.get("agent"))
            or finalizing
            or str(self.config.get("permission_mode") or "ask") != "read-auto"
        ):
            return False
        project_root = project_scope_for_chat(self.chat, self.cwd)
        batch = read_only_batch(native_calls, self.cwd, project_root)
        if not batch:
            return False
        visible = strip_tool_markup(assistant_text)
        self.replace_last_assistant_text(visible or f"Reading {len(batch)} project locations...")
        self.append_assistant_end()
        self.messages.append({"role": "assistant", "content": assistant_text, "tool_calls": native_calls})
        self.save_current_chat()
        for call in batch:
            if self.cancel_event.is_set():
                break
            self.run_tool_call(call, approved_by="read-auto")
        return True

    def run_tool_call(self, call: dict[str, str], approved_by: str) -> None:
        call = normalize_coordinator_tool_call(call)
        repeat_refusal = self._loop_guard.refusal(call)
        if repeat_refusal:
            self.messages.append(denied_tool_history_message(call, repeat_refusal))
            display = tool_denied_display(call, "identical action already ran; result unchanged", "NOT RUN")
            append_action = getattr(self, "append_action", None)
            if callable(append_action):
                append_action(display)
            else:
                self.append_system(display)
            self.save_current_chat()
            return
        if not self.reserve_agent_action():
            self.messages.append(denied_tool_history_message(call, "because the task action budget is exhausted"))
            display = tool_denied_display(
                call,
                f"task action budget reached ({self._agent_steps_used}/{agent_action_limit(self.config)})",
                "NOT RUN",
            )
            append_action = getattr(self, "append_action", None)
            if callable(append_action):
                append_action(display)
            else:
                self.append_system(display)
            self.save_current_chat()
            return
        step_label = f"{self._agent_steps_used}/{agent_action_limit(self.config)}"
        begin_action = getattr(self, "begin_tool_action", None)
        finish_action = getattr(self, "finish_tool_action", None)
        if callable(begin_action):
            begin_action(call, step_label)
        else:
            self.set_busy(True, tool_activity_label(call, step_label))
        started = time.monotonic()
        runtime = self._route_config or context_runtime_config(self.config, self.chat)[0]
        result_limit = tool_result_char_budget(self.messages, self.chat, runtime)
        try:
            try:
                project_root = project_scope_for_chat(self.chat, self.cwd)
                if call.get("name") == "consult_specialist":
                    specialty = coordinator_specialty(call).replace("_", " ")
                    decision = None
                    try:
                        if len((self._active_route or {}).get("delegations") or []) < coordinator_delegation_limit(
                            self.config, self._active_route
                        ):
                            decision = select_coordinator_specialist(
                                self.provider, self.config, call, self._active_route
                            )
                            self.set_busy(True, f"specialist / {decision['specialist']} / action {step_label}")
                    except Exception:
                        self.set_busy(True, f"coordinator / {specialty}")
                    code, output, _ = execute_coordinator_delegation(
                        self.provider,
                        self.config,
                        project_root,
                        call,
                        self._active_route,
                        self.messages,
                        self.cancel_event,
                        decision,
                    )
                    if self._active_route is not None:
                        self.chat["last_route"] = self._active_route
                else:
                    code, output = execute_tool_call(
                        call,
                        self.cwd,
                        project_root,
                        self.cancel_event,
                        enforce_project_scope=approved_by == "read-auto",
                        output_limit=result_limit,
                    )
                    remember_indexed_project(self.chat, self.cwd, call, code, project_root)
            except Exception as exc:
                code, output = 1, f"action failed: {exc}"
            result = bounded_tool_output(output, result_limit)
            result += self._loop_guard.record(call, result)
            result = fit_tool_result_for_context(self.messages, self.chat, runtime, call, code, result, self.cwd)
            elapsed = time.monotonic() - started
            if call.get("name") == "consult_specialist":
                # Delegation is internal evidence. Live model/phase feedback belongs in
                # the activity line; persistent details remain available through /route.
                pass
            else:
                display = tool_result_display(call, code, result, approved_by, elapsed)
                append_action = getattr(self, "append_action", None)
                if callable(append_action):
                    append_action(display)
                else:
                    self.append_system(display)
            self.messages.append(tool_history_message(call, code, result))
            self.save_current_chat()
        finally:
            if callable(finish_action):
                finish_action()

    def approve_pending_tool(self) -> None:
        if not self.pending_tool:
            self.append_system("No pending action.")
            return
        call = self.pending_tool
        self.pending_tool = None
        self.app.layout.focus(self.input)
        self.app.invalidate()
        self.set_busy(True, "continuing")

        def worker() -> None:
            try:
                self.run_tool_call(call, approved_by="approved")
                self.set_busy(True, "continuing")
                self.generate_worker()
            except Exception as exc:
                self.append_error(str(exc))
                self.set_busy(False)
                self.flush_queued_prompt(False)

        self.start_worker(worker, "dairack-action")

    def deny_pending_tool(self) -> None:
        if not self.pending_tool:
            self.append_system("No pending action.")
            return
        call = self.pending_tool
        self.pending_tool = None
        self.messages.append(denied_tool_history_message(call, "by user"))
        display = tool_denied_display(call, "denied by user")
        append_action = getattr(self, "append_action", None)
        if callable(append_action):
            append_action(display)
        else:
            self.append_system(display)
        self.save_current_chat()
        self.app.layout.focus(self.input)
        self.app.invalidate()
        self.flush_queued_prompt(False)

    def handle_command(self, line: str) -> None:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self.append_system(f"command parse error: {exc}")
            return
        command = parts[0][1:] if parts else ""
        args = parts[1:]

        if command in {"exit", "quit", "q"}:
            self.app.exit()
        elif command in {"help", "h"}:
            self.append_reference(help_text(args))
        elif command in {"models", "library"}:
            self.show_models()
        elif command == "profiles":
            self.append_system(format_model_profiles())
        elif command == "profile":
            self.command_profile(args)
        elif command == "model":
            self.command_model(args)
        elif command in {"coordinator", "orchestrator"}:
            try:
                detail = configure_orchestrator(self.config, args)
            except ValueError as exc:
                self.append_error(str(exc))
            else:
                self._active_route = None
                self._route_config = None
                save_config(self.config)
                self.append_system(detail)
                self.save_current_chat()
                self.app.invalidate()
        elif command == "route":
            if args and args[0].lower() == "feedback":
                route = self._active_route or (
                    self.chat.get("last_route") if isinstance(self.chat.get("last_route"), dict) else {}
                )
                self.append_system(record_route_feedback(self.config, route, args[1] if len(args) > 1 else ""))
                self.save_current_chat()
            elif args and args[0].lower() in {"history", "log", "all"}:
                self.append_system(format_route_history(self.chat))
            else:
                route = self._active_route or (
                    self.chat.get("last_route") if isinstance(self.chat.get("last_route"), dict) else {}
                )
                self.append_system(format_route_report(route))
        elif command == "compute":
            self.append_system(
                "COMPUTE\n"
                f"  {self.config.get('compute_mode', 'local')} / {self.config.get('compute_name', 'Local Ollama')}\n"
                f"  {self.config.get('ollama_host')}\n\n"
                "The legacy interface reports compute status only. Run `dairack connect --help` to change it."
            )
        elif command == "hardware":
            self.append_reference(format_hardware_status(self.config, PATHS))
        elif command == "image":
            if not args:
                self.append_system("usage: /image <path>")
            elif len(args) > 1:
                self.append_system("usage: /image <path>\nQuote paths that contain spaces.")
            else:
                try:
                    path = resolve_image_path(self.cwd, args[0])
                except ValueError as exc:
                    self.append_error(str(exc))
                else:
                    if path in self.pending_images:
                        self.append_system(f"already staged: {path.name}")
                    elif len(self.pending_images) >= MAX_ATTACHED_IMAGES:
                        self.append_system(f"at most {MAX_ATTACHED_IMAGES} images can be attached")
                    else:
                        self.pending_images.append(path)
                        self.append_system(f"image staged: {path.name}\n{path}")
        elif command == "images":
            if not self.pending_images:
                self.append_system("No images are staged for the next prompt.")
            else:
                self.append_system(
                    "STAGED VISUAL INPUT\n"
                    + "\n".join(f"{index}. {path}" for index, path in enumerate(self.pending_images, 1))
                )
        elif command == "detach":
            if not self.pending_images:
                self.append_system("No images are staged for the next prompt.")
            elif not args or args[0].lower() in {"all", "*"}:
                self.pending_images = []
                self.append_system("staged images detached")
            else:
                try:
                    index = int(args[0]) - 1
                    if index < 0:
                        raise IndexError
                    path = self.pending_images.pop(index)
                except (ValueError, IndexError):
                    self.append_system("usage: /detach [image-number|all]")
                else:
                    self.append_system(f"detached: {path.name}")
        elif command == "pull":
            if not args:
                self.append_system("usage: /pull <model>")
            else:
                self.start_shell_job(f"pulling {args[0]}", ["ollama", "pull", args[0]])
        elif command == "ctx":
            self.command_ctx(args)
        elif command == "think":
            self.command_toggle("think", args)
        elif command == "agent":
            self.command_agent(args)
            self.messages[0]["content"] = system_prompt(self.cwd, bool(self.config.get("agent")), self.config)
            self.save_current_chat()
        elif command in {"permissions", "permission", "perm"}:
            self.command_permissions(args)
        elif command in {"allow", "approve"}:
            self.approve_pending_tool()
        elif command == "deny":
            self.deny_pending_tool()
        elif command == "chats":
            self.append_system(format_chat_list())
        elif command == "resume":
            if self.busy:
                self.append_system("Still processing. Press Esc before resuming another chat.")
            elif self.pending_tool:
                self.append_system("Approve or reject the pending action before resuming another chat.")
            else:
                try:
                    self.load_chat(args[0] if args else "latest")
                except ValueError as exc:
                    self.append_error(str(exc))
        elif command == "new":
            if self.busy:
                self.append_system("Still processing. Press Esc before starting a new chat.")
            else:
                self.start_new_chat(" ".join(args))
        elif command == "save":
            self.save_current_chat(" ".join(args) if args else None, announce=True)
        elif command == "context":
            self.append_system(context_report(self.messages, self.chat.get("summary", ""), self.config, self.chat))
        elif command == "compact":
            self.command_compact(args)
        elif command == "autocompact":
            self.command_autocompact(args)
        elif command == "reset":
            del self.messages[1:]
            self.chat["summary"] = ""
            self.chat["summary_upto"] = 1
            self.chat["summary_format"] = GROUNDED_MEMORY_FORMAT
            self.clear_transcript()
            self.append_system("history cleared")
            self.save_current_chat()
        elif command == "pwd":
            self.append_system(str(self.cwd))
        elif command == "cd":
            self.command_cd(args)
        elif command == "index":
            self.command_index(args)
        elif command == "find":
            self.command_find(args)
        elif command == "symbols":
            self.command_symbols(args)
        elif command == "deps":
            self.command_deps(args)
        elif command == "repo":
            self.command_repo()
        elif command == "tests":
            self.command_tests()
        elif command == "test":
            self.command_test(args)
        elif command == "read":
            self.command_open(args)
        elif command == "ls":
            self.command_ls(args)
        elif command == "diff":
            self.command_diff()
        elif command == "checkpoints":
            self.append_system(list_checkpoints())
        elif command == "undo":
            self.command_undo(args)
        elif command == "search":
            self.command_search(args)
        elif command == "open":
            self.command_open(args)
        elif command == "web":
            self.command_web(args)
        elif command == "url":
            self.command_url(args)
        elif command == "run":
            if not args:
                self.append_system("usage: /run <command>")
            else:
                self.start_shell_text_job("running command", line.split(" ", 1)[1])
        elif command == "copy":
            self.copy_transcript()
        elif command == "config":
            self.append_system(json.dumps(self.config, indent=2, sort_keys=True))
        else:
            append_error = getattr(self, "append_error", None)
            if callable(append_error):
                append_error(unknown_command_display(command))
            else:
                self.append_system(unknown_command_display(command))

    def show_models(self) -> None:
        try:
            models = self.provider.list_models()
        except Exception as exc:
            self.append_error(f"could not list models: {exc}")
            return
        if not models:
            self.append_system("No models installed. Use /pull <model>.")
            return
        mode = str(self.config.get("model_mode") or "direct")
        policy = str(self.config.get("orchestrator_policy") or "adaptive")
        lines = [f"{'*' if mode == 'orchestrator' else ' '} COORDINATOR / {policy}"]
        current = str(self.config.get("model") or "")
        for i, model in enumerate(models, 1):
            marker = "*" if mode != "orchestrator" and model.name == current else " "
            lines.append(f"{i}. {marker} {model.label()}")
        lines.append("")
        lines.append("Switch with /model <name> or /model <number>.")
        self.append_system("\n".join(lines))

    def open_model_picker(self) -> None:
        if self.busy:
            self.append_system("Still processing. Press Esc before switching models.")
            return
        if self.pending_tool:
            self.append_system("Approve or reject the pending action before switching models.")
            return
        try:
            models = [ModelInfo(name=ORCHESTRATOR_MODEL_ID)] + self.provider.list_models()
        except Exception as exc:
            self.append_error(f"could not list models: {exc}")
            return
        if not models:
            self.append_system("No models installed. Use /pull <model>.")
            return
        current = (
            ORCHESTRATOR_MODEL_ID
            if str(self.config.get("model_mode") or "direct") == "orchestrator"
            else str(self.config.get("model") or "")
        )
        self.model_picker_items = models
        self.model_picker_index = next((i for i, model in enumerate(models) if model.name == current), 0)
        self.model_picker_active = True
        self.input.text = ""
        self.app.invalidate()

    def close_model_picker(self) -> None:
        self.model_picker_active = False
        self.app.invalidate()

    def move_model_picker(self, delta: int) -> None:
        if not self.model_picker_items:
            return
        self.model_picker_index = (self.model_picker_index + delta) % len(self.model_picker_items)
        self.app.invalidate()

    def select_model_picker(self) -> None:
        if not self.model_picker_items:
            self.close_model_picker()
            return
        choice = self.model_picker_items[self.model_picker_index].name
        self.close_model_picker()
        self.apply_model_choice(choice)

    def apply_model_choice(self, choice: str) -> None:
        if choice in {ORCHESTRATOR_MODEL_ID, LEGACY_ORCHESTRATOR_MODEL_ID} or choice.lower() in {
            "coordinator",
            "orchestrator",
            "auto",
        }:
            self.config["model_mode"] = "orchestrator"
            self._active_route = None
            self._route_config = None
            save_config(self.config)
            self.append_system("coordinator selected\n" + orchestrator_status(self.config))
            self.save_current_chat()
            self.app.invalidate()
            return
        self.config["model_mode"] = "direct"
        self._active_route = None
        self._route_config = None
        profile_note = apply_model_profile(self.config, choice)
        save_config(self.config)
        self.append_system(f"model set to {choice}\n{profile_note}")
        self.save_current_chat()
        self.app.invalidate()

    def command_model(self, args: list[str]) -> None:
        if not args:
            self.open_model_picker()
            return
        choice = args[0]
        if choice.isdigit():
            try:
                models = self.provider.list_models()
                choice = models[int(choice) - 1].name
            except Exception:
                self.append_system("invalid model number")
                return
        self.apply_model_choice(choice)

    def command_profile(self, args: list[str]) -> None:
        model = str(self.config.get("model") or "")
        if not model:
            self.append_system("No model selected.")
            return
        if not args:
            self.append_system(format_current_profile(self.config))
            return
        action = args[0].lower()
        if action == "reset":
            removed = reset_profile_override(self.config, model)
            note = apply_model_profile(self.config, model)
            save_config(self.config)
            self.append_system(("profile overrides reset\n" if removed else "no overrides to reset\n") + note)
            self.save_current_chat()
            self.app.invalidate()
            return
        if action == "set" and len(args) >= 3:
            try:
                detail = set_profile_override(self.config, model, args[1], args[2])
                persist_profile_override(self.config, model)
                note = apply_model_profile(self.config, model)
            except ValueError as exc:
                self.append_system(f"profile error: {exc}")
                return
            save_config(self.config)
            self.append_system(f"{detail}\n{note}")
            self.save_current_chat()
            self.app.invalidate()
            return
        self.append_system(
            "usage:\n"
            "/profile\n"
            "/profile set ctx N\n"
            "/profile set batch N\n"
            "/profile set threads N\n"
            "/profile set gpu N\n"
            "/profile set think on|off\n"
            "/profile reset"
        )

    def command_ctx(self, args: list[str]) -> None:
        runtime, executor = context_runtime_config(self.config, self.chat)
        if not args:
            self.append_system(
                f"num_ctx: {runtime.get('num_ctx')}" + (f"\nactive executor: {executor}" if executor else "")
            )
            return
        try:
            value = int(args[0])
        except ValueError:
            self.append_system("usage: /ctx <tokens>")
            return
        if not 512 <= value <= 262144:
            self.append_system("context tokens must be between 512 and 262144")
            return
        self.config["num_ctx"] = value
        model = executor or str(self.config.get("model") or "")
        if model:
            set_profile_override(self.config, model, "ctx", str(value))
            persist_profile_override(self.config, model)
        save_config(self.config)
        self.append_system(f"num_ctx set to {value}" + (f"\nsaved as override for {model}" if model else ""))

    def command_toggle(self, key: str, args: list[str]) -> None:
        if not args or args[0] not in {"on", "off"}:
            self.append_system(f"{key}: {'on' if self.config.get(key) else 'off'}")
            return
        self.config[key] = args[0] == "on"
        if key == "think":
            model = str(self.config.get("model") or "")
            if model:
                set_profile_override(self.config, model, "think", args[0])
                persist_profile_override(self.config, model)
        save_config(self.config)
        self.append_system(f"{key}: {args[0]}")

    def command_agent(self, args: list[str]) -> None:
        if not args or args[0] in {"status", "show"}:
            self.append_system(
                f"agent: {'on' if self.config.get('agent') else 'off'}\n"
                f"task action budget: {agent_action_limit(self.config)}"
            )
            return
        if args[0] in {"on", "off"}:
            self.config["agent"] = args[0] == "on"
            with self.lock:
                self.messages[0]["content"] = system_prompt(
                    self.cwd,
                    bool(self.config.get("agent")),
                    self.config,
                )
            save_config(self.config)
            self.append_system(f"agent: {args[0]}")
            return
        if args[0] == "budget" and len(args) == 2:
            try:
                value = int(args[1])
            except ValueError:
                value = 0
            if not 1 <= value <= MAX_AGENT_ACTION_LIMIT:
                self.append_system(f"agent action budget must be between 1 and {MAX_AGENT_ACTION_LIMIT}")
                return
            self.config["max_agent_steps"] = value
            save_config(self.config)
            self.append_system(f"task action budget: {value}")
            return
        self.append_system("usage: /agent on|off|budget <1-64>")

    def command_permissions(self, args: list[str]) -> None:
        modes = {"ask", "read-auto", "deny"}
        if not args:
            self.append_system(
                "permissions: "
                f"{self.config.get('permission_mode', 'ask')}\n\n"
                "Modes:\n"
                "ask       ask before every model-requested action\n"
                "read-auto auto-run workspace reads and safe hardware status; ask for network and other actions\n"
                "deny      block all model-requested actions"
            )
            return
        mode = args[0]
        if mode not in modes:
            self.append_system("usage: /permissions ask|read-auto|deny")
            return
        self.config["permission_mode"] = mode
        save_config(self.config)
        self.append_system(f"permissions: {mode}")
        self.app.invalidate()

    def command_autocompact(self, args: list[str]) -> None:
        if not args or args[0] in {"status", "show"}:
            runtime, executor = context_runtime_config(self.config, self.chat)
            should, reason = should_auto_compact(self.messages, self.chat, runtime)
            executor_line = f"executor: {executor}\n" if executor else ""
            self.append_system(
                "auto compact: "
                f"{'on' if config_bool(runtime, 'auto_compact', True) else 'off'}\n"
                f"{executor_line}"
                f"trigger: {reason}\n"
                f"would compact now: {'yes' if should else 'no'}\n"
                f"keep recent: {config_int(runtime, 'auto_compact_keep_recent', 16, 4, 120)} messages\n"
                f"trigger ratio: {config_float(runtime, 'auto_compact_trigger_ratio', 0.88, 0.50, 1.50):0.2f}\n"
                f"min messages: {config_int(runtime, 'auto_compact_min_messages', 10, 4, 80)}"
            )
            return
        if args[0] in {"on", "off"}:
            self.config["auto_compact"] = args[0] == "on"
            save_config(self.config)
            self.append_system(f"auto compact: {args[0]}")
            return
        if args[0] == "keep" and len(args) > 1:
            try:
                value = int(args[1])
            except ValueError:
                self.append_system("usage: /autocompact keep <messages>")
                return
            self.config["auto_compact_keep_recent"] = max(4, min(120, value))
            save_config(self.config)
            self.append_system(f"auto compact keep recent: {self.config['auto_compact_keep_recent']}")
            return
        if args[0] == "threshold" and len(args) > 1:
            try:
                value = float(args[1])
            except ValueError:
                self.append_system("usage: /autocompact threshold <ratio>")
                return
            self.config["auto_compact_trigger_ratio"] = max(0.50, min(1.50, value))
            save_config(self.config)
            self.append_system(f"auto compact trigger ratio: {self.config['auto_compact_trigger_ratio']:0.2f}")
            return
        self.append_system("usage: /autocompact on|off|status|keep <messages>|threshold <ratio>")

    def command_compact(self, args: list[str]) -> None:
        try:
            keep_recent = int(args[0]) if args else 12
        except ValueError:
            self.append_system("usage: /compact [recent-message-count]")
            return
        keep_recent = max(4, min(80, keep_recent))
        start, end = compact_candidate_range(self.messages, self.chat, keep_recent)
        if end <= start:
            self.append_system("Nothing old enough to compact yet.")
            return
        if self.busy:
            self.append_system("Still processing. Press Esc before compacting.")
            return
        self.interrupt_requested = False
        self.cancel_event.clear()
        self.set_busy(True, "compacting memory")

        def worker() -> None:
            try:
                runtime, executor = context_runtime_config(self.config, self.chat)
                changed, detail = compact_chat_memory(
                    self.provider,
                    executor,
                    self.messages,
                    self.chat,
                    runtime,
                    keep_recent=keep_recent,
                    cancel_event=self.cancel_event,
                )
                self.append_system(("compacted memory updated\n" if changed else "compaction skipped\n") + detail)
                self.save_current_chat()
            except Exception as exc:
                self.append_error(str(exc))
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-compact")

    def command_cd(self, args: list[str]) -> None:
        target = Path(args[0]).expanduser() if args else Path.home()
        if not target.is_absolute():
            target = self.cwd / target
        target = target.resolve()
        if not target.is_dir():
            self.append_system(f"not a directory: {target}")
            return
        self.cwd = target
        update_project_scope_after_cd(self.chat, self.cwd)
        self.messages[0]["content"] = system_prompt(self.cwd, bool(self.config.get("agent")), self.config)
        self.append_system(str(self.cwd))
        self.save_current_chat()

    def start_direct_action(
        self,
        call: dict[str, str],
        operation: Callable[[threading.Event], tuple[int, str]],
        *,
        after_result: Callable[[int, str], None] | None = None,
        record_history: bool = True,
    ) -> None:
        if self.busy:
            self.append_system("Still processing. Press Esc to stop the current operation first.")
            return
        self.interrupt_requested = False
        self.cancel_event.clear()
        self.begin_tool_action(call)
        started = time.monotonic()

        def worker() -> None:
            code = 1
            output = "Action ended before returning a result."
            bookkeeping_error = ""
            try:
                code, output = operation(self.cancel_event)
                if after_result is not None:
                    try:
                        after_result(code, output)
                    except Exception as exc:
                        bookkeeping_error = f"could not update local action state: {exc}"
            except Exception as exc:
                code, output = 1, f"action failed: {exc}"
            finally:
                runtime = getattr(self, "_route_config", None) or context_runtime_config(self.config, self.chat)[0]
                result = bounded_tool_output(
                    output,
                    tool_result_char_budget(self.messages, self.chat, runtime),
                )
                result = fit_tool_result_for_context(self.messages, self.chat, runtime, call, code, result, self.cwd)
                try:
                    if record_history:
                        self.messages.append(tool_history_message(call, code, result))
                    self.append_action(tool_result_display(call, code, result, "user", time.monotonic() - started))
                    if bookkeeping_error:
                        self.append_system(bookkeeping_error)
                    self.save_current_chat()
                finally:
                    self.finish_tool_action()
                    self.interrupt_requested = False
                    self.cancel_event.clear()
                    self.set_busy(False)

        name = str(call.get("name") or "action").replace("_", "-")
        self.start_worker(worker, f"dairack-{name}")

    def command_index(self, args: list[str]) -> None:
        target = resolve_user_path(self.cwd, args[0]) if args else self.cwd
        if not target.exists() or not target.is_dir():
            self.append_system(f"not a directory: {target}")
            return
        if self.busy:
            self.append_system("Still processing. Press Esc before indexing.")
            return
        call = {"name": "index_project", "path": str(target)}
        self.start_direct_action(
            call,
            lambda cancel: index_project_with_vectors(self.provider, target, cancel),
            after_result=lambda code, _output: remember_indexed_project(self.chat, self.cwd, call, code),
        )

    def command_find(self, args: list[str]) -> None:
        if not args:
            self.append_system("usage: /find <query>")
            return
        query = " ".join(args)
        scope = project_scope_for_chat(self.chat, self.cwd)
        code, output = search_project_index(scope, query, limit=config_int(self.config, "retrieval_results", 5, 1, 12))
        self.append_system(f"/find {query}\n{truncate(output)}\n[exit {code}]")
        self.save_current_chat()

    def command_symbols(self, args: list[str]) -> None:
        query = " ".join(args)
        code, output = search_symbols(project_scope_for_chat(self.chat, self.cwd), query, limit=80)
        self.append_system(f"/symbols {query}\n{truncate(output)}\n[exit {code}]")
        self.save_current_chat()

    def command_deps(self, args: list[str]) -> None:
        query = " ".join(args)
        code, output = search_imports(project_scope_for_chat(self.chat, self.cwd), query, limit=100)
        self.append_system(f"/deps {query}\n{truncate(output)}\n[exit {code}]")
        self.save_current_chat()

    def command_repo(self) -> None:
        code, output = repo_profile_text(project_scope_for_chat(self.chat, self.cwd))
        self.append_system(f"/repo\n{truncate(output)}\n[exit {code}]")
        self.save_current_chat()

    def command_tests(self) -> None:
        code, output = format_test_commands(project_scope_for_chat(self.chat, self.cwd))
        self.append_system(f"/tests\n{output}\n[exit {code}]")
        self.save_current_chat()

    def command_test(self, args: list[str]) -> None:
        scope = project_scope_for_chat(self.chat, self.cwd)
        code, cmd, message = resolve_test_command(scope, args)
        if code != 0:
            self.append_system(message)
            return
        self.append_system(f"test selected\n$ {cmd}\n{message}")
        self.start_shell_text_job("running tests", cmd, cwd=scope)

    def command_ls(self, args: list[str]) -> None:
        code, output = list_directory(self.cwd, args[0] if args else ".")
        self.append_system(f"{output}\n[exit {code}]")
        self.save_current_chat()

    def command_diff(self) -> None:
        code, output = git_diff(self.cwd)
        self.append_diff(truncate(output, 50000) if output.startswith("diff --git") else truncate(output, 50000))
        self.append_system(f"git diff\n[exit {code}]")
        self.save_current_chat()

    def command_undo(self, args: list[str]) -> None:
        ref = args[0] if args else "latest"
        try:
            code, output = undo_checkpoint(ref)
        except ValueError as exc:
            self.append_error(str(exc))
            return
        self.append_system(f"/undo {ref}\n{output}\n[exit {code}]")
        self.save_current_chat()

    def command_search(self, args: list[str]) -> None:
        if not args:
            self.append_system("usage: /search <pattern>")
            return
        pattern = " ".join(args)
        call = {"name": "search_project", "query": pattern}
        self.start_direct_action(call, lambda cancel: search_files(self.cwd, pattern, cancel))

    def command_web(self, args: list[str]) -> None:
        if not args:
            self.append_system("usage: /web <query>")
            return
        query = " ".join(args)
        call = {"name": "web_search", "query": query}
        self.start_direct_action(
            call,
            lambda cancel: internet_search(query, cancel_event=cancel),
        )

    def command_url(self, args: list[str]) -> None:
        if not args:
            self.append_system("usage: /url <http-url>")
            return
        url = args[0]
        call = {"name": "web_open", "url": url}
        self.start_direct_action(
            call,
            lambda cancel: web_open_url(url, cancel_event=cancel),
        )

    def command_open(self, args: list[str]) -> None:
        if not args:
            self.append_system("usage: /open <file> [line]")
            return
        line = None
        if len(args) > 1:
            try:
                line = int(args[1])
            except ValueError:
                self.append_system("usage: /open <file> [line]")
                return
        code, output = open_file_preview(self.cwd, args[0], line=line)
        self.append_system(f"{output}\n[exit {code}]")
        self.save_current_chat()

    def start_shell_job(self, label: str, cmd: list[str]) -> None:
        del label
        command = shlex.join(cmd)
        self.start_direct_action(
            {"name": "shell", "cmd": command},
            lambda cancel: run_argv(cmd, self.cwd, cancel_event=cancel),
            record_history=False,
        )

    def start_shell_text_job(self, label: str, cmd: str, cwd: Path | None = None) -> None:
        del label
        scope = cwd or self.cwd
        self.start_direct_action(
            {"name": "shell", "cmd": cmd},
            lambda cancel: run_shell(cmd, scope, cancel_event=cancel),
        )


def handle_command(
    line: str,
    *,
    config: dict[str, Any],
    provider: OllamaProvider,
    chat: dict[str, Any],
    messages: list[dict[str, str]],
    cwd: Path,
) -> tuple[bool, Path]:
    parts = shlex.split(line)
    command = parts[0][1:] if parts else ""
    args = parts[1:]

    if command in {"exit", "quit", "q"}:
        raise EOFError
    if command in {"help", "h"}:
        print(help_text(args))
    elif command in {"models", "library"}:
        for model in provider.list_models():
            print(f"- {model.label()}")
    elif command == "profiles":
        print(format_model_profiles())
    elif command == "profile":
        model = str(config.get("model") or "")
        if not model:
            print("No model selected.")
        elif not args:
            print(format_current_profile(config))
        elif args[0] == "reset":
            removed = reset_profile_override(config, model)
            profile_note = apply_model_profile(config, model)
            save_config(config)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print("profile overrides reset" if removed else "no overrides to reset")
            print(profile_note)
        elif args[0] == "set" and len(args) >= 3:
            try:
                detail = set_profile_override(config, model, args[1], args[2])
                persist_profile_override(config, model)
                profile_note = apply_model_profile(config, model)
            except ValueError as exc:
                print(f"profile error: {exc}")
            else:
                save_config(config)
                save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
                print(detail)
                print(profile_note)
        else:
            print("usage: /profile | /profile set ctx|batch|threads|gpu|think VALUE | /profile reset")
    elif command == "model":
        model = args[0] if args else select_model(provider, str(config.get("model") or ""))
        if model.lower() in {
            "coordinator",
            "orchestrator",
            "auto",
            ORCHESTRATOR_MODEL_ID,
            LEGACY_ORCHESTRATOR_MODEL_ID,
        }:
            config["model_mode"] = "orchestrator"
            save_config(config)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(orchestrator_status(config))
            return True, cwd
        if model.isdigit():
            try:
                models = provider.list_models()
                model = models[int(model) - 1].name
            except Exception:
                print("invalid model number")
                return False, cwd
        config["model_mode"] = "direct"
        profile_note = apply_model_profile(config, model)
        save_config(config)
        save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
        print(f"model: {model}")
        print(profile_note)
    elif command in {"coordinator", "orchestrator"}:
        try:
            detail = configure_orchestrator(config, args)
        except ValueError as exc:
            print(exc)
        else:
            save_config(config)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(detail)
    elif command == "route":
        if args and args[0].lower() == "feedback":
            route = chat.get("last_route") if isinstance(chat.get("last_route"), dict) else {}
            print(record_route_feedback(config, route, args[1] if len(args) > 1 else ""))
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
        elif args and args[0].lower() in {"history", "log", "all"}:
            print(format_route_history(chat))
        else:
            route = chat.get("last_route") if isinstance(chat.get("last_route"), dict) else {}
            print(format_route_report(route))
    elif command == "compute":
        print("COMPUTE")
        print(f"  {config.get('compute_mode', 'local')} / {config.get('compute_name', 'Local Ollama')}")
        print(f"  {config.get('ollama_host')}")
        print("Run `dairack connect --help` outside this plain session to change it.")
    elif command == "hardware":
        print(format_hardware_status(config, PATHS))
    elif command == "image":
        pending = chat.setdefault("_pending_images", [])
        if not isinstance(pending, list):
            pending = []
            chat["_pending_images"] = pending
        if not args:
            print("usage: /image <path>")
        elif len(args) > 1:
            print("usage: /image <path> (quote paths containing spaces)")
        else:
            try:
                path = resolve_image_path(cwd, args[0])
            except ValueError as exc:
                print(exc)
            else:
                value = str(path)
                if value in pending:
                    print(f"already staged: {path.name}")
                elif len(pending) >= MAX_ATTACHED_IMAGES:
                    print(f"at most {MAX_ATTACHED_IMAGES} images can be attached")
                else:
                    pending.append(value)
                    print(f"image staged: {path.name}")
    elif command == "images":
        pending = chat.get("_pending_images") if isinstance(chat.get("_pending_images"), list) else []
        if not pending:
            print("No images are staged for the next prompt.")
        else:
            print("STAGED VISUAL INPUT")
            for index, path in enumerate(pending, 1):
                print(f"{index}. {path}")
    elif command == "detach":
        pending = chat.get("_pending_images") if isinstance(chat.get("_pending_images"), list) else []
        if not pending:
            print("No images are staged for the next prompt.")
        elif not args or args[0].lower() in {"all", "*"}:
            pending.clear()
            print("staged images detached")
        else:
            try:
                index = int(args[0]) - 1
                if index < 0:
                    raise IndexError
                path = pending.pop(index)
            except (ValueError, IndexError):
                print("usage: /detach [image-number|all]")
            else:
                print(f"detached: {Path(path).name}")
    elif command == "pull":
        if not args:
            print("usage: /pull <model>")
        else:
            subprocess.run(["ollama", "pull", args[0]], check=False)
    elif command == "ctx":
        runtime, executor = context_runtime_config(config, chat)
        if not args:
            print(f"num_ctx: {runtime.get('num_ctx')}")
            if executor:
                print(f"active executor: {executor}")
        else:
            try:
                value = int(args[0])
            except ValueError:
                print("usage: /ctx <tokens>")
            else:
                if not 512 <= value <= 262144:
                    print("context tokens must be between 512 and 262144")
                    return True, cwd
                config["num_ctx"] = value
                model = executor or str(config.get("model") or "")
                if model:
                    set_profile_override(config, model, "ctx", str(value))
                    persist_profile_override(config, model)
                save_config(config)
                print(f"num_ctx: {value}")
    elif command == "think":
        if not args or args[0] not in {"on", "off"}:
            print(f"think: {'on' if config.get('think') else 'off'}")
        else:
            config["think"] = args[0] == "on"
            model = str(config.get("model") or "")
            if model:
                set_profile_override(config, model, "think", args[0])
                persist_profile_override(config, model)
            save_config(config)
            print(f"think: {args[0]}")
    elif command == "agent":
        if not args or args[0] in {"status", "show"}:
            print(f"agent: {'on' if config.get('agent') else 'off'}")
            print(f"task action budget: {agent_action_limit(config)}")
        elif args[0] in {"on", "off"}:
            config["agent"] = args[0] == "on"
            messages[0]["content"] = system_prompt(cwd, bool(config.get("agent")), config)
            save_config(config)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(f"agent: {args[0]}")
        elif args[0] == "budget" and len(args) == 2:
            try:
                value = int(args[1])
            except ValueError:
                value = 0
            if not 1 <= value <= MAX_AGENT_ACTION_LIMIT:
                print(f"agent action budget must be between 1 and {MAX_AGENT_ACTION_LIMIT}")
            else:
                config["max_agent_steps"] = value
                save_config(config)
                print(f"task action budget: {value}")
        else:
            print("usage: /agent on|off|budget <1-64>")
    elif command in {"permissions", "permission", "perm"}:
        if not args:
            print(f"permissions: {config.get('permission_mode', 'ask')}")
        elif args[0] in {"ask", "read-auto", "deny"}:
            config["permission_mode"] = args[0]
            save_config(config)
            print(f"permissions: {args[0]}")
        else:
            print("usage: /permissions ask|read-auto|deny")
    elif command == "chats":
        print(format_chat_list())
    elif command == "resume":
        try:
            session = resolve_chat_session(args[0] if args else "latest")
            loaded_chat, loaded_cwd, loaded_messages, _loaded_blocks = chat_runtime_state(session, cwd, config)
        except ValueError as exc:
            print(exc)
        else:
            chat.clear()
            chat.update(loaded_chat)
            cwd = loaded_cwd
            messages[:] = loaded_messages
            if chat.get("model"):
                config["model"] = chat["model"]
            config["model_mode"] = str(chat.get("model_mode") or config.get("model_mode") or "direct")
            config["orchestrator_policy"] = str(
                chat.get("orchestrator_policy") or config.get("orchestrator_policy") or "adaptive"
            )
            config["last_chat"] = chat["id"]
            save_config(config)
            print(f"resumed chat: {chat['title']}")
            print(chat["id"])
    elif command == "new":
        chat.clear()
        chat.update(new_chat_state(cwd, config, " ".join(args)))
        messages[:] = [{"role": "system", "content": system_prompt(cwd, bool(config.get("agent")), config)}]
        save_chat_session(chat, cwd, config, messages, [])
        config["last_chat"] = chat["id"]
        save_config(config)
        print(f"new chat: {chat['title']}")
    elif command == "save":
        if args:
            chat["title"] = clean_chat_title(" ".join(args), "new chat")
        path = save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
        config["last_chat"] = chat["id"]
        save_config(config)
        print(f"saved chat: {chat['title']}")
        print(path)
    elif command == "context":
        print(context_report(messages, chat.get("summary", ""), config, chat))
    elif command == "autocompact":
        if not args or args[0] in {"status", "show"}:
            should, reason = should_auto_compact(messages, chat, config)
            print(f"auto compact: {'on' if config_bool(config, 'auto_compact', True) else 'off'}")
            print(f"trigger: {reason}")
            print(f"would compact now: {'yes' if should else 'no'}")
            print(f"keep recent: {config_int(config, 'auto_compact_keep_recent', 16, 4, 120)} messages")
            print(f"trigger ratio: {config_float(config, 'auto_compact_trigger_ratio', 0.88, 0.50, 1.50):0.2f}")
            print(f"min messages: {config_int(config, 'auto_compact_min_messages', 10, 4, 80)}")
        elif args[0] in {"on", "off"}:
            config["auto_compact"] = args[0] == "on"
            save_config(config)
            print(f"auto compact: {args[0]}")
        elif args[0] == "keep" and len(args) > 1:
            try:
                value = int(args[1])
            except ValueError:
                print("usage: /autocompact keep <messages>")
            else:
                config["auto_compact_keep_recent"] = max(4, min(120, value))
                save_config(config)
                print(f"auto compact keep recent: {config['auto_compact_keep_recent']}")
        elif args[0] == "threshold" and len(args) > 1:
            try:
                value = float(args[1])
            except ValueError:
                print("usage: /autocompact threshold <ratio>")
            else:
                config["auto_compact_trigger_ratio"] = max(0.50, min(1.50, value))
                save_config(config)
                print(f"auto compact trigger ratio: {config['auto_compact_trigger_ratio']:0.2f}")
        else:
            print("usage: /autocompact on|off|status|keep <messages>|threshold <ratio>")
    elif command == "compact":
        try:
            keep_recent = int(args[0]) if args else 12
        except ValueError:
            print("usage: /compact [recent-message-count]")
        else:
            keep_recent = max(4, min(80, keep_recent))
            start, end = compact_candidate_range(messages, chat, keep_recent)
            if end <= start:
                print("Nothing old enough to compact yet.")
            else:
                runtime, executor = context_runtime_config(config, chat)
                changed, detail = compact_chat_memory(
                    provider,
                    executor,
                    messages,
                    chat,
                    runtime,
                    keep_recent=keep_recent,
                )
                save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
                print(("compacted memory updated: " if changed else "compaction skipped: ") + detail)
    elif command == "reset":
        del messages[1:]
        chat["summary"] = ""
        chat["summary_upto"] = 1
        chat["summary_format"] = GROUNDED_MEMORY_FORMAT
        save_chat_session(chat, cwd, config, messages, [])
        print("history cleared")
    elif command == "pwd":
        print(cwd)
    elif command == "cd":
        target = Path(args[0]).expanduser() if args else Path.home()
        if not target.is_absolute():
            target = cwd / target
        target = target.resolve()
        if not target.is_dir():
            print(f"not a directory: {target}")
        else:
            cwd = target
            update_project_scope_after_cd(chat, cwd)
            messages[0]["content"] = system_prompt(cwd, bool(config.get("agent")), config)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(cwd)
    elif command == "index":
        target = resolve_user_path(cwd, args[0]) if args else cwd
        if not target.exists() or not target.is_dir():
            print(f"not a directory: {target}")
        else:
            code, output = index_project_with_vectors(provider, target)
            remember_indexed_project(
                chat,
                cwd,
                {"name": "index_project", "path": str(target)},
                code,
            )
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(output)
            if code != 0:
                print(f"exit code {code}")
    elif command == "find":
        if not args:
            print("usage: /find <query>")
        else:
            scope = project_scope_for_chat(chat, cwd)
            code, output = search_project_index(
                scope, " ".join(args), limit=config_int(config, "retrieval_results", 5, 1, 12)
            )
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "symbols":
        code, output = search_symbols(project_scope_for_chat(chat, cwd), " ".join(args), limit=80)
        print(truncate(output) if output else "(no output)")
        if code != 0:
            print(f"exit code {code}")
    elif command == "deps":
        code, output = search_imports(project_scope_for_chat(chat, cwd), " ".join(args), limit=100)
        print(truncate(output) if output else "(no output)")
        if code != 0:
            print(f"exit code {code}")
    elif command == "repo":
        code, output = repo_profile_text(project_scope_for_chat(chat, cwd))
        print(truncate(output) if output else "(no output)")
        if code != 0:
            print(f"exit code {code}")
    elif command == "tests":
        code, output = format_test_commands(project_scope_for_chat(chat, cwd))
        print(output)
        if code != 0:
            print(f"exit code {code}")
    elif command == "test":
        scope = project_scope_for_chat(chat, cwd)
        code, cmd, message = resolve_test_command(scope, args)
        if code != 0:
            print(message)
        else:
            print(f"$ {cmd}")
            test_code, output = run_shell(cmd, scope, timeout=DEFAULT_TIMEOUT * 5)
            print(truncate(output) if output else "(no output)")
            if test_code != 0:
                print(f"exit code {test_code}")
    elif command == "read":
        if not args:
            print("usage: /read <file> [line]")
        else:
            line_number = None
            if len(args) > 1:
                try:
                    line_number = int(args[1])
                except ValueError:
                    print("usage: /read <file> [line]")
                    return True, cwd
            code, output = open_file_preview(cwd, args[0], line=line_number)
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "ls":
        code, output = list_directory(cwd, args[0] if args else ".")
        print(truncate(output) if output else "(no output)")
        if code != 0:
            print(f"exit code {code}")
    elif command == "diff":
        code, output = git_diff(cwd)
        print(truncate(output, 50000) if output else "(no output)")
        if code != 0:
            print(f"exit code {code}")
    elif command == "checkpoints":
        print(list_checkpoints())
    elif command == "undo":
        try:
            code, output = undo_checkpoint(args[0] if args else "latest")
        except ValueError as exc:
            print(exc)
        else:
            print(output)
            if code != 0:
                print(f"exit code {code}")
    elif command == "search":
        if not args:
            print("usage: /search <pattern>")
        else:
            pattern = " ".join(args)
            code, output = search_files(cwd, pattern)
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "open":
        if not args:
            print("usage: /open <file> [line]")
        else:
            line_number = None
            if len(args) > 1:
                try:
                    line_number = int(args[1])
                except ValueError:
                    print("usage: /open <file> [line]")
                    return True, cwd
            code, output = open_file_preview(cwd, args[0], line=line_number)
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "web":
        if not args:
            print("usage: /web <query>")
        else:
            query = " ".join(args)
            code, output = internet_search(query)
            messages.append({"role": "system", "content": f"Internet search result for {query}:\n{output}"})
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "url":
        if not args:
            print("usage: /url <http-url>")
        else:
            code, output = web_open_url(args[0])
            messages.append({"role": "system", "content": f"Web page text from {args[0]}:\n{output}"})
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            print(truncate(output) if output else "(no output)")
            if code != 0:
                print(f"exit code {code}")
    elif command == "run":
        if not args:
            print("usage: /run <command>")
        else:
            cmd = line.split(" ", 1)[1]
            code, output = run_shell(cmd, cwd)
            print(truncate(output) if output else f"exit code {code}")
            if code != 0:
                print(f"exit code {code}")
    elif command == "copy":
        print("\n".join(f"{m['role']}:\n{m['content']}" for m in messages if m.get("role") != "system"))
    elif command == "config":
        visible = config.copy()
        print(json.dumps(visible, indent=2, sort_keys=True))
    else:
        print(unknown_command_display(command))
    return True, cwd


def chat_turn(
    provider: OllamaProvider,
    model: str,
    messages: list[dict[str, str]],
    config: dict[str, Any],
    cwd: Path,
    chat: dict[str, Any],
) -> None:
    project_root = project_scope_for_chat(chat, cwd)
    route = select_orchestrator_route(provider, config, messages, project_root, previous_route=chat.get("last_route"))
    route["tool_steps"] = 0
    route["tool_limit"] = agent_action_limit(config)
    executor = str(route.get("executor") or model)
    require_vision_support(provider, executor, messages)
    runtime = runtime_config_for_model(config, executor)
    chat["last_route"] = route
    if route.get("mode") == "orchestrator":
        print(
            ansi(
                f"[coordinator/{route.get('policy')} -> {executor} | {route.get('task_kind')} | {route.get('strategy')}]",
                "33;1",
            )
        )
    try:
        changed, detail = auto_compact_if_needed(provider, executor, messages, chat, runtime)
        if changed:
            print(f"[auto compact] {detail}")
    except Exception as exc:
        print(f"[auto compact skipped] {exc}")
    plan = ""
    if route.get("planner"):
        plan = orchestrator_plan(provider, route, messages, project_root, config)
    native_tools = native_tools_for(provider, executor, bool(config.get("agent")), route)
    action_feedback = ""

    def routed_messages(
        feedback: str = "",
        finalizing: bool = False,
        synthesis_retry: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        directives: list[str] = []
        directive = coordinator_executor_directive(route, config)
        if directive:
            directives.append(directive)
        if plan:
            directives.append("Internal advisory execution brief; verify it and do not mention it:\n" + plan)
        if feedback:
            directives.append("Return a complete corrected answer without mentioning review. Corrections:\n" + feedback)
        if action_feedback:
            directives.append(action_feedback)
        if finalizing:
            directives.append(
                agent_synthesis_directive(
                    state.action_steps,
                    state.action_limit,
                    retry=synthesis_retry,
                )
            )
        request_tools = [] if finalizing else native_tools
        selected = request_context_messages(messages, chat, runtime, cwd, provider=provider)
        try:
            return fit_agent_request_context_messages(
                canonicalize_messages(selected, directives), runtime, request_tools
            )
        except RequestContextError:
            reduced = request_context_messages(messages, chat, runtime, cwd, include_retrieval=False)
            fitted = fit_agent_request_context_messages(
                canonicalize_messages(reduced, directives), runtime, request_tools
            )
            route["context_degraded"] = "project retrieval omitted to fit the context window"
            return fitted

    request_messages, _request_tools = routed_messages()
    if not config.get("agent"):
        print(ansi("assistant > ", "35;1"), end="", flush=True)
        response = provider.chat(
            executor,
            request_messages,
            stream=True,
            think=bool(runtime.get("think")),
            num_ctx=int(runtime.get("num_ctx") or 4096),
            extra_options=ollama_options(runtime),
        )
        if route.get("reviewer"):
            route["review"] = {"verdict": "skipped", "feedback": "", "reason": "plain streaming mode"}
        messages.append({"role": "assistant", "content": response})
        return

    state = TurnState(action_limit=agent_action_limit(config))
    revision_feedback = ""
    action_feedback = ""
    loop_guard = ActionLoopGuard()
    while True:
        try:
            changed, detail = auto_compact_if_needed(provider, executor, messages, chat, runtime)
            if changed:
                print(f"[context compacted] {detail}")
        except Exception as exc:
            print(f"[context compaction skipped] {exc}")
        is_final = finalizing(state, loop_guard.force_synthesis)
        if is_final:
            state.synthesis_attempts += 1
            if synthesis_exhausted(state):
                print("Task action budget reached; final synthesis could not be completed.")
                return
        native_calls: list[dict[str, Any]] = []
        request_messages, request_tools = routed_messages(
            revision_feedback,
            is_final,
            state.synthesis_attempts > 1,
        )
        revision_feedback = ""
        request_retry_used = False
        response_allowance = executor_response_allowance(request_messages, request_tools, runtime)
        generation_error: Exception | None = None
        while True:
            try:
                response = provider.chat(
                    executor,
                    request_messages,
                    stream=False,
                    think=bool(runtime.get("think")),
                    num_ctx=int(runtime.get("num_ctx") or 4096),
                    num_predict=response_allowance,
                    keep_alive=executor_keep_alive(runtime),
                    extra_options=ollama_options(runtime),
                    tools=request_tools or None,
                    tool_call_sink=native_calls.append,
                )
                break
            except Exception as exc:
                if request_retry_used or not transient_stream_error(exc):
                    generation_error = exc
                    break
                request_retry_used = True
                native_calls.clear()
        if generation_error is not None:
            replacement = (
                coordinator_recovery_executor(route, executor)
                if not state.executor_recovery_attempted and recoverable_model_protocol_error(generation_error)
                else ""
            )
            if replacement:
                state.executor_recovery_attempted = True
                reason = str(generation_error)
                record_executor_recovery(route, executor, replacement, reason)
                executor = replacement
                runtime = runtime_config_for_model(config, replacement)
                native_tools = native_tools_for(provider, replacement, True, route)
                state.completion_repair_attempted = False
                action_feedback = executor_recovery_directive(reason)
                chat["last_route"] = route
                continue
            raise generation_error
        action_feedback = ""
        call, parse_error = resolve_tool_request(response, native_calls)
        assistant_message: dict[str, Any] = {"role": "assistant", "content": response}
        if native_calls:
            assistant_message["tool_calls"] = native_calls
        incomplete_reason = ""
        if not call and not parse_error:
            incomplete_reason = response_incomplete_reason(
                response,
                dict(getattr(provider, "last_stats", {}) or {}),
            )
        facts = ResponseFacts(
            has_call=bool(call),
            parse_error=parse_error or "",
            incomplete_reason=incomplete_reason,
            response_blank=not response.strip(),
        )
        recovery_executor = (
            coordinator_recovery_executor(route, executor)
            if incomplete_reason and not is_final and not state.executor_recovery_attempted
            else ""
        )
        route_facts = RouteFacts(
            has_reviewer=bool(route.get("reviewer")),
            action_requirement=action_contract_directive(route, retry=True) or "",
            contract_capability=action_completion_required(route, state.action_steps, True),
            has_recovery_executor=bool(recovery_executor),
        )
        action = next_action(state, facts, route_facts, is_final)

        if action is TurnAction.REPAIR_CONTRACT:
            state.contract_repair_attempted = True
            action_feedback = route_facts.action_requirement
            continue
        if action is TurnAction.RETRY_COMPLETION:
            state.completion_repair_attempted = True
            route["completion_retry"] = {"attempted": True, "reason": incomplete_reason, "recovered": False}
            revision_feedback = completion_retry_directive(incomplete_reason)
            continue
        if action is TurnAction.RECOVER_EXECUTOR:
            state.executor_recovery_attempted = True
            reason = incomplete_reason or "unusable continuation"
            record_executor_recovery(route, executor, recovery_executor, reason)
            executor = recovery_executor
            runtime = runtime_config_for_model(config, recovery_executor)
            native_tools = native_tools_for(provider, recovery_executor, True, route)
            state.completion_repair_attempted = False
            action_feedback = executor_recovery_directive(reason)
            chat["last_route"] = route
            continue
        if state.completion_repair_attempted and not incomplete_reason and not call and not parse_error:
            retry_record = route.get("completion_retry")
            if isinstance(retry_record, dict):
                retry_record["recovered"] = True
        if action is TurnAction.STOP_INCOMPLETE:
            messages.append(assistant_message)
            print(f"Response remained structurally incomplete after one retry: {incomplete_reason}")
            return
        if action is TurnAction.CHECK_COMPLETION:
            try:
                completion = assess_action_completion(
                    provider,
                    config,
                    route,
                    [*messages, assistant_message],
                    response,
                )
            except Exception as exc:
                completion = {"error": str(exc)}
            if completion:
                route["action_completion"] = completion
            enforce_completion = bool(
                completion
                and not completion.get("error")
                and not completion.get("complete")
                and float(completion.get("confidence") or 0) >= 0.65
            )
            outcome = completion_arbiter_outcome(state, enforce_completion)
            if outcome is CompletionOutcome.REPAIR:
                state.action_completion_repairs += 1
                action_feedback = action_completion_directive(completion)
                continue
            if outcome is CompletionOutcome.STOP:
                reason = str(completion.get("reason") or "completion could not be verified")
                stopped = f"The requested action did not complete.\n{reason}"
                messages.append({"role": "assistant", "content": stopped})
                print(stopped)
                return
            action = next_action(state, facts, route_facts, is_final, completion_checked=True)

        if action is TurnAction.SYNTHESIZE_RETRY:
            messages.append(assistant_message)
            if call:
                messages.append(denied_tool_history_message(call, "because the task action budget is exhausted"))
            else:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool actions are unavailable for this task now. Return a concise final answer "
                            "using the action results already present."
                        ),
                    }
                )
            continue
        if action is TurnAction.FINALIZE_FAIL:
            messages.append(assistant_message)
            print("Task action budget reached; the executor did not return a usable final synthesis.")
            return
        if action is TurnAction.FINALIZE_DELIVER:
            messages.append(assistant_message)
            print(response)
            return
        if action is TurnAction.REPAIR_PARSE:
            state.parse_repair_attempted = True
            messages.append(assistant_message)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Action request rejected by the runtime: "
                        f"{parse_error}. Return exactly one corrected tool request and no other text."
                    ),
                }
            )
            continue
        if action is TurnAction.BLOCK_PARSE:
            messages.append(assistant_message)
            observe_route_outcome(config, route, -1.0, weight=1.0, source="tool-protocol")
            route["action_parse_error"] = parse_error
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No action was run because the action request stayed malformed. Do not describe "
                        "results of actions that did not run."
                    ),
                }
            )
            print("The requested action stayed malformed after a correction attempt; no action was run.")
            return
        if action is TurnAction.REVIEW:
            state.review_rounds += 1
            review = orchestrator_review(provider, route, messages, response, config)
            review["round"] = state.review_rounds
            route["review"] = review
            if review.get("verdict") in {"pass", "revise"}:
                observe_route_outcome(
                    config,
                    route,
                    1.0 if review["verdict"] == "pass" else -1.0,
                    weight=0.75,
                    source="independent-review",
                )
            outcome = review_outcome(state, str(review.get("verdict") or ""), bool(review.get("feedback")))
            if outcome is ReviewOutcome.REVISE:
                revision_feedback = str(review["feedback"])
                print("[review requested corrections; revising]")
                continue
            if outcome is ReviewOutcome.UNRESOLVED:
                review["unresolved"] = True
                print("[review still requests changes; kept the revised answer]")
            print(response)
            messages.append(assistant_message)
            return
        if action is TurnAction.DELIVER:
            print(response)
            messages.append(assistant_message)
            return

        messages.append(assistant_message)
        call = normalize_coordinator_tool_call(call)
        mode = str(config.get("permission_mode") or "ask")
        internal = is_internal_coordinator_call(call)
        project_root = project_scope_for_chat(chat, cwd)
        if internal and coordinator_delegation_limit(config, route) == 0:
            messages.append(
                denied_tool_history_message(call, "because this route requires a direct conversational answer")
            )
            continue
        auto_approved = internal or (mode == "read-auto" and is_auto_approvable_tool_call(call, cwd, project_root))
        if mode == "deny" and not internal:
            messages.append(denied_tool_history_message(call, "by permissions policy"))
            print(f"blocked by permissions: {tool_summary(call)}")
            return
        if not auto_approved and not approve_tool(call, project_root):
            messages.append(denied_tool_history_message(call, "by user"))
            print("denied")
            return

        repeat_refusal = loop_guard.refusal(call)
        if repeat_refusal:
            messages.append(denied_tool_history_message(call, repeat_refusal))
            print(f"skipped repeat action: {tool_summary(call)}")
            continue

        state.action_steps += 1
        route["tool_steps"] = state.action_steps
        route["tool_limit"] = state.action_limit
        chat["last_route"] = route
        if call.get("name") == "consult_specialist":
            specialty = coordinator_specialty(call).replace("_", " ")
            print(ansi(f"\n[coordinator delegating {specialty}]", "33;1"))
            code, output, record = execute_coordinator_delegation(
                provider,
                config,
                project_root,
                call,
                route,
                messages,
            )
            chat["last_route"] = route
            if record:
                print(ansi(f"[specialist {record.get('specialist')} / {record.get('seconds', 0):.1f}s]", "36;1"))
        else:
            result_limit = tool_result_char_budget(messages, chat, runtime)
            code, output = execute_tool_call(
                call,
                cwd,
                project_root,
                enforce_project_scope=mode == "read-auto" and auto_approved,
                output_limit=result_limit,
            )
            remember_indexed_project(chat, cwd, call, code, project_root)
        if call.get("name") == "consult_specialist":
            result_limit = tool_result_char_budget(messages, chat, runtime)
        result = bounded_tool_output(output, result_limit)
        result += loop_guard.record(call, result)
        result = fit_tool_result_for_context(messages, chat, runtime, call, code, result, cwd)
        print(f"\n{tool_summary(call)}")
        print(result)
        print(f"[exit {code}]")
        messages.append(tool_history_message(call, code, result))


def startup_chat_reference(args: argparse.Namespace, config: dict[str, Any]) -> str:
    if bool(getattr(args, "new_chat", False)):
        return ""
    explicit = str(getattr(args, "chat", None) or getattr(args, "resume", None) or "").strip()
    if explicit:
        return explicit
    if config.get("startup_chat") == "resume-last":
        return str(config.get("last_chat") or "")
    return ""


def interactive(args: argparse.Namespace, config: dict[str, Any]) -> None:
    if args.no_color:
        os.environ["DAIRACK_NO_COLOR"] = "1"

    provider = provider_from_config(config)
    if args.host:
        provider = configured_compute_provider(config, PATHS, host=args.host)
        config["ollama_host"] = args.host

    try:
        version = provider.version()
    except Exception as exc:
        raise SystemExit(
            f"could not reach Ollama at {provider.host}: {exc}\n\n"
            "Start the Ollama application or run `ollama serve`, then retry.\n"
            "For another machine, run `dairack connect URL`; use `dairack serve --tailscale` on the model server.\n"
            "Use `dairack doctor` for a complete environment report."
        ) from exc

    if args.model:
        config["model_mode"] = "direct"
        apply_model_profile(config, args.model)
    elif args.select:
        selected = select_model(provider, str(config.get("model") or ""))
        if selected in {ORCHESTRATOR_MODEL_ID, LEGACY_ORCHESTRATOR_MODEL_ID}:
            config["model_mode"] = "orchestrator"
        else:
            config["model_mode"] = "direct"
            apply_model_profile(config, selected)
    elif not config.get("model"):
        try:
            initialized = initialize_app(PATHS, args.host)
        except Exception:
            initialized = None
        if initialized:
            config.clear()
            config.update(initialized.config)
    save_config(config)

    if not config.get("model") and not textual_enabled(args):
        raise SystemExit("no Ollama models are installed; run `dairack setup` or `dairack models pull <model>`")

    cwd = Path.cwd()
    chat = new_chat_state(cwd, config)
    messages = [{"role": "system", "content": system_prompt(cwd, bool(config.get("agent")), config)}]
    blocks: list[dict[str, str]] = []
    startup_notice = (
        "No compute model is installed. The model library is open; choose a fitted setup profile or any Ollama model."
        if not config.get("model")
        else ""
    )
    resume_ref = startup_chat_reference(args, config)
    if args.new_chat:
        save_chat_session(chat, cwd, config, messages, blocks)
        config["last_chat"] = chat["id"]
        save_config(config)
    elif resume_ref:
        try:
            session = resolve_chat_session(resume_ref)
            chat, cwd, messages, blocks = chat_runtime_state(session, cwd, config)
            if chat.get("model"):
                config["model"] = chat["model"]
            config["model_mode"] = str(chat.get("model_mode") or config.get("model_mode") or "direct")
            config["orchestrator_policy"] = str(
                chat.get("orchestrator_policy") or config.get("orchestrator_policy") or "adaptive"
            )
            config["last_chat"] = chat["id"]
            save_config(config)
            startup_notice = f"resumed chat: {chat['title']}"
        except ValueError as exc:
            startup_notice = f"could not resume chat: {exc}\nstarted a new chat"
            chat["_transient"] = True
    else:
        chat["_transient"] = True
    if textual_enabled(args):
        os.environ.pop("NO_COLOR", None)
        try:
            from .ui.textual_app import run_textual_tui
        except ImportError as exc:
            startup_notice = (startup_notice + "\n" if startup_notice else "") + f"Textual UI unavailable: {exc}"
        else:
            textual_blocks = list(blocks)
            if startup_notice:
                textual_blocks.append({"role": "system", "text": startup_notice})
            run_textual_tui(
                sys.modules[__name__],
                DairackTui,
                provider,
                version,
                config,
                cwd,
                chat=chat,
                messages=messages,
                blocks=textual_blocks,
            )
            return

    fancy = fancy_enabled(args)
    if fancy:
        tui = DairackTui(provider, version, config, cwd, chat=chat, messages=messages, blocks=blocks)
        if startup_notice:
            tui.append_system(startup_notice)
        tui.run()
        return

    session = None

    print_header(config, version, fancy)
    if startup_notice:
        print(startup_notice)
    if not fancy:
        setup_readline()

    while True:
        try:
            line = read_line(session, config, cwd, fancy)
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.startswith("/"):
            try:
                _, cwd = handle_command(
                    line,
                    config=config,
                    provider=provider,
                    chat=chat,
                    messages=messages,
                    cwd=cwd,
                )
                save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
            except EOFError:
                return
            continue

        message: dict[str, Any] = {"role": "user", "content": line}
        pending_images = chat.pop("_pending_images", [])
        if isinstance(pending_images, list) and pending_images:
            message["image_paths"] = [str(path) for path in pending_images[:MAX_ATTACHED_IMAGES]]
        messages.append(message)
        save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
        try:
            chat_turn(provider, str(config["model"]), messages, config, cwd, chat)
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))
        except (urllib.error.URLError, RuntimeError) as exc:
            print(f"error: {exc}")
            messages.append(runtime_failure_message(exc))
            save_chat_session(chat, cwd, config, messages, blocks_from_messages(messages))


def one_shot(args: argparse.Namespace, config: dict[str, Any], prompt: str) -> int:
    provider = provider_from_config(config)
    if args.host:
        provider = configured_compute_provider(config, PATHS, host=args.host)
    if args.model:
        config = config.copy()
        config["model_mode"] = "direct"
        apply_model_profile(config, args.model)

    cwd = Path.cwd()
    messages = [
        {"role": "system", "content": system_prompt(cwd, False, config)},
        {"role": "user", "content": prompt},
    ]
    route = select_orchestrator_route(provider, config, messages, cwd)
    model = str(route.get("executor") or config.get("model") or "")
    if not model:
        eprint("no model selected and no Ollama models installed")
        return 2
    runtime = runtime_config_for_model(config, model)
    request_messages = messages
    if route.get("planner"):
        plan = orchestrator_plan(provider, route, messages, cwd, config)
        if plan:
            request_messages = canonicalize_messages(
                messages,
                ["Internal advisory execution brief; verify it and do not mention it:\n" + plan],
            )
    try:
        request_messages = fit_request_context_messages(request_messages, runtime)
        response = provider.chat(
            model,
            request_messages,
            stream=not args.no_stream,
            think=bool(runtime.get("think")),
            num_ctx=int(runtime.get("num_ctx") or 4096),
            num_predict=args.max_tokens,
            extra_options=ollama_options(runtime),
        )
        if args.no_stream:
            print(response)
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP,
        description="Local terminal assistant backed by Ollama.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""management commands:
  setup         configure Ollama, hardware, models, and operating mode
  models        install, update, remove, inspect, and tune compute models
  connect       inspect or change the model inference endpoint
  serve         expose local Ollama as an authenticated compute service
  coordinator   configure automatic multi-model routing
  update        check, configure, or apply Dairack software releases
  doctor        inspect the installation and required system tools
  hardware      show detected compute and conservative model capacity
  init          refresh hardware and model metadata

Run `dairack COMMAND --help` for command-specific options.
Inside the terminal UI, press Ctrl+P for the native command palette.""",
    )
    parser.add_argument("prompt", nargs="*", help="optional one-shot prompt")
    parser.add_argument("-m", "--model", help="model name, e.g. model:tag")
    parser.add_argument("--host", help="Ollama host, default from config")
    parser.add_argument("--list", action="store_true", help="list installed models")
    parser.add_argument("--select", action="store_true", help="open model selector on startup")
    parser.add_argument("--resume", nargs="?", const="latest", help="resume a saved chat, latest when omitted")
    parser.add_argument("--chat", help="resume a saved chat by id or number")
    parser.add_argument("--new-chat", action="store_true", help="start a fresh saved chat")
    parser.add_argument("--plain", action="store_true", help="disable the styled terminal interface")
    parser.add_argument("--legacy-ui", action="store_true", help="use the earlier prompt-toolkit interface")
    parser.add_argument("--no-color", action="store_true", help="disable Dairack colors")
    parser.add_argument("--no-stream", action="store_true", help="disable streaming in one-shot mode")
    parser.add_argument("--max-tokens", type=int, help="max tokens for one-shot response")
    return parser.parse_args()


def main() -> int:
    PATHS.ensure()
    args = parse_args()
    config = load_config()
    provider = provider_from_config(config)
    if args.host:
        provider = configured_compute_provider(config, PATHS, host=args.host)

    if args.list:
        for model in provider.list_models():
            print(model.label())
        return 0

    if args.prompt:
        return one_shot(args, config, " ".join(args.prompt))

    interactive(args, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
