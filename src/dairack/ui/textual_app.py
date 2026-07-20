from __future__ import annotations

import asyncio
import shlex
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from rich.cells import cell_len
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .. import __version__
from ..bootstrap import InitializationResult, initialize
from ..catalog import BundleRecommendation, catalog_model, installed_role_coverage, recommendation_set
from ..compute import (
    LOCAL_OLLAMA_ENDPOINT,
    ComputeError,
    compute_token,
    probe_compute,
    provider_for_config,
    save_compute_token,
    stored_compute_token,
    validate_compute_endpoint,
)
from ..config import default_config
from ..identity import env_value
from ..model_ops import (
    PullProgress,
    TransferCancelled,
    local_ollama_free_bytes,
    pull_model,
    remove_model,
    validate_model_name,
)
from ..models import CAPABILITY_NAMES, clear_capability_overrides, load_registry, set_capability_override
from ..paths import PATHS
from ..providers.ollama import OllamaError, OllamaProvider
from ..updates import UpdateError, UpdateInfo, apply_update, check_for_update, format_update_command, update_command

PALETTE = {
    "ink": "#090a09",
    "graphite": "#0e100e",
    "surface": "#141611",
    "raised": "#1a1c16",
    "line": "#393a31",
    "line_hot": "#766342",
    "paper": "#ddd8c8",
    "muted": "#969282",
    "quiet": "#888477",
    "amber": "#c4934f",
    "brass": "#a77c45",
    "olive": "#7f9168",
    "teal": "#659087",
    "red": "#c67a70",
    "green": "#78966b",
    "signal_haze": "#554833",
    "signal_peak": "#dfbb78",
    "signal_core": "#211b12",
    "signal_wash": "#181610",
}

UI_TICK_SECONDS = 0.1
SIGNAL_STEP_HZ = 3.2
SIGNAL_STEP_SECONDS = 1.0 / SIGNAL_STEP_HZ
SIGNAL_PULSE_HZ = SIGNAL_STEP_HZ / 4.0
PHASE_GLINT_SECONDS = SIGNAL_STEP_SECONDS * 2.0
FOCUS_GLINT_SECONDS = SIGNAL_STEP_SECONDS * 2.0
COMPLETION_GLINT_SECONDS = SIGNAL_STEP_SECONDS * 3.0
WORDMARK_REVEAL_HZ = 11.5
WELCOME_SETTLE_SECONDS = 1.4
STREAM_RENDER_INTERVAL = 0.04
WELCOME_WORDMARK = "DAIRACK"
WELCOME_GLYPHS = (
    ("├─╮", "│ │", "├─╯"),
    ("╭─╮", "├─┤", "╵ ╵"),
    (" ╷ ", " │ ", " ╵ "),
    ("╭─╮", "├─╯", "╵ ╲"),
    ("╭─╮", "├─┤", "╵ ╵"),
    ("╭─╴", "│  ", "╰─╴"),
    ("╷ ╱", "├╴ ", "╵ ╲"),
)


def mix_color(start: str, end: str, amount: float) -> str:
    """Interpolate two RGB colors without introducing an animation dependency."""
    level = max(0.0, min(1.0, amount))
    left = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    right = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    channels = tuple(round(a + (b - a) * level) for a, b in zip(left, right, strict=True))
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def signal_envelope(age: float, duration: float) -> float:
    """Fast attack and controlled decay for one-shot interface feedback."""
    if age < 0.0 or duration <= 0.0 or age >= duration:
        return 0.0
    phase = age / duration
    if phase < 0.22:
        attack = phase / 0.22
        return attack * attack * (3.0 - 2.0 * attack)
    decay = (phase - 0.22) / 0.78
    return (1.0 - decay) ** 2


def append_signal_track(target: Text, width: int, cursor: int, *, wrap: bool = True) -> None:
    """Render a fixed-width light packet with a restrained spatial falloff."""
    width = max(1, width)
    cursor = cursor % width if wrap else max(0, min(width - 1, cursor))
    for index in range(width):
        direct = abs(index - cursor)
        distance = min(direct, width - direct) if wrap else direct
        if distance == 0:
            target.append("=", style=f"bold {PALETTE['signal_peak']} on {PALETTE['signal_core']}")
        elif distance == 1:
            target.append("-", style=f"{PALETTE['amber']} on {PALETTE['signal_wash']}")
        elif distance == 2:
            target.append("-", style=PALETTE["line_hot"])
        elif distance == 3 and width >= 12:
            target.append("-", style=PALETTE["signal_haze"])
        else:
            target.append("-", style=PALETTE["line"])


def signal_pulse(elapsed: float, reduced_motion: bool) -> tuple[str, str]:
    if reduced_motion:
        return "•", PALETTE["amber"]
    frames = (
        ("·", PALETTE["signal_haze"]),
        ("•", PALETTE["brass"]),
        ("●", PALETTE["signal_peak"]),
        ("•", PALETTE["amber"]),
    )
    return frames[int(max(0.0, elapsed) * SIGNAL_PULSE_HZ * len(frames)) % len(frames)]


LOCKED_THEME = Theme(
    name="dairack-locked",
    primary=PALETTE["amber"],
    secondary=PALETTE["teal"],
    warning=PALETTE["brass"],
    error=PALETTE["red"],
    success=PALETTE["green"],
    accent=PALETTE["amber"],
    foreground=PALETTE["paper"],
    background=PALETTE["ink"],
    surface=PALETTE["surface"],
    panel=PALETTE["raised"],
    dark=True,
    luminosity_spread=0.08,
    text_alpha=0.96,
)


COMMAND_DESCRIPTIONS = {
    "/help": "Command reference",
    "/model": "Switch the active model",
    "/coordinator": "Configure adaptive multi-model coordination",
    "/orchestrator": "Alias for /coordinator",
    "/route": "Inspect the coordinator's last decision",
    "/compute": "Inspect or change the model compute server",
    "/hardware": "Distinguish client and compute hardware",
    "/image": "Attach visual input to the next prompt",
    "/images": "Inspect staged image attachments",
    "/detach": "Remove a staged image attachment",
    "/library": "Open the active compute model library",
    "/models": "Compatibility alias for /library",
    "/profile": "Tune the selected model profile",
    "/profiles": "Inspect hardware-tuned model profiles",
    "/pull": "Download an Ollama model",
    "/ctx": "Show or set request context tokens",
    "/chats": "Open saved conversations",
    "/resume": "Resume a saved conversation",
    "/new": "Start a clean conversation",
    "/save": "Save or rename this conversation",
    "/context": "Inspect active context pressure",
    "/compact": "Summarize older conversation turns",
    "/autocompact": "Configure automatic compaction",
    "/permissions": "Change the action approval policy",
    "/allow": "Approve the pending action",
    "/deny": "Reject the pending action",
    "/agent": "Enable or disable agent actions",
    "/think": "Enable or disable model thinking",
    "/reset": "Clear this conversation history",
    "/pwd": "Show the working directory",  # pragma: allowlist secret
    "/cd": "Change the working directory",
    "/index": "Build local project memory",
    "/find": "Search local project memory",
    "/symbols": "Search indexed symbols",
    "/deps": "Inspect indexed dependencies",
    "/repo": "Show the current repository profile",
    "/tests": "Show detected test commands",
    "/test": "Run a detected or explicit test",
    "/read": "Read a file with line numbers",
    "/ls": "List a directory",
    "/diff": "Show the current Git diff",
    "/undo": "Restore an Dairack edit checkpoint",
    "/checkpoints": "List available edit checkpoints",
    "/search": "Search files in the current project",
    "/open": "Open a text file around a line",
    "/web": "Search the public internet",
    "/url": "Read a web page",
    "/run": "Run an explicit shell command",
    "/copy": "Copy the complete transcript",
    "/config": "Show the active configuration",
    "/update": "Check for an Dairack software release",
    "/exit": "Leave Dairack",
    "/quit": "Leave Dairack",
}


def clip_middle(value: str, width: int) -> str:
    value = value.replace("\n", " ").strip()
    if width <= 3:
        return value[:width]
    if len(value) <= width:
        return value
    left = max(1, (width - 3) // 2)
    return value[:left] + "..." + value[-(width - left - 3) :]


def short_number(value: int | float) -> str:
    number = float(value)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}m"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(int(number))


def elapsed_text(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:0.1f}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


def width_band(width: int) -> str:
    if width < 52:
        return "narrow"
    if width < 96:
        return "standard"
    return "wide"


def clip_right(value: str, width: int) -> str:
    value = value.replace("\n", " ").strip()
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


def clip_model(value: str, width: int) -> str:
    value = value.replace("\n", " ").strip()
    if len(value) <= width:
        return value
    if width <= 3 or ":" not in value:
        return clip_middle(value, width)
    family, tag = value.rsplit(":", 1)
    suffix = ":" + tag
    family_width = width - len(suffix) - 3
    if family_width < 2:
        return clip_middle(value, width)
    return family[:family_width] + "..." + suffix


def archive_time(value: str, now: datetime | None = None) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return clip_right(value.replace("T", " "), 16).upper()
    current = now or datetime.now(timestamp.tzinfo)
    if timestamp.date() == current.date():
        return f"TODAY {timestamp:%H:%M}"
    if timestamp.date() == (current - timedelta(days=1)).date():
        return f"YESTERDAY {timestamp:%H:%M}"
    if timestamp.year == current.year:
        return timestamp.strftime("%d %b %H:%M").upper()
    return timestamp.strftime("%d %b %Y").upper()


def apply_modal_responsive_classes(screen: ModalScreen[Any]) -> None:
    screen.set_class(screen.size.width < 52, "compact")
    screen.set_class(screen.size.height < 22, "short")


def styled_line_with_urls(line: str, base_style: str = "") -> Text:
    result = Text(style=base_style)
    cursor = 0
    import re

    for match in re.finditer(r"https?://[^\s)\]>]+", line):
        result.append(line[cursor : match.start()])
        url = match.group(0)
        result.append(url, style=f"underline {PALETTE['teal']} link {url}")
        cursor = match.end()
    result.append(line[cursor:])
    return result


def system_renderable(source: str, severity: str = "info") -> Text:
    rendered = Text()
    for index, line in enumerate(source.splitlines() or [""]):
        lowered = line.lower().strip()
        if severity == "error":
            part = styled_line_with_urls(line, f"bold {PALETTE['red']}" if index == 0 else PALETTE["red"])
        elif severity == "warning":
            part = styled_line_with_urls(line, f"bold {PALETTE['brass']}" if index == 0 else PALETTE["muted"])
        elif severity == "success":
            part = styled_line_with_urls(line, f"bold {PALETTE['olive']}" if index == 0 else PALETTE["muted"])
        elif line.startswith("$ "):
            part = styled_line_with_urls(line, f"bold {PALETTE['amber']}")
        elif line.startswith("[exit "):
            style = PALETTE["green"] if line.startswith("[exit 0]") else PALETTE["red"]
            part = Text(line, style=f"bold {style}")
        elif lowered.startswith(("action approved", "action read-auto", "checkpoint:")):
            part = styled_line_with_urls(line, PALETTE["olive"])
        elif lowered.startswith(("internet search:", "url:", "title:")):
            part = styled_line_with_urls(line, PALETTE["brass"])
        elif lowered.startswith(("usage:", "permissions:", "model set", "resumed chat", "new chat")):
            part = styled_line_with_urls(line, PALETTE["amber"])
        else:
            part = styled_line_with_urls(line, PALETTE["muted"])
        rendered.append_text(part)
        if index < len(source.splitlines()) - 1:
            rendered.append("\n")
    return rendered


def action_renderable(source: str) -> Text:
    rendered = Text()
    lines = source.splitlines() or [""]
    labels = ("QUERY", "URL", "PATH", "TASK", "IMAGE", "CHANGE", "ACCESS", "REASON")
    for index, line in enumerate(lines):
        if index == 0:
            failed = any(
                state in line for state in ("FAILED", "INTERRUPTED", "TIMED OUT", "DENIED", "BLOCKED", "NOT RUN")
            )
            style = f"bold {PALETTE['red'] if failed else PALETTE['olive']}"
            part = styled_line_with_urls(line, style)
        elif line.startswith("$ "):
            part = styled_line_with_urls(line, f"bold {PALETTE['amber']}")
        elif line == "RESULT":
            part = Text(line, style=f"bold {PALETTE['brass']}")
        elif line.startswith(labels) and "  " in line:
            label, value = line.split("  ", 1)
            part = Text(f"{label}  ", style=f"bold {PALETTE['quiet']}")
            part.append_text(styled_line_with_urls(value, PALETTE["paper"]))
        else:
            part = styled_line_with_urls(line, PALETTE["muted"])
        rendered.append_text(part)
        if index < len(lines) - 1:
            rendered.append("\n")
    return rendered


def coordinator_renderable(source: str) -> Text:
    rendered = Text()
    lines = source.splitlines() or [""]
    for index, line in enumerate(lines):
        if line.startswith("DELEGATION  "):
            style = f"bold {PALETTE['amber']}"
        elif line.startswith("FLOW  "):
            style = f"bold {PALETTE['teal']}"
        elif line.startswith("STATE  "):
            style = f"bold {PALETTE['red'] if 'FAILED' in line or 'INTERRUPTED' in line else PALETTE['olive']}"
        elif line.startswith("FIT  "):
            style = PALETTE["brass"]
        elif line.startswith(("TASK  ", "INPUT  ")):
            style = PALETTE["muted"]
        elif line == "EVIDENCE":
            style = f"bold {PALETTE['brass']}"
        else:
            style = PALETTE["paper"]
        rendered.append(line, style=style)
        if index < len(lines) - 1:
            rendered.append("\n")
    return rendered


def diff_renderable(source: str) -> Text:
    rendered = Text()
    lines = source.splitlines() or [""]
    for index, line in enumerate(lines):
        if line.startswith("+") and not line.startswith("+++"):
            style = f"{PALETTE['green']} on #101710"
        elif line.startswith("-") and not line.startswith("---"):
            style = f"{PALETTE['red']} on #1a1010"
        elif line.startswith("@@"):
            style = f"bold {PALETTE['teal']}"
        elif line.startswith(("diff --git", "---", "+++")):
            style = f"bold {PALETTE['amber']}"
        else:
            style = PALETTE["muted"]
        rendered.append(line, style=style)
        if index < len(lines) - 1:
            rendered.append("\n")
    return rendered


class Composer(TextArea):
    class Submitted(Message):
        def __init__(self, composer: "Composer", text: str) -> None:
            super().__init__()
            self.composer = composer
            self.text = text

        @property
        def control(self) -> "Composer":
            return self.composer

    class History(Message):
        def __init__(self, composer: "Composer", delta: int) -> None:
            super().__init__()
            self.composer = composer
            self.delta = delta

    BINDINGS = [
        Binding("enter", "submit", "Send", show=False, priority=True),
        Binding("shift+enter,alt+enter,ctrl+j", "newline", "New line", show=False, priority=True),
        Binding("escape", "interrupt", "Stop", show=False, priority=True),
        Binding("ctrl+up", "history(-1)", "Previous prompt", show=False),
        Binding("ctrl+down", "history(1)", "Next prompt", show=False),
    ]

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self, self.text))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history(self, delta: int) -> None:
        self.post_message(self.History(self, delta))

    def action_interrupt(self) -> None:
        self.app.action_escape()


class TranscriptEntry(Vertical):
    def __init__(
        self,
        role: str,
        text: str,
        index: int,
        severity: str = "info",
        kind: str = "message",
    ) -> None:
        severity = severity if severity in {"info", "success", "warning", "error"} else "info"
        kind = kind if kind == "reference" else "message"
        super().__init__(classes=f"entry role-{role} severity-{severity} kind-{kind}", id=f"entry-{index}")
        self.role = role
        self.severity = severity
        self.kind = kind
        self.source_text = text
        self._label = Static(self.role_label, classes="entry-label")
        if self.role in {"you", "assistant"}:
            self._body: Markdown | Static = Markdown(
                self.source_text or " ",
                classes="entry-markdown",
                open_links=True,
            )
        elif self.role == "diff":
            self._body = Static(diff_renderable(self.source_text), classes="entry-plain diff-content")
        elif self.role == "action":
            self._body = Static(action_renderable(self.source_text), classes="entry-plain action-content")
        elif self.role == "coordinator":
            self._body = Static(
                coordinator_renderable(self.source_text),
                classes="entry-plain coordinator-content",
            )
        else:
            self._body = Static(
                system_renderable(self.source_text, self.severity),
                classes="entry-plain system-content",
            )

    @property
    def role_label(self) -> str:
        if self.role == "system" and self.severity == "error":
            return "ERROR"
        if self.role == "system" and self.severity == "warning":
            return "NOTICE"
        if self.role == "system" and self.severity == "success":
            return "STATUS"
        return {
            "you": "YOU",
            "assistant": "DAIRACK",
            "action": "ACTION",
            "coordinator": "COORDINATOR",
            "system": "SYSTEM",
            "diff": "PATCH",
        }.get(self.role, self.role.upper())

    def compose(self) -> ComposeResult:
        yield self._label
        if self.role == "diff":
            with ScrollableContainer(classes="entry-code-scroll"):
                yield self._body
        else:
            yield self._body

    async def _apply_source_text(self) -> None:
        if isinstance(self._body, Markdown):
            await self._body.update(self.source_text or " ")
        elif self.role == "diff":
            self._body.update(diff_renderable(self.source_text))
        elif self.role == "action":
            self._body.update(action_renderable(self.source_text))
        elif self.role == "coordinator":
            self._body.update(coordinator_renderable(self.source_text))
        else:
            self._body.update(system_renderable(self.source_text, self.severity))

    async def set_text(self, value: str) -> None:
        self.source_text = value
        if self._body.is_mounted:
            await self._apply_source_text()
        elif isinstance(self._body, Markdown):
            self._body._initial_markdown = value or " "
        elif self.role == "diff":
            self._body.update(diff_renderable(value))
        elif self.role == "action":
            self._body.update(action_renderable(value))
        elif self.role == "coordinator":
            self._body.update(coordinator_renderable(value))
        else:
            self._body.update(system_renderable(value, self.severity))


class SelectorScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        detail: str,
        options: list[tuple[str, Text]],
        highlighted: int = 0,
        family: str = "standard",
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.dialog_detail = detail
        self.options = options
        self.highlighted = max(0, min(highlighted, max(0, len(options) - 1)))
        self.family = family if family in {"standard", "library", "archive"} else "standard"

    def compose(self) -> ComposeResult:
        option_widgets = [
            Option(prompt, id=option_id, disabled=option_id.startswith("__heading__:"))
            for option_id, prompt in self.options
        ]
        with Vertical(id="selector-dialog", classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title")
            yield Static(self.dialog_detail, classes="dialog-detail")
            yield OptionList(*option_widgets, id="selector-options")
            yield Static("UP/DOWN MOVE   ENTER SELECT   ESC CANCEL", classes="dialog-keys")

    def _fit_dialog(self) -> None:
        explicit_rows = sum(max(1, prompt.plain.count("\n") + 1) for _, prompt in self.options)
        detail_rows = max(
            2, min(4, (cell_len(self.dialog_detail) + max(24, self.size.width - 8) - 1) // max(24, self.size.width - 8))
        )
        desired = max(12, explicit_rows + detail_rows + 7)
        self.query_one("#selector-dialog", Vertical).styles.height = min(
            desired,
            max(10, self.size.height - 4),
            40,
        )

    def _update_key_legend(self) -> None:
        if self.size.width < 38:
            value = "UP/DOWN   ENTER   ESC"
        elif self.size.width < 60:
            value = "UP/DOWN   ENTER SELECT   ESC CANCEL"
        else:
            value = "UP/DOWN MOVE   ENTER SELECT   ESC CANCEL"
        self.query_one(".dialog-keys", Static).update(value)

    def on_mount(self) -> None:
        self.set_class(True, f"family-{self.family}")
        apply_modal_responsive_classes(self)
        self._fit_dialog()
        self._update_key_legend()
        options = self.query_one("#selector-options", OptionList)
        options.highlighted = self.highlighted if self.options else None
        options.focus()

    def on_resize(self, _event: Any) -> None:
        apply_modal_responsive_classes(self)
        self._fit_dialog()
        self._update_key_legend()

    @on(OptionList.OptionSelected, "#selector-options")
    def select_option(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ApprovalScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False, priority=True),
        Binding("d", "deny", "Deny", show=False),
        Binding("a", "approve", "Allow once", show=False),
        Binding("r", "read_auto", "Trust reads", show=False),
    ]

    def __init__(self, core: Any, call: dict[str, str], allow_read_auto: bool) -> None:
        super().__init__()
        self.core = core
        self.call = call
        self.allow_read_auto = allow_read_auto and bool(core.is_read_only_tool_call(call))

    @property
    def presentation(self) -> dict[str, Any]:
        return self.core.tool_presentation(self.call)

    @property
    def deny_first(self) -> bool:
        return str(self.presentation.get("risk") or "read").upper() not in {"READ", "COORDINATOR"}

    def approve_label(self, compact: bool = False) -> str:
        if compact:
            return "ALLOW"
        return {
            "shell": "RUN COMMAND",
            "patch": "APPLY PATCH",
            "read_file": "ALLOW READ",
            "list_dir": "ALLOW READ",
            "find_paths": "ALLOW SEARCH",
            "hardware_status": "ALLOW READ",
            "search_project": "ALLOW SEARCH",
            "index_project": "BUILD INDEX",
            "web_search": "SEARCH WEB",
            "web_open": "OPEN URL",
        }.get(str(self.call.get("name") or ""), "ALLOW ONCE")

    def compose(self) -> ComposeResult:
        name = self.call.get("name") or "unknown"
        presentation = self.presentation
        risk = str(presentation["risk"]).upper()
        reason = self.call.get("reason") or "No reason supplied by the model."
        title = Text(str(presentation["display_name"]).upper() + "  ", style=f"bold {PALETTE['paper']}")
        risk_color = (
            PALETTE["red"]
            if risk in {"WRITE", "SYSTEM"}
            else PALETTE["brass"]
            if risk in {"NETWORK", "LOCAL"}
            else PALETTE["teal"]
            if risk == "COORDINATOR"
            else PALETTE["olive"]
        )
        title.append(risk, style=f"bold {risk_color}")

        if name == "patch":
            preview = diff_renderable(self.call.get("patch", ""))
        elif name == "shell":
            preview = Text("$ " + self.call.get("cmd", ""), style=f"bold {PALETTE['amber']}")
        else:
            target = str(presentation.get("target") or "")
            preview = Text(f"{presentation['target_label']}  ", style=f"bold {PALETTE['quiet']}")
            preview.append_text(styled_line_with_urls(target or "(default)", PALETTE["paper"]))

        with Vertical(id="approval-dialog", classes="dialog"):
            yield Static(title, classes="dialog-title")
            yield Static(
                Text("REASON  ", style=PALETTE["quiet"]) + Text(reason, style=PALETTE["paper"]),
                classes="approval-reason",
            )
            with ScrollableContainer(id="approval-preview"):
                yield Static(preview, classes="approval-code")
            with Horizontal(id="approval-actions"):
                if self.deny_first:
                    yield Button("DENY", id="deny", variant="default")
                yield Button(self.approve_label(), id="approve", variant="primary")
                if not self.deny_first:
                    yield Button("DENY", id="deny", variant="default")
                if self.allow_read_auto:
                    yield Button("TRUST READS", id="read-auto", variant="default")
            yield Static(
                "A ALLOW ONCE   D DENY" + ("   R TRUST READS" if self.allow_read_auto else "") + "   ENTER CHOOSE",
                classes="dialog-keys",
            )

    def _update_key_legend(self) -> None:
        if self.size.width < 34:
            value = "A ALLOW   D DENY   ENTER"
        elif self.size.width < 60:
            value = "A ALLOW   D DENY" + ("   R READS" if self.allow_read_auto else "") + "   ENTER"
        else:
            value = "A ALLOW ONCE   D DENY" + ("   R TRUST READS" if self.allow_read_auto else "") + "   ENTER CHOOSE"
        self.query_one(".dialog-keys", Static).update(value)

    def _update_action_labels(self) -> None:
        compact = self.size.width < 40
        self.query_one("#approve", Button).label = self.approve_label(compact)
        if self.allow_read_auto:
            self.query_one("#read-auto", Button).label = "READS" if compact else "TRUST READS"

    def on_mount(self) -> None:
        apply_modal_responsive_classes(self)
        self._update_key_legend()
        self._update_action_labels()
        self.query_one("#deny" if self.deny_first else "#approve", Button).focus()

    def on_resize(self, _event: Any) -> None:
        apply_modal_responsive_classes(self)
        self._update_key_legend()
        self._update_action_labels()

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss("approve")
        elif event.button.id == "read-auto":
            self.dismiss("read-auto")
        else:
            self.dismiss("deny")

    def action_approve(self) -> None:
        self.dismiss("approve")

    def action_deny(self) -> None:
        self.dismiss("deny")

    def action_read_auto(self) -> None:
        if self.allow_read_auto:
            self.dismiss("read-auto")


class TextInputScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        detail: str,
        placeholder: str = "",
        value: str = "",
        *,
        password: bool = False,
    ) -> None:
        super().__init__()
        self.dialog_title = title
        self.dialog_detail = detail
        self.placeholder = placeholder
        self.value = value
        self.password = password

    def compose(self) -> ComposeResult:
        with Vertical(id="input-dialog", classes="dialog"):
            yield Static(self.dialog_title, classes="dialog-title")
            yield Static(self.dialog_detail, classes="dialog-detail input-detail")
            yield Input(value=self.value, placeholder=self.placeholder, password=self.password, id="modal-input")
            yield Static("ENTER CONFIRM   ESC CANCEL", classes="dialog-keys")

    def on_mount(self) -> None:
        apply_modal_responsive_classes(self)
        field = self.query_one("#modal-input", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_resize(self, _event: Any) -> None:
        apply_modal_responsive_classes(self)

    @on(Input.Submitted, "#modal-input")
    def submit(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelTransferScreen(ModalScreen[dict[str, Any]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("enter", "accept", "Done", show=False, priority=True),
    ]

    def __init__(self, provider: Any, models: list[str]) -> None:
        super().__init__()
        self.provider = provider
        self.models = models
        self.cancel_event = threading.Event()
        self.started = 0.0
        self.complete = False
        self.succeeded = False
        self.error = ""
        self.active_index = 0
        self.progress: PullProgress | None = None
        self.pulse = 0
        self._thread: threading.Thread | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="transfer-dialog", classes="dialog"):
            yield Static("MODEL TRANSFER", classes="dialog-title")
            yield Static(self._plan_detail(), classes="transfer-plan")
            yield Static(id="transfer-model")
            yield Static(id="transfer-meter")
            yield Static(id="transfer-status")
            with Horizontal(id="transfer-actions"):
                yield Button("CANCEL", id="transfer-action", variant="default")
            yield Static("ESC CANCEL", id="transfer-keys", classes="dialog-keys")

    def _plan_detail(self, compact: bool = False) -> str:
        if compact:
            label = "1 MODEL" if len(self.models) == 1 else f"{len(self.models)} MODELS"
            return f"{label}  /  EXISTING LAYERS REUSED"
        if len(self.models) == 1:
            return "Installing or updating one Ollama model. Existing data is reused when unchanged."
        return f"Installing or updating {len(self.models)} models in sequence. Existing layers are reused."

    def _update_plan_detail(self) -> None:
        compact = self.size.width < 60 or self.size.height < 22
        self.query_one(".transfer-plan", Static).update(self._plan_detail(compact))

    def on_mount(self) -> None:
        apply_modal_responsive_classes(self)
        self._update_plan_detail()
        self.started = time.monotonic()
        self.set_interval(UI_TICK_SECONDS, self._tick, name="model-transfer-feedback")
        self._render_progress()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="dairack-model-transfer")
        self._thread.start()

    def on_unmount(self) -> None:
        self.cancel_event.set()

    def _post(self, callback: Any, *args: Any) -> None:
        try:
            self.app.call_from_thread(callback, *args)
        except RuntimeError:
            pass

    def on_resize(self, _event: Any) -> None:
        apply_modal_responsive_classes(self)
        self._update_plan_detail()

    def _worker(self) -> None:
        try:
            for index, model in enumerate(self.models):
                self.active_index = index
                pull_model(
                    self.provider,
                    model,
                    cancel_event=self.cancel_event,
                    on_progress=lambda value: self._post(self._receive_progress, value),
                )
            self._post(self._finish, True, "")
        except TransferCancelled:
            self._post(self._finish, False, "cancelled")
        except Exception as exc:
            self._post(self._finish, False, str(exc))

    def _receive_progress(self, progress: PullProgress) -> None:
        self.progress = progress
        self._render_progress()

    def _overall_percent(self) -> float | None:
        if self.complete and self.succeeded:
            return 1.0
        if not self.models or not self.progress or self.progress.percent is None:
            return None
        return min(1.0, (self.active_index + self.progress.percent) / len(self.models))

    def _meter(self, width: int = 44) -> Text:
        percent = self._overall_percent()
        result = Text("  ")
        if percent is None:
            append_signal_track(result, width, self.pulse % width)
            result.append("   --.-%", style=PALETTE["quiet"])
            return result
        filled = round(width * percent)
        result.append("=" * filled, style=PALETTE["amber"])
        result.append("-" * (width - filled), style=PALETTE["line"])
        result.append(f"   {percent * 100:5.1f}%", style=f"bold {PALETTE['paper']}")
        return result

    def _render_progress(self) -> None:
        if not self.is_mounted:
            return
        model = self.models[min(self.active_index, len(self.models) - 1)] if self.models else ""
        prefix = f"{self.active_index + 1}/{len(self.models)}  " if len(self.models) > 1 else ""
        self.query_one("#transfer-model", Static).update(
            Text(prefix, style=PALETTE["quiet"]) + Text(model, style=f"bold {PALETTE['paper']}")
        )
        self.query_one("#transfer-meter", Static).update(self._meter(max(18, min(44, self.size.width - 20))))
        elapsed = time.monotonic() - self.started
        if self.complete:
            status = "READY" if self.succeeded else "CANCELLED" if self.error == "cancelled" else "FAILED"
            detail = (
                self.error
                if self.error and self.error != "cancelled"
                else "Transfer complete"
                if self.succeeded
                else "No further data will be downloaded"
            )
            color = (
                PALETTE["olive"]
                if self.succeeded
                else PALETTE["red"]
                if self.error != "cancelled"
                else PALETTE["brass"]
            )
            value = Text(f"{status}  ", style=f"bold {color}") + Text(detail, style=PALETTE["muted"])
        elif self.cancel_event.is_set():
            value = Text("CANCELLING  ", style=f"bold {PALETTE['brass']}") + Text(
                "closing the active transfer safely", style=PALETTE["muted"]
            )
        else:
            status = self.progress.status if self.progress else "contacting Ollama"
            amount = ""
            if self.progress and self.progress.total:
                amount = f"  {self.progress.completed / 1024**3:.1f}/{self.progress.total / 1024**3:.1f} GiB"
            value = Text("WORKING  ", style=f"bold {PALETTE['amber']}") + Text(
                f"{status}{amount}  {elapsed:.1f}s", style=PALETTE["muted"]
            )
        self.query_one("#transfer-status", Static).update(value)

    def _tick(self) -> None:
        self.pulse = int(max(0.0, time.monotonic() - self.started) * SIGNAL_STEP_HZ)
        self._render_progress()

    def _finish(self, succeeded: bool, error: str) -> None:
        self.complete = True
        self.succeeded = succeeded
        self.error = error
        button = self.query_one("#transfer-action", Button)
        button.label = "DONE" if succeeded else "CLOSE"
        button.variant = "primary" if succeeded else "default"
        self.query_one("#transfer-keys", Static).update("ENTER CLOSE   ESC CLOSE")
        button.focus()
        self._render_progress()

    @on(Button.Pressed, "#transfer-action")
    def transfer_action(self) -> None:
        if self.complete:
            self.action_accept()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        if self.complete:
            self.action_accept()
            return
        self.cancel_event.set()
        self._render_progress()

    def action_accept(self) -> None:
        if self.complete:
            self.dismiss(
                {
                    "success": self.succeeded,
                    "cancelled": self.error == "cancelled",
                    "error": self.error,
                    "models": self.models,
                }
            )


DAIRACK_CSS = """
Screen {
    background: #090a09;
    color: #ddd8c8;
    layout: vertical;
}

#topbar {
    height: 1;
    padding: 0 1;
    background: #1a1c16;
    color: #ddd8c8;
}

#metabar {
    height: 1;
    padding: 0 1;
    background: #0e100e;
    color: #969282;
}

#transcript {
    height: 1fr;
    padding: 1 2 0 2;
    background: #090a09;
    scrollbar-background: #0e100e;
    scrollbar-size-vertical: 1;
    scrollbar-color: #4a493e;
    scrollbar-color-hover: #766342;
    scrollbar-color-active: #c4934f;
}

#empty-state {
    width: 100%;
    height: 100%;
    min-height: 10;
    padding: 1 2;
    color: #888477;
    content-align: center middle;
    text-align: center;
}

TranscriptEntry {
    height: auto;
    min-height: 2;
    margin: 0 0 1 0;
    padding: 0 1 0 1;
    background: transparent;
    border-left: solid #393a31;
}

TranscriptEntry.role-you { border-left: solid #a77c45; }
TranscriptEntry.role-assistant { border-left: solid #c4934f; }
TranscriptEntry.role-system { border-left: solid #545348; }
TranscriptEntry.role-system.severity-error { border-left: solid #c67a70; }
TranscriptEntry.role-system.severity-warning { border-left: solid #a77c45; }
TranscriptEntry.role-system.severity-success { border-left: solid #7f9168; }
TranscriptEntry.role-system.kind-reference { border-left: solid #393a31; }
TranscriptEntry.role-action { border-left: solid #659087; }
TranscriptEntry.role-diff { border-left: solid #7f9168; }
TranscriptEntry.role-coordinator { border-left: solid #5e817d; }

.entry-label {
    height: 1;
    width: auto;
    color: #888477;
    text-style: bold;
}

.role-you .entry-label { color: #a77c45; }
.role-assistant .entry-label { color: #c4934f; }
.role-system .entry-label { color: #888477; }
.role-system.severity-error .entry-label { color: #c67a70; }
.role-system.severity-warning .entry-label { color: #a77c45; }
.role-system.severity-success .entry-label { color: #7f9168; }
.role-system.kind-reference .entry-label { color: #6f6d63; }
.role-action .entry-label { color: #659087; }
.role-diff .entry-label { color: #7f9168; }
.role-coordinator .entry-label { color: #759995; }

.entry-markdown {
    height: auto;
    padding: 0;
    margin: 0;
    color: #ddd8c8;
    background: transparent;
    max-width: 108;
}

.role-you .entry-markdown { color: #c9c4b5; }

.entry-plain {
    height: auto;
    padding: 0;
    color: #969282;
    background: transparent;
}

.system-content { max-width: 120; }
.coordinator-content { max-width: 112; }
.action-content { max-width: 120; }
.entry-code-scroll {
    width: 100%;
    height: auto;
    max-height: 30;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-size-horizontal: 1;
    scrollbar-background: #0e100e;
    scrollbar-color: #545348;
}
.diff-content {
    width: auto;
    min-width: 100%;
    text-wrap: nowrap;
}

TranscriptEntry MarkdownBlock { color: #ddd8c8; }
TranscriptEntry MarkdownParagraph { margin: 0 0 1 0; }
TranscriptEntry MarkdownH1,
TranscriptEntry MarkdownH2,
TranscriptEntry MarkdownH3,
TranscriptEntry MarkdownH4 {
    margin: 0 0 1 0;
    color: #d2b47b;
    text-style: bold;
}
TranscriptEntry MarkdownH1 { border-bottom: solid #393a31; }
TranscriptEntry MarkdownBlockQuote {
    border-left: solid #545348;
    color: #aaa595;
    padding-left: 1;
}
TranscriptEntry MarkdownFence {
    margin: 1 0;
    padding: 0;
    color: #d5d0c0;
    background: #11130f;
    border-left: solid #766342;
    scrollbar-size-horizontal: 0;
}
TranscriptEntry MarkdownFence > Label { padding: 1 2; }
TranscriptEntry MarkdownBlock > .code_inline {
    color: #d2b47b;
    background: #1a1c16;
    text-style: bold;
}

#activity {
    height: 1;
    padding: 0 1;
    color: #969282;
    background: #0e100e;
}

#composer-shell {
    height: 6;
    min-height: 5;
    max-height: 11;
    padding: 0 1;
    background: #11130f;
    border-top: solid #393a31;
}

#composer-title {
    height: 1;
    color: #a77c45;
    text-style: bold;
}

#attachment-bar {
    display: none;
    height: 1;
    padding: 0 1;
    color: #969282;
    background: #0e100e;
    border-left: solid #659087;
}

Screen.has-attachments #attachment-bar { display: block; }

#composer {
    height: 3;
    min-height: 2;
    max-height: 7;
    padding: 0 1;
    border: none;
    border-left: solid #151711;
    background: #151711;
    color: #e1dccb;
    scrollbar-size-vertical: 1;
    scrollbar-background: #151711;
    scrollbar-color: #545348;
}

#composer:focus {
    background: #181a14;
    border-left: solid #c4934f;
}

#composer > .text-area--cursor { color: #090a09; background: #c4934f; }
#composer > .text-area--cursor-line { background: transparent; }
#composer > .text-area--selection { background: #766342 55%; }
#composer > .text-area--placeholder { color: #888477; }

#composer-meta {
    height: 1;
    padding: 0 1;
    color: #888477;
}

#keybar {
    height: 1;
    padding: 0 1;
    background: #1a1c16;
    color: #888477;
}

.dialog {
    width: 92%;
    max-width: 104;
    height: 78%;
    max-height: 38;
    padding: 0 1;
    background: #11130f;
    border: tall #545348;
}

SelectorScreen .dialog { max-width: 88; }
SelectorScreen.family-library .dialog,
SelectorScreen.family-archive .dialog { max-width: 96; }
ApprovalScreen .dialog { max-width: 104; }
TextInputScreen .dialog,
ModelTransferScreen .dialog { max-width: 88; }

SelectorScreen,
ApprovalScreen,
TextInputScreen,
ModelTransferScreen {
    align: center middle;
    background: #000000 68%;
}

.dialog-title {
    height: 2;
    padding: 0 1;
    content-align: left middle;
    color: #ddd8c8;
    background: #1a1c16;
    text-style: bold;
}

.dialog-detail {
    height: auto;
    min-height: 2;
    max-height: 4;
    padding: 0 1;
    color: #969282;
    content-align: left middle;
}

.dialog-keys {
    height: 1;
    padding: 0 1;
    color: #888477;
    background: #0e100e;
    text-style: bold;
}

#selector-options {
    height: 1fr;
    background: #0e100e;
    border: none;
    scrollbar-background: #0e100e;
    scrollbar-color: #545348;
}

#selector-options > .option-list--option {
    padding: 0 1;
    color: #b8b3a4;
    text-wrap: nowrap;
    border-left: solid #0e100e;
}
#selector-options > .option-list--option-highlighted {
    padding: 0 1;
    color: #e5dfcd;
    background: #2a261c;
    text-style: bold;
    border-left: solid #c4934f;
}
#selector-options > .option-list--option-hover { background: #1a1c16; }
#selector-options > .option-list--option-disabled {
    color: #6f6d63;
    background: #0e100e;
    text-style: bold;
}

#approval-dialog { height: 72%; max-height: 34; }
.approval-reason { height: auto; min-height: 2; padding: 1; }
#approval-preview {
    height: 1fr;
    padding: 1;
    background: #0b0c0a;
    border: solid #393a31;
    scrollbar-background: #0b0c0a;
    scrollbar-color: #545348;
    scrollbar-size-horizontal: 1;
    scrollbar-size-vertical: 1;
    overflow-x: auto;
    overflow-y: auto;
}
.approval-code { width: auto; min-width: 100%; height: auto; color: #c9c4b5; text-wrap: nowrap; }
#approval-actions { height: 3; align-horizontal: right; padding: 0 1; }
#approval-actions Button {
    min-width: 14;
    height: 3;
    margin-left: 1;
    border: none;
    background: #24251e;
    color: #b8b3a4;
}
#approval-actions Button:focus {
    background: #c4934f;
    color: #090a09;
    text-style: bold;
}

#input-dialog {
    height: 12;
    max-height: 12;
}

.input-detail {
    height: 3;
    padding-top: 1;
}

#modal-input {
    height: 3;
    margin: 1 1;
    padding: 0 1;
    border: tall #393a31;
    background: #151711;
    color: #e1dccb;
}

#modal-input:focus {
    border: tall #c4934f;
}

#transfer-dialog {
    height: 19;
    max-height: 19;
    max-width: 88;
}

.transfer-plan {
    height: 3;
    padding: 1;
    color: #969282;
}

#transfer-model {
    height: 2;
    padding: 0 2;
    content-align: left middle;
}

#transfer-meter {
    height: 2;
    padding: 0 1;
    content-align: left middle;
    background: #0b0c0a;
}

#transfer-status {
    height: 3;
    padding: 1 2;
    color: #969282;
}

#transfer-actions {
    height: 4;
    padding: 0 1;
    align-horizontal: right;
}

#transfer-actions Button {
    width: 14;
    height: 3;
    border: none;
    background: #24251e;
    color: #b8b3a4;
}

#transfer-actions Button:focus {
    background: #c4934f;
    color: #090a09;
    text-style: bold;
}

CommandPalette { align: center middle; background: #000000 68%; }
CommandPalette > Vertical {
    width: 92%;
    max-width: 88;
    height: 78%;
    max-height: 40;
    padding: 0 1;
    background: #11130f;
    border: tall #545348;
}
CommandPalette #--input { border: tall #766342; background: #151711; color: #ddd8c8; }
CommandPalette > .command-palette--help-text { color: #888477; }
CommandPalette > .command-palette--highlight { color: #c4934f; text-style: bold; }

SelectorScreen.compact .dialog,
ApprovalScreen.compact .dialog,
TextInputScreen.compact .dialog,
ModelTransferScreen.compact .dialog {
    width: 98%;
    padding: 0;
}

SelectorScreen.short .dialog,
ApprovalScreen.short .dialog {
    height: 94%;
}

ApprovalScreen.short #approval-dialog {
    height: 94%;
    max-height: 94%;
}

ApprovalScreen.compact #approval-actions { padding: 0; }
ApprovalScreen.compact #approval-actions Button {
    width: 1fr;
    min-width: 0;
    margin-left: 0;
    padding: 0;
}

TextInputScreen.compact #input-dialog,
ModelTransferScreen.compact #transfer-dialog {
    width: 98%;
}

ModelTransferScreen.short #transfer-dialog {
    height: 94%;
    max-height: 94%;
}

ModelTransferScreen.short .transfer-plan { height: 2; padding: 0 1; }
ModelTransferScreen.short #transfer-model { height: 1; padding: 0 1; }
ModelTransferScreen.short #transfer-status { height: 2; padding: 0 1; }
ModelTransferScreen.short #transfer-actions { height: 3; padding: 0; }

Screen.compact #transcript { padding: 1 1 0 1; }
Screen.compact TranscriptEntry { padding-left: 1; }
Screen.compact #composer-shell { padding: 0; }
Screen.compact #empty-state { padding: 0 1; }
Screen.short #metabar { display: none; }
Screen.short #keybar { display: none; }
Screen.short #composer-shell { min-height: 4; }
Screen.short #empty-state { min-height: 5; padding: 0; }
"""


class DairackTextualBase(App[None]):
    CSS = DAIRACK_CSS
    COMMAND_PALETTE_BINDING = "ctrl+p"
    ENABLE_COMMAND_PALETTE = True
    legacy_tui_class: type[Any]

    BINDINGS = [
        Binding("escape", "escape", "Stop", show=False),
        Binding("pageup", "transcript_page(-1)", "Scroll up", show=False, priority=True),
        Binding("pagedown", "transcript_page(1)", "Scroll down", show=False, priority=True),
        Binding("ctrl+end", "tail", "Latest", show=False, priority=True),
        Binding("ctrl+t", "focus_transcript", "Transcript", show=False),
        Binding("ctrl+l", "clear_transcript_view", "Clear view", show=False),
        Binding("f2", "model_picker", "Models", show=False),
        Binding("f3", "chat_picker", "Chats", show=False),
        Binding("f4", "image_picker", "Images", show=False),
        Binding("f6", "model_library", "Model library", show=False, priority=True),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        core: Any,
        provider: Any,
        version: str,
        config: dict[str, Any],
        cwd: Path,
        chat: dict[str, Any] | None = None,
        messages: list[dict[str, str]] | None = None,
        blocks: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(ansi_color=False)
        self.core = core
        self.provider = provider
        self.version = version
        self.config = config
        self.cwd = cwd
        self.chat = chat or core.new_chat_state(cwd, config)
        self.lock = threading.RLock()
        self.messages = core.SynchronizedMessages(
            messages or [{"role": "system", "content": core.system_prompt(cwd, bool(config.get("agent")), config)}],
            self.lock,
        )
        self.blocks: list[dict[str, str]] = list(blocks or [])
        self._worker_threads: set[threading.Thread] = set()
        self.busy = False
        self.busy_label = ""
        self.busy_started = 0.0
        self._busy_interruptible = True
        self.interrupt_requested = False
        self.cancel_event = threading.Event()
        self.pending_tool: dict[str, str] | None = None
        self.model_picker_active = False
        self.model_picker_items: list[Any] = []
        self.model_picker_index = 0
        self.model_library_active = False
        self._model_library_models: list[Any] = []
        self._model_library_recommendations: dict[str, BundleRecommendation] = {}
        self._coordinator_settings_active = False
        self._compute_settings_active = False
        self._compute_candidate_endpoint = ""
        self._coordinator_preference_role = ""
        self._profile_model = ""
        self._profile_field = ""
        self._available_update: UpdateInfo | None = None
        self._update_check_running = False
        self.chat_picker_active = False
        self.image_picker_active = False
        self._pending_images: list[Path] = []
        self._transcript_ui_lock = asyncio.Lock()
        self._entry_by_index: dict[int, TranscriptEntry] = {}
        self._empty_state: Static | None = None
        self._welcome_started = time.monotonic()
        self._ui_thread_id = 0
        self._ui_loop: asyncio.AbstractEventLoop | None = None
        self._ui_ready = False
        self._unread = 0
        self._notice = ""
        self._notice_until = 0.0
        self._notice_severity = "info"
        self._chrome_cache: dict[str, str] = {}
        self._stream_chars = 0
        self._stream_started = 0.0
        self._last_stream_render = 0.0
        self._last_turn_stats: dict[str, Any] = {}
        self._last_turn_stats_until = 0.0
        self._phase_glint_started = 0.0
        self._completion_glint_started = 0.0
        self._focus_glint_started = 0.0
        self._composer_was_focused = False
        self._executor_stats: dict[str, Any] = {}
        self._active_tool_call: dict[str, str] | None = None
        self._active_route: dict[str, Any] | None = None
        self._last_route: dict[str, Any] = dict(self.chat.get("last_route") or {})
        self._route_config: dict[str, Any] | None = None
        self._route_plan = ""
        self._route_feedback = ""
        self._route_action_feedback = ""
        self._route_original_answer = ""
        self._route_planned = False
        self._route_review_rounds = 0
        self._agent_steps_used = 0
        self._loop_guard = self.core.ActionLoopGuard()
        self._history = [
            str(message.get("content") or "")
            for message in self.messages
            if message.get("role") == "user"
            and not str(message.get("content") or "").startswith(
                (
                    "Shell tool result:",
                    "Patch tool result:",
                    "Structured tool result:",
                    "Coordinator specialist result:",
                    "Tool result:",
                )
            )
        ]
        self._history_index = len(self._history)
        self._history_draft = ""
        reduced_motion = env_value("REDUCED_MOTION").strip().lower()
        self._reduced_motion = bool(config.get("reduced_motion")) or reduced_motion in {"1", "true", "yes", "on"}
        self.title = "DAIRACK"
        self.sub_title = "local intelligence"
        self.register_theme(LOCKED_THEME)
        self.theme = LOCKED_THEME.name

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        yield Static(id="metabar")
        yield VerticalScroll(id="transcript")
        yield Static(id="activity")
        with Vertical(id="composer-shell"):
            yield Static("PROMPT", id="composer-title")
            yield Static(id="attachment-bar")
            yield Composer(
                id="composer",
                soft_wrap=True,
                tab_behavior="focus",
                placeholder="Ask, build, inspect, or type /help...",
            )
            yield Static(id="composer-meta")
        yield Static(id="keybar")

    async def on_mount(self) -> None:
        self._ui_thread_id = threading.get_ident()
        self._ui_loop = asyncio.get_running_loop()
        self._ui_ready = True
        self._apply_responsive_classes()
        await self._rebuild_transcript_main()
        self.set_interval(UI_TICK_SECONDS, self._feedback_tick, name="dairack-feedback")
        self.query_one("#composer", Composer).focus()
        self.screen.set_class(bool(self._pending_images), "has-attachments")
        self.refresh_chrome(force=True)
        self.call_after_refresh(self._scroll_tail_main)
        if not self.config.get("model"):
            self.call_after_refresh(self.open_model_library)
        if self.config.get("check_updates", True) and self.config.get("update_index_url"):
            self.call_after_refresh(self._start_update_check)

    def on_resize(self, _event: Any) -> None:
        self._apply_responsive_classes()
        self._resize_composer()
        self.refresh_chrome(force=True)

    def on_unmount(self) -> None:
        self._ui_ready = False
        self.stop_workers(timeout=2.0)
        try:
            self.save_current_chat()
        except Exception:
            pass
        self._ui_loop = None

    def _apply_responsive_classes(self) -> None:
        width = self.size.width
        height = self.size.height
        self.screen.set_class(width < 52, "compact")
        self.screen.set_class(height < 22, "short")

    def get_system_commands(self, _screen: Any) -> Iterable[SystemCommand]:
        commands = [
            ("Intelligence mode", "Choose coordinated routing or a direct compute model", "/model"),
            ("Compute server", "Inspect or change where model inference runs", "/compute"),
            ("Hardware map", "Distinguish the client from the inference server", "/hardware"),
            ("Model library", "Install, update, remove, and inspect compute models", "/library"),
            ("Coordinator settings", "Configure policy, stages, and soft role preferences", "/coordinator"),
            ("Coordinator: adaptive", "Balance response quality and compute cost", "/coordinator adaptive"),
            ("Coordinator: quality", "Use specialists and review more aggressively", "/coordinator quality"),
            ("Coordinator: efficient", "Prefer resident models and minimal handoffs", "/coordinator efficient"),
            ("Inspect route", "Show model ranking, stages, and delegations", "/route"),
            ("Attach image", "Choose visual input from the current project", None),
            ("Open chats", "Resume a saved conversation", "/chats"),
            ("New chat", "Start a clean saved conversation", "/new"),
            ("Context report", "Inspect context use and compaction", "/context"),
            ("Git diff", "Review current repository changes", "/diff"),
            ("Search the web", "Enter /web followed by a query", None),
            ("Permissions: ask", "Approve every model-requested action", "/permissions ask"),
            ("Permissions: read automatically", "Auto-run read-only inspection", "/permissions read-auto"),
            ("Permissions: deny", "Block all model-requested actions", "/permissions deny"),
            ("Copy transcript", "Copy the complete conversation", "/copy"),
            ("Software update", "Inspect the active Dairack release channel", "/update"),
        ]
        for title, help_text, command in commands:
            if title == "Attach image":
                yield SystemCommand(title, help_text, self.open_image_picker)
            elif command is None:
                yield SystemCommand(title, help_text, lambda: self._prefill_composer("/web "))
            else:
                yield SystemCommand(title, help_text, lambda value=command: self.handle_command(value))

    def _prefill_composer(self, value: str) -> None:
        composer = self.query_one("#composer", Composer)
        composer.load_text(value)
        composer.move_cursor((0, len(value)))
        composer.focus()

    def _dispatch(self, callback: Any, *args: Any) -> None:
        loop = self._ui_loop
        if not self._ui_ready or loop is None or loop.is_closed():
            return
        try:
            if threading.get_ident() == self._ui_thread_id:
                self.call_later(callback, *args)
            else:
                loop.call_soon_threadsafe(self.call_later, callback, *args)
        except RuntimeError:
            pass

    def _start_update_check(self, force: bool = False, announce: bool = False) -> None:
        if self._update_check_running:
            if announce:
                self.set_notice("Release check already running")
            return
        source_url = str(self.config.get("update_index_url") or "")
        if not source_url:
            if announce:
                self.append_system(
                    "Software update checks are not configured for this build.\n"
                    "Set a release channel with `dairack update channel <https-url>`."
                )
            return
        self._update_check_running = True
        if announce:
            self.set_notice("Checking the Dairack release channel...", seconds=5.0)

        def worker() -> None:
            info: UpdateInfo | None = None
            error = ""
            try:
                info = check_for_update(
                    __version__,
                    source_url,
                    paths=PATHS,
                    force=force,
                    max_age_seconds=float(self.config.get("update_check_interval_hours", 24)) * 3600,
                )
            except UpdateError as exc:
                error = str(exc)
            self._dispatch(self._finish_update_check, info, error, announce)

        self.start_worker(worker, "dairack-update-check")

    def _finish_update_check(self, info: UpdateInfo | None, error: str, announce: bool) -> None:
        self._update_check_running = False
        if error:
            if announce:
                self.append_error(f"update check failed: {error}")
            return
        if info is None:
            return
        self._available_update = info if info.available else None
        self.invalidate()
        if not announce:
            return
        if info.available:
            self._show_update_center(info)
        else:
            self.append_system(f"Dairack {info.current_version} is current (channel latest: {info.latest_version}).")

    def open_update_center(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before updating Dairack")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before updating Dairack")
            return
        if self._available_update:
            self._show_update_center(self._available_update)
            return
        self._start_update_check(force=True, announce=True)

    def _show_update_center(self, info: UpdateInfo) -> None:
        command = format_update_command(update_command(info.latest_version))
        install = Text("UPDATE NOW", style=f"bold {PALETTE['amber']}")
        install.append(
            f"\n        install {info.latest_version}, preserve local state, then exit cleanly",
            style=PALETTE["muted"],
        )
        copy = Text("COPY COMMAND", style=f"bold {PALETTE['paper']}")
        copy.append("\n        " + command, style=PALETTE["quiet"])
        options: list[tuple[str, Text]] = [("apply", install), ("copy", copy)]
        if info.notes_url:
            notes = Text("RELEASE NOTES", style=f"bold {PALETTE['teal']}")
            notes.append("\n        " + info.notes_url, style=PALETTE["quiet"])
            options.append(("notes", notes))
        refresh = Text("CHECK AGAIN", style=f"bold {PALETTE['muted']}")
        refresh.append("\n        bypass cached release metadata", style=PALETTE["quiet"])
        options.extend((("refresh", refresh), ("back", Text("BACK", style=f"bold {PALETTE['muted']}"))))
        self.push_screen(
            SelectorScreen(
                f"DAIRACK UPDATE / {info.latest_version}",
                f"Installed {info.current_version}. Updates change application files only; chats, profiles, and models remain local.",
                options,
            ),
            lambda choice: self._update_center_result(info, choice),
        )

    def _update_center_result(self, info: UpdateInfo, choice: str | None) -> None:
        if choice == "apply":
            self._show_update_confirmation(info)
        elif choice == "copy":
            self.copy_to_clipboard(format_update_command(update_command(info.latest_version)))
            self.set_notice("Update command copied")
        elif choice == "notes":
            self.append_system(f"RELEASE NOTES\n{info.notes_url}")
        elif choice == "refresh":
            self._start_update_check(force=True, announce=True)
        else:
            self.query_one("#composer", Composer).focus()

    def _show_update_confirmation(self, info: UpdateInfo) -> None:
        command = format_update_command(update_command(info.latest_version))
        confirm = Text("RUN UPDATE", style=f"bold {PALETTE['amber']}")
        confirm.append("\n        " + command, style=PALETTE["paper"])
        confirm.append("\n        Dairack exits after a successful install", style=PALETTE["muted"])
        self.push_screen(
            SelectorScreen(
                "CONFIRM / SOFTWARE UPDATE",
                "The exact local package command runs in the attached terminal. No remote command data is executed.",
                [("confirm", confirm), ("back", Text("BACK", style=f"bold {PALETTE['muted']}"))],
                highlighted=1,
            ),
            lambda choice: self._update_confirmation_result(info, choice),
        )

    def _update_confirmation_result(self, info: UpdateInfo, choice: str | None) -> None:
        if choice != "confirm":
            self.query_one("#composer", Composer).focus()
            return
        self.save_current_chat()
        command = format_update_command(update_command(info.latest_version))
        try:
            with self.suspend():
                print(f"\n[dairack update] $ {command}\n", flush=True)
                result = apply_update(info)
                if result.returncode:
                    print(f"\n[exit {result.returncode}] update failed; returning to Dairack", flush=True)
                else:
                    print(f"\nDairack {info.latest_version} installed. Restart with `dairack`.\n", flush=True)
        except Exception as exc:
            self.append_error(f"update failed: {exc}")
            return
        if result.returncode:
            self.append_error(f"update command failed with exit {result.returncode}\n$ {command}")
            return
        self.exit()

    def invalidate(self) -> None:
        self._dispatch(self.refresh_chrome, True)

    def _context_values(self) -> tuple[int, int, float]:
        runtime = self._route_config or self.config
        active = self.core.active_context_messages(
            self.messages,
            str(self.chat.get("summary") or ""),
            runtime,
        )
        used = sum(self.core.estimate_message_tokens(message) for message in active)
        budget = self.core.context_budget(runtime)
        return used, budget, min(1.0, used / max(1, budget))

    def _orchestrator_enabled(self) -> bool:
        return str(self.config.get("model_mode") or "direct") == "orchestrator"

    def _display_model(self, include_executor: bool = True, width: int | None = None) -> str:
        display_width = width or max(20, self.size.width)
        if not self._orchestrator_enabled():
            return str(self.config.get("model") or "no model").upper()
        policy = str(self.config.get("orchestrator_policy") or "adaptive").upper()
        route = self._active_route or self._last_route
        executor = str(route.get("executor") or "").upper() if route else ""
        band = width_band(display_width)
        if band == "narrow":
            prefix = {"ADAPTIVE": "ADAPT", "EFFICIENT": "EFFIC", "QUALITY": "QUALITY"}.get(policy, policy)
        elif band == "standard":
            prefix = f"COORD / {policy}"
        else:
            prefix = f"COORDINATOR / {policy}"
        if include_executor and executor:
            return f"{prefix} > {executor}"
        return prefix

    def _topbar_content(self, width: int) -> Text:
        remote_compute = self.config.get("compute_mode") == "remote"
        left = Text()
        left.append(" DAIRACK ", style=f"bold {PALETTE['ink']} on {PALETTE['amber']}")
        if width >= 58:
            left.append("  LOCAL AGENT" if remote_compute else "  LOCAL INTELLIGENCE", style=f"bold {PALETTE['muted']}")
        right = Text()
        if self._available_update:
            right.append(f"UPDATE {self._available_update.latest_version}", style=f"bold {PALETTE['brass']}")
            right.append(" / ", style=PALETTE["quiet"])
        if remote_compute:
            compute_name = clip_middle(str(self.config.get("compute_name") or "REMOTE"), 24).upper()
            right.append(f"COMPUTE {compute_name} / ONLINE", style=PALETTE["olive"])
        else:
            right.append(f"OLLAMA {self.version} / ONLINE", style=PALETTE["olive"])
        available = width - len(left.plain) - len(right.plain) - 2
        if available >= 1:
            left.append(" " * available)
            left.append_text(right)
        elif width >= 34:
            compact_right = (
                Text(f" UPDATE {self._available_update.latest_version}", style=f"bold {PALETTE['brass']}")
                if self._available_update
                else Text(" ONLINE", style=PALETTE["olive"])
            )
            gap = max(1, width - len(left.plain) - len(compact_right.plain) - 1)
            left.append(" " * gap)
            left.append_text(compact_right)
        return left

    def _metabar_content(self, width: int) -> Text:
        used, budget, ratio = self._context_values()
        model = self._display_model(width=width)
        policy = str(self.config.get("permission_mode") or "ask").upper()
        agent = "AGENT" if self.config.get("agent") else "CHAT"
        band = width_band(width)
        if band == "narrow":
            value = Text(" MODE ", style=PALETTE["quiet"])
            value.append(clip_model(model, max(6, width - 13)), style=f"bold {PALETTE['amber']}")
            value.append(f"  {policy}", style=PALETTE["brass"])
            return value
        if band == "standard":
            value = Text(" MODE ", style=PALETTE["quiet"])
            value.append(clip_model(model, max(12, width - 34)), style=f"bold {PALETTE['amber']}")
            value.append(f"  CTX {short_number(used)}/{short_number(budget)}", style=PALETTE["muted"])
            value.append(f"  {policy}", style=PALETTE["brass"])
            return value

        cells = 10
        filled = min(cells, int(round(ratio * cells)))
        context_meter = "=" * filled + "-" * (cells - filled)
        title = self.core.clean_chat_title(str(self.chat.get("title") or ""), "new chat")
        value = Text(" MODE ", style=PALETTE["quiet"])
        value.append(clip_model(model, 38), style=f"bold {PALETTE['amber']}")
        value.append("   CTX ", style=PALETTE["quiet"])
        meter_color = PALETTE["red"] if ratio >= 0.92 else PALETTE["brass"] if ratio >= 0.75 else PALETTE["olive"]
        value.append(context_meter, style=meter_color)
        value.append(f" {ratio * 100:2.0f}%", style=PALETTE["muted"])
        value.append("   ACCESS ", style=PALETTE["quiet"])
        value.append(f"{agent}/{policy}", style=PALETTE["brass"])
        value.append("   CHAT ", style=PALETTE["quiet"])
        value.append(clip_right(title, max(12, width - len(value.plain) - 2)), style=PALETTE["teal"])
        return value

    def _activity_content(self, width: int) -> Text:
        now = time.monotonic()
        if self.busy:
            elapsed = max(0.0, now - self.busy_started)
            interruptible = (
                bool(self.core.tool_presentation(self._active_tool_call).get("interruptible"))
                if self._active_tool_call
                else self._busy_interruptible
            )
            phase = str(getattr(self.provider, "stream_phase", "") or "")
            coordination_phase = (self.busy_label or "").split(" / ", 1)[0]
            active_model = str(getattr(self.provider, "current_model", "") or "").upper()
            interrupting = self.interrupt_requested
            if interrupting:
                label = f"INTERRUPTING / {active_model}" if active_model else "INTERRUPTING"
            elif coordination_phase in {"loading model", "processing context"}:
                label = self.busy_label.upper()
            elif coordination_phase == "routing" and active_model and phase in {"thinking", "responding", "loading"}:
                label = f"COORDINATOR / {active_model}"
            elif coordination_phase in {
                "routing",
                "planning",
                "reviewing",
                "revising",
                "executing",
                "coordinator",
                "specialist",
            }:
                label = self.busy_label.upper()
            elif phase in {"thinking", "responding", "loading"}:
                phase_label = {"thinking": "THINKING", "responding": "RESPONDING", "loading": "LOADING MODEL"}[phase]
                label = f"{phase_label} / {active_model}" if active_model else phase_label
            else:
                label = (self.busy_label or "WORKING").upper()
            pulse, pulse_color = signal_pulse(elapsed, self._reduced_motion)
            phase_energy = (
                0.0 if self._reduced_motion else signal_envelope(now - self._phase_glint_started, PHASE_GLINT_SECONDS)
            )
            label_color = mix_color(PALETTE["paper"], PALETTE["signal_peak"], phase_energy * 0.46)
            if width < 44:
                value = Text(" ")
                value.append(pulse, style=f"bold {pulse_color}")
                value.append(" " + clip_middle(label, max(6, width - 14)), style=f"bold {label_color}")
                value.append(" " + elapsed_text(elapsed), style=PALETTE["brass"])
                suffix = " WAIT" if interrupting else " ESC" if interruptible else " SAFE"
                suffix_color = "brass" if interrupting else "red" if interruptible else "quiet"
                value.append(suffix, style=f"bold {PALETTE[suffix_color]}")
                return value
            track_width = 12 if width >= 72 else 8
            cursor = track_width // 2 if self._reduced_motion else int(elapsed * SIGNAL_STEP_HZ) % track_width
            value = Text(" ")
            value.append(pulse, style=f"bold {pulse_color}")
            label_width = 12 if width < 60 else 22 if width < 90 else 34
            value.append(f"  {clip_right(label, label_width)}  ", style=f"bold {label_color}")
            if (
                width >= 104
                and not interrupting
                and coordination_phase in {"routing", "planning", "reviewing", "revising", "executing", "specialist"}
                and phase in {"thinking", "responding", "loading"}
            ):
                phase_label = {"thinking": "THINK", "responding": "OUTPUT", "loading": "LOAD"}[phase]
                value.append(f"{phase_label:<6} ", style=PALETTE["brass"])
            append_signal_track(value, track_width, cursor)
            value.append(f"  {elapsed_text(elapsed)}", style=PALETTE["brass"])
            current = getattr(self.provider, "current_stats", {}) or {}
            output_tokens = int(current.get("eval_count") or max(0, self._stream_chars // 4))
            rate = float(current.get("tokens_per_second") or 0.0)
            if width >= 78 and output_tokens:
                value.append(f"  {output_tokens} tok", style=PALETTE["muted"])
            if width >= 92 and rate:
                value.append(f"  {rate:0.1f} tok/s", style=PALETTE["teal"])
            if interrupting:
                value.append("  WAIT", style=f"bold {PALETTE['brass']}")
            elif interruptible:
                value.append("  ESC STOP", style=f"bold {PALETTE['red']}")
            else:
                value.append("  FINISHING SAFELY", style=f"bold {PALETTE['quiet']}")
            return value

        if self._notice and now < self._notice_until:
            labels = {"info": "INFO", "success": "OK", "warning": "NOTE", "error": "ERR"}
            colors = {"info": "teal", "success": "olive", "warning": "brass", "error": "red"}
            severity = self._notice_severity if self._notice_severity in labels else "info"
            value = Text(" ")
            value.append(labels[severity], style=f"bold {PALETTE[colors[severity]]}")
            value.append("  " + clip_middle(self._notice, max(12, width - 7)), style=PALETTE["muted"])
            return value

        if self._unread:
            if width < 52:
                value = Text(" PAUSED", style=f"bold {PALETTE['brass']}")
                value.append(f" {self._unread}", style=PALETTE["muted"])
                value.append(" ^END", style=f"bold {PALETTE['amber']}")
                return value
            value = Text(" VIEW PAUSED  ", style=f"bold {PALETTE['brass']}")
            value.append(f"{self._unread} new update{'s' if self._unread != 1 else ''}  ", style=PALETTE["muted"])
            value.append("CTRL+END LATEST", style=f"bold {PALETTE['amber']}")
            return value

        value = Text(" READY", style=f"bold {PALETTE['olive']}")
        completion_age = now - self._completion_glint_started
        if not self._reduced_motion and 0.0 <= completion_age < COMPLETION_GLINT_SECONDS and width >= 44:
            track_width = 7 if width < 72 else 9
            progress = min(1.0, completion_age / COMPLETION_GLINT_SECONDS)
            cursor = min(track_width - 1, round(progress * (track_width - 1)))
            value.append("  ")
            append_signal_track(value, track_width, cursor, wrap=False)
        if self._last_turn_stats and now < self._last_turn_stats_until and width >= 72:
            count = int(self._last_turn_stats.get("eval_count") or 0)
            rate = float(self._last_turn_stats.get("tokens_per_second") or 0.0)
            if count:
                value.append(f"  LAST {count} tok", style=PALETTE["muted"])
            if rate:
                value.append(f" / {rate:0.1f} tok/s", style=PALETTE["teal"])
        if self.size.height < 22:
            _, _, ratio = self._context_values()
            status = (
                f"{self._display_model(include_executor=False, width=width)}  "
                f"{str(self.config.get('permission_mode') or 'ask').upper()}  CTX {ratio * 100:.0f}%"
            )
            value.append("  " + clip_right(status, max(4, width - len(value.plain) - 2)), style=PALETTE["quiet"])
        else:
            value.append("  ")
            value.append(clip_middle(str(self.cwd), max(4, width - len(value.plain) - 2)), style=PALETTE["quiet"])
        return value

    def _composer_meta_content(self, width: int) -> Text:
        composer = self.query_one("#composer", Composer)
        source = composer.text.strip()
        if source.startswith("/") and " " not in source and "\n" not in source:
            matches = [command for command in COMMAND_DESCRIPTIONS if command.startswith(source.lower())]
            if matches:
                command = matches[0]
                value = Text("  ")
                value.append(command, style=f"bold {PALETTE['amber']}")
                value.append(
                    "  " + clip_middle(COMMAND_DESCRIPTIONS[command], max(12, width - len(command) - 24)),
                    style=PALETTE["muted"],
                )
                if source.lower() != command and width >= 48:
                    value.append("   RIGHT COMPLETE", style=PALETTE["quiet"])
                return value
        draft_held = self.busy and bool(self.query_one("#composer", Composer).text.strip())
        value = Text("  ")
        if draft_held:
            value.append("DRAFT" if width < 38 else "DRAFT HELD", style=f"bold {PALETTE['olive']}")
            value.append("   ", style=PALETTE["quiet"])
        if self.size.height < 22:
            value.append("^P CMD   /HELP" if width < 38 else "^P COMMANDS   /HELP", style=PALETTE["quiet"])
        elif width >= 76:
            value.append("ENTER SEND   SHIFT+ENTER NEWLINE", style=PALETTE["quiet"])
        else:
            value.append("ENTER SEND   /HELP", style=PALETTE["quiet"])
        return value

    def _attachment_bar_content(self, width: int) -> Text:
        value = Text(" IMAGE ", style=f"bold {PALETTE['ink']} on {PALETTE['teal']}")
        if not self._pending_images:
            return value
        names = ", ".join(path.name for path in self._pending_images)
        if width < 36:
            value.append("  " + clip_middle(names, max(3, width - 12)), style=f"bold {PALETTE['paper']}")
            return value
        total = sum(path.stat().st_size for path in self._pending_images if path.exists())
        size_label = f"  {self.core.size_human(total)}"
        detach_label = "   /DETACH" if width >= 62 else ""
        available = max(4, width - 3 - len(value.plain) - 2 - len(size_label) - len(detach_label))
        value.append("  " + clip_middle(names, available), style=f"bold {PALETTE['paper']}")
        value.append(size_label, style=PALETTE["brass"])
        value.append(detach_label, style=PALETTE["quiet"])
        return value

    def _keybar_content(self, width: int) -> Text:
        band = width_band(width)
        if band == "wide":
            candidates = [
                ("CTRL+P", "COMMANDS"),
                ("F2", "MODE"),
                ("F3", "CHATS"),
                ("F4", "IMAGE"),
                ("F6", "LIBRARY"),
                ("CTRL+Q", "EXIT"),
            ]
        elif band == "standard":
            candidates = [
                ("^P", "CMD"),
                ("F2", "MODE"),
                ("F3", "CHATS"),
                ("F4", "IMAGE"),
                ("F6", "LIBRARY"),
                ("^Q", "EXIT"),
            ]
        else:
            candidates = [("^P", "CMD"), ("/HELP", "")]

        items: list[tuple[str, str]] = []
        occupied = 1
        for key, label in candidates:
            item_width = len(key) + (len(label) + 1 if label else 0) + (3 if items else 0)
            if occupied + item_width > width - 1:
                break
            items.append((key, label))
            occupied += item_width
        value = Text(" ")
        for index, (key, label) in enumerate(items):
            if index:
                value.append("   ")
            value.append(key, style=f"bold {PALETTE['amber']}")
            if label:
                value.append(" " + label, style=PALETTE["quiet"])
        return value

    def _update_static(self, widget_id: str, content: Text, force: bool) -> None:
        key = content.plain + repr(content.spans)
        if force or self._chrome_cache.get(widget_id) != key:
            self.query_one(widget_id, Static).update(content)
            self._chrome_cache[widget_id] = key

    def _update_welcome(self, width: int, force: bool) -> None:
        empty = self._empty_state
        if empty is None:
            return
        content = self._welcome_content(width)
        key = content.plain + repr(content.spans)
        if force or self._chrome_cache.get("#empty-state") != key:
            empty.update(content)
            self._chrome_cache["#empty-state"] = key

    def _welcome_content(self, width: int) -> Text:
        elapsed = (
            WELCOME_SETTLE_SECONDS
            if self._reduced_motion
            else max(
                0.0,
                time.monotonic() - self._welcome_started,
            )
        )
        compact = width_band(width) == "narrow" or self.size.height < 22
        settled = elapsed >= WELCOME_SETTLE_SECONDS
        glyph_count = len(WELCOME_WORDMARK)
        revealed = glyph_count if settled else min(glyph_count, max(1, int(elapsed * WORDMARK_REVEAL_HZ) + 1))
        accent = -1
        accent_start = 0.58
        accent_step = 0.1
        if not settled and accent_start <= elapsed < accent_start + glyph_count * accent_step:
            accent = min(glyph_count - 1, int((elapsed - accent_start) / accent_step))
        wordmark = Text(justify="center")
        if compact:
            letters = " ".join(WELCOME_WORDMARK)
            letter_index = 0
            for character in letters:
                if character == " ":
                    wordmark.append(character)
                    continue
                if letter_index >= revealed:
                    style = PALETTE["line"]
                elif accent >= 0 and abs(letter_index - accent) == 0:
                    style = PALETTE["signal_peak"]
                elif accent >= 0 and abs(letter_index - accent) == 1:
                    style = PALETTE["amber"]
                else:
                    style = PALETTE["paper"]
                wordmark.append(character, style=f"bold {style}")
                letter_index += 1
            tagline = "REMOTE COMPUTE" if self.config.get("compute_mode") == "remote" else "LOCAL INTELLIGENCE"
            wordmark.append("\n" + tagline, style=f"bold {PALETTE['muted']}")
        else:
            for row in range(3):
                for index, glyph in enumerate(WELCOME_GLYPHS):
                    if index:
                        wordmark.append("  ")
                    if index >= revealed:
                        style = PALETTE["line"]
                    elif accent >= 0 and abs(index - accent) == 0:
                        style = PALETTE["signal_peak"]
                    elif accent >= 0 and abs(index - accent) == 1:
                        style = PALETTE["amber"]
                    else:
                        style = PALETTE["paper"]
                    wordmark.append(glyph[row], style=f"bold {style}")
                wordmark.append("\n")
            tagline = (
                "L O C A L   A G E N T   /   R E M O T E   C O M P U T E"
                if self.config.get("compute_mode") == "remote"
                else "L O C A L   I N T E L L I G E N C E"
            )
            wordmark.append(tagline, style=f"bold {PALETTE['muted']}")

        track_width = 13 if compact else 23
        cursor = (
            track_width // 2 if settled else min(track_width - 1, int((elapsed / WELCOME_SETTLE_SECONDS) * track_width))
        )
        wordmark.append("\n\n")
        append_signal_track(wordmark, track_width, cursor, wrap=False)
        wordmark.append("\n")
        if elapsed < 0.58:
            state = "RUNTIME / ONLINE"
        elif elapsed < 1.18:
            state = "COORDINATOR / CALIBRATED"
        else:
            state = "NEW SESSION / READY"
        wordmark.append(state, style=f"bold {PALETTE['olive']}")
        return wordmark

    def _feedback_tick(self) -> None:
        if not self._ui_ready or not self.is_running:
            return
        width = max(20, self.size.width)
        self._update_static("#activity", self._activity_content(width), False)
        self._update_welcome(width, False)
        self._update_signal_surfaces(time.monotonic())

    def refresh_chrome(self, force: bool = False) -> None:
        if not self._ui_ready or not self.is_running:
            return
        width = max(20, self.size.width)
        self._update_static("#topbar", self._topbar_content(width), force)
        self._update_static("#metabar", self._metabar_content(width), force)
        self._update_static("#activity", self._activity_content(width), force)
        self._update_static("#attachment-bar", self._attachment_bar_content(width), force)
        self._update_static("#composer-meta", self._composer_meta_content(width), force)
        self._update_static("#keybar", self._keybar_content(width), force)
        self._update_welcome(width, force)
        self._update_signal_surfaces(time.monotonic())

    def _update_signal_surfaces(self, now: float) -> None:
        composer = self.query_one("#composer", Composer)
        focused = composer.has_focus
        if focused and not self._composer_was_focused:
            self._focus_glint_started = now
        self._composer_was_focused = focused

        if self._reduced_motion:
            focus_energy = 0.0
            completion_energy = 0.0
        else:
            focus_energy = signal_envelope(now - self._focus_glint_started, FOCUS_GLINT_SECONDS)
            completion_energy = signal_envelope(
                now - self._completion_glint_started,
                COMPLETION_GLINT_SECONDS,
            )
        rail_energy = max(focus_energy * 0.58, completion_energy) if focused else 0.0
        rail_base = PALETTE["amber"] if focused else "#151711"
        rail_color = mix_color(rail_base, PALETTE["signal_peak"], rail_energy)
        shell_energy = completion_energy * 0.62 if focused else 0.0
        shell_line = mix_color(PALETTE["line"], PALETTE["line_hot"], shell_energy)
        composer.styles.border_left = ("solid", rail_color)
        self.query_one("#composer-shell", Vertical).styles.border_top = ("solid", shell_line)

    def set_notice(self, value: str, seconds: float | None = None, severity: str = "success") -> None:
        severity = severity if severity in {"info", "success", "warning", "error"} else "info"
        self._notice = value.strip()
        self._notice_severity = severity
        duration = seconds if seconds is not None else 6.0 if severity == "error" else 3.0
        self._notice_until = time.monotonic() + duration
        self.invalidate()

    def set_error_notice(self, value: str, seconds: float | None = None) -> None:
        self.set_notice(value, seconds, "error")

    def set_warning_notice(self, value: str, seconds: float | None = None) -> None:
        self.set_notice(value, seconds, "warning")

    def _transcript_follows_tail(self) -> bool:
        if not self._ui_ready:
            return True
        transcript = self.query_one("#transcript", VerticalScroll)
        return transcript.max_scroll_y - transcript.scroll_y <= 2

    def _canonical_transcript_block(self, index: int) -> tuple[str, str, str, str] | None:
        with self.lock:
            if not 0 <= index < len(self.blocks):
                return None
            block = dict(self.blocks[index])
        role = str(block.get("role") or "system")
        text = str(block.get("text") or "")
        severity = str(block.get("severity") or "info")
        severity = severity if severity in {"info", "success", "warning", "error"} else "info"
        kind = "reference" if str(block.get("kind") or "message") == "reference" else "message"
        return role, text, severity, kind

    @staticmethod
    def _entry_matches_block(
        entry: TranscriptEntry,
        role: str,
        severity: str,
        kind: str,
    ) -> bool:
        return entry.role == role and entry.severity == severity and entry.kind == kind

    async def _rebuild_transcript_main(self) -> None:
        async with self._transcript_ui_lock:
            await self._rebuild_transcript_locked()

    async def _rebuild_transcript_locked(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        with self.lock:
            blocks = [dict(block) for block in self.blocks]
        self._empty_state = None
        self._chrome_cache.pop("#empty-state", None)
        await transcript.remove_children()
        self._entry_by_index.clear()
        if not blocks:
            empty = Static(self._welcome_content(max(20, self.size.width)), id="empty-state")
            await transcript.mount(empty)
            self._empty_state = empty
            return
        for index, block in enumerate(blocks):
            entry = TranscriptEntry(
                str(block.get("role") or "system"),
                str(block.get("text") or ""),
                index,
                str(block.get("severity") or "info"),
                str(block.get("kind") or "message"),
            )
            await transcript.mount(entry)
            self._entry_by_index[index] = entry

    async def _append_entry_main(
        self,
        role: str,
        text: str,
        index: int,
        severity: str = "info",
        kind: str = "message",
    ) -> None:
        follow = self._transcript_follows_tail()
        async with self._transcript_ui_lock:
            canonical = self._canonical_transcript_block(index)
            if canonical is None:
                return
            role, text, severity, kind = canonical
            existing = self._entry_by_index.get(index)
            if existing is not None and existing.is_mounted:
                if not self._entry_matches_block(existing, role, severity, kind):
                    await self._rebuild_transcript_locked()
                elif existing.source_text != text:
                    await existing.set_text(text)
                return
            transcript = self.query_one("#transcript", VerticalScroll)
            empty = self._empty_state
            self._empty_state = None
            self._chrome_cache.pop("#empty-state", None)
            if empty is not None and empty.is_mounted:
                await empty.remove()
            entry = TranscriptEntry(role, text, index, severity, kind)
            await transcript.mount(entry)
            self._entry_by_index[index] = entry
        if follow:
            self.call_after_refresh(self._scroll_tail_main)
        else:
            self._unread += 1
            self.refresh_chrome(force=True)

    async def _update_entry_main(self, index: int, text: str, force_tail: bool = False) -> None:
        follow = force_tail or self._transcript_follows_tail()
        async with self._transcript_ui_lock:
            canonical = self._canonical_transcript_block(index)
            if canonical is None:
                return
            role, text, severity, kind = canonical
            entry = self._entry_by_index.get(index)
            if entry is None or not entry.is_mounted or not self._entry_matches_block(entry, role, severity, kind):
                await self._rebuild_transcript_locked()
                self.call_after_refresh(self._scroll_tail_main)
                return
            await entry.set_text(text)
        if follow:
            self.call_after_refresh(self._scroll_tail_main)
        else:
            self._unread += 1
            self.refresh_chrome(force=True)

    async def _finish_assistant_entry_main(self, index: int, text: str) -> None:
        await self._update_entry_main(index, text, force_tail=True)

    def _scroll_tail_main(self) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.scroll_end(animate=False, force=True)
        self._unread = 0
        self.refresh_chrome(force=True)

    def render_transcript_text(self) -> str:
        if not self.blocks:
            return "DAIRACK / READY\n"
        chunks: list[str] = []
        for block in self.blocks:
            role = str(block.get("role") or "system")
            chunks.append("dairack" if role == "assistant" else role)
            chunks.append(str(block.get("text") or "").rstrip())
            chunks.append("")
        return "\n".join(chunks).rstrip() + "\n"

    def append_block(self, role: str, text: str, severity: str = "info", kind: str = "message") -> None:
        with self.lock:
            block = {"role": role, "text": text}
            if role == "system" and severity in {"success", "warning", "error"}:
                block["severity"] = severity
            if role == "system" and kind == "reference":
                block["kind"] = kind
            self.blocks.append(block)
            index = len(self.blocks) - 1
            if role == "assistant":
                self._last_stream_render = 0.0
        self._dispatch(self._append_entry_main, role, text, index, severity, kind)

    def append_to_last(self, text: str) -> None:
        with self.lock:
            if not self.blocks:
                self.blocks.append({"role": "assistant", "text": ""})
            self.blocks[-1]["text"] += text
            index = len(self.blocks) - 1
            value = self.blocks[-1]["text"]
        self._dispatch(self._update_entry_main, index, value)

    def append_coordinator(self, text: str) -> None:
        self.append_block("coordinator", text.strip())

    def append_action(self, text: str) -> None:
        self.append_block("action", text.strip())

    def replace_last_assistant_text(self, text: str) -> None:
        update: tuple[int, str] | None = None
        with self.lock:
            for index in range(len(self.blocks) - 1, -1, -1):
                if self.blocks[index].get("role") == "assistant":
                    self.blocks[index]["text"] = text
                    now = time.monotonic()
                    if now - self._last_stream_render >= STREAM_RENDER_INTERVAL:
                        self._last_stream_render = now
                        update = (index, text)
                    break
        if update is not None:
            self._dispatch(self._update_entry_main, *update)

    def append_assistant_end(self) -> None:
        update: tuple[int, str] | None = None
        with self.lock:
            for index in range(len(self.blocks) - 1, -1, -1):
                if self.blocks[index].get("role") == "assistant":
                    text = str(self.blocks[index].get("text") or "")
                    self._last_stream_render = time.monotonic()
                    update = (index, text)
                    break
        if update is not None:
            self._dispatch(self._finish_assistant_entry_main, *update)
        else:
            self._dispatch(self._scroll_tail_main)

    def discard_last_assistant_entry(self) -> None:
        with self.lock:
            if not self.blocks or self.blocks[-1].get("role") != "assistant":
                return
            self.blocks.pop()
        self._dispatch(self._rebuild_transcript_main)

    def redraw_transcript(self) -> None:
        self._dispatch(self._rebuild_transcript_main)

    def clear_transcript(self) -> None:
        with self.lock:
            self.blocks = []
        self._dispatch(self._rebuild_transcript_main)

    def focus_transcript(self) -> None:
        self.query_one("#transcript", VerticalScroll).focus()

    def scroll_transcript(self, delta: int) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.scroll_relative(y=delta, animate=False)

    def scroll_transcript_to(self, row: int) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.scroll_to(y=max(0, row), animate=False)

    def copy_transcript(self) -> None:
        self.copy_to_clipboard(self.render_transcript_text())
        self.set_notice("Transcript copied to the terminal clipboard")

    def set_busy(self, value: bool, label: str = "", *, interruptible: bool | None = None) -> None:
        was_busy = self.busy
        previous_label = self.busy_label
        now = time.monotonic()
        self.busy = value
        self.busy_label = label
        if value and not was_busy:
            self.busy_started = now
            self._stream_started = self.busy_started
            self._stream_chars = 0
            self._busy_interruptible = True if interruptible is None else interruptible
            self._phase_glint_started = now
        elif value and interruptible is not None:
            self._busy_interruptible = interruptible
        if value and label and label != previous_label:
            self._phase_glint_started = now
        elif not value:
            self.busy_started = 0.0
            self._busy_interruptible = True
            self._active_tool_call = None
            self.interrupt_requested = False
            stats = dict(self._executor_stats or getattr(self.provider, "last_stats", {}) or {})
            if stats:
                self._last_turn_stats = stats
                self._last_turn_stats_until = time.monotonic() + 2.5
            else:
                self._last_turn_stats = {}
                self._last_turn_stats_until = 0.0
            if was_busy:
                self._completion_glint_started = now
        self.invalidate()

    def begin_tool_action(self, call: dict[str, str], step_label: str = "") -> None:
        self._active_tool_call = dict(call)
        self.set_busy(True, self.core.tool_activity_label(call, step_label))

    def finish_tool_action(self) -> None:
        self._active_tool_call = None
        self.invalidate()

    def request_interrupt(self) -> None:
        if not self.busy:
            return
        action_interruptible = not self._active_tool_call or bool(
            self.core.tool_presentation(self._active_tool_call).get("interruptible")
        )
        if not action_interruptible or (not self._active_tool_call and not self._busy_interruptible):
            self.set_warning_notice("This action is finishing atomically", seconds=2.5)
            return
        if not self.interrupt_requested:
            self.interrupt_requested = True
            self.cancel_event.set()
            self.busy_label = "interrupting"
        self.invalidate()

    def action_escape(self) -> None:
        if self.busy:
            self.request_interrupt()

    def action_transcript_page(self, direction: int) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        if direction < 0:
            transcript.scroll_page_up(animate=False)
        else:
            transcript.scroll_page_down(animate=False)

    def action_tail(self) -> None:
        self._scroll_tail_main()

    def action_focus_transcript(self) -> None:
        self.focus_transcript()

    def action_clear_transcript_view(self) -> None:
        self.clear_transcript()
        self.set_notice("Transcript view cleared; conversation context remains")

    def action_model_picker(self) -> None:
        self.open_model_picker()

    def action_chat_picker(self) -> None:
        self.open_chat_picker()

    def action_image_picker(self) -> None:
        self.open_image_picker()

    def action_model_library(self) -> None:
        self.open_model_library()

    @on(Composer.Submitted)
    def composer_submitted(self, event: Composer.Submitted) -> None:
        text = event.text.strip()
        if not text and not self._pending_images:
            return
        if not text:
            text = "Analyze the attached image carefully and report the relevant details."
        if self.busy:
            self.set_warning_notice("Response active. Draft retained; press Esc to stop it.", seconds=3.0)
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before sending another prompt", seconds=3.0)
            return

        composer = event.composer
        composer.load_text("")
        self._history.append(text)
        self._history_index = len(self._history)
        self._history_draft = ""
        self._record_prompt_history(text)
        if text.startswith("/"):
            self.handle_command(text)
            return
        image_paths = [str(path) for path in self._pending_images]
        message: dict[str, Any] = {"role": "user", "content": text}
        if image_paths:
            message["image_paths"] = image_paths
        self.messages.append(message)
        if image_paths:
            labels = "  ".join(f"`IMAGE {Path(path).name}`" for path in image_paths)
            self.append_user(labels + "\n\n" + text)
            self._pending_images = []
            self._sync_attachment_state()
        else:
            self.append_user(text)
        self.save_current_chat()
        self.start_generation()

    @on(Composer.History)
    def composer_history(self, event: Composer.History) -> None:
        if not self._history:
            return
        composer = event.composer
        if self._history_index == len(self._history):
            self._history_draft = composer.text
        self._history_index = max(0, min(len(self._history), self._history_index + event.delta))
        value = self._history_draft if self._history_index == len(self._history) else self._history[self._history_index]
        composer.load_text(value)
        lines = value.splitlines() or [""]
        composer.move_cursor((len(lines) - 1, len(lines[-1])))

    @on(TextArea.Changed, "#composer")
    def composer_changed(self, event: TextArea.Changed) -> None:
        source = event.text_area.text.strip()
        suggestion = ""
        if source.startswith("/") and " " not in source and "\n" not in source:
            matches = [command for command in COMMAND_DESCRIPTIONS if command.startswith(source.lower())]
            if matches and matches[0] != source.lower():
                suggestion = matches[0][len(source) :]
        event.text_area.suggestion = suggestion
        self._resize_composer()
        self.refresh_chrome(force=True)

    def _resize_composer(self) -> None:
        if not self._ui_ready:
            return
        composer = self.query_one("#composer", Composer)
        width = max(20, composer.size.width - 3)
        display_lines = 0
        for line in composer.text.splitlines() or [""]:
            cells = cell_len(line.expandtabs(4))
            display_lines += max(1, (cells + width - 1) // width)
        max_lines = 4 if self.size.height < 24 else 7
        lines = max(2, min(max_lines, display_lines))
        composer.styles.height = lines
        self.query_one("#composer-shell", Vertical).styles.height = lines + (4 if self._pending_images else 3)

    def _record_prompt_history(self, text: str) -> None:
        try:
            file_history = self.core.FileHistory(str(self.core.HISTORY_PATH))
            file_history.append_string(text)
        except Exception:
            pass

    def handle_command(self, line: str) -> None:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self.append_system(f"command parse error: {exc}")
            return
        command = parts[0][1:].lower() if parts else ""
        args = parts[1:]
        if command == "update":
            if args:
                self.append_system("usage: /update")
            else:
                self.open_update_center()
            return
        if command in {"models", "library"}:
            self.open_model_library()
            return
        if command == "compute":
            if not args or args == ["status"]:
                self.open_compute_center()
            elif args[0].lower() in {"local", "remote"} and len(args) == 1:
                target = (
                    LOCAL_OLLAMA_ENDPOINT
                    if args[0].lower() == "local"
                    else str(self.config.get("remote_ollama_host") or "")
                )
                if target:
                    self._begin_compute_connection(target)
                else:
                    self.append_error("No remote compute server has been saved. Use /compute connect <url>.")
            elif args[0].lower() == "connect" and len(args) == 2:
                self._begin_compute_connection(args[1])
            else:
                self.append_system("usage: /compute [status|local|remote|connect <url>]")
            return
        if command == "pull":
            if not args:
                self.open_custom_model_input()
            elif len(args) == 1:
                try:
                    model = validate_model_name(args[0])
                except ValueError as exc:
                    self.append_error(str(exc))
                else:
                    self._show_custom_model_confirmation(model)
            else:
                self.append_system("usage: /pull <ollama-model>")
            return
        if command == "image":
            if not args:
                self.open_image_picker()
            elif len(args) > 1:
                self.append_system("usage: /image <path>\nQuote paths that contain spaces.")
            else:
                self.attach_image(args[0])
            return
        if command == "images":
            if not self._pending_images:
                self.append_system("No images are staged for the next prompt.")
            else:
                lines = ["STAGED VISUAL INPUT"]
                for index, path in enumerate(self._pending_images, 1):
                    lines.append(f"{index}. {path}  {self.core.size_human(path.stat().st_size)}")
                self.append_system("\n".join(lines))
            return
        if command == "detach":
            self.detach_image(args[0] if args else "all")
            return
        if command in {"coordinator", "orchestrator"}:
            if not args:
                self.open_coordinator_settings()
                return
            try:
                detail = self.core.configure_orchestrator(self.config, args)
            except ValueError as exc:
                self.append_error(str(exc))
                return
            self._active_route = None
            self._route_config = None
            self.core.save_config(self.config)
            self.append_system(detail)
            self.save_current_chat()
            self.set_notice(self._display_model(include_executor=False))
            return
        if command == "route":
            if args and args[0].lower() == "feedback":
                detail = self.core.record_route_feedback(
                    self.config,
                    self._active_route or self._last_route,
                    args[1] if len(args) > 1 else "",
                )
                self.append_system(detail)
                self.save_current_chat()
            elif args and args[0].lower() in {"history", "log", "all"}:
                self.append_system(self.core.format_route_history(self.chat))
            else:
                self.append_system(self.core.format_route_report(self._active_route or self._last_route))
            return
        if command == "chats" or (command == "resume" and not args):
            self.open_chat_picker()
            return
        if command == "run" and args:
            raw_command = line.split(" ", 1)[1]
            if self.core.command_needs_interactive_tty(raw_command):
                self.run_interactive_user_command(raw_command)
                return
        self.legacy_tui_class.handle_command(self, line)

    def _sync_attachment_state(self) -> None:
        if self._ui_ready:
            self.screen.set_class(bool(self._pending_images), "has-attachments")
            self._resize_composer()
            self.refresh_chrome(force=True)

    def attach_image(self, raw_path: str) -> None:
        try:
            path = self.core.resolve_image_path(self.cwd, raw_path)
        except ValueError as exc:
            self.append_error(str(exc))
            return
        if path in self._pending_images:
            self.set_notice(f"Already staged: {path.name}")
            return
        if len(self._pending_images) >= self.core.MAX_ATTACHED_IMAGES:
            self.append_system(f"At most {self.core.MAX_ATTACHED_IMAGES} images can be attached to one prompt.")
            return
        self._pending_images.append(path)
        self._sync_attachment_state()
        self.set_notice(f"Image staged: {path.name}")

    def detach_image(self, ref: str = "all") -> None:
        if not self._pending_images:
            self.set_notice("No staged images")
            return
        if ref.lower() in {"all", "*"}:
            count = len(self._pending_images)
            self._pending_images = []
            detail = f"Detached {count} image{'s' if count != 1 else ''}"
        else:
            try:
                index = int(ref) - 1
                if index < 0:
                    raise IndexError
                path = self._pending_images.pop(index)
            except (ValueError, IndexError):
                self.append_system("usage: /detach [image-number|all]")
                return
            detail = f"Detached {path.name}"
        self._sync_attachment_state()
        self.set_notice(detail)

    def open_image_picker(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before attaching visual input")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before attaching visual input")
            return
        self.image_picker_active = True
        self.set_busy(True, "scanning project images")

        def worker() -> None:
            try:
                images = self.core.discover_image_files(self.cwd, limit=120, cancel_event=self.cancel_event)
                if not self.cancel_event.is_set():
                    self._dispatch(self._show_image_picker_main, images)
            except Exception as exc:
                self.image_picker_active = False
                self.append_error(f"could not scan project images: {exc}")
            finally:
                if self.cancel_event.is_set():
                    self.image_picker_active = False
                    self.set_warning_notice("Image scan interrupted")
                self.interrupt_requested = False
                self.cancel_event.clear()
                self.set_busy(False)

        self.start_worker(worker, "dairack-images")

    def _show_image_picker_main(self, images: list[Path]) -> None:
        staged = set(self._pending_images)
        path_input = Text("ATTACH BY PATH", style=f"bold {PALETTE['amber']}")
        path_input.append("\n        Enter a local PNG, JPEG, WebP, GIF, or BMP path", style=PALETTE["muted"])
        options: list[tuple[str, Text]] = [("__path__", path_input)]
        for path in images:
            try:
                relative = path.relative_to(self.cwd)
            except ValueError:
                relative = path
            row = Text()
            row.append("STAGED  " if path in staged else "        ", style=f"bold {PALETTE['olive']}")
            row.append(clip_middle(path.name, max(16, min(48, self.size.width - 22))), style=f"bold {PALETTE['paper']}")
            row.append(f"  {self.core.size_human(path.stat().st_size)}", style=PALETTE["brass"])
            row.append(
                "\n        " + clip_middle(str(relative), max(18, min(70, self.size.width - 20))),
                style=PALETTE["quiet"],
            )
            options.append((str(path), row))
        screen = SelectorScreen(
            "VISUAL INPUT / PROJECT MEDIA",
            (
                f"{len(images)} project image{'s' if len(images) != 1 else ''}. "
                f"Attachments are sent with the next prompt and routed to a vision-capable model."
            ),
            options,
            highlighted=0,
            family="library",
        )
        self.push_screen(screen, self._image_picker_result)

    def _image_picker_result(self, choice: str | None) -> None:
        if choice == "__path__":
            self.push_screen(
                TextInputScreen(
                    "VISUAL INPUT / LOCAL PATH",
                    f"Attach one image to the next prompt; up to {self.core.MAX_ATTACHED_IMAGES} may be staged.",
                    placeholder="/path/to/image.png",
                ),
                self._image_path_input_result,
            )
            return
        self.image_picker_active = False
        if choice:
            self.attach_image(choice)
        self.query_one("#composer", Composer).focus()

    def _image_path_input_result(self, value: str | None) -> None:
        self.image_picker_active = False
        if value:
            self.attach_image(value)
        self.query_one("#composer", Composer).focus()

    def run_interactive_user_command(self, command: str) -> None:
        call = {"name": "shell", "cmd": command}
        self.begin_tool_action(call)
        self.set_busy(True, "attached terminal")
        started = time.monotonic()
        try:
            with self.suspend():
                print(f"\n[dairack interactive] $ {command}\n", flush=True)
                invocation, use_shell = self.core.shell_invocation(command)
                code = subprocess.call(
                    invocation,
                    cwd=str(self.cwd),
                    shell=use_shell,
                    env=self.core.command_environment(),
                )
                print(f"\n[exit {code}] returning to dairack", flush=True)
            result = "Output was shown in the attached terminal."
        except Exception as exc:
            code = 1
            result = f"interactive command failed: {exc}"
        try:
            self.append_action(self.core.tool_result_display(call, code, result, "user", time.monotonic() - started))
            self.messages.append(self.core.tool_history_message(call, code, result))
            self.save_current_chat()
        finally:
            self.finish_tool_action()
            self.set_busy(False)
        self.set_notice(
            f"Interactive command finished with exit {code}",
            severity="success" if code == 0 else "error",
        )

    def open_compute_center(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before changing compute")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before changing compute")
            return
        self._compute_settings_active = True
        self._show_compute_center()

    def _show_compute_center(self) -> None:
        endpoint = str(self.config.get("ollama_host") or LOCAL_OLLAMA_ENDPOINT)
        mode = str(self.config.get("compute_mode") or "local").upper()
        transport = str(self.config.get("compute_transport") or "ollama").upper()
        name = str(self.config.get("compute_name") or "Compute")
        verified = bool(self.config.get("compute_hardware_verified"))
        registry = load_registry(PATHS)
        model_count = len(registry.models) if registry else 0

        current = Text("ACTIVE   ", style=f"bold {PALETTE['olive']}")
        current.append(clip_middle(name.upper(), 44), style=f"bold {PALETTE['paper']}")
        current.append(f"  / {mode} / {transport}", style=PALETTE["teal"])
        current.append(
            f"\n         {clip_middle(endpoint, 72)}\n         {model_count} models  /  "
            + ("HARDWARE VERIFIED" if verified else "BACKEND SETTINGS AUTOMATIC"),
            style=PALETTE["muted"],
        )
        connect = Text("CONNECT SERVER", style=f"bold {PALETTE['amber']}")
        connect.append("\n         Pair an HTTPS, Tailscale, or trusted Ollama endpoint", style=PALETTE["muted"])
        options: list[tuple[str, Text]] = [("refresh", current), ("connect", connect)]

        if self.config.get("compute_mode") == "remote":
            local = Text("USE LOCAL OLLAMA", style=f"bold {PALETTE['paper']}")
            local.append(
                "\n         Return inference to this client; tools and chats do not move", style=PALETTE["muted"]
            )
            options.append(("local", local))
            token = Text("REPLACE ACCESS TOKEN", style=f"bold {PALETTE['teal']}")
            token.append("\n         Stored privately outside the main configuration", style=PALETTE["muted"])
            options.append(("token", token))
        else:
            remote = str(self.config.get("remote_ollama_host") or "")
            if remote:
                saved = Text("USE SAVED SERVER", style=f"bold {PALETTE['paper']}")
                saved.append(f"\n         {clip_middle(remote, 68)}", style=PALETTE["muted"])
                options.append(("remote", saved))

        self.push_screen(
            SelectorScreen(
                "COMPUTE / CONNECTION",
                "Models run at this endpoint. Files, shell actions, approvals, and chat state remain on this client.",
                options,
                highlighted=0,
                family="library",
            ),
            self._compute_center_result,
        )

    def _compute_center_result(self, choice: str | None) -> None:
        if not choice:
            self._compute_settings_active = False
            self.query_one("#composer", Composer).focus()
            return
        if choice == "connect":
            self.push_screen(
                TextInputScreen(
                    "COMPUTE / SERVER ADDRESS",
                    "Use the HTTPS URL from `dairack serve --tailscale`, a Tailscale 100.x address, or local Ollama.",
                    placeholder="https://server.tailnet.ts.net",
                ),
                self._compute_url_result,
            )
            return
        if choice == "token":
            self._request_compute_token(str(self.config.get("ollama_host") or ""))
            return
        if choice == "local":
            self._begin_compute_connection(LOCAL_OLLAMA_ENDPOINT)
            return
        if choice == "remote":
            self._begin_compute_connection(str(self.config.get("remote_ollama_host") or ""))
            return
        self._begin_compute_connection(str(self.config.get("ollama_host") or LOCAL_OLLAMA_ENDPOINT))

    def _compute_url_result(self, value: str | None) -> None:
        if value:
            self._begin_compute_connection(value)
        else:
            self.call_later(self._show_compute_center)

    def _request_compute_token(self, endpoint: str) -> None:
        self._compute_candidate_endpoint = endpoint
        self.push_screen(
            TextInputScreen(
                "COMPUTE / ACCESS TOKEN",
                "Enter the token shown by the server. It is stored privately and never written to chat history.",
                placeholder="Bearer token",
                password=True,
            ),
            self._compute_token_result,
        )

    def _compute_token_result(self, value: str | None) -> None:
        if value:
            self._begin_compute_connection(self._compute_candidate_endpoint, token=value)
        else:
            self.call_later(self._show_compute_center)

    def _begin_compute_connection(self, endpoint: str, *, token: str | None = None) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before changing compute")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before changing compute")
            return
        try:
            policy = validate_compute_endpoint(endpoint)
        except ComputeError as exc:
            self._show_compute_failure(str(exc), endpoint)
            return
        self._compute_candidate_endpoint = policy.endpoint
        self.save_current_chat()
        self.set_busy(True, "connecting compute", interruptible=False)

        def worker() -> None:
            previous_token = ""
            token_changed = False
            try:
                credential = token.strip() if token is not None else compute_token(policy.endpoint, PATHS)
                provider = (
                    OllamaProvider(policy.endpoint, credential) if credential else OllamaProvider(policy.endpoint)
                )
                try:
                    probe_compute(provider, include_models=False)
                except OllamaError as exc:
                    if exc.status_code == 401 and token is None:
                        self._dispatch(self._request_compute_token, policy.endpoint)
                        return
                    raise
                previous_token = stored_compute_token(policy.endpoint, PATHS)
                token_changed = token is not None and credential != previous_token
                if token_changed:
                    save_compute_token(policy.endpoint, credential, PATHS)
                result = initialize(PATHS, policy.endpoint)
                connected_provider = provider_for_config(result.config, PATHS)
                self._dispatch(self._compute_connection_ready, result, connected_provider)
            except Exception as exc:
                if token_changed:
                    try:
                        save_compute_token(policy.endpoint, previous_token, PATHS)
                    except Exception:
                        pass
                self._dispatch(self._show_compute_failure, str(exc), policy.endpoint)
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-compute-connect")

    def _compute_connection_ready(self, result: InitializationResult, provider: OllamaProvider) -> None:
        with self.lock:
            self.config.clear()
            self.config.update(result.config)
            self.messages[0]["content"] = self.core.system_prompt(
                self.cwd,
                bool(self.config.get("agent")),
                self.config,
            )
        self.provider = provider
        self.version = result.ollama_version
        self._active_route = None
        self._last_route = {}
        self._route_config = None
        self.save_current_chat()
        self.refresh_chrome(force=True)
        self.set_notice(
            f"Compute ready: {self.config.get('compute_name')} / {len(result.registry.models)} models",
            severity="success",
        )
        self.call_later(self._show_compute_center)

    def _show_compute_failure(self, detail: str, endpoint: str) -> None:
        self._compute_candidate_endpoint = endpoint
        retry = Text("RETRY", style=f"bold {PALETTE['amber']}")
        retry.append(f"\n         {clip_middle(endpoint, 68)}", style=PALETTE["muted"])
        self.push_screen(
            SelectorScreen(
                "COMPUTE / CONNECTION FAILED",
                clip_right(detail, 240),
                [("retry", retry), ("back", Text("BACK", style=f"bold {PALETTE['muted']}"))],
                highlighted=1,
            ),
            self._compute_failure_result,
        )

    def _compute_failure_result(self, choice: str | None) -> None:
        if choice == "retry":
            self._begin_compute_connection(self._compute_candidate_endpoint)
        else:
            self.call_later(self._show_compute_center)

    def open_model_library(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before managing models")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before managing models")
            return
        self.model_library_active = True
        self.set_busy(True, "refreshing model library", interruptible=False)

        def worker() -> None:
            try:
                result = initialize(PATHS, getattr(self.provider, "host", None))
                recommendations = (
                    recommendation_set(result.hardware, result.registry) if result.registry.hardware_verified else ()
                )
                self._dispatch(self._show_model_library_main, result, recommendations)
            except Exception as exc:
                self.model_library_active = False
                self.append_error(f"could not refresh the model library: {exc}")
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-model-library")

    def _show_model_library_main(
        self,
        result: InitializationResult,
        recommendations: tuple[BundleRecommendation, ...],
    ) -> None:
        with self.lock:
            self.config.clear()
            self.config.update(result.config)
        self._model_library_models = [record.descriptor for record in result.registry.models.values()]
        self._model_library_recommendations = {value.bundle.id: value for value in recommendations}
        installed_options: list[tuple[str, Text]] = []
        has_models = bool(self._model_library_models)
        install = Text("INSTALL MODEL", style=f"bold {PALETTE['amber']}")
        install.append("\n        Add any Ollama library tag or private registry model", style=PALETTE["muted"])
        action_options: list[tuple[str, Text]] = [("custom", install)]
        if result.registry.hardware_verified:
            sets = Text("HARDWARE-FITTED SETS", style=f"bold {PALETTE['paper']}")
            sets.append("\n        Optional role coverage calibrated for this compute hardware", style=PALETTE["muted"])
            action_options.append(("sets", sets))
        if has_models:
            refresh = Text("UPDATE INSTALLED", style=f"bold {PALETTE['teal']}")
            refresh.append(
                f"\n        Refresh {len(self._model_library_models)} tags; unchanged Ollama layers are reused",
                style=PALETTE["muted"],
            )
            action_options.append(("update-all", refresh))

        registry = result.registry
        for descriptor in self._model_library_models:
            record = registry.models.get(descriptor.name)
            profile = record.effective_runtime() if record else {}
            capability = record.effective_capability() if record else None
            row = Text()
            active = str(self.config.get("model") or "") == descriptor.name
            row.append("ACTIVE   " if active else "         ", style=f"bold {PALETTE['olive']}")
            row.append(
                clip_middle(descriptor.name, max(18, min(48, self.size.width - 24))), style=f"bold {PALETTE['paper']}"
            )
            row.append(f"  {descriptor.size / 1024**3:.1f} GiB", style=PALETTE["brass"])
            row.append(f"\n         {record.role if record else 'Ollama model'}", style=PALETTE["muted"])
            row.append(
                f"  / {profile.get('fit', 'unknown')} fit  / ctx {profile.get('num_ctx', '?')}",
                style=PALETTE["quiet"],
            )
            if capability:
                source = (
                    "CURATED"
                    if "catalog" in capability.source
                    else "CALIBRATED"
                    if "user" in capability.source
                    else "INFERRED"
                )
                row.append(
                    f"  / {source} {capability.confidence * 100:.0f}%",
                    style=PALETTE["olive"] if source != "INFERRED" else PALETTE["quiet"],
                )
            installed_options.append((f"model|{descriptor.name}", row))

        options = [*action_options, *installed_options]

        hardware = result.hardware
        accelerator = hardware.primary_accelerator
        fit = (
            f"{hardware.memory_total_bytes / 1024**3:.0f} GiB RAM"
            + (f" / {accelerator.name} {accelerator.memory_bytes / 1024**3:.0f} GiB" if accelerator else " / CPU")
            if result.registry.hardware_verified
            else "backend-managed hardware"
        )
        compute_name = str(self.config.get("compute_name") or "COMPUTE").upper()
        screen = SelectorScreen(
            f"MODEL LIBRARY / {clip_middle(compute_name, 32)}",
            (
                f"{len(self._model_library_models)} installed. Select a model to manage it; optional model sets are fitted to {fit}."
                if has_models
                else f"No models installed. Choose a fitted model set for {fit}, or enter any Ollama model name."
            ),
            options,
            highlighted=0,
            family="library",
        )
        self.push_screen(screen, self._model_library_result)

    def _model_library_result(self, choice: str | None) -> None:
        if not choice:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()
            return
        if choice == "sets":
            self._show_model_set_picker()
            return
        if choice == "update-all":
            self._show_update_all_confirmation()
            return
        if choice == "custom":
            self.open_custom_model_input()
            return
        if choice.startswith("model|"):
            self._show_installed_model_actions(choice.split("|", 1)[1])

    def _show_model_set_picker(self) -> None:
        has_models = bool(self._model_library_models)
        options: list[tuple[str, Text]] = []
        for identifier in ("balanced", "minimal", "complete"):
            recommendation = self._model_library_recommendations[identifier]
            row = Text()
            row.append(
                "RECOMMENDED  " if identifier == "balanced" and not has_models else "             ",
                style=f"bold {PALETTE['olive']}",
            )
            row.append(recommendation.bundle.label.upper(), style=f"bold {PALETTE['amber']}")
            if recommendation.models:
                row.append(
                    f"  / {len(recommendation.models)} TO INSTALL  ~{recommendation.download_gib:.1f} GiB",
                    style=PALETTE["brass"],
                )
            else:
                row.append("  / COVERED", style=f"bold {PALETTE['olive']}")
            row.append("\n             " + recommendation.bundle.summary, style=PALETTE["muted"])
            covered = " / ".join(
                f"{role} > {clip_middle(model, 24)}" for role, model in recommendation.covered_roles.items()
            )
            if covered:
                row.append("\n             " + covered, style=PALETTE["quiet"])
            options.append((f"bundle|{identifier}", row))
        options.append(("back", Text("BACK", style=f"bold {PALETTE['muted']}")))
        self.push_screen(
            SelectorScreen(
                "MODEL SETS / HARDWARE FITTED",
                "Sets are optional presets. Coordinator also calibrates arbitrary installed models by capability.",
                options,
            ),
            self._model_set_picker_result,
        )

    def _model_set_picker_result(self, choice: str | None) -> None:
        if choice and choice.startswith("bundle|"):
            self._show_bundle_plan(choice.split("|", 1)[1])
        elif choice == "back":
            self.open_model_library()
        else:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()

    def _show_update_all_confirmation(self) -> None:
        names = [descriptor.name for descriptor in self._model_library_models]
        if not names:
            self.model_library_active = False
            self.set_notice("No installed models to update")
            return
        row = Text(f"REFRESH {len(names)} MODEL TAGS", style=f"bold {PALETTE['teal']}")
        for name in names[:8]:
            row.append("\n        " + clip_middle(name, 58), style=PALETTE["paper"])
        if len(names) > 8:
            row.append(f"\n        + {len(names) - 8} more", style=PALETTE["muted"])
        row.append("\n\n        Existing layers are reused; profiles and chats remain intact", style=PALETTE["muted"])
        self.push_screen(
            SelectorScreen(
                "MODEL LIBRARY / UPDATE INSTALLED",
                "Ollama checks each manifest in sequence and downloads only changed content.",
                [("confirm-update-all", row), ("back", Text("BACK", style=PALETTE["muted"]))],
            ),
            self._update_all_result,
        )

    def _update_all_result(self, choice: str | None) -> None:
        if choice == "confirm-update-all":
            self.start_model_transfer([descriptor.name for descriptor in self._model_library_models])
        elif choice == "back":
            self.open_model_library()
        else:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()

    def _show_bundle_plan(self, identifier: str) -> None:
        recommendation = self._model_library_recommendations[identifier]
        if not recommendation.models:
            self.model_library_active = False
            self.set_notice(f"{recommendation.bundle.label} coverage is already present")
            self.query_one("#composer", Composer).focus()
            return
        row = Text()
        row.append("INSTALL PLAN", style=f"bold {PALETTE['amber']}")
        for model in recommendation.models:
            row.append(f"\n        {model.name}", style=f"bold {PALETTE['paper']}")
            row.append(f"  ~{model.download_gib:.1f} GiB", style=PALETTE["brass"])
        row.append(f"\n        TOTAL  ~{recommendation.download_gib:.1f} GiB", style=f"bold {PALETTE['brass']}")
        warning = self._model_transfer_space_warning([model.name for model in recommendation.models])
        if warning:
            row.append("\n\n        STORAGE WARNING  ", style=f"bold {PALETTE['red']}")
            row.append(warning, style=PALETTE["muted"])
        cancel = Text("BACK", style=f"bold {PALETTE['muted']}")
        screen = SelectorScreen(
            f"SETUP / {recommendation.bundle.label.upper()}",
            "Downloads can be cancelled with Esc. Ollama reuses layers already present and verifies content before activation.",
            [(f"install-bundle|{identifier}", row), ("back", cancel)],
            highlighted=1 if warning else 0,
        )
        self.push_screen(screen, self._bundle_plan_result)

    def _bundle_plan_result(self, choice: str | None) -> None:
        if choice and choice.startswith("install-bundle|"):
            recommendation = self._model_library_recommendations[choice.split("|", 1)[1]]
            self.start_model_transfer([model.name for model in recommendation.models])
        elif choice == "back":
            self.open_model_library()
        else:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()

    def open_custom_model_input(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before installing a model")
            return
        self.model_library_active = True
        screen = TextInputScreen(
            "INSTALL / OLLAMA MODEL",
            "Enter any Ollama model name or tag. Recommended profiles are optional and Coordinator does not require a fixed stack.",
            placeholder="model:tag",
        )
        self.push_screen(screen, self._custom_model_input_result)

    def _custom_model_input_result(self, value: str | None) -> None:
        if not value:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()
            return
        try:
            name = validate_model_name(value)
        except ValueError as exc:
            self.model_library_active = False
            self.append_error(str(exc))
            return
        self._show_custom_model_confirmation(name)

    def _model_transfer_space_warning(self, models: list[str]) -> str:
        known = [catalog_model(name) for name in models]
        estimate = sum(model.download_gib for model in known if model)
        if not estimate:
            return ""
        free = local_ollama_free_bytes(str(getattr(self.provider, "host", "127.0.0.1:11434")))
        if free is None:
            return ""
        free_gib = free / 1024**3
        if free_gib >= estimate * 1.15:
            return ""
        return f"{free_gib:.1f} GiB free; plan is approximately {estimate:.1f} GiB plus working space."

    def _show_custom_model_confirmation(self, name: str) -> None:
        self.model_library_active = True
        known = catalog_model(name)
        row = Text("INSTALL  ", style=f"bold {PALETTE['amber']}") + Text(name, style=f"bold {PALETTE['paper']}")
        if known:
            row.append(f"\n        estimated download {known.download_gib:.1f} GiB", style=PALETTE["brass"])
            row.append("\n        " + ", ".join(known.capabilities).upper(), style=PALETTE["teal"])
        else:
            row.append("\n        size and capabilities will be reported by Ollama", style=PALETTE["muted"])
        warning = self._model_transfer_space_warning([name])
        if warning:
            row.append("\n\n        STORAGE WARNING  ", style=f"bold {PALETTE['red']}")
            row.append(warning, style=PALETTE["muted"])
        screen = SelectorScreen(
            "CONFIRM MODEL INSTALL",
            "Pulling an existing tag checks for an updated manifest; unchanged layers are not downloaded again.",
            [(f"install-custom|{name}", row), ("back", Text("BACK", style=PALETTE["muted"]))],
            highlighted=1 if warning else 0,
        )
        self.push_screen(screen, self._custom_model_confirm_result)

    def _custom_model_confirm_result(self, choice: str | None) -> None:
        if choice and choice.startswith("install-custom|"):
            self.start_model_transfer([choice.split("|", 1)[1]])
        elif choice == "back":
            self.open_custom_model_input()
        else:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()

    def start_model_transfer(self, models: list[str]) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before installing models")
            return
        self.model_library_active = True
        self.push_screen(ModelTransferScreen(self.provider, models), self._model_transfer_result)

    def _model_transfer_result(self, result: dict[str, Any] | None) -> None:
        result = result or {}
        if not result.get("success"):
            self.model_library_active = False
            if result.get("cancelled"):
                self.set_warning_notice("Model transfer cancelled")
            elif result.get("error"):
                self.append_error(f"model transfer failed: {result['error']}")
            self.query_one("#composer", Composer).focus()
            return
        models = [str(model) for model in result.get("models", [])]
        self.set_busy(True, "calibrating installed models", interruptible=False)

        def worker() -> None:
            try:
                initialized = initialize(PATHS, getattr(self.provider, "host", None))
                self._dispatch(self._model_transfer_ready, initialized, models)
            except Exception as exc:
                self.model_library_active = False
                self.append_error(f"models installed, but profile calibration failed: {exc}")
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-model-calibration")

    def _model_transfer_ready(self, result: InitializationResult, models: list[str]) -> None:
        with self.lock:
            self.config.clear()
            self.config.update(result.config)
        self.model_library_active = False
        self.append_system(
            "MODEL LIBRARY UPDATED\n"
            + "\n".join(f"ready  {model}" for model in models)
            + f"\n\n{len(result.registry.models)} model(s) calibrated for the active compute endpoint."
        )
        self.save_current_chat()
        self.set_notice("Model installation complete")
        self.query_one("#composer", Composer).focus()

    def _show_installed_model_actions(self, name: str) -> None:
        descriptor = next((model for model in self._model_library_models if model.name == name), None)
        if not descriptor:
            self.model_library_active = False
            self.set_notice("Model registry changed; reopen the library")
            return
        active = str(self.config.get("model") or "") == name
        use = Text()
        use.append("ACTIVE  " if active else "        ", style=f"bold {PALETTE['olive']}")
        use.append("USE DIRECTLY", style=f"bold {PALETTE['paper']}")
        use.append("\n        Bypass Coordinator and make this the active model", style=PALETTE["muted"])
        update = Text("        CHECK FOR UPDATE", style=f"bold {PALETTE['amber']}")
        update.append("\n        Re-pull this tag; unchanged layers are reused", style=PALETTE["muted"])
        profile = Text("        PROFILE / ADVANCED", style=f"bold {PALETTE['teal']}")
        profile.append(
            "\n        Inspect generated fit and tune context, batching, or thinking", style=PALETTE["muted"]
        )
        remove = Text("        REMOVE MODEL", style=f"bold {PALETTE['red']}")
        remove.append(
            f"\n        Permanently release {descriptor.size / 1024**3:.1f} GiB from Ollama storage",
            style=PALETTE["muted"],
        )
        screen = SelectorScreen(
            f"MODEL / {clip_middle(name, 60)}",
            "Model actions are explicit. Updating preserves your profile overrides; removal does not affect chats.",
            [
                (f"use|{name}", use),
                (f"update|{name}", update),
                (f"profile|{name}", profile),
                (f"remove|{name}", remove),
                ("back", Text("        BACK", style=PALETTE["muted"])),
            ],
        )
        self.push_screen(screen, self._installed_model_action_result)

    def _installed_model_action_result(self, choice: str | None) -> None:
        if not choice:
            self.model_library_active = False
        elif choice == "back":
            self.open_model_library()
        elif choice.startswith("use|"):
            self.model_library_active = False
            self.apply_model_choice(choice.split("|", 1)[1])
        elif choice.startswith("update|"):
            self.start_model_transfer([choice.split("|", 1)[1]])
        elif choice.startswith("profile|"):
            name = choice.split("|", 1)[1]
            self._show_model_profile(name)
        elif choice.startswith("remove|"):
            self._show_model_remove_confirmation(choice.split("|", 1)[1])
        self.query_one("#composer", Composer).focus()

    def _show_model_profile(self, name: str) -> None:
        registry = load_registry(PATHS)
        record = registry.models.get(name) if registry else None
        if not record:
            self.model_library_active = False
            self.set_notice("Model profile changed; refresh the library")
            return
        self._profile_model = name
        automatic = record.runtime.to_profile()
        effective = record.effective_runtime()
        auto_options = automatic.get("options") if isinstance(automatic.get("options"), dict) else {}
        options = effective.get("options") if isinstance(effective.get("options"), dict) else {}
        rows: list[tuple[str, Text]] = []
        fields = (
            ("ctx", "CONTEXT", effective.get("num_ctx"), automatic.get("num_ctx"), "tokens available to each request"),
            ("batch", "BATCH", options.get("num_batch"), auto_options.get("num_batch"), "prompt processing batch size"),
            ("threads", "CPU THREADS", options.get("num_thread"), auto_options.get("num_thread"), "CPU worker threads"),
            (
                "gpu",
                "GPU LAYERS",
                options.get("num_gpu", "auto"),
                auto_options.get("num_gpu", "auto"),
                "Ollama layer offload override",
            ),
        )
        for field, label, value, generated, detail in fields:
            row = Text("        " + label, style=f"bold {PALETTE['paper']}")
            row.append(f"  / {value}", style=f"bold {PALETTE['brass']}")
            row.append(f"  AUTO {generated}", style=PALETTE["quiet"])
            row.append("\n        " + detail, style=PALETTE["muted"])
            rows.append((f"profile-edit|{field}", row))
        think = bool(effective.get("think", False))
        generated_think = bool(automatic.get("think", False))
        think_row = Text(
            "ON      " if think else "OFF     ", style=f"bold {PALETTE['olive'] if think else PALETTE['quiet']}"
        )
        think_row.append("MODEL THINKING", style=f"bold {PALETTE['paper']}")
        think_row.append(f"  / AUTO {'ON' if generated_think else 'OFF'}", style=PALETTE["quiet"])
        think_row.append("\n        expose additional reasoning when the model supports it", style=PALETTE["muted"])
        rows.append(("profile-think", think_row))
        capability = record.effective_capability()
        source = (
            "CURATED" if "catalog" in capability.source else "CALIBRATED" if "user" in capability.source else "INFERRED"
        )
        calibration = Text("        ROUTING CALIBRATION", style=f"bold {PALETTE['teal']}")
        calibration.append(f"  / {source} {capability.confidence * 100:.0f}%", style=PALETTE["brass"])
        calibration.append("\n        advanced capability priors used only by Coordinator", style=PALETTE["muted"])
        rows.append(("profile-capabilities", calibration))
        if record.override.get("runtime"):
            reset = Text("        RESTORE HARDWARE DEFAULTS", style=f"bold {PALETTE['red']}")
            reset.append("\n        remove context, batch, thread, GPU, and thinking overrides", style=PALETTE["muted"])
            rows.append(("profile-reset", reset))
        rows.append(("back", Text("        BACK", style=PALETTE["muted"])))
        self.push_screen(
            SelectorScreen(
                f"PROFILE / {clip_middle(name, 56)}",
                f"{record.role}. {effective.get('fit', 'unknown')} hardware fit; changes are per model and reversible.",
                rows,
            ),
            self._model_profile_result,
        )

    def _model_profile_result(self, choice: str | None) -> None:
        if not choice:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()
            return
        if choice == "back":
            self._show_installed_model_actions(self._profile_model)
        elif choice.startswith("profile-edit|"):
            self._profile_field = choice.split("|", 1)[1]
            current = self._profile_field_value(self._profile_model, self._profile_field)
            detail = {
                "ctx": "Context tokens, from 512 to 262144.",
                "batch": "Prompt batch size, from 1 to 4096.",
                "threads": "CPU worker threads, from 1 to 64.",
                "gpu": "GPU layers, from 0 to 999. Use hardware defaults unless you have measured a better value.",
            }[self._profile_field]
            self.push_screen(
                TextInputScreen(
                    f"PROFILE / {self._profile_field.upper()}",
                    detail,
                    placeholder="numeric value",
                    value=str(current),
                ),
                self._model_profile_input_result,
            )
        elif choice == "profile-think":
            registry = load_registry(PATHS)
            record = registry.models.get(self._profile_model) if registry else None
            value = not bool(record.effective_runtime().get("think", False)) if record else False
            self._apply_profile_value("think", "on" if value else "off")
        elif choice == "profile-capabilities":
            self._show_capability_calibration()
        elif choice == "profile-reset":
            self.core.reset_profile_override(self.config, self._profile_model)
            if str(self.config.get("model") or "") == self._profile_model:
                self.core.apply_model_profile(self.config, self._profile_model)
            self.core.save_config(self.config)
            self.save_current_chat()
            self.set_notice("Hardware profile defaults restored")
            self.call_later(self._show_model_profile, self._profile_model)

    def _profile_field_value(self, model: str, field: str) -> Any:
        registry = load_registry(PATHS)
        record = registry.models.get(model) if registry else None
        if not record:
            return ""
        runtime = record.effective_runtime()
        options = runtime.get("options") if isinstance(runtime.get("options"), dict) else {}
        return {
            "ctx": runtime.get("num_ctx", 4096),
            "batch": options.get("num_batch", 128),
            "threads": options.get("num_thread", 1),
            "gpu": options.get("num_gpu", 0),
        }[field]

    def _model_profile_input_result(self, value: str | None) -> None:
        if value is None:
            self.call_later(self._show_model_profile, self._profile_model)
            return
        self._apply_profile_value(self._profile_field, value)

    def _apply_profile_value(self, field: str, value: str) -> None:
        try:
            detail = self.core.set_profile_override(self.config, self._profile_model, field, value)
            self.core.persist_profile_override(self.config, self._profile_model)
            if str(self.config.get("model") or "") == self._profile_model:
                self.core.apply_model_profile(self.config, self._profile_model)
            self.core.save_config(self.config)
        except (TypeError, ValueError) as exc:
            self.append_system(f"profile setting rejected: {exc}")
        else:
            self.save_current_chat()
            self.set_notice(detail)
        self.call_later(self._show_model_profile, self._profile_model)

    def _show_capability_calibration(self) -> None:
        registry = load_registry(PATHS)
        record = registry.models.get(self._profile_model) if registry else None
        if not record:
            self.model_library_active = False
            self.set_notice("Model profile changed; refresh the library")
            return
        capability = record.effective_capability()
        options: list[tuple[str, Text]] = []
        for field in CAPABILITY_NAMES:
            row = Text("        " + field.upper(), style=f"bold {PALETTE['paper']}")
            row.append(f"  / {getattr(capability, field):.2f}", style=f"bold {PALETTE['brass']}")
            options.append((f"capability|{field}", row))
        if record.override.get("capabilities"):
            reset = Text("        RESTORE AUTOMATIC PRIORS", style=f"bold {PALETTE['red']}")
            reset.append("\n        remove all user capability calibration for this model", style=PALETTE["muted"])
            options.append(("capability-reset", reset))
        options.append(("back", Text("        BACK", style=PALETTE["muted"])))
        self.push_screen(
            SelectorScreen(
                f"ROUTING CALIBRATION / {clip_middle(self._profile_model, 44)}",
                "Advanced values range from 0.0 to 1.0. They affect ranking, never model compatibility or hard vision gates.",
                options,
            ),
            self._capability_calibration_result,
        )

    def _capability_calibration_result(self, choice: str | None) -> None:
        if not choice or choice == "back":
            self.call_later(self._show_model_profile, self._profile_model)
            return
        if choice == "capability-reset":
            clear_capability_overrides(self._profile_model, PATHS)
            self.set_notice("Routing calibration restored")
            self.call_later(self._show_capability_calibration)
            return
        self._profile_field = choice.split("|", 1)[1]
        registry = load_registry(PATHS)
        record = registry.models.get(self._profile_model) if registry else None
        current = getattr(record.effective_capability(), self._profile_field) if record else 0.5
        self.push_screen(
            TextInputScreen(
                f"CALIBRATE / {self._profile_field.upper()}",
                "Enter a measured routing prior from 0.0 to 1.0. This marks the model profile as user calibrated.",
                placeholder="0.00 - 1.00",
                value=f"{current:.2f}",
            ),
            self._capability_input_result,
        )

    def _capability_input_result(self, value: str | None) -> None:
        if value is None:
            self.call_later(self._show_capability_calibration)
            return
        try:
            set_capability_override(self._profile_model, self._profile_field, float(value), PATHS)
        except (TypeError, ValueError) as exc:
            self.append_system(f"calibration rejected: {exc}")
        else:
            self.set_notice(f"{self._profile_field} calibration updated")
        self.call_later(self._show_capability_calibration)

    def _show_model_remove_confirmation(self, name: str) -> None:
        descriptor = next((model for model in self._model_library_models if model.name == name), None)
        size = descriptor.size / 1024**3 if descriptor else 0.0
        confirm = Text("REMOVE  ", style=f"bold {PALETTE['red']}") + Text(name, style=f"bold {PALETTE['paper']}")
        confirm.append(f"\n        delete approximately {size:.1f} GiB at the compute endpoint", style=PALETTE["muted"])
        screen = SelectorScreen(
            "CONFIRM / REMOVE MODEL",
            "This removes the model from Ollama. Conversation history remains, but saved routes may reference it.",
            [(f"confirm-remove|{name}", confirm), ("back", Text("BACK", style=PALETTE["muted"]))],
            highlighted=1,
        )
        self.push_screen(screen, self._model_remove_confirmation_result)

    def _model_remove_confirmation_result(self, choice: str | None) -> None:
        if choice and choice.startswith("confirm-remove|"):
            self._remove_model_async(choice.split("|", 1)[1])
        elif choice == "back":
            self.open_model_library()
        else:
            self.model_library_active = False
            self.query_one("#composer", Composer).focus()

    def _remove_model_async(self, name: str) -> None:
        self.set_busy(True, f"removing {name}", interruptible=False)

        def worker() -> None:
            try:
                remove_model(self.provider, name)
                initialized = initialize(PATHS, getattr(self.provider, "host", None))
                self._dispatch(self._model_remove_ready, initialized, name)
            except Exception as exc:
                self.model_library_active = False
                self.append_error(f"model removal failed: {exc}")
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-model-remove")

    def _model_remove_ready(self, result: InitializationResult, name: str) -> None:
        with self.lock:
            self.config.clear()
            self.config.update(result.config)
        self.append_system(f"Removed compute model: {name}")
        self.save_current_chat()
        self.set_notice("Model removed")
        self.open_model_library()

    def open_coordinator_settings(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before changing Coordinator")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before changing Coordinator")
            return
        self._coordinator_settings_active = True
        enabled = self._orchestrator_enabled()
        policy = str(self.config.get("orchestrator_policy") or "adaptive")
        options: list[tuple[str, Text]] = []
        coverage = installed_role_coverage(load_registry(PATHS))
        core_roles = ("general", "coding", "reasoning", "vision")
        missing_roles = [role for role in core_roles if role not in coverage]
        mode = Text("ACTIVE  " if enabled else "        ", style=f"bold {PALETTE['olive']}")
        mode.append("COORDINATOR MODE", style=f"bold {PALETTE['amber']}")
        mode.append("\n        automatic task routing with direct-model fallback", style=PALETTE["muted"])
        options.append(("toggle-mode", mode))
        policy_row = Text("        OPERATING POLICY", style=f"bold {PALETTE['paper']}")
        policy_row.append(f"  / {policy.upper()}", style=f"bold {PALETTE['brass']}")
        policy_row.append("\n        quality, latency, residency, and handoff thresholds", style=PALETTE["muted"])
        options.append(("policy", policy_row))
        feature_labels = {
            "planning": ("PLANNING", "create an internal execution brief for complex work"),
            "review": ("INDEPENDENT REVIEW", "verify high-risk answers with a separate pass"),
            "delegation": ("SPECIALIST DELEGATION", "allow bounded model-to-model consultation"),
            "semantic": ("SEMANTIC ARBITRATION", "classify intent and capability demand for meaningful work"),
            "learning": ("BOUNDED LEARNING", "calibrate close model choices from trusted outcomes"),
        }
        for feature, (label, detail) in feature_labels.items():
            key = (
                "orchestrator_semantic_routing"
                if feature == "semantic"
                else "coordinator_learning"
                if feature == "learning"
                else f"orchestrator_{feature}"
            )
            active = bool(self.config.get(key, True))
            row = Text(
                "ON      " if active else "OFF     ", style=f"bold {PALETTE['olive'] if active else PALETTE['quiet']}"
            )
            row.append(label, style=f"bold {PALETTE['paper']}")
            row.append("\n        " + detail, style=PALETTE["muted"])
            options.append((f"feature|{feature}", row))
        preferences = self.config.get("coordinator_role_preferences")
        count = len(preferences) if isinstance(preferences, dict) else 0
        roles = Text("        ROLE PREFERENCES", style=f"bold {PALETTE['teal']}")
        roles.append(f"  / {count} SET" if count else "  / AUTOMATIC", style=PALETTE["brass"])
        roles.append("\n        optional soft preferences; unavailable models fall back safely", style=PALETTE["muted"])
        roles.append(
            "\n        coverage "
            + (" / ".join(role for role in core_roles if role in coverage) or "general fallback only")
            + (f"  / gaps {', '.join(missing_roles)}" if missing_roles else "  / complete"),
            style=PALETTE["quiet"],
        )
        options.append(("roles", roles))
        reset = Text("        RESTORE AUTOMATIC DEFAULTS", style=f"bold {PALETTE['muted']}")
        options.append(("reset", reset))
        screen = SelectorScreen(
            "COORDINATOR / CONTROL",
            "Automatic routing needs no manual assignment. Advanced controls tune depth without changing model compatibility.",
            options,
        )
        self.push_screen(screen, self._coordinator_settings_result)

    def _coordinator_settings_result(self, choice: str | None) -> None:
        if not choice:
            self._coordinator_settings_active = False
            self.query_one("#composer", Composer).focus()
            return
        if choice == "toggle-mode":
            if self._orchestrator_enabled():
                if not self.config.get("model"):
                    self.set_warning_notice("Install and select a model before using direct mode")
                else:
                    self.config["model_mode"] = "direct"
            else:
                self.config["model_mode"] = "orchestrator"
            self._save_coordinator_setting("Coordinator mode updated")
        elif choice == "policy":
            self._show_coordinator_policy_settings()
        elif choice.startswith("feature|"):
            feature = choice.split("|", 1)[1]
            key = (
                "orchestrator_semantic_routing"
                if feature == "semantic"
                else "coordinator_learning"
                if feature == "learning"
                else f"orchestrator_{feature}"
            )
            self.config[key] = not bool(self.config.get(key, True))
            self._save_coordinator_setting(f"Coordinator {feature}: {'on' if self.config[key] else 'off'}")
        elif choice == "roles":
            self._show_coordinator_role_picker()
        elif choice == "reset":
            defaults = default_config()
            for key in (
                "model_mode",
                "orchestrator_policy",
                "orchestrator_planning",
                "orchestrator_review",
                "orchestrator_delegation",
                "orchestrator_semantic_routing",
                "coordinator_learning",
                "coordinator_role_preferences",
            ):
                self.config[key] = defaults[key]
            self._save_coordinator_setting("Coordinator defaults restored")

    def _save_coordinator_setting(self, notice: str, reopen: bool = True) -> None:
        self._active_route = None
        self._route_config = None
        self.core.save_config(self.config)
        self.save_current_chat()
        self.set_notice(notice)
        if reopen:
            self.call_later(self.open_coordinator_settings)

    def _show_coordinator_policy_settings(self) -> None:
        current = str(self.config.get("orchestrator_policy") or "adaptive")
        definitions = {
            "adaptive": "Balances capability, latency, task risk, and residency per request.",
            "quality": "Lowers planning and review thresholds for deliberate high-quality execution.",
            "efficient": "Prefers resident models and single-pass completion for routine work.",
        }
        options: list[tuple[str, Text]] = []
        active = 0
        for index, (policy, detail) in enumerate(definitions.items()):
            if policy == current:
                active = index
            row = Text("ACTIVE  " if policy == current else "        ", style=f"bold {PALETTE['olive']}")
            row.append(policy.upper(), style=f"bold {PALETTE['amber']}")
            row.append("\n        " + detail, style=PALETTE["muted"])
            options.append((f"setting-policy|{policy}", row))
        self.push_screen(
            SelectorScreen(
                "COORDINATOR / POLICY",
                "Policy changes thresholds, not model compatibility. Adaptive is the consumer default.",
                options,
                highlighted=active,
            ),
            self._coordinator_policy_settings_result,
        )

    def _coordinator_policy_settings_result(self, choice: str | None) -> None:
        if choice and choice.startswith("setting-policy|"):
            policy = choice.split("|", 1)[1]
            self.config["orchestrator_policy"] = policy
            self.config["model_mode"] = "orchestrator"
            self._save_coordinator_setting(f"Coordinator policy: {policy}")
        else:
            self.call_later(self.open_coordinator_settings)

    def _show_coordinator_role_picker(self) -> None:
        preferences = self.config.get("coordinator_role_preferences")
        preferences = preferences if isinstance(preferences, dict) else {}
        descriptions = {
            "general": "ordinary questions and broad synthesis",
            "coding": "implementation and code-heavy requests",
            "agent": "multi-step tool and repository work",
            "reasoning": "deep analysis and difficult decisions",
            "vision": "images and visual inspection",
            "planner": "internal execution briefs",
            "reviewer": "independent quality gates",
        }
        options = []
        for role, detail in descriptions.items():
            current = str(preferences.get(role) or "AUTOMATIC")
            row = Text("        " + role.upper(), style=f"bold {PALETTE['paper']}")
            row.append(
                f"  / {clip_middle(current, 34)}", style=PALETTE["teal"] if current != "AUTOMATIC" else PALETTE["quiet"]
            )
            row.append("\n        " + detail, style=PALETTE["muted"])
            options.append((f"role|{role}", row))
        self.push_screen(
            SelectorScreen(
                "COORDINATOR / ROLE PREFERENCES",
                "Preferences are soft score bonuses. Capability checks and automatic fallback remain active.",
                options,
            ),
            self._coordinator_role_result,
        )

    def _coordinator_role_result(self, choice: str | None) -> None:
        if not choice:
            self.call_later(self.open_coordinator_settings)
            return
        self._coordinator_preference_role = choice.split("|", 1)[1]
        self.set_busy(True, "reading model capabilities", interruptible=False)

        def worker() -> None:
            try:
                models = self.provider.list_models()
                self._dispatch(self._show_coordinator_role_models, models)
            except Exception as exc:
                self.append_error(f"could not list models: {exc}")
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-coordinator-roles")

    def _show_coordinator_role_models(self, models: list[Any]) -> None:
        role = self._coordinator_preference_role
        preferences = self.config.get("coordinator_role_preferences")
        current = str(preferences.get(role) or "") if isinstance(preferences, dict) else ""
        options: list[tuple[str, Text]] = []
        automatic = Text("ACTIVE  " if not current else "        ", style=f"bold {PALETTE['olive']}")
        automatic.append("AUTOMATIC", style=f"bold {PALETTE['amber']}")
        automatic.append("\n        rank every available model for each request", style=PALETTE["muted"])
        options.append(("role-model|auto", automatic))
        for model in models:
            profile = self.core.model_profile_for(model.name)
            capability = self.core.model_capabilities(model)
            row = Text("ACTIVE  " if model.name == current else "        ", style=f"bold {PALETTE['olive']}")
            row.append(clip_middle(model.name, 46), style=f"bold {PALETTE['paper']}")
            row.append(
                f"\n        {profile.get('role', 'compute model') if profile else 'compute model'}",
                style=PALETTE["muted"],
            )
            field = "code" if role == "coding" else "general" if role in {"planner", "reviewer"} else role
            if field in capability:
                row.append(f"  / {field} {capability[field] * 100:.0f}%", style=PALETTE["brass"])
            options.append((f"role-model|{model.name}", row))
        self.push_screen(
            SelectorScreen(
                f"ROLE / {role.upper()}",
                "Select a soft preference or leave the role automatic. Unsupported input always falls back.",
                options,
                highlighted=next(
                    (
                        index
                        for index, model in enumerate(["auto", *[item.name for item in models]])
                        if model == current
                    ),
                    0,
                ),
            ),
            self._coordinator_role_model_result,
        )

    def _coordinator_role_model_result(self, choice: str | None) -> None:
        if not choice:
            self.call_later(self._show_coordinator_role_picker)
            return
        model = choice.split("|", 1)[1]
        preferences = self.config.setdefault("coordinator_role_preferences", {})
        if model == "auto":
            preferences.pop(self._coordinator_preference_role, None)
            detail = f"{self._coordinator_preference_role}: automatic"
        else:
            preferences[self._coordinator_preference_role] = model
            detail = f"{self._coordinator_preference_role}: prefer {model}"
        self._save_coordinator_setting(detail)

    def open_model_picker(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before switching models")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before switching models")
            return
        self.model_picker_active = True
        self.set_busy(True, "reading model registry", interruptible=False)

        def worker() -> None:
            try:
                models = self.provider.list_models()
                self.model_picker_items = models
                self._dispatch(self._show_model_picker_main, models)
            except Exception as exc:
                self.model_picker_active = False
                self.append_error(f"could not list models: {exc}")
            finally:
                self.set_busy(False)

        self.start_worker(worker, "dairack-models")

    def _show_model_picker_main(self, models: list[Any]) -> None:
        if not models:
            self.model_picker_active = False
            self.set_warning_notice("No compute models installed. Use /library to install one.")
            return
        current = str(self.config.get("model") or "")
        orchestrator_active = self._orchestrator_enabled()
        options: list[tuple[str, Text]] = []
        active_index = 0
        policy = str(self.config.get("orchestrator_policy") or "adaptive").upper()
        compact = self.size.width < 60
        orchestrator_row = Text()
        orchestrator_row.append("ACTIVE  " if orchestrator_active else "        ", style=f"bold {PALETTE['olive']}")
        orchestrator_row.append("COORDINATOR", style=f"bold {PALETTE['amber']}")
        orchestrator_row.append(f"  / {policy}", style=f"bold {PALETTE['brass']}")
        orchestrator_row.append(
            "\n        TASK CONTROL\n        route / delegate / judge"
            if compact
            else "\n        TASK CONTROL  routing / delegation / quality gates",
            style=PALETTE["teal"],
        )
        orchestrator_row.append(
            (
                f"\n        plan {'on' if self.config.get('orchestrator_planning', True) else 'off'}"
                f"  delegate {'on' if self.config.get('orchestrator_delegation', True) else 'off'}"
                f"\n        review {'on' if self.config.get('orchestrator_review', True) else 'off'}"
                f"  semantic {'on' if self.config.get('orchestrator_semantic_routing', True) else 'off'}"
                if compact
                else f"\n        planning {'on' if self.config.get('orchestrator_planning', True) else 'off'}"
                f"  delegation {'on' if self.config.get('orchestrator_delegation', True) else 'off'}"
                f"  review {'on' if self.config.get('orchestrator_review', True) else 'off'}"
                f"\n        semantic arbitration {'on' if self.config.get('orchestrator_semantic_routing', True) else 'off'}"
            ),
            style=PALETTE["quiet"],
        )
        options.append((self.core.ORCHESTRATOR_MODEL_ID, orchestrator_row))
        for index, model in enumerate(models, start=1):
            if not orchestrator_active and model.name == current:
                active_index = index
            profile = self.core.model_profile_for(model.name)
            role = str(profile.get("role") if profile else "custom compute model")
            row = Text()
            row.append(
                "ACTIVE  " if not orchestrator_active and model.name == current else "        ",
                style=f"bold {PALETTE['olive']}",
            )
            name_width = max(14, min(48, self.size.width - 22))
            row.append(clip_middle(model.name, name_width), style=f"bold {PALETTE['paper']}")
            row.append(f"\n        {self.core.size_human(model.size)}", style=PALETTE["brass"])
            if model.params:
                row.append(f"  {model.params}", style=PALETTE["muted"])
            if model.quant:
                row.append(f"/{model.quant}", style=PALETTE["quiet"])
            if "vision" in {str(value).lower() for value in (getattr(model, "capabilities", ()) or ())}:
                row.append("  VISION", style=f"bold {PALETTE['teal']}")
            row.append(f"\n        {clip_middle(role, name_width)}", style=PALETTE["quiet"])
            options.append((model.name, row))
        screen = SelectorScreen(
            "INTELLIGENCE / OPERATING MODE",
            f"The coordinator assigns bounded work by capability and compute cost. {len(models)} direct models remain available.",
            options,
            highlighted=active_index,
            family="library",
        )
        self.push_screen(screen, self._model_picker_result)

    def _model_picker_result(self, choice: str | None) -> None:
        if choice == self.core.ORCHESTRATOR_MODEL_ID:
            self.call_later(self._show_orchestrator_policy_picker)
            return
        self.model_picker_active = False
        if choice:
            self.apply_model_choice(choice)
            self.set_notice(self._display_model(include_executor=False))
        self.query_one("#composer", Composer).focus()

    def _show_orchestrator_policy_picker(self) -> None:
        current = str(self.config.get("orchestrator_policy") or "adaptive")
        definitions = [
            (
                "adaptive",
                "ADAPTIVE",
                "Balances quality, latency, model residency, and task risk. Adds stages only when justified.",
                "DEFAULT",
            ),
            (
                "quality",
                "QUALITY",
                "Routes for maximum capability and invokes planning or independent review at lower thresholds.",
                "DELIBERATE",
            ),
            (
                "efficient",
                "EFFICIENT",
                "Prioritizes latency, resident models, and single-pass execution for routine work.",
                "LEAN",
            ),
        ]
        options: list[tuple[str, Text]] = []
        active_index = 0
        for index, (policy, title, detail, badge) in enumerate(definitions):
            if policy == current:
                active_index = index
            row = Text()
            row.append("ACTIVE  " if policy == current else "        ", style=f"bold {PALETTE['olive']}")
            row.append(title, style=f"bold {PALETTE['amber']}")
            row.append(f"  / {badge}", style=PALETTE["brass"])
            if self.size.width < 60:
                compact_detail = {
                    "adaptive": "quality / latency\nrisk-aware stages",
                    "quality": "maximum capability\nplan + review",
                    "efficient": "latency / residency\nsingle pass",
                }[policy]
                row.append("\n        " + compact_detail.replace("\n", "\n        "), style=PALETTE["muted"])
            else:
                row.append("\n        " + detail, style=PALETTE["muted"])
            options.append((f"orchestrator-policy:{policy}", row))
        screen = SelectorScreen(
            "COORDINATOR / OPERATING POLICY",
            "Policy controls handoff frequency and depth. Task scoring and hardware profiles remain automatic.",
            options,
            highlighted=active_index,
        )
        self.push_screen(screen, self._orchestrator_policy_result)

    def _orchestrator_policy_result(self, choice: str | None) -> None:
        self.model_picker_active = False
        if choice and choice.startswith("orchestrator-policy:"):
            policy = choice.split(":", 1)[1]
            self.core.configure_orchestrator(self.config, [policy])
            self._active_route = None
            self._route_config = None
            self.core.save_config(self.config)
            self.append_system(self.core.orchestrator_status(self.config))
            self.save_current_chat()
            self.set_notice(f"COORDINATOR / {policy.upper()}")
        self.query_one("#composer", Composer).focus()

    def close_model_picker(self) -> None:
        self.model_picker_active = False
        if isinstance(self.screen, SelectorScreen):
            self.pop_screen()

    def move_model_picker(self, _delta: int) -> None:
        return

    def select_model_picker(self) -> None:
        return

    def apply_model_choice(self, choice: str) -> None:
        self._active_route = None
        self._route_config = None
        if choice in {
            self.core.ORCHESTRATOR_MODEL_ID,
            self.core.LEGACY_ORCHESTRATOR_MODEL_ID,
        } or choice.lower() in {"coordinator", "orchestrator", "auto"}:
            self.config["model_mode"] = "orchestrator"
            detail = self.core.orchestrator_status(self.config)
            self.append_system("coordinator selected\n" + detail)
        else:
            self.config["model_mode"] = "direct"
            profile_note = self.core.apply_model_profile(self.config, choice)
            self.append_system(f"direct model selected: {choice}\n{profile_note}")
        self.core.save_config(self.config)
        self.save_current_chat()
        self.invalidate()

    def open_chat_picker(self) -> None:
        if self.busy:
            self.set_warning_notice("Stop the active response before changing chats")
            return
        if self.pending_tool:
            self.set_warning_notice("Resolve the action request before changing chats")
            return
        sessions = self.core.list_chat_sessions(limit=80)
        self.chat_picker_active = True
        current_id = str(self.chat.get("id") or "")
        fresh = Text("NEW SESSION", style=f"bold {PALETTE['amber']}")
        fresh.append("\n        Clean context; saved conversations remain in the archive", style=PALETTE["muted"])
        startup_mode = str(self.config.get("startup_chat") or "new")
        startup = Text("STARTUP BEHAVIOR", style=f"bold {PALETTE['paper']}")
        startup.append(
            "\n        " + ("New session" if startup_mode == "new" else "Resume most recent") + " on launch",
            style=PALETTE["muted"],
        )
        heading = Text("SESSIONS", style=f"bold {PALETTE['quiet']}")
        options: list[tuple[str, Text]] = [
            ("__new__", fresh),
            ("__startup__", startup),
            ("__heading__:sessions", heading),
        ]
        active_index = 0
        for index, session in enumerate(sessions):
            chat_id = str(session.get("id") or "")
            if chat_id == current_id:
                active_index = index + 3
            updated = archive_time(str(session.get("updated_at") or ""))
            title = self.core.clean_chat_title(str(session.get("title") or ""), "new chat")
            if str(session.get("model_mode") or "direct") == "orchestrator":
                policy = str(session.get("orchestrator_policy") or "adaptive").upper()
                route = session.get("last_route") if isinstance(session.get("last_route"), dict) else {}
                executor = str(route.get("executor") or "").upper()
                model = f"COORD / {policy}" + (f" > {executor}" if executor else "")
            else:
                model = str(session.get("model") or "unknown model").upper()
            messages = self.core.sanitize_messages(session.get("messages"), self.cwd, False, self.config)
            count = self.core.message_count(messages)
            count_label = f"{count} {'MSG' if count == 1 else 'MSGS'}"
            row_width = max(28, min(82, self.size.width - 12))
            title_width = max(10, row_width - 8 - len(count_label) - 2)
            row = Text()
            row.append("ACTIVE  " if chat_id == current_id else "        ", style=f"bold {PALETTE['olive']}")
            clipped_title = clip_right(title, title_width)
            row.append(clipped_title.ljust(title_width), style=f"bold {PALETTE['paper']}")
            row.append(f"  {count_label}", style=PALETTE["brass"])
            row.append(f"\n        {updated}   {clip_model(model, 42)}", style=PALETTE["quiet"])
            options.append((chat_id, row))
        screen = SelectorScreen(
            "CHAT / SESSION ARCHIVE",
            f"{len(sessions)} saved conversation{'s' if len(sessions) != 1 else ''}. Fresh drafts are saved after the first prompt.",
            options,
            highlighted=active_index,
            family="archive",
        )
        self.push_screen(screen, self._chat_picker_result)

    def _chat_picker_result(self, choice: str | None) -> None:
        self.chat_picker_active = False
        if choice == "__new__":
            self.start_new_chat()
        elif choice == "__startup__":
            self._show_startup_behavior()
            return
        elif choice and choice != str(self.chat.get("id") or ""):
            try:
                self.load_chat(choice)
            except ValueError as exc:
                self.append_error(str(exc))
        self.query_one("#composer", Composer).focus()

    def _show_startup_behavior(self) -> None:
        current = str(self.config.get("startup_chat") or "new")
        fresh = Text("NEW SESSION", style=f"bold {PALETTE['amber'] if current == 'new' else PALETTE['paper']}")
        fresh.append("\n        Open on the animated welcome with clean context", style=PALETTE["muted"])
        resume = Text(
            "RESUME MOST RECENT",
            style=f"bold {PALETTE['amber'] if current == 'resume-last' else PALETTE['paper']}",
        )
        resume.append("\n        Continue the latest saved conversation on launch", style=PALETTE["muted"])
        self.push_screen(
            SelectorScreen(
                "CHAT / STARTUP",
                "This changes launch behavior only; F3 always keeps both paths available.",
                [("new", fresh), ("resume-last", resume), ("back", Text("BACK", style=PALETTE["muted"]))],
                highlighted=0 if current == "new" else 1,
            ),
            self._startup_behavior_result,
        )

    def _startup_behavior_result(self, choice: str | None) -> None:
        if choice in {"new", "resume-last"}:
            self.config["startup_chat"] = choice
            self.core.save_config(self.config)
            self.set_notice("Startup: new session" if choice == "new" else "Startup: resume most recent")
        elif choice == "back":
            self.open_chat_picker()
            return
        self.query_one("#composer", Composer).focus()

    def load_chat(self, ref: str = "latest") -> None:
        session = self.core.resolve_chat_session(ref)
        chat, cwd, messages, blocks = self.core.chat_runtime_state(session, self.cwd, self.config)
        self.chat = chat
        self.cwd = cwd
        self.messages = self.core.SynchronizedMessages(messages, self.lock)
        self.blocks = blocks
        if chat.get("model"):
            self.config["model"] = chat["model"]
        self.config["model_mode"] = str(chat.get("model_mode") or self.config.get("model_mode") or "direct")
        self.config["orchestrator_policy"] = str(
            chat.get("orchestrator_policy") or self.config.get("orchestrator_policy") or "adaptive"
        )
        self._active_route = None
        self._last_route = dict(chat.get("last_route") or {})
        self._route_config = None
        self._pending_images = []
        self._sync_attachment_state()
        self.config["last_chat"] = chat["id"]
        self.core.save_config(self.config)
        with self.lock:
            self.messages[0]["content"] = self.core.system_prompt(self.cwd, bool(self.config.get("agent")), self.config)
            self.blocks.append({"role": "system", "text": f"resumed chat: {self.chat['title']}"})
        self.save_current_chat()
        self._history = [
            str(message.get("content") or "")
            for message in self.messages
            if message.get("role") == "user"
            and not str(message.get("content") or "").startswith(
                ("Shell tool result:", "Patch tool result:", "Structured tool result:", "Tool result:")
            )
        ]
        self._history_index = len(self._history)
        self._dispatch(self._rebuild_transcript_main)
        self._dispatch(self._scroll_tail_main)
        self.set_notice(f"Resumed {self.chat['title']}")

    def start_new_chat(self, title: str = "") -> None:
        self.chat = self.core.new_chat_state(self.cwd, self.config, title)
        self.chat["_transient"] = True
        self.messages = self.core.SynchronizedMessages(
            [
                {
                    "role": "system",
                    "content": self.core.system_prompt(self.cwd, bool(self.config.get("agent")), self.config),
                }
            ],
            self.lock,
        )
        self.blocks = []
        self.pending_tool = None
        self._pending_images = []
        self._sync_attachment_state()
        self._active_route = None
        self._last_route = {}
        self._route_config = None
        self._history = []
        self._history_index = 0
        self.save_current_chat()
        self._dispatch(self._rebuild_transcript_main)
        self._welcome_started = time.monotonic()
        self.set_notice("New conversation ready")

    def _visible_stream_text(self, source: str) -> str:
        if not source:
            return ""
        lowered = source.lower()
        marker_positions = [
            position for marker in ("<tool", "<dairack_tool>") if (position := lowered.find(marker)) >= 0
        ]
        if marker_positions:
            return source[: min(marker_positions)].rstrip()
        return self.core.strip_tool_markup(source)

    def _request_start_label(self, model: str) -> str:
        try:
            resident = {name.lower() for name in self.provider.running_models()}
        except Exception:
            resident = set()
        phase = "processing context" if model.lower() in resident else "loading model"
        return f"{phase} / {model}"

    def start_generation(self, label: str = "loading model") -> None:
        self._active_route = None
        self._route_config = None
        self._route_plan = ""
        self._route_feedback = ""
        self._route_action_feedback = ""
        self._route_planned = False
        self._route_review_rounds = 0
        self._route_original_answer = ""
        self._executor_stats = {}
        if self._orchestrator_enabled():
            policy = str(self.config.get("orchestrator_policy") or "adaptive")
            label = f"routing / {policy}"
        self.legacy_tui_class.start_generation(self, label)

    def _prepare_active_route(self) -> tuple[dict[str, Any], dict[str, Any]]:
        project_root = self.core.project_scope_for_chat(self.chat, self.cwd)
        if self._active_route is None:
            policy = str(self.config.get("orchestrator_policy") or "adaptive")
            self.set_busy(True, f"routing / {policy}")
            route_started = time.monotonic()
            route = self.core.select_orchestrator_route(
                self.provider, self.config, self.messages, project_root, self.cancel_event
            )
            route["timings"] = {"route": round(time.monotonic() - route_started, 3)}
            route["passes"] = 0
            route["tool_steps"] = self._agent_steps_used
            route["tool_limit"] = self.core.agent_action_limit(self.config)
            executor = str(route.get("executor") or self.config.get("model") or "")
            if not executor:
                raise RuntimeError("no compute model is available for this route")
            self.core.require_vision_support(self.provider, executor, self.messages)
            self._active_route = route
            self._last_route = route
            self._route_config = self.core.runtime_config_for_model(self.config, executor)
            self.chat["last_route"] = route
            self.save_current_chat()
            self.invalidate()

        route = self._active_route
        runtime = self._route_config
        if route is None or runtime is None:
            raise RuntimeError("coordinator route was not initialized")
        if route.get("planner") and not self._route_planned:
            self._route_planned = True
            planner = str(route["planner"])
            self.set_busy(True, f"planning / {planner}")
            plan_started = time.monotonic()
            try:
                self._route_plan = self.core.orchestrator_plan(
                    self.provider,
                    route,
                    self.messages,
                    project_root,
                    self.config,
                    self.cancel_event,
                )
                route["plan_used"] = bool(self._route_plan)
            except Exception as exc:
                route["plan_error"] = str(exc)
                self._route_plan = ""
            route.setdefault("timings", {})["plan"] = round(time.monotonic() - plan_started, 3)
            self.chat["last_route"] = route
            self.save_current_chat()
        return route, runtime

    def _orchestrated_request_messages(
        self,
        runtime: dict[str, Any],
        revision_feedback: str = "",
        action_feedback: str = "",
        include_retrieval: bool = True,
    ) -> list[dict[str, str]]:
        request_messages = self.core.request_context_messages(
            self.messages,
            self.chat,
            runtime,
            self.cwd,
            include_retrieval=include_retrieval,
        )
        route = self._active_route or {}
        directives: list[str] = []
        directive = self.core.coordinator_executor_directive(route, self.config)
        if directive:
            directives.append(directive)
        if self._route_plan:
            directives.append(
                "Internal coordinator brief. Treat this as advisory, verify it against files and tool "
                "results, and do not mention the coordination process to the user:\n" + self._route_plan
            )
        if action_feedback:
            directives.append(action_feedback)
        if revision_feedback:
            directives.append(
                "Produce a complete corrected replacement answer. Do not discuss internal review or retry state, "
                "and do not call the answer a revision. Required corrections:\n" + revision_feedback
            )
        return self.core.canonicalize_messages(request_messages, directives)

    def generate_worker(self) -> None:
        try:
            route, runtime = self._prepare_active_route()
            if not self.maybe_auto_compact(runtime, str(route["executor"])):
                return
            if self.cancel_event.is_set():
                self.append_warning("Interrupted during coordination.")
                self.save_current_chat()
                return
            repairing_action = False
            action_repair_attempted = False
            action_contract_repair_attempted = False
            action_completion_repairs = 0
            completion_repair_attempted = False
            synthesis_attempts = 0
            while True:
                if self.cancel_event.is_set():
                    self.append_warning("Interrupted.")
                    self.save_current_chat()
                    return
                action_limit = self.core.agent_action_limit(self.config)
                finalizing = self._agent_steps_used >= action_limit or self._loop_guard.force_synthesis
                if finalizing:
                    synthesis_attempts += 1
                    if synthesis_attempts > 2:
                        self.append_warning(
                            "Task action budget reached; final synthesis could not be completed. "
                            "All action results remain in this chat."
                        )
                        self.save_current_chat()
                        return
                assistant_text = ""
                first_chunk = True
                revision_feedback = self._route_feedback
                action_feedback = self._route_action_feedback
                revising = bool(revision_feedback)
                self._route_feedback = ""
                self._route_action_feedback = ""
                reuse_assistant_block = revising or repairing_action
                repairing_action = False
                if reuse_assistant_block:
                    self.replace_last_assistant_text("")
                else:
                    self.append_assistant_start()

                def build_request(
                    include_retrieval: bool = True,
                    revision_feedback: str = revision_feedback,
                    action_feedback: str = action_feedback,
                    finalizing: bool = finalizing,
                    action_limit: int = action_limit,
                    synthesis_attempts: int = synthesis_attempts,
                ) -> list[dict[str, str]]:
                    built = self._orchestrated_request_messages(
                        runtime,
                        revision_feedback,
                        action_feedback,
                        include_retrieval,
                    )
                    if finalizing:
                        built = self.core.canonicalize_messages(
                            built,
                            [
                                self.core.agent_synthesis_directive(
                                    self._agent_steps_used,
                                    action_limit,
                                    retry=synthesis_attempts > 1,
                                )
                            ],
                        )
                    return built

                request_messages = build_request()
                executor = str(route["executor"])
                native_calls: list[dict[str, Any]] = []
                native_tools = (
                    []
                    if finalizing
                    else self.core.native_tools_for(
                        self.provider,
                        executor,
                        bool(self.config.get("agent")),
                        route,
                    )
                )
                try:
                    request_messages, native_tools = self.core.fit_agent_request_context_messages(
                        request_messages,
                        runtime,
                        native_tools,
                    )
                except self.core.RequestContextError:
                    request_messages, native_tools = self.core.fit_agent_request_context_messages(
                        build_request(include_retrieval=False),
                        runtime,
                        native_tools,
                    )
                    route["context_degraded"] = "project retrieval omitted to fit the context window"
                self.set_busy(
                    True,
                    f"synthesizing / {executor}" if finalizing else self._request_start_label(executor),
                )
                execution_started = time.monotonic()
                stream_retry_used = False
                while True:
                    try:
                        for chunk in self.provider.chat_stream(
                            executor,
                            request_messages,
                            think=bool(runtime.get("think")),
                            num_ctx=int(runtime.get("num_ctx") or 4096),
                            cancel_event=self.cancel_event,
                            extra_options=self.core.ollama_options(runtime),
                            tools=native_tools or None,
                            tool_call_sink=native_calls.append,
                        ):
                            if self.cancel_event.is_set():
                                break
                            if first_chunk:
                                phase = "synthesizing" if finalizing else "revising" if revising else "executing"
                                self.set_busy(True, f"{phase} / {executor}")
                                first_chunk = False
                            assistant_text += chunk
                            self._stream_chars = len(assistant_text)
                            self.replace_last_assistant_text(self._visible_stream_text(assistant_text))
                        break
                    except Exception as exc:
                        if stream_retry_used or self.cancel_event.is_set() or not self.core.transient_stream_error(exc):
                            raise
                        stream_retry_used = True
                        assistant_text = ""
                        native_calls.clear()
                        first_chunk = True
                        self.replace_last_assistant_text("")
                        self.set_busy(True, f"reconnecting / {executor}")
                timings = route.setdefault("timings", {})
                timings["execute"] = round(float(timings.get("execute") or 0) + time.monotonic() - execution_started, 3)
                route["passes"] = int(route.get("passes") or 0) + 1
                visible_text = self._visible_stream_text(assistant_text)
                call, parse_error = self.core.resolve_tool_request(assistant_text, native_calls)
                internal_call = bool(
                    call and self.core.is_internal_coordinator_call(self.core.normalize_coordinator_tool_call(call))
                )
                if call and not internal_call:
                    action_text = self.core.tool_request_display(call)
                    rendered_text = action_text
                elif call:
                    rendered_text = visible_text
                elif parse_error:
                    rendered_text = visible_text or "Correcting action request format..."
                else:
                    rendered_text = visible_text or "No response was returned."
                if internal_call and not rendered_text:
                    self.discard_last_assistant_entry()
                else:
                    self.replace_last_assistant_text(rendered_text)
                    self.append_assistant_end()
                if self.cancel_event.is_set():
                    if revising and self._route_original_answer:
                        self.replace_last_assistant_text(self._route_original_answer)
                        self.messages.append({"role": "assistant", "content": self._route_original_answer})
                        self.append_system("Revision interrupted; retained the completed draft.")
                    elif assistant_text:
                        self.messages.append({"role": "assistant", "content": assistant_text + "\n\n[interrupted]"})
                        self.append_system("Interrupted.")
                    else:
                        self.append_system("Interrupted.")
                    self.save_current_chat()
                    return
                assistant_message: dict[str, Any] = {"role": "assistant", "content": assistant_text}
                if native_calls:
                    assistant_message["tool_calls"] = native_calls
                self.messages.append(assistant_message)
                self._executor_stats = dict(getattr(self.provider, "last_stats", {}) or {})
                self._last_turn_stats = dict(self._executor_stats)
                self.save_current_chat()
                action_requirement = self.core.action_contract_directive(route, retry=True)
                if (
                    not call
                    and not parse_error
                    and not finalizing
                    and bool(self.config.get("agent"))
                    and self._agent_steps_used == 0
                    and action_requirement
                    and not action_contract_repair_attempted
                ):
                    action_contract_repair_attempted = True
                    if self.messages and self.messages[-1].get("role") == "assistant":
                        self.messages.pop()
                    self._route_action_feedback = action_requirement
                    repairing_action = True
                    self.replace_last_assistant_text("")
                    self.set_busy(True, "preparing requested action")
                    self.save_current_chat()
                    continue
                incomplete_reason = ""
                if not call and not parse_error:
                    incomplete_reason = self.core.response_incomplete_reason(
                        assistant_text,
                        self._executor_stats,
                    )
                if incomplete_reason and not completion_repair_attempted:
                    completion_repair_attempted = True
                    if self.messages and self.messages[-1].get("role") == "assistant":
                        self.messages.pop()
                    route["completion_retry"] = {
                        "attempted": True,
                        "reason": incomplete_reason,
                        "recovered": False,
                    }
                    self._route_feedback = self.core.completion_retry_directive(incomplete_reason)
                    self.replace_last_assistant_text("Completing response...")
                    self.set_busy(True, f"completing / {executor}")
                    self.save_current_chat()
                    continue
                if completion_repair_attempted and not incomplete_reason and not call and not parse_error:
                    retry_record = route.get("completion_retry")
                    if isinstance(retry_record, dict):
                        retry_record["recovered"] = True
                if incomplete_reason and not finalizing:
                    self.append_system(
                        "Response remained structurally incomplete after one retry.\n" + incomplete_reason
                    )
                    self.save_current_chat()
                    return
                contract = route.get("action_contract")
                if (
                    not call
                    and not parse_error
                    and not incomplete_reason
                    and not finalizing
                    and bool(self.config.get("agent"))
                    and self._agent_steps_used > 0
                    and isinstance(contract, dict)
                    and contract.get("capability")
                ):
                    self.set_busy(True, "verifying action result")
                    try:
                        completion = self.core.assess_action_completion(
                            self.provider,
                            self.config,
                            route,
                            self.messages,
                            assistant_text,
                            self.cancel_event,
                        )
                    except Exception as exc:
                        completion = {"error": str(exc)}
                    if completion:
                        route["action_completion"] = completion
                    enforce_completion = (
                        completion
                        and not completion.get("error")
                        and not completion.get("complete")
                        and float(completion.get("confidence") or 0) >= 0.65
                    )
                    if enforce_completion and action_completion_repairs < 2:
                        action_completion_repairs += 1
                        if self.messages and self.messages[-1].get("role") == "assistant":
                            self.messages.pop()
                        self._route_action_feedback = self.core.action_completion_directive(completion)
                        repairing_action = True
                        self.replace_last_assistant_text("")
                        self.set_busy(True, "continuing requested action")
                        self.save_current_chat()
                        continue
                    if enforce_completion:
                        reason = str(completion.get("reason") or "completion could not be verified")
                        self.replace_last_assistant_text("The requested action did not complete.")
                        self.append_error(f"Agent stopped after two completion corrections.\n{reason}")
                        self.save_current_chat()
                        return
                if finalizing:
                    invalid_final = bool(call or parse_error or incomplete_reason or not assistant_text.strip())
                    if invalid_final and synthesis_attempts < 2:
                        if call:
                            self.messages.append(
                                self.core.denied_tool_history_message(
                                    call,
                                    "because the task action budget is exhausted",
                                )
                            )
                        else:
                            self.messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Tool actions are unavailable for this task now. Return a concise final "
                                        "answer using the action results already present."
                                    ),
                                }
                            )
                        repairing_action = True
                        self.replace_last_assistant_text("Concluding from collected evidence...")
                        self.set_busy(True, f"synthesizing / {executor}")
                        self.save_current_chat()
                        continue
                    if invalid_final:
                        self.append_system(
                            "Task action budget reached; the executor did not return a usable final synthesis. "
                            "All action results remain in this chat."
                        )
                        self.save_current_chat()
                    return
                if parse_error:
                    if not action_repair_attempted:
                        action_repair_attempted = True
                        repairing_action = True
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Action request rejected by the runtime: "
                                    f"{parse_error}. Return exactly one corrected tool request and no other text."
                                ),
                            }
                        )
                        self.set_busy(True, "correcting action request")
                        self.save_current_chat()
                        continue
                    route["action_parse_error"] = parse_error
                    self.replace_last_assistant_text(
                        "I could not format that action correctly, so nothing was run. Rephrasing the request may help."
                    )
                    self.append_system(
                        "The requested action stayed malformed after a correction attempt; no action was run. "
                        "Details are available with /route."
                    )
                    self.messages.append(
                        {
                            "role": "user",
                            "content": (
                                "No action was run because the action request stayed malformed. Do not describe "
                                "results of actions that did not run."
                            ),
                        }
                    )
                    self.core.observe_route_outcome(
                        self.config,
                        route,
                        -1.0,
                        weight=1.0,
                        source="tool-protocol",
                    )
                    self.save_current_chat()
                    return
                if not call:
                    reviewer = str(route.get("reviewer") or "")
                    if reviewer and self._route_review_rounds < 2 and assistant_text.strip():
                        self._route_review_rounds += 1
                        review_round = self._route_review_rounds
                        if review_round == 1:
                            self._route_original_answer = assistant_text
                        self.set_busy(True, f"reviewing / {reviewer}")
                        review_started = time.monotonic()
                        try:
                            review = self.core.orchestrator_review(
                                self.provider,
                                route,
                                self.messages,
                                assistant_text,
                                self.config,
                                self.cancel_event,
                            )
                        except Exception as exc:
                            review = {"verdict": "error", "feedback": "", "error": str(exc)}
                        timings = route.setdefault("timings", {})
                        timings["review"] = round(
                            float(timings.get("review") or 0) + time.monotonic() - review_started, 3
                        )
                        review["round"] = review_round
                        route["review"] = review
                        if review.get("verdict") in {"pass", "revise"}:
                            self.core.observe_route_outcome(
                                self.config,
                                route,
                                1.0 if review["verdict"] == "pass" else -1.0,
                                weight=0.75,
                                source="independent-review",
                            )
                        self.chat["last_route"] = route
                        if self.cancel_event.is_set():
                            self.append_system("Quality review interrupted; retained the completed response.")
                            self.save_current_chat()
                            return
                        if review.get("verdict") == "revise" and review.get("feedback"):
                            if review_round >= 2:
                                review["unresolved"] = True
                                self.append_system(
                                    "Independent review still requests changes after one revision; "
                                    "kept the revised answer. Details are available with /route."
                                )
                                self.save_current_chat()
                                return
                            if self.messages and self.messages[-1].get("role") == "assistant":
                                self.messages.pop()
                            self._route_feedback = str(review["feedback"])
                            self.append_system("Independent review requested corrections; revising.")
                            self.set_busy(True, f"revising / {executor}")
                            continue
                        if review_round >= 2 and review.get("verdict") == "pass":
                            retry_record = route.get("review")
                            if isinstance(retry_record, dict):
                                retry_record["revision_confirmed"] = True
                        self.save_current_chat()
                    return
                if not self.config.get("agent"):
                    self.append_system(
                        "Agent mode is off. Enable it with /agent on before approving model-requested actions."
                    )
                    return
                outcome = self.handle_tool_request(call)
                if outcome == "continue":
                    self.set_busy(True, "continuing")
                    continue
                return
        except Exception as exc:
            self.record_runtime_failure(exc)
        finally:
            self.interrupt_requested = False
            self.cancel_event.clear()
            self.set_busy(False)

    def handle_tool_request(self, call: dict[str, str]) -> str:
        call = self.core.normalize_coordinator_tool_call(call)
        if self.core.is_internal_coordinator_call(call):
            if self.core.coordinator_delegation_limit(self.config, self._active_route) == 0:
                self.messages.append(
                    self.core.denied_tool_history_message(
                        call,
                        "because this route requires a direct conversational answer",
                    )
                )
                self.save_current_chat()
                return "continue"
            self.run_tool_call(call, approved_by="coordinator")
            return "continue"
        mode = str(self.config.get("permission_mode") or "ask")
        if mode == "deny":
            self.messages.append(self.core.denied_tool_history_message(call, "by permissions policy"))
            self.append_action(self.core.tool_denied_display(call, "permissions policy", "BLOCKED"))
            return "stop"
        if mode == "read-auto" and self.core.is_auto_approvable_tool_call(
            call,
            self.cwd,
            self.core.project_scope_for_chat(self.chat, self.cwd),
        ):
            self.run_tool_call(call, approved_by="read-auto")
            return "continue"

        approval_diff = self.core.tool_approval_diff(call, self.core.project_scope_for_chat(self.chat, self.cwd))
        if approval_diff:
            self.append_diff(approval_diff)
        self.save_current_chat()
        self._dispatch(self._show_approval_main, call)
        return "pending"

    def _show_approval_main(self, call: dict[str, str]) -> None:
        project_root = self.core.project_scope_for_chat(self.chat, self.cwd)
        allow_read_auto = self.core.is_read_only_tool_call(call) and self.core.is_auto_approvable_tool_call(
            call,
            self.cwd,
            project_root,
        )
        screen = ApprovalScreen(self.core, call, allow_read_auto)
        self.push_screen(screen, self._approval_result)
        self.pending_tool = call

    def _approval_result(self, result: str | None) -> None:
        call = self.pending_tool
        if call is None:
            return
        if result not in {"approve", "read-auto"}:
            self.deny_pending_tool()
            return
        self.pending_tool = None
        if result == "read-auto":
            self.config["permission_mode"] = "read-auto"
            self.core.save_config(self.config)
            approved_by = "approved; read-only actions now trusted"
        else:
            approved_by = "approved"
        self.query_one("#composer", Composer).focus()
        if call.get("name") == "shell" and self.core.command_needs_interactive_tty(call.get("cmd", "")):
            self._run_interactive_tool(call, approved_by)
            return
        self.set_busy(True, "continuing")

        def worker() -> None:
            try:
                self.run_tool_call(call, approved_by=approved_by)
                self.set_busy(True, "continuing")
                self.generate_worker()
            except Exception as exc:
                self.append_error(str(exc))
                self.set_busy(False)

        self.start_worker(worker, "dairack-action")

    def _run_interactive_tool(self, call: dict[str, str], approved_by: str) -> None:
        command = call.get("cmd", "")
        if not self.reserve_agent_action():
            self.messages.append(
                self.core.denied_tool_history_message(call, "because the task action budget is exhausted")
            )
            self.append_action(
                self.core.tool_denied_display(
                    call,
                    "task action budget reached "
                    f"({self._agent_steps_used}/{self.core.agent_action_limit(self.config)})",
                    "NOT RUN",
                )
            )
            self.save_current_chat()
            self.start_worker(self.generate_worker, "dairack-continue")
            return
        step_label = f"{self._agent_steps_used}/{self.core.agent_action_limit(self.config)}"
        self.begin_tool_action(call, step_label)
        self.set_busy(True, f"attached terminal / {step_label}")
        started = time.monotonic()
        try:
            with self.suspend():
                print(f"\n[dairack action / {approved_by}] $ {command}\n", flush=True)
                invocation, use_shell = self.core.shell_invocation(command)
                code = subprocess.call(
                    invocation,
                    cwd=str(self.cwd),
                    shell=use_shell,
                    env=self.core.command_environment(),
                )
                print(f"\n[exit {code}] returning to dairack", flush=True)
            result = "Output was shown in the attached terminal."
        except Exception as exc:
            code = 1
            result = f"interactive action failed: {exc}"
        self._loop_guard.record(call, result)
        try:
            self.append_action(
                self.core.tool_result_display(call, code, result, approved_by, time.monotonic() - started)
            )
            self.messages.append(self.core.tool_history_message(call, code, result))
            self.save_current_chat()
        finally:
            self.finish_tool_action()
        self.set_busy(True, "continuing")
        self.start_worker(self.generate_worker, "dairack-continue")

    def approve_pending_tool(self) -> None:
        if not self.pending_tool:
            self.set_notice("No pending action")
            return
        if isinstance(self.screen, ApprovalScreen):
            self.screen.dismiss("approve")
        else:
            self._approval_result("approve")

    def deny_pending_tool(self) -> None:
        if not self.pending_tool:
            self.set_notice("No pending action")
            return
        call = self.pending_tool
        self.pending_tool = None
        self.messages.append(self.core.denied_tool_history_message(call, "by user"))
        self.append_action(self.core.tool_denied_display(call, "denied by user"))
        self.save_current_chat()
        self.query_one("#composer", Composer).focus()
        self.set_warning_notice("Action denied")


def build_textual_app(
    core: Any,
    legacy_tui_class: type[Any],
    provider: Any,
    version: str,
    config: dict[str, Any],
    cwd: Path,
    chat: dict[str, Any] | None = None,
    messages: list[dict[str, str]] | None = None,
    blocks: list[dict[str, str]] | None = None,
) -> DairackTextualBase:
    app_class = type(
        "DairackTextualApp",
        (DairackTextualBase, legacy_tui_class),
        {"legacy_tui_class": legacy_tui_class},
    )
    return app_class(core, provider, version, config, cwd, chat=chat, messages=messages, blocks=blocks)


def run_textual_tui(
    core: Any,
    legacy_tui_class: type[Any],
    provider: Any,
    version: str,
    config: dict[str, Any],
    cwd: Path,
    chat: dict[str, Any] | None = None,
    messages: list[dict[str, str]] | None = None,
    blocks: list[dict[str, str]] | None = None,
) -> None:
    app = build_textual_app(
        core,
        legacy_tui_class,
        provider,
        version,
        config,
        cwd,
        chat=chat,
        messages=messages,
        blocks=blocks,
    )
    app.run(mouse=True)
