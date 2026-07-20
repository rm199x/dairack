"""Ollama HTTP API adapter."""

from __future__ import annotations

import base64
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, Iterable, Iterator, Mapping

from ..identity import BRIDGE_INFO_PATH, LEGACY_BRIDGE_INFO_PATH
from ..messages import canonicalize_messages
from ..models import ModelDescriptor


class OllamaError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


GENERATION_IDLE_TIMEOUT = 180


def normalize_host(host: str) -> str:
    value = host.strip() or "127.0.0.1:11434"
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value.rstrip("/")


def _request_json(
    method: str,
    url: str,
    payload: Mapping[str, Any] | None = None,
    timeout: float | None = 15,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json", **dict(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail or exc.reason}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(f"could not reach Ollama at {urllib.parse.urlsplit(url).netloc}: {exc.reason}") from exc
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise OllamaError("Ollama returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise OllamaError("Ollama returned an unexpected response")
    if parsed.get("error"):
        raise OllamaError(str(parsed["error"]))
    return parsed


def _stream_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
    cancel_event: Event | None = None,
) -> Iterator[dict[str, Any]]:
    if cancel_event and cancel_event.is_set():
        return
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson", **dict(headers or {})},
    )
    try:
        response = urllib.request.urlopen(request, timeout=GENERATION_IDLE_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise OllamaError(f"Ollama returned HTTP {exc.code}: {detail or exc.reason}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise OllamaError(f"could not reach Ollama: {exc.reason}") from exc
    watcher_stop = Event()

    def interrupt_blocked_read() -> None:
        while not watcher_stop.wait(0.1):
            if cancel_event and cancel_event.is_set():
                try:
                    response_socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                    if response_socket is not None:
                        response_socket.shutdown(socket.SHUT_RDWR)
                    response.close()
                except (AttributeError, OSError, ValueError):
                    pass
                return

    watcher = None
    if cancel_event is not None:
        watcher = Thread(target=interrupt_blocked_read, daemon=True, name="dairack-ollama-cancel")
        watcher.start()
    try:
        with response:
            for raw_line in response:
                if cancel_event and cancel_event.is_set():
                    return
                if not raw_line.strip():
                    continue
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise OllamaError("Ollama returned malformed stream data") from exc
                if not isinstance(event, dict):
                    continue
                if event.get("error"):
                    raise OllamaError(str(event["error"]))
                yield event
    except (AttributeError, OSError, ValueError) as exc:
        if not (cancel_event and cancel_event.is_set()):
            raise OllamaError(f"Ollama stream stalled or disconnected: {exc}") from exc
    finally:
        watcher_stop.set()
        if watcher is not None:
            watcher.join(timeout=0.2)


def _message_payload(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for message in canonicalize_messages(messages):
        item: dict[str, Any] = {
            "role": str(message.get("role") or "user"),
            "content": str(message.get("content") or ""),
        }
        encoded: list[str] = []
        unavailable: list[str] = []
        for raw_path in message.get("image_paths", []) if isinstance(message.get("image_paths"), list) else []:
            try:
                path = Path(str(raw_path)).expanduser()
                encoded.append(base64.b64encode(path.read_bytes()).decode("ascii"))
            except (OSError, RuntimeError, ValueError):
                label = str(raw_path).replace("\x00", "")
                unavailable.append(Path(label).name if label else "attachment")
        if encoded:
            item["images"] = encoded
        if unavailable:
            labels = ", ".join(unavailable)
            item["content"] = (item["content"] + f"\n\n[Unavailable image attachment: {labels}]").strip()
        tool_calls = message.get("tool_calls")
        if item["role"] == "assistant" and isinstance(tool_calls, list):
            item["tool_calls"] = [dict(call) for call in tool_calls if isinstance(call, Mapping)]
        if item["role"] == "tool" and message.get("tool_name"):
            item["tool_name"] = str(message["tool_name"])
        rendered.append(item)
    return rendered


def _completion_stats(
    event: Mapping[str, Any],
    *,
    started: float,
    first_token_at: float,
    thinking_chars: int,
    output_chars: int,
) -> dict[str, Any]:
    eval_count = int(event.get("eval_count") or max(1, output_chars // 4))
    eval_duration = int(event.get("eval_duration") or 0)
    total_duration = int(event.get("total_duration") or 0)
    return {
        "eval_count": eval_count,
        "prompt_eval_count": int(event.get("prompt_eval_count") or 0),
        "thinking_chars": thinking_chars,
        "tokens_per_second": eval_count / (eval_duration / 1_000_000_000) if eval_count and eval_duration else 0.0,
        "time_to_first_token": first_token_at - started if first_token_at else 0.0,
        "load_seconds": int(event.get("load_duration") or 0) / 1_000_000_000,
        "total_seconds": total_duration / 1_000_000_000 if total_duration else max(0.0, time.monotonic() - started),
        "done_reason": str(event.get("done_reason") or "stop"),
    }


class OllamaProvider:
    name = "ollama"

    def __init__(self, host: str = "127.0.0.1:11434", token: str = "") -> None:
        self.host = normalize_host(host)
        self.token = token.strip()
        self.stream_phase = "idle"
        self.current_model = ""
        self.current_stats: dict[str, Any] = {}
        self.last_stats: dict[str, Any] = {}
        self._show_cache: dict[str, dict[str, Any]] = {}

    def url(self, path: str) -> str:
        return f"{self.host}{path}"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def health(self) -> bool:
        try:
            self.version()
        except OllamaError:
            return False
        return True

    def version(self) -> str:
        return str(
            _request_json("GET", self.url("/api/version"), timeout=5, headers=self.headers).get("version") or "unknown"
        )

    def compute_info(self) -> dict[str, Any]:
        try:
            return _request_json("GET", self.url(BRIDGE_INFO_PATH), timeout=5, headers=self.headers)
        except OllamaError as exc:
            if exc.status_code not in {404, 405}:
                raise
        return _request_json("GET", self.url(LEGACY_BRIDGE_INFO_PATH), timeout=5, headers=self.headers)

    def show_model(self, model: str, refresh: bool = False) -> dict[str, Any]:
        key = model.lower()
        if not refresh and key in self._show_cache:
            return self._show_cache[key]
        value = _request_json("POST", self.url("/api/show"), {"model": model}, timeout=15, headers=self.headers)
        self._show_cache[key] = value
        return value

    def list_models(self) -> list[ModelDescriptor]:
        payload = _request_json("GET", self.url("/api/tags"), timeout=10, headers=self.headers)
        result: list[ModelDescriptor] = []
        for item in payload.get("models", []):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("model") or "")
            if not name:
                continue
            details = item.get("details") if isinstance(item.get("details"), Mapping) else {}
            try:
                show = self.show_model(name)
            except OllamaError:
                show = {}
            show_details = show.get("details") if isinstance(show.get("details"), Mapping) else {}
            model_info = show.get("model_info") if isinstance(show.get("model_info"), Mapping) else {}
            architecture = str(model_info.get("general.architecture") or "")
            context_keys = [key for key in model_info if str(key).endswith(".context_length")]
            context = int(details.get("context_length") or 0)
            if not context and context_keys:
                try:
                    context = max(int(model_info[key]) for key in context_keys)
                except (TypeError, ValueError):
                    context = 0
            capabilities = show.get("capabilities") if isinstance(show.get("capabilities"), list) else []
            result.append(
                ModelDescriptor(
                    name=name,
                    size=max(0, int(item.get("size") or 0)),
                    parameter_size=str(details.get("parameter_size") or show_details.get("parameter_size") or ""),
                    quantization=str(details.get("quantization_level") or show_details.get("quantization_level") or ""),
                    context_length=context,
                    family=str(details.get("family") or show_details.get("family") or ""),
                    architecture=architecture,
                    digest=str(item.get("digest") or ""),
                    capabilities=tuple(str(value).lower() for value in capabilities),
                )
            )
        return result

    def model_features(self, model: str) -> tuple[str, ...]:
        show = self.show_model(model)
        raw = show.get("capabilities")
        return tuple(str(value).lower() for value in raw) if isinstance(raw, list) else ()

    def supports(self, model: str, capability: str) -> bool:
        return capability.lower() in self.model_features(model)

    def running_details(self) -> list[dict[str, Any]]:
        payload = _request_json("GET", self.url("/api/ps"), timeout=5, headers=self.headers)
        return [dict(item) for item in payload.get("models", []) if isinstance(item, Mapping)]

    def running_models(self) -> list[str]:
        return [
            str(item.get("name") or item.get("model") or "")
            for item in self.running_details()
            if str(item.get("name") or item.get("model") or "")
        ]

    def chat_payload(
        self,
        model: str,
        messages: Iterable[Mapping[str, Any]],
        *,
        stream: bool,
        think: bool | str,
        num_ctx: int,
        num_predict: int | None = None,
        extra_options: Mapping[str, Any] | None = None,
        response_format: str | Mapping[str, Any] | None = None,
        keep_alive: str | int | None = None,
        tools: Iterable[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        options = dict(extra_options or {})
        options["num_ctx"] = int(num_ctx)
        if num_predict is not None:
            options["num_predict"] = int(num_predict)
        payload: dict[str, Any] = {
            "model": model,
            "messages": _message_payload(messages),
            "stream": stream,
            "think": think,
            "options": options,
        }
        if response_format is not None:
            payload["format"] = response_format
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
        return payload

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
    ) -> Iterator[str]:
        self.current_model = model
        self.last_stats = {}
        payload = self.chat_payload(
            model,
            messages,
            stream=True,
            think=think,
            num_ctx=num_ctx,
            num_predict=num_predict,
            extra_options=extra_options,
            response_format=response_format,
            keep_alive=keep_alive,
            tools=tools,
        )
        started = time.monotonic()
        first_token_at = 0.0
        output_chars = 0
        thinking_chars = 0
        self.stream_phase = "loading"
        self.current_stats = {
            "started_at": started,
            "eval_count": 0,
            "thinking_chars": 0,
            "tokens_per_second": 0.0,
        }
        seen_tool_calls: set[str] = set()
        done_seen = False
        try:
            for event in _stream_json(self.url("/api/chat"), payload, self.headers, cancel_event):
                if cancel_event and cancel_event.is_set():
                    break
                message = event.get("message") if isinstance(event.get("message"), Mapping) else {}
                thinking = str(message.get("thinking") or "")
                content = str(message.get("content") or "")
                tool_calls = message.get("tool_calls")
                if tool_call_sink and isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, Mapping):
                            rendered_call = dict(call)
                            signature = json.dumps(rendered_call, sort_keys=True, default=str)
                            if signature not in seen_tool_calls:
                                seen_tool_calls.add(signature)
                                tool_call_sink(rendered_call)
                if thinking:
                    self.stream_phase = "thinking"
                    thinking_chars += len(thinking)
                    self.current_stats["thinking_chars"] = thinking_chars
                if content:
                    self.stream_phase = "responding"
                    if not first_token_at:
                        first_token_at = time.monotonic()
                    output_chars += len(content)
                    elapsed = max(0.001, time.monotonic() - first_token_at)
                    estimated_tokens = max(1, output_chars // 4)
                    self.current_stats.update(
                        {
                            "eval_count": estimated_tokens,
                            "tokens_per_second": estimated_tokens / elapsed,
                            "time_to_first_token": first_token_at - started,
                        }
                    )
                    yield content
                if event.get("done"):
                    done_seen = True
                    self.last_stats = _completion_stats(
                        event,
                        started=started,
                        first_token_at=first_token_at,
                        thinking_chars=thinking_chars,
                        output_chars=output_chars,
                    )
                    self.current_stats = dict(self.last_stats)
                    break
        finally:
            if not done_seen:
                reason = "cancelled" if cancel_event and cancel_event.is_set() else "stream_ended"
                self.last_stats = _completion_stats(
                    {"done_reason": reason},
                    started=started,
                    first_token_at=first_token_at,
                    thinking_chars=thinking_chars,
                    output_chars=output_chars,
                )
                self.current_stats = dict(self.last_stats)
            self.stream_phase = "idle"
            self.current_model = ""

    def chat(
        self,
        model: str,
        messages: Iterable[Mapping[str, Any]],
        *,
        stream: bool,
        think: bool | str,
        num_ctx: int,
        num_predict: int | None = None,
        cancel_event: Event | None = None,
        extra_options: Mapping[str, Any] | None = None,
        response_format: str | Mapping[str, Any] | None = None,
        keep_alive: str | int | None = None,
        tools: Iterable[Mapping[str, Any]] | None = None,
        tool_call_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if stream:
            chunks: list[str] = []
            for chunk in self.chat_stream(
                model,
                messages,
                think=think,
                num_ctx=num_ctx,
                num_predict=num_predict,
                cancel_event=cancel_event,
                extra_options=extra_options,
                response_format=response_format,
                keep_alive=keep_alive,
                tools=tools,
                tool_call_sink=tool_call_sink,
            ):
                print(chunk, end="", flush=True)
                chunks.append(chunk)
            print()
            return "".join(chunks)
        self.current_model = model
        self.stream_phase = "loading"
        self.last_stats = {}
        started = time.monotonic()
        payload = self.chat_payload(
            model,
            messages,
            stream=False,
            think=think,
            num_ctx=num_ctx,
            num_predict=num_predict,
            extra_options=extra_options,
            response_format=response_format,
            keep_alive=keep_alive,
            tools=tools,
        )
        try:
            response = _request_json(
                "POST", self.url("/api/chat"), payload, timeout=GENERATION_IDLE_TIMEOUT, headers=self.headers
            )
        finally:
            self.stream_phase = "idle"
            self.current_model = ""
        message = response.get("message") if isinstance(response.get("message"), Mapping) else {}
        tool_calls = message.get("tool_calls")
        if tool_call_sink and isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, Mapping):
                    tool_call_sink(dict(call))
        content = str(message.get("content") or "")
        thinking = str(message.get("thinking") or "")
        self.last_stats = _completion_stats(
            response,
            started=started,
            first_token_at=0.0,
            thinking_chars=len(thinking),
            output_chars=len(content),
        )
        self.current_stats = dict(self.last_stats)
        return content

    def pull(self, model: str) -> Iterator[dict[str, Any]]:
        yield from _stream_json(self.url("/api/pull"), {"model": model, "stream": True}, self.headers)

    def delete(self, model: str) -> None:
        _request_json("DELETE", self.url("/api/delete"), {"model": model}, timeout=30, headers=self.headers)
