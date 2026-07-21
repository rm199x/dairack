"""Small bounded plain-text helpers shared across runtime domains."""

from __future__ import annotations

MAX_TEXT_OUTPUT = 24000


def truncate(text: str, limit: int = MAX_TEXT_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def truncate_middle(text: str, limit: int = MAX_TEXT_OUTPUT) -> str:
    """Bound long output while retaining the beginning and final verdicts."""
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.6))
    tail = max(1, limit - head)
    omitted = len(text) - head - tail
    return (
        text[:head] + f"\n...[{omitted} chars omitted from the middle; beginning and end retained]...\n" + text[-tail:]
    )
