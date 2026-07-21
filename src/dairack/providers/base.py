"""Provider contract used by the runtime and coordinator."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from threading import Event
from typing import Any, Protocol

from ..models import ModelDescriptor


class ModelProvider(Protocol):
    name: str
    host: str
    stream_phase: str
    current_model: str
    current_stats: dict[str, Any]
    last_stats: dict[str, Any]

    def version(self) -> str: ...

    def list_models(self) -> list[ModelDescriptor]: ...

    def running_models(self) -> list[str]: ...

    def supports(self, model: str, capability: str) -> bool: ...

    def embed(self, model: str, texts: list[str]) -> list[list[float]]: ...

    def chat_stream(
        self,
        model: str,
        messages: Iterable[Mapping[str, Any]],
        *,
        think: bool | str,
        num_ctx: int,
        num_predict: int | None = None,
        cancel_event: Event | None = None,
        extra_options: Mapping[str, Any] | None = None,
        response_format: str | Mapping[str, Any] | None = None,
        keep_alive: str | int | None = None,
        tools: Iterable[Mapping[str, Any]] | None = None,
        tool_call_sink: Callable[[dict[str, Any]], None] | None = None,
        thinking_sink: Callable[[str], None] | None = None,
    ) -> Iterator[str]: ...
