"""Shared Ollama model lifecycle operations with structured progress."""

from __future__ import annotations

import os
import re
import shutil
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable, Mapping

from .providers.ollama import OllamaProvider


class ModelOperationError(ValueError):
    pass


class TransferCancelled(RuntimeError):
    pass


MODEL_NAME_PATTERN = re.compile(r"^[^\s\x00-\x1f\x7f|]{1,240}$")


def validate_model_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise ModelOperationError("model name cannot be empty")
    if not MODEL_NAME_PATTERN.fullmatch(name):
        raise ModelOperationError("model name contains unsupported characters")
    return name


def local_ollama_free_bytes(host: str) -> int | None:
    normalized = host if "://" in host else "http://" + host
    hostname = (urllib.parse.urlsplit(normalized).hostname or "").lower()
    if hostname not in {"", "localhost", "127.0.0.1", "::1"}:
        return None
    target = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models").expanduser()
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return int(shutil.disk_usage(target).free)
    except OSError:
        return None


@dataclass(frozen=True, slots=True)
class PullProgress:
    model: str
    status: str
    completed: int
    total: int
    percent: float | None
    elapsed: float
    digest: str = ""


@dataclass(frozen=True, slots=True)
class PullResult:
    model: str
    elapsed: float
    completed: int
    total: int
    status: str


ProgressCallback = Callable[[PullProgress], None]


def _invalidate_provider_model_cache(provider: OllamaProvider) -> None:
    invalidate = getattr(provider, "invalidate_model_cache", None)
    if callable(invalidate):
        invalidate()


def _progress_from_layers(
    model: str,
    event: Mapping[str, Any],
    layers: dict[str, tuple[int, int]],
    started: float,
) -> PullProgress:
    digest = str(event.get("digest") or "")
    try:
        total = max(0, int(event.get("total") or 0))
        completed = max(0, int(event.get("completed") or 0))
    except (TypeError, ValueError):
        total, completed = 0, 0
    if digest and (total or completed):
        layers[digest] = (completed, total)
    aggregate_completed = sum(value[0] for value in layers.values())
    aggregate_total = sum(value[1] for value in layers.values())
    percent = None
    if aggregate_total:
        percent = min(1.0, aggregate_completed / aggregate_total)
    return PullProgress(
        model=model,
        status=str(event.get("status") or "working"),
        completed=aggregate_completed,
        total=aggregate_total,
        percent=percent,
        elapsed=max(0.0, time.monotonic() - started),
        digest=digest,
    )


def pull_model(
    provider: OllamaProvider,
    model: str,
    *,
    cancel_event: Event | None = None,
    on_progress: ProgressCallback | None = None,
) -> PullResult:
    name = validate_model_name(model)
    started = time.monotonic()
    layers: dict[str, tuple[int, int]] = {}
    latest = PullProgress(name, "starting", 0, 0, None, 0.0)
    stream = provider.pull(name)
    try:
        for event in stream:
            if cancel_event and cancel_event.is_set():
                raise TransferCancelled(f"download cancelled: {name}")
            latest = _progress_from_layers(name, event, layers, started)
            if on_progress:
                on_progress(latest)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    if cancel_event and cancel_event.is_set():
        raise TransferCancelled(f"download cancelled: {name}")
    _invalidate_provider_model_cache(provider)
    return PullResult(
        model=name,
        elapsed=max(0.0, time.monotonic() - started),
        completed=latest.completed,
        total=latest.total,
        status=latest.status,
    )


def remove_model(provider: OllamaProvider, model: str) -> str:
    name = validate_model_name(model)
    provider.delete(name)
    _invalidate_provider_model_cache(provider)
    return name
