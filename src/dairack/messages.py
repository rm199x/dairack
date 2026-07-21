"""Canonical chat request construction and message inspection shared by runtimes."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

MAX_ATTACHED_IMAGES = 4
SYSTEM_PARTS_KEY = "_dairack_system_parts"

TOOL_RESULT_PREFIXES = (
    "Shell tool result:",
    "Patch tool result:",
    "Structured tool result:",
    "Coordinator specialist result:",
    "Tool result:",
    "Tool request denied",
    "Runtime event:",
)

CONTEXT_REFERENCE_PATTERN = re.compile(
    r"\b(?:it|its|that|this|these|those|one|ones|former|latter|above|earlier|previous|same|other)\b"
)
CONTEXT_CONTINUATION_PATTERN = re.compile(
    r"^(?:and|but|so|then)\b|\b(?:continue|proceed|resume|retry|again|instead|go ahead|carry on|do so)\b"
)
CONTEXT_REPLY_PATTERN = re.compile(
    r"^(?:yes|no|yep|nope|sure|maybe|why|why not|how so|which one|the first|the second|the last)\??$"
)
WEB_REFERENCE_PATTERN = re.compile(r"\b(?:this|that|the|same)\s+(?:web\s*page|website|site|url|link|domain)\b")


def latest_user_message(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content and not content.startswith(TOOL_RESULT_PREFIXES):
            return message
    return {}


def latest_user_task(messages: list[dict[str, Any]]) -> str:
    return str(latest_user_message(messages).get("content") or "")


def message_image_paths(message: dict[str, Any]) -> list[str]:
    values = message.get("image_paths")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value).strip()][:MAX_ATTACHED_IMAGES]


def latest_user_images(messages: list[dict[str, Any]]) -> list[str]:
    return message_image_paths(latest_user_message(messages))


def depends_on_conversation_context(prompt: str) -> bool:
    """Detect discourse references without inheriting complexity from history by default."""
    normalized = prompt.lower().replace("'", "")
    normalized = re.sub(r"[^a-z0-9\s?]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    return bool(
        CONTEXT_REFERENCE_PATTERN.search(normalized)
        or CONTEXT_CONTINUATION_PATTERN.search(normalized)
        or CONTEXT_REPLY_PATTERN.fullmatch(normalized)
        or WEB_REFERENCE_PATTERN.search(normalized)
    )


def canonicalize_messages(
    messages: Iterable[Mapping[str, Any]],
    system_directives: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return one leading system message followed by conversation messages in order."""
    system_parts: list[str] = []
    conversation: list[dict[str, Any]] = []
    for raw in messages:
        item = dict(raw)
        if str(item.get("role") or "") == "system":
            parts = item.get(SYSTEM_PARTS_KEY)
            if isinstance(parts, list):
                system_parts.extend(str(value).strip() for value in parts if str(value).strip())
            else:
                content = str(item.get("content") or "").strip()
                if content:
                    system_parts.append(content)
            continue
        conversation.append(item)

    system_parts.extend(str(value).strip() for value in system_directives if str(value).strip())
    if not system_parts:
        return conversation
    return [
        {
            "role": "system",
            "content": "\n\n".join(system_parts),
            SYSTEM_PARTS_KEY: system_parts,
        },
        *conversation,
    ]


def expand_system_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Restore canonical system sections for budget-aware selection."""
    expanded: list[dict[str, Any]] = []
    for raw in messages:
        item = dict(raw)
        parts = item.get(SYSTEM_PARTS_KEY)
        if str(item.get("role") or "") == "system" and isinstance(parts, list):
            expanded.extend({"role": "system", "content": str(value).strip()} for value in parts if str(value).strip())
        else:
            item.pop(SYSTEM_PARTS_KEY, None)
            expanded.append(item)
    return expanded
