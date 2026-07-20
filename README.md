# Dairack

[![CI](https://github.com/rm199x/dairack/actions/workflows/ci.yml/badge.svg)](https://github.com/rm199x/dairack/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-4f746c)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-c4934f)](LICENSE)

Dairack is a local-first terminal agent with local or remote Ollama compute. It combines a polished Textual interface,
resumable chats, project indexing, explicit action approvals, code diffs and checkpoints, web tools, image handoffs,
automatic context compaction, and multi-model coordination.

The agent runtime always lives on the client: files, shell actions, approvals, chats, project indexes, and checkpoints
stay on the machine where the interface is open. Model inference can run there or on another Dairack machine. The
coordinator treats models at the active compute endpoint as one capability pool and profiles them against that
machine's hardware when it can be verified. Pulling a new model does not require a Dairack source change.

> **Status:** alpha. The tool is functional and locally verified on Linux. Linux, Windows, and Apple Silicon macOS CI
> are defined for every change, but public configuration and extension APIs may still change before 1.0.

## Requirements

- Python 3.11 or newer
- [Ollama](https://ollama.com/) on the local machine or a Dairack compute server
- At least one model installed through Dairack or Ollama before chatting
- `patch` or Git for agent-applied unified diffs
- `rg` is recommended for faster repository search

Linux is the primary locally tested platform. Hardware discovery supports NVIDIA, ROCm systems where `rocm-smi` is
available, Apple Silicon/Metal, and conservative Windows CIM probes. CPU-only operation is supported.

## Install

Install directly from GitHub with an isolated Python tool manager:

```bash
uv tool install git+https://github.com/rm199x/dairack.git
# or
pipx install git+https://github.com/rm199x/dairack.git
```

Alternatively, install from a cloned checkout:

```bash
git clone https://github.com/rm199x/dairack.git
cd dairack
./scripts/install.sh
dairack setup
dairack
```

On Windows PowerShell:

```powershell
git clone https://github.com/rm199x/dairack.git
Set-Location dairack
.\scripts\install.ps1
dairack setup
dairack
```

The installer prefers `uv tool`, then `pipx`, then an isolated user virtual environment. It never installs Python
packages into the system interpreter. See [Installation](docs/installation.md) for direct package-manager commands
and platform notes.

## Local Or Remote Compute

One Dairack package supports both roles. A normal `dairack` process is the client runtime. The optional `dairack serve`
command turns an Ollama machine into an authenticated, inference-only compute service; the same server installation
can still run the normal terminal interface locally.

For a Tailscale deployment, run this on the model server:

```bash
dairack serve --tailscale --name "Home Server"
```

The service remains bound to loopback while Tailscale provides the tailnet HTTPS endpoint. It prints a pairing token.
On the computer where you want to work:

```bash
dairack connect https://server-name.tailnet-name.ts.net
dairack
```

Enter the pairing token in the private prompt. The default TUI exposes the same flow through `/compute` or the command
palette. Use `dairack connect local` to return to local Ollama and `dairack connect remote` to restore the saved server.
`dairack connect` reports connection health, server hardware confidence, models, and latency.

The compute bridge exposes only the Ollama calls Dairack needs plus read-only hardware identity. It has no file, shell,
chat, approval, index, or checkpoint API. Prompts, selected context, tool results, and attached image bytes still travel
to the configured inference endpoint so the model can process them. See [Installation](docs/installation.md) and
[Security](SECURITY.md) before using a server outside a trusted tailnet.

## Manage Models

```bash
dairack models recommend
dairack models pull <model>
dairack models update <model>
dairack models update --all
dairack models remove <model>
dairack models
```

The terminal UI exposes the same operations through `F6` or `/library`; `/models` remains a compatibility alias.
Install, fitted-set, and update actions appear before the installed inventory, with native transfer progress,
cancellation, profile inspection, and confirmed removal. Setup profiles are optional; any Ollama chat model can be
selected directly. Pulling the same mutable tag checks for a newer manifest, while a new family or versioned tag is
installed explicitly and does not silently remove the old one.

`dairack init` and `dairack models refresh` query the active endpoint and regenerate `models.json`. A local endpoint uses
client hardware; the Dairack bridge supplies verified server hardware. A plain remote Ollama endpoint gets conservative
backend-managed settings instead of being incorrectly tuned against the client GPU. Existing user overrides survive.

Inspect or tune a generated profile without editing source:

```bash
dairack models inspect <model>
dairack models set <model> reasoning 0.92
dairack models set <model> num_ctx 8192
dairack models set <model> num_batch 192
dairack models reset <model>
```

Capability values are routing priors from `0.0` to `1.0`. Ollama's declared support for tools, vision, and thinking
is authoritative. Known catalog entries receive versioned curated priors; unconventional models receive transparent,
lower-confidence inferred profiles. Local evaluation can override those priors without changing source.

## Operating Modes

- **Coordinator / adaptive:** balances quality, model-load cost, task complexity, and model residency.
- **Coordinator / quality:** permits planning, review, and more specialist work for substantive tasks.
- **Coordinator / efficient:** favors a resident or smaller capable model and avoids semantic arbitration.
- **Direct model:** uses the selected model without routing.

The active executor, planning/review stages, specialist handoffs, timing, and route rationale are represented in the
interface. Press `Esc` to close dialogs or interrupt work that supports cancellation. Atomic operations say
`FINISHING SAFELY` instead of presenting a stop control that cannot work.

Coordinator configuration is optional and available in `/coordinator` or from the command line:

```bash
dairack coordinator policy adaptive
dairack coordinator set review on
dairack coordinator prefer coding <installed-model>
dairack coordinator prefer coding auto
```

Role preferences are soft. Capability gates and automatic fallback remain active if a preferred model is missing or
cannot handle the input. Bounded model-by-role learning starts from neutral, requires repeated evidence, and can never
override modality gates. One model is sufficient; complementary models make specialist handoffs useful.

Natural-language compute directions such as asking for a deeper answer, a materially larger executor, or a lighter
response are interpreted as per-turn preferences. They never persist into the next prompt. Coordinator resolves the
underlying task, applies the preference only at high enough confidence, preserves modality and action gates, and keeps
the ordinary automatic route when the request is ambiguous or no suitable alternative exists. Describing content as
heavier, deeper, or faster does not change model selection.

## Terminal Interface

`/help` shows the primary working set; `/help all` exposes the complete command and profile reference. `Ctrl+P` opens
the command palette and remains the primary navigation path on compact or mobile terminals where function keys may be
unavailable. Prose uses a governed reading width while commands, code, and diffs retain horizontal review space.

Startup motion settles into a static ready state. Set `DAIRACK_REDUCED_MOTION=1` for an immediately settled welcome and
static activity indicators, or persist `"reduced_motion": true` in the Dairack configuration file.

## Sessions and Images

`dairack` opens a fresh, unsaved session by default. The draft is persisted after its first prompt, so opening and
closing the interface does not create empty chat records. `F3` exposes a new-session action, the saved archive, and a
startup preference for either fresh sessions or automatic recent-chat resume. `dairack --resume`, `/resume`, and
`dairack --chat <id>` remain explicit resume paths.

Press `F4` or use `/image` to attach project media or enter a local path. Up to four PNG, JPEG, WebP, GIF, or BMP files
can be staged for the next prompt. Attachments are sent through Ollama's image input and Coordinator enforces a vision
capability gate before selecting an executor. Project discovery uses `rg` when available and a bounded native
filesystem scan otherwise.

## Software Updates

Public builds can use a small HTTPS release manifest or PyPI JSON endpoint. Checks run off the UI thread, are cached
for 24 hours by default, and fail silently when offline. When a newer version exists, the top bar and `/update` expose
the version, notes, exact local install command, copy action, and a confirmed attached-terminal update. A successful
in-app update saves the current chat and exits so the next process starts entirely on the new release.

```bash
dairack update channel https://example.org/dairack/releases.json
dairack update check --force
dairack update apply
dairack update channel off
```

The endpoint may return `{"version":"0.2.0","notes_url":"https://..."}` or standard PyPI project JSON. It cannot
provide executable commands: Dairack constructs a pinned `uv`, `pipx`, or managed-venv command locally. Source builds
have no default channel until the repository/package owner configures one.

## Permissions

Agent mode and action permissions are separate. `ask` is the default and requires approval before model-requested
shell commands, patches, file reads, or network access. `deny` blocks them. `read-auto` only auto-runs workspace-scoped
structured reads and a narrow machine-status command set; outbound web calls and arbitrary shell reads still prompt.

Read [Permissions](docs/permissions.md) before enabling unattended workflows. Models are untrusted decision makers;
the permission layer, not a prompt, is the security boundary.

Every tool action uses the same compact status contract: action type, target, authority, outcome, exit code, elapsed
time, and result. `/run`, `/test`, `/search`, `/index`, `/web`, and `/url` use that contract too. Their bounded results
are retained as structured evidence for the next model turn; maintenance output such as model downloads is not added
to conversation context. Interactive commands in the default TUI temporarily use the attached terminal, keeping OS
password prompts separate from the composer.

Natural-language requests to inspect a public website are routed to `web_open`; referenced domains can be resolved from
recent conversation context. Direct page opening accepts any validated public HTTP or HTTPS URL and extracts readable
text without executing page scripts. The built-in keyless search backend is DuckDuckGo Lite; search is used for discovery
or verification and can be followed by `web_open` without handing the workflow back to the user.

## State

Dairack follows XDG paths on Linux/macOS:

| Purpose | Default |
| --- | --- |
| Configuration and model registry | `~/.config/dairack/` |
| Chats, checkpoints, and project index | `~/.local/share/dairack/` |
| Cache | `~/.cache/dairack/` |
| Runtime state | `~/.local/state/dairack/` |

Set `DAIRACK_HOME=/path` for a fully isolated or portable state tree. Standard `XDG_*_HOME` variables are also
honored. Windows uses `%APPDATA%\Dairack` for configuration and `%LOCALAPPDATA%\Dairack` for data, cache, and state.
Configuration and conversation JSON files are written atomically with private permissions where supported.
Compute bearer tokens are kept in a separate private `compute-credentials.json`; they are never written to the main
configuration, transcript, or chat archive.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m ruff check src tests tools
python -m pytest
python -m build
```

Architecture and ownership boundaries are documented in [Architecture](ARCHITECTURE.md). Contributions should start
with [CONTRIBUTING.md](CONTRIBUTING.md); security-sensitive reports should follow [SECURITY.md](SECURITY.md).
