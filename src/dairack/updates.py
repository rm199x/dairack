"""Release discovery and deterministic self-update commands."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from packaging.version import InvalidVersion, Version

from .config import ConfigError, atomic_write_json, validate_update_url
from .network import FetchResult, NetworkError, fetch_public_url
from .paths import PATHS, AppPaths

MAX_RESPONSE_BYTES = 1_048_576
MAX_NOTES_URL_LENGTH = 2048


class UpdateError(RuntimeError):
    """Raised when a release channel cannot be read or applied."""


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    source_url: str
    notes_url: str = ""
    checked_at: float = 0.0
    from_cache: bool = False

    @property
    def available(self) -> bool:
        try:
            return Version(self.latest_version) > Version(self.current_version)
        except InvalidVersion:
            return False

    def to_cache(self) -> dict[str, Any]:
        return {
            "checked_at": self.checked_at,
            "latest_version": self.latest_version,
            "notes_url": self.notes_url,
            "source_url": self.source_url,
        }


def _release_values(payload: Mapping[str, Any]) -> tuple[str, str]:
    info = payload.get("info")
    if isinstance(info, Mapping):
        version = str(info.get("version") or "").strip()
        project_urls = info.get("project_urls")
        notes_url = ""
        if isinstance(project_urls, Mapping):
            for key in ("Changelog", "Release notes", "Releases", "Source"):
                if project_urls.get(key):
                    notes_url = str(project_urls[key]).strip()
                    break
        notes_url = notes_url or str(info.get("package_url") or info.get("project_url") or "").strip()
    else:
        version = str(payload.get("version") or payload.get("latest_version") or "").strip()
        notes_url = str(
            payload.get("notes_url") or payload.get("release_url") or payload.get("changelog_url") or ""
        ).strip()
    if not version:
        raise UpdateError("release response does not contain a version")
    try:
        Version(version)
    except InvalidVersion as exc:
        raise UpdateError(f"release response contains an invalid version: {version}") from exc
    if notes_url and (
        len(notes_url) > MAX_NOTES_URL_LENGTH
        or any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in notes_url)
        or not notes_url.startswith("https://")
        or not urllib.parse.urlparse(notes_url).hostname
    ):
        notes_url = ""
    return version, notes_url


def _cached_info(path: Path, current_version: str, source_url: str) -> UpdateInfo | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or str(payload.get("source_url") or "") != source_url:
            return None
        latest, notes_url = _release_values(payload)
        checked_at = float(payload.get("checked_at") or 0.0)
        if checked_at <= 0:
            return None
        return UpdateInfo(current_version, latest, source_url, notes_url, checked_at, True)
    except (OSError, ValueError, TypeError, UpdateError):
        return None


def check_for_update(
    current_version: str,
    source_url: str,
    *,
    paths: AppPaths = PATHS,
    force: bool = False,
    max_age_seconds: float = 86_400,
    timeout: float = 4.0,
    opener: Callable[..., Any] | None = None,
) -> UpdateInfo:
    """Read a PyPI-style JSON response or a small Dairack release manifest."""

    try:
        source_url = validate_update_url(source_url)
    except ConfigError as exc:
        raise UpdateError(str(exc)) from exc
    if not source_url:
        raise UpdateError("this build has no release channel configured")
    try:
        Version(current_version)
    except InvalidVersion as exc:
        raise UpdateError(f"current Dairack version is invalid: {current_version}") from exc

    now = time.time()
    cached = _cached_info(paths.update_cache_file, current_version, source_url)
    if cached and not force and 0 <= now - cached.checked_at < max(0.0, max_age_seconds):
        return cached

    headers = {
        "Accept": "application/json",
        "User-Agent": f"Dairack/{current_version} update-check",
    }
    try:
        if opener is None:
            parsed_source = urllib.parse.urlparse(source_url)
            allow_loopback = str(parsed_source.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}
            fetched: FetchResult = fetch_public_url(
                source_url,
                max_bytes=MAX_RESPONSE_BYTES,
                headers=headers,
                idle_timeout=timeout,
                total_timeout=timeout,
                require_https=True,
                allow_loopback=allow_loopback,
            )
            body = fetched.body
            final_url = fetched.final_url
        else:
            request = urllib.request.Request(source_url, headers=headers)
            with opener(request, timeout=timeout) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else source_url
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                    raise UpdateError("release response is too large")
                body = response.read(MAX_RESPONSE_BYTES + 1)
        try:
            validate_update_url(final_url)
        except ConfigError as exc:
            raise UpdateError("release endpoint redirected to an insecure URL") from exc
    except UpdateError:
        raise
    except (NetworkError, OSError, ValueError, urllib.error.URLError) as exc:
        raise UpdateError(f"could not check for updates: {exc}") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise UpdateError("release response is too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("release response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("release response must be a JSON object")
    latest, notes_url = _release_values(payload)
    result = UpdateInfo(current_version, latest, source_url, notes_url, now, False)
    atomic_write_json(paths.update_cache_file, result.to_cache())
    return result


def update_command(
    version: str,
    *,
    executable: str | os.PathLike[str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> list[str]:
    """Return an exact local installer command without accepting remote command data."""

    try:
        normalized_version = Version(version)
    except InvalidVersion as exc:
        raise UpdateError(f"cannot install invalid version: {version}") from exc
    python = Path(executable or sys.executable).resolve()
    normalized = str(python).replace("\\", "/").lower()
    spec = f"dairack=={normalized_version}"
    uv = which("uv")
    if uv and any(marker in normalized for marker in ("/uv/tools/dairack/", "/uv/tools/asusai/")):
        return [uv, "tool", "install", "--force", spec]
    pipx = which("pipx")
    if pipx and any(marker in normalized for marker in ("/pipx/venvs/dairack/", "/pipx/venvs/asusai/")):
        return [pipx, "install", "--force", spec]
    return [str(python), "-m", "pip", "install", "--upgrade", spec]


def format_update_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def apply_update(info: UpdateInfo) -> subprocess.CompletedProcess[str]:
    if not info.available:
        raise UpdateError("no newer Dairack release is available")
    command = update_command(info.latest_version)
    try:
        return subprocess.run(command, check=False, text=True)
    except OSError as exc:
        raise UpdateError(f"could not start updater: {exc}") from exc
