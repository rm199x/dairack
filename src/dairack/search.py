"""Shared project-search policy and the isolated no-ripgrep fallback."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

EXCLUDED_DIRECTORY_NAMES = frozenset({".git", ".cache", "node_modules", ".venv", "venv", "__pycache__"})
EXCLUDED_DIRECTORY_PATHS = (
    (".codex", "sessions"),
    (".local", "share", "dairack", "vendor"),
)


def _folded(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.casefold() for part in parts if part and part not in {os.sep, "/", "\\"})


def search_parts_are_excluded(parts: tuple[str, ...]) -> bool:
    """Return whether path parts enter internal, generated, or dependency state."""
    normalized = _folded(parts)
    excluded_names = {name.casefold() for name in EXCLUDED_DIRECTORY_NAMES}
    if any(part in excluded_names for part in normalized):
        return True
    for raw_sequence in EXCLUDED_DIRECTORY_PATHS:
        sequence = tuple(part.casefold() for part in raw_sequence)
        width = len(sequence)
        if any(normalized[index : index + width] == sequence for index in range(len(normalized) - width + 1)):
            return True
    return False


def _rg_exclusion_globs() -> tuple[str, ...]:
    names = tuple(f"!**/{name}/**" for name in sorted(EXCLUDED_DIRECTORY_NAMES))
    paths = tuple(f"!**/{'/'.join(parts)}/**" for parts in EXCLUDED_DIRECTORY_PATHS)
    return names + paths


RG_EXCLUSION_GLOBS = _rg_exclusion_globs()


def _bounded_match(
    rows: list[str],
    rendered: str,
    output_chars: int,
    max_output: int,
) -> tuple[int, bool]:
    marker = "...[results truncated]"
    remaining = max_output - output_chars
    required = len(rendered) + 1
    if required <= remaining:
        rows.append(rendered)
        return output_chars + required, False
    if remaining > len(marker) + 1:
        rows.append(rendered[: remaining - len(marker) - 1])
    rows.append(marker)
    return max_output, True


def search_with_python(
    target: Path,
    pattern: str,
    *,
    max_output: int,
    max_file_bytes: int,
    max_files: int,
) -> tuple[int, str]:
    """Bounded scanner used only inside the cancellable fallback process."""
    try:
        expression = re.compile(pattern)
    except re.error as exc:
        return 2, f"invalid search expression: {exc}"

    if not target.exists():
        return 1, f"search root not found: {target}"

    rows: list[str] = []
    output_chars = 0
    visited = 0

    def scan_file(path: Path, label: Path) -> tuple[bool, str]:
        nonlocal output_chars
        try:
            size = path.stat().st_size
            if size > max_file_bytes:
                return False, f"file exceeds search limit ({max_file_bytes} bytes): {path}"
            data = path.read_bytes()
        except OSError as exc:
            return False, f"could not read {path}: {exc}"
        if b"\x00" in data[:4096]:
            return False, f"binary file not searched: {path}"
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if expression.search(line):
                output_chars, stopped = _bounded_match(
                    rows,
                    f"{label}:{number}:{line}",
                    output_chars,
                    max_output,
                )
                if stopped:
                    return True, ""
        return False, ""

    if target.is_file():
        stopped, error = scan_file(target, Path(target.name))
        if error:
            return 1, error
        return (0, "\n".join(rows)) if rows else (1, "no matches")
    if not target.is_dir():
        return 1, f"search root not found: {target}"

    for root, directories, filenames in os.walk(target, onerror=lambda _error: None):
        root_path = Path(root)
        relative_root = root_path.relative_to(target)
        directories[:] = [name for name in directories if not search_parts_are_excluded(relative_root.parts + (name,))]
        for filename in filenames:
            visited += 1
            if visited > max_files:
                had_matches = bool(rows)
                rows.append(f"...[search stopped after {max_files} files]")
                return (0 if had_matches else 1), "\n".join(rows)
            path = root_path / filename
            if path.is_symlink():
                continue
            stopped, _error = scan_file(path, path.relative_to(target))
            if stopped:
                return 0, "\n".join(rows)
    return (0, "\n".join(rows)) if rows else (1, "no matches")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--max-output", type=int, required=True)
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--max-files", type=int, required=True)
    parser.add_argument("pattern")
    parser.add_argument("target", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    code, output = search_with_python(
        args.target.resolve(),
        args.pattern,
        max_output=max(1, args.max_output),
        max_file_bytes=max(1, args.max_file_bytes),
        max_files=max(1, args.max_files),
    )
    if output:
        print(output)
    return code


if __name__ == "__main__":
    sys.exit(main())
