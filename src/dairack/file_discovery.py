"""Bounded, cross-platform discovery of named files and directories."""

from __future__ import annotations

import fnmatch
import os
import threading
from collections import deque
from pathlib import Path

DEFAULT_LIMIT = 40
MAX_VISITED_ENTRIES = 60_000
MAX_DEPTH = 10
SKIP_DIRECTORIES = {
    ".cache",
    ".git",
    ".gradle",
    ".idea",
    ".local",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "appdata",
    "node_modules",
    "venv",
}
PROJECT_SUFFIXES = {".uproject", ".code-workspace", ".sln", ".xcodeproj"}


def _matches(name: str, query: str) -> bool:
    lowered_name = name.casefold()
    lowered_query = query.casefold()
    if any(marker in query for marker in "*?["):
        return fnmatch.fnmatch(lowered_name, lowered_query)
    return lowered_query in lowered_name


def _match_rank(path: Path, query: str) -> tuple[int, int, str]:
    name = path.name.casefold()
    stem = path.stem.casefold()
    target = query.casefold()
    exact = 0 if name == target or stem == target else 1
    project = 0 if path.suffix.casefold() in PROJECT_SUFFIXES else 1
    return exact, project, str(path).casefold()


def find_paths(
    root: Path,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    root = root.expanduser().resolve()
    query = query.strip()
    if not query:
        return 2, "path search requires a non-empty name"
    if not root.exists():
        return 1, f"search root not found: {root}"
    if not root.is_dir():
        return 1, f"search root is not a directory: {root}"

    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    matches: list[Path] = []
    visited = 0
    inaccessible = 0
    capped = False
    while queue:
        if cancel_event and cancel_event.is_set():
            return 130, "path search interrupted"
        directory, depth = queue.popleft()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError:
            inaccessible += 1
            continue
        for entry in entries:
            visited += 1
            if visited > MAX_VISITED_ENTRIES:
                capped = True
                queue.clear()
                break
            path = Path(entry.path)
            if _matches(entry.name, query):
                matches.append(path)
            if depth >= MAX_DEPTH:
                continue
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError:
                inaccessible += 1
                continue
            if is_directory and entry.name.casefold() not in SKIP_DIRECTORIES:
                queue.append((path, depth + 1))

    selected = sorted(matches, key=lambda path: _match_rank(path, query))[: max(1, min(limit, DEFAULT_LIMIT))]
    lines = [f"root: {root}", f"query: {query}", f"scanned: {min(visited, MAX_VISITED_ENTRIES)} entries"]
    if inaccessible:
        lines.append(f"unreadable directories: {inaccessible}")
    if capped:
        lines.append(f"scope limit: stopped after {MAX_VISITED_ENTRIES} entries")
    if not selected:
        lines.append("matches: none")
        return 0, "\n".join(lines)
    lines.append("matches:")
    for path in selected:
        kind = "project" if path.suffix.casefold() in PROJECT_SUFFIXES else "directory" if path.is_dir() else "file"
        lines.append(f"  {kind:<9} {path}")
    if len(matches) > len(selected):
        lines.append(f"  ... {len(matches) - len(selected)} additional matches omitted")
    return 0, "\n".join(lines)
