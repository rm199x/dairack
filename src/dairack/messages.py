"""Canonical chat request construction shared by runtimes and providers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


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
            content = str(item.get("content") or "").strip()
            if content:
                system_parts.append(content)
            continue
        conversation.append(item)

    system_parts.extend(str(value).strip() for value in system_directives if str(value).strip())
    if not system_parts:
        return conversation
    return [{"role": "system", "content": "\n\n".join(system_parts)}, *conversation]
