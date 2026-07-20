"""Canonical tool registry and schema-driven call validation."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

REASON_PROPERTY = {"type": "string", "description": "Why this action is needed now."}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: Mapping[str, Mapping[str, Any]]
    required: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    field_aliases: Mapping[str, str] = field(default_factory=dict)
    body_field: str = ""
    exposed: bool = True
    display_name: str = "Action"
    activity: str = "Running action"
    target_field: str = ""
    target_label: str = "TARGET"
    risk: str = "read"
    interruptible: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {**self.properties, "reason": REASON_PROPERTY},
                    "required": list(self.required),
                    "additionalProperties": False,
                },
            },
        }


class ToolRegistry:
    def __init__(self, specs: tuple[ToolSpec, ...]) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._aliases = {alias: spec.name for spec in specs for alias in spec.aliases}

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    @property
    def known_names(self) -> frozenset[str]:
        return frozenset((*self._specs, *self._aliases))

    def canonical_name(self, value: Any) -> str:
        name = str(value or "").strip().lower()
        return self._aliases.get(name, name)

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._specs.values() if spec.exposed]

    def prompt_catalog(self) -> str:
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self._specs.values() if spec.exposed)

    def presentation(self, value: Any) -> dict[str, Any]:
        name = self.canonical_name(value)
        spec = self._specs.get(name)
        if spec is None:
            return {
                "name": name or "unknown",
                "display_name": "Action",
                "activity": "Running action",
                "target_field": "",
                "target_label": "TARGET",
                "risk": "system",
                "interruptible": False,
            }
        return {
            "name": spec.name,
            "display_name": spec.display_name,
            "activity": spec.activity,
            "target_field": spec.target_field,
            "target_label": spec.target_label,
            "risk": spec.risk,
            "interruptible": spec.interruptible,
        }

    def validate(self, data: Mapping[str, Any], body: str = "") -> tuple[dict[str, str] | None, str]:
        payload, error = self._flatten(data)
        if error:
            return None, error

        raw_name = payload.get("name") or payload.get("tool")
        name = self.canonical_name(raw_name)
        if not name:
            return None, "action request has no tool name"
        spec = self._specs.get(name)
        if spec is None:
            return None, f"unsupported action tool: {raw_name}"

        normalized = {"name": name, "reason": str(payload.get("reason") or "")}
        for alias, target in spec.field_aliases.items():
            if target not in payload and alias in payload:
                payload[target] = payload[alias]
        if body and spec.body_field and not payload.get(spec.body_field):
            payload[spec.body_field] = body

        for field_name, field_schema in spec.properties.items():
            if payload.get(field_name) is None:
                continue
            value, value_error = self._normalize_value(name, field_name, payload[field_name], field_schema)
            if value_error:
                return None, value_error
            normalized[field_name] = value

        for field_name in spec.required:
            if not str(normalized.get(field_name) or "").strip():
                return None, f"{name} action is missing {field_name}"
        return normalized, ""

    @staticmethod
    def _flatten(data: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        payload = dict(data)
        function = payload.pop("function", None)
        if isinstance(function, Mapping):
            payload = {**function, **payload}
        arguments = payload.pop("arguments", None)
        if isinstance(arguments, str):
            try:
                arguments = _loads_tool_json(arguments)
            except (ValueError, RecursionError) as exc:
                return {}, f"action request arguments contain invalid JSON: {getattr(exc, 'msg', str(exc))}"
        if arguments is not None and not isinstance(arguments, Mapping):
            return {}, "action request arguments must be an object"
        if isinstance(arguments, Mapping):
            payload = {**arguments, **payload}
        return payload, ""

    @staticmethod
    def _normalize_value(
        tool_name: str,
        field_name: str,
        value: Any,
        schema: Mapping[str, Any],
    ) -> tuple[str, str]:
        expected = str(schema.get("type") or "string")
        if expected == "integer":
            if isinstance(value, bool):
                return "", f"{tool_name} action field {field_name} must be an integer"
            try:
                number = int(value)
            except (TypeError, ValueError):
                return "", f"{tool_name} action field {field_name} must be an integer"
            minimum = schema.get("minimum")
            if minimum is not None and number < int(minimum):
                return "", f"{tool_name} action field {field_name} must be at least {minimum}"
            rendered = str(number)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            rendered = str(value)
        else:
            return "", f"{tool_name} action field {field_name} must be a string"

        allowed = schema.get("enum")
        if isinstance(allowed, list) and rendered not in {str(item) for item in allowed}:
            return "", f"{tool_name} action field {field_name} must be one of: {', '.join(map(str, allowed))}"
        return rendered, ""


TOOL_REGISTRY = ToolRegistry(
    (
        ToolSpec(
            "shell",
            "Run one shell command in the current working directory after permission review.",
            {"cmd": {"type": "string", "description": "Exact shell command to run."}},
            required=("cmd",),
            aliases=("bash", "execute", "run_command", "terminal"),
            field_aliases={"command": "cmd", "input": "cmd"},
            body_field="cmd",
            display_name="Command",
            activity="Executing command",
            target_field="cmd",
            target_label="COMMAND",
            risk="system",
            interruptible=True,
        ),
        ToolSpec(
            "patch",
            "Apply a unified diff after showing additions and removals for permission review.",
            {"patch": {"type": "string", "description": "Complete unified diff."}},
            required=("patch",),
            aliases=("apply_patch",),
            field_aliases={"diff": "patch"},
            body_field="patch",
            display_name="Patch",
            activity="Applying patch",
            target_field="patch",
            target_label="CHANGE",
            risk="write",
        ),
        ToolSpec(
            "read_file",
            "Read a text file, optionally centered around a line number.",
            {
                "path": {"type": "string", "description": "File path."},
                "line": {"type": "integer", "minimum": 1, "description": "Optional line number."},
            },
            required=("path",),
            body_field="path",
            display_name="File read",
            activity="Reading file",
            target_field="path",
            target_label="PATH",
        ),
        ToolSpec(
            "list_dir",
            "List a directory without modifying it.",
            {"path": {"type": "string", "description": "Directory path; defaults to the working directory."}},
            body_field="path",
            display_name="Directory",
            activity="Listing directory",
            target_field="path",
            target_label="PATH",
        ),
        ToolSpec(
            "find_paths",
            "Find files or directories by name under one explicit client-side search root.",
            {
                "query": {"type": "string", "description": "File, directory, or project name to match."},
                "path": {"type": "string", "description": "Explicit client-side directory to search beneath."},
            },
            required=("query", "path"),
            display_name="Path search",
            activity="Finding paths",
            target_field="path",
            target_label="ROOT",
            interruptible=True,
        ),
        ToolSpec(
            "hardware_status",
            "Read Dairack's authoritative client and configured compute hardware identities.",
            {},
            display_name="Hardware status",
            activity="Reading hardware status",
        ),
        ToolSpec(
            "search_project",
            "Search the indexed project memory for files, symbols, or concepts.",
            {
                "query": {"type": "string", "description": "Focused search query."},
                "path": {
                    "type": "string",
                    "description": "Optional indexed project path; defaults to the active project.",
                },
            },
            required=("query",),
            body_field="query",
            display_name="Project search",
            activity="Searching project",
            target_field="query",
            target_label="QUERY",
            interruptible=True,
        ),
        ToolSpec(
            "index_project",
            "Build or refresh the local project-memory index.",
            {"path": {"type": "string", "description": "Project path; defaults to the working directory."}},
            body_field="path",
            display_name="Project index",
            activity="Indexing project",
            target_field="path",
            target_label="PATH",
            risk="local",
            interruptible=True,
        ),
        ToolSpec(
            "consult_specialist",
            "Ask the coordinator to route one bounded question to a suitable local specialist model.",
            {
                "task": {"type": "string", "description": "One precise question for the specialist."},
                "specialty": {
                    "type": "string",
                    "enum": ["auto", "reasoning", "code_review", "vision", "general"],
                },
                "quality": {"type": "string", "enum": ["routine", "balanced", "high", "critical"]},
                "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "context": {"type": "string", "description": "Minimum evidence needed to answer."},
                "path": {"type": "string", "description": "Optional local image path for visual analysis."},
            },
            required=("task",),
            body_field="task",
            display_name="Specialist",
            activity="Consulting specialist",
            target_field="task",
            target_label="TASK",
            risk="coordinator",
            interruptible=True,
        ),
        ToolSpec(
            "analyze_image",
            "Inspect a local image through the coordinator's vision specialist.",
            {
                "path": {"type": "string", "description": "Local image path."},
                "task": {"type": "string", "description": "Question to answer about the image."},
            },
            required=("path",),
            body_field="task",
            exposed=False,
            display_name="Image analysis",
            activity="Inspecting image",
            target_field="path",
            target_label="IMAGE",
            risk="coordinator",
            interruptible=True,
        ),
        ToolSpec(
            "web_search",
            "Search the public internet when current or externally verified information is needed.",
            {"query": {"type": "string", "description": "Specific web search query."}},
            required=("query",),
            body_field="query",
            display_name="Web search",
            activity="Searching web",
            target_field="query",
            target_label="QUERY",
            risk="network",
            interruptible=True,
        ),
        ToolSpec(
            "web_open",
            "Fetch readable text from one HTTP or HTTPS URL.",
            {"url": {"type": "string", "description": "Absolute HTTP or HTTPS URL."}},
            required=("url",),
            body_field="url",
            display_name="Web page",
            activity="Opening web page",
            target_field="url",
            target_label="URL",
            risk="network",
            interruptible=True,
        ),
    )
)

TOOL_TAG_PATTERN = r"(?:tool|DAIRACK_TOOL|ASUSAI_TOOL|tool_call)"


def _loads_tool_json(candidate: str) -> Any:
    """Parse action JSON, tolerating the unescaped path backslashes models commonly emit on Windows."""
    try:
        return json.loads(candidate)
    except RecursionError:
        raise
    except ValueError as exc:
        repaired = re.sub(r'\\(?!["\\/])', r"\\\\", candidate)
        if repaired != candidate:
            try:
                return json.loads(repaired)
            except ValueError:
                raise exc from None
        raise


def _call_style_arguments(raw: str) -> dict[str, str]:
    arguments: dict[str, str] = {}
    pattern = re.compile(
        r"([A-Za-z_][\w-]*)\s*=\s*(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^,]*)",
        re.DOTALL,
    )
    for match in pattern.finditer(raw):
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            quote = value[0]
            value = value[1:-1].replace("\\" + quote, quote)
        arguments[match.group(1).lower()] = html.unescape(value)
    return arguments


def _decode_call_style(
    candidate: str,
    registry: ToolRegistry,
) -> tuple[dict[str, str] | None, str, bool]:
    """Decode `tool(field=value, ...)` and `<tool(field=value, ...)>` near-miss action syntax."""
    unwrapped = candidate
    angle = re.fullmatch(r"<\s*(.+?)\s*/?\s*>", candidate, re.DOTALL)
    if angle:
        unwrapped = angle.group(1).strip()
    paren = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)\s*\((.*)\)\s*;?", unwrapped, re.DOTALL)
    if paren:
        raw_name = paren.group(1).lower()
        if registry.canonical_name(raw_name) not in registry.known_names:
            return None, "", False
        inner = paren.group(2).strip()
        if inner.startswith("{"):
            try:
                nested = _loads_tool_json(inner)
            except (ValueError, RecursionError) as exc:
                return None, f"action request contains invalid JSON: {getattr(exc, 'msg', str(exc))}", True
            if not isinstance(nested, Mapping):
                return None, "action request JSON must be an object", True
            call, error = registry.validate({"name": raw_name, "arguments": dict(nested)})
            return call, error, True
        call, error = registry.validate({"name": raw_name, "arguments": _call_style_arguments(inner)})
        return call, error, True
    if not angle:
        return None, "", False
    tag_match = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)\b(.*)", unwrapped, re.DOTALL)
    if not tag_match:
        return None, "", False
    raw_name = tag_match.group(1).lower()
    if registry.canonical_name(raw_name) not in registry.known_names:
        return None, "", False
    attributes = _tool_attributes(tag_match.group(2))
    call, error = registry.validate({"name": raw_name, "arguments": attributes})
    return call, error, True


def _known_call_style_tail(text: str, registry: ToolRegistry) -> str:
    """Return a trailing angle-wrapped near-miss action expression, if one ends the text."""
    tail = re.search(r"<\s*[A-Za-z_][A-Za-z0-9_-]*[^<]*>\s*$", text, re.DOTALL)
    if not tail:
        return ""
    candidate = tail.group(0).strip()
    name_match = re.match(r"<\s*([A-Za-z_][A-Za-z0-9_-]*)", candidate)
    if not name_match:
        return ""
    if registry.canonical_name(name_match.group(1).lower()) not in registry.known_names:
        return ""
    return candidate


def _tool_attributes(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    pattern = re.compile(
        r"([A-Za-z_][\w-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s]+))",
        re.DOTALL,
    )
    for match in pattern.finditer(raw):
        value = next((group for group in match.groups()[1:] if group is not None), "")
        attrs[match.group(1).lower()] = html.unescape(value)
    return attrs


def _tool_envelope(text: str) -> tuple[str | None, str, str]:
    opening = re.search(rf"<(?P<tag>{TOOL_TAG_PATTERN})\b", text, re.IGNORECASE)
    if not opening:
        return None, "", ""

    quote = ""
    opening_end = -1
    for index in range(opening.end(), len(text)):
        char = text[index]
        if quote:
            if char == quote and (index == 0 or text[index - 1] != "\\"):
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == ">":
            opening_end = index
            break
    if opening_end < 0:
        return None, "", "incomplete action request: opening tool tag was not closed"

    raw_attrs = text[opening.end() : opening_end].strip()
    self_closing = raw_attrs.endswith("/")
    if self_closing:
        raw_attrs = raw_attrs[:-1].rstrip()
        body = ""
        envelope_end = opening_end + 1
    else:
        tag = str(opening.group("tag"))
        closing = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE).search(text, opening_end + 1)
        if not closing:
            return None, "", "incomplete action request: closing tool tag is missing"
        body_start = opening_end + 1
        closing_start = closing.start()
        body = text[body_start:closing_start].strip()
        envelope_end = closing.end()
    if re.search(rf"<{TOOL_TAG_PATTERN}\b", text[envelope_end:], re.IGNORECASE):
        return None, "", "multiple action requests were returned; exactly one is required"
    return raw_attrs, body, ""


def _mapping_tool_name(data: Mapping[str, Any]) -> str:
    function = data.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "").strip().lower()
    return str(data.get("name") or data.get("tool") or "").strip().lower()


def _decode_shorthand(
    text: str,
    registry: ToolRegistry,
) -> tuple[dict[str, str] | None, str, bool]:
    candidate = text.strip()
    if not candidate:
        return None, "", False

    if candidate.startswith("{"):
        try:
            data = _loads_tool_json(candidate)
        except (ValueError, RecursionError) as exc:
            looks_explicit = bool(re.search(r'"(?:name|tool|function)"\s*:', candidate))
            error = (
                f"compatibility action request contains invalid JSON: {getattr(exc, 'msg', str(exc))}"
                if looks_explicit
                else ""
            )
            return None, error, looks_explicit
        if isinstance(data, Mapping):
            raw_name = _mapping_tool_name(data)
            if raw_name in registry.known_names:
                call, error = registry.validate(data)
                return call, error, True
        return None, "", False

    provider_candidate = re.sub(r"</s>\s*$", "", candidate, flags=re.IGNORECASE)
    provider_wrapper = re.fullmatch(
        r"\[TOOL_CALLS\]\s*([A-Za-z_][A-Za-z0-9_-]*)(?:\[ARGS\])?\s*(\{.*)",
        provider_candidate,
        re.DOTALL | re.IGNORECASE,
    )
    if provider_wrapper:
        candidate = provider_wrapper.group(1) + provider_wrapper.group(2)

    name_only = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)", candidate)
    if name_only:
        raw_name = name_only.group(1).lower()
        if raw_name not in registry.known_names:
            return None, "", False
        call, error = registry.validate({"name": raw_name})
        return call, error, True

    call, error, recognized = _decode_call_style(candidate, registry)
    if recognized:
        return call, error, True

    call_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*)\s*(\{.*)", candidate, re.DOTALL)
    if not call_match:
        if candidate.upper().startswith("[TOOL_CALLS]"):
            return None, "unrecognized provider action serialization", True
        return None, "", False

    raw_name = call_match.group(1).lower()
    if raw_name not in registry.known_names:
        return None, "", False
    try:
        arguments = json.loads(call_match.group(2))
    except (ValueError, RecursionError) as exc:
        return None, f"compatibility action request contains invalid JSON: {getattr(exc, 'msg', str(exc))}", True
    if not isinstance(arguments, Mapping):
        return None, "compatibility action request arguments must be an object", True
    call, error = registry.validate({"name": raw_name, "arguments": arguments})
    return call, error, True


def decode_text_tool_call(
    text: str,
    registry: ToolRegistry = TOOL_REGISTRY,
) -> tuple[dict[str, str] | None, str, bool]:
    """Decode one compatibility action and distinguish prose from malformed protocol."""
    raw_attrs, body, envelope_error = _tool_envelope(text)
    if envelope_error:
        return None, envelope_error, True
    if raw_attrs is None:
        call, error, recognized = _decode_shorthand(text, registry)
        if recognized:
            return call, error, True
        tail = _known_call_style_tail(text, registry)
        if tail:
            return _decode_call_style(tail, registry)
        return None, "", False

    attrs = _tool_attributes(raw_attrs)
    if attrs.get("name"):
        data: dict[str, Any] = dict(attrs)
        if body.startswith("{"):
            try:
                nested = _loads_tool_json(body)
            except (ValueError, RecursionError) as exc:
                return None, f"action request contains invalid JSON: {getattr(exc, 'msg', str(exc))}", True
            if not isinstance(nested, Mapping):
                return None, "action request JSON must be an object", True
            data = {**nested, **data}
            body = ""
        call, error = registry.validate(data, body)
        return call, error, True

    if not body:
        return None, "action request has no payload", True
    try:
        data = _loads_tool_json(body)
    except (ValueError, RecursionError) as exc:
        return None, f"action request contains invalid JSON: {getattr(exc, 'msg', str(exc))}", True
    if not isinstance(data, Mapping):
        return None, "action request JSON must be an object", True
    call, error = registry.validate(data)
    return call, error, True


def strip_tool_protocol(text: str, registry: ToolRegistry = TOOL_REGISTRY) -> str:
    parts: list[str] = []
    cursor = 0
    opening_pattern = re.compile(rf"<(?P<tag>{TOOL_TAG_PATTERN})\b", re.IGNORECASE)
    while opening := opening_pattern.search(text, cursor):
        parts.append(text[cursor : opening.start()])
        quote = ""
        opening_end = -1
        for index in range(opening.end(), len(text)):
            char = text[index]
            if quote:
                if char == quote and text[index - 1] != "\\":
                    quote = ""
            elif char in {'"', "'"}:
                quote = char
            elif char == ">":
                opening_end = index
                break
        if opening_end < 0:
            cursor = len(text)
            break
        attributes = text[opening.end() : opening_end].rstrip()
        if attributes.endswith("/"):
            cursor = opening_end + 1
            continue
        tag = str(opening.group("tag"))
        closing = re.compile(rf"</{re.escape(tag)}\s*>", re.IGNORECASE).search(text, opening_end + 1)
        if not closing:
            cursor = len(text)
            break
        cursor = closing.end()
    parts.append(text[cursor:])
    cleaned = "".join(parts).strip()
    _call, _error, recognized = _decode_shorthand(cleaned, registry)
    if recognized:
        return ""
    cleaned = _strip_near_miss_markup(cleaned, registry)
    marker = re.search(rf"<{TOOL_TAG_PATTERN}\b", cleaned, re.IGNORECASE)
    return cleaned[: marker.start()].rstrip() if marker else cleaned


def _strip_near_miss_markup(text: str, registry: ToolRegistry) -> str:
    """Excise angle-wrapped action expressions for known tools that missed the protocol grammar."""
    if "<" not in text:
        return text
    names = "|".join(sorted(re.escape(name) for name in registry.known_names))
    pattern = re.compile(rf"<\s*(?:{names})\b", re.IGNORECASE)
    parts: list[str] = []
    cursor = 0
    while match := pattern.search(text, cursor):
        parts.append(text[cursor : match.start()])
        quote = ""
        end = -1
        for index in range(match.end(), len(text)):
            char = text[index]
            if quote:
                if char == quote and text[index - 1] != "\\":
                    quote = ""
            elif char in {'"', "'"}:
                quote = char
            elif char == ">":
                end = index
                break
        if end < 0:
            cursor = len(text)
            break
        cursor = end + 1
    parts.append(text[cursor:])
    return "".join(parts).strip()
