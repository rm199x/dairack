# Installation

## Recommended Tool Installers

Install from a checkout with either isolated Python tool manager:

```bash
uv tool install .
# or
pipx install .
```

Then run the consumer setup flow against the local Ollama service:

```bash
dairack setup
dairack doctor
```

## Client And Compute Server

Install the same Dairack package on both machines. The server does not need a separate edition or daemon package.

On the machine with Ollama and the models:

```bash
dairack serve --tailscale --name "Home Server"
```

`dairack serve` binds to `127.0.0.1:11435`, creates a private bearer token, validates local Ollama, and exposes only a
strict model API allowlist plus read-only hardware metadata. `--tailscale` configures Tailscale Serve to publish that
loopback port over tailnet HTTPS. Keep the foreground process running, or place this command under the operating
system's user service manager when it should start at login or boot.

On each client machine:

```bash
dairack connect https://server-name.tailnet-name.ts.net
dairack doctor
dairack
```

Enter the token shown by the server. It is stored separately with private file permissions. For scripts, pass it
without placing it in command history:

```bash
printf '%s\n' "$DAIRACK_PAIR_TOKEN" | dairack connect https://server.example --token-stdin
```

Connection lifecycle commands:

```bash
dairack connect                 # inspect and test the active endpoint
dairack connect local           # use local Ollama
dairack connect remote          # restore the last saved server
dairack connect test --json     # machine-readable health and hardware report
```

An existing Ollama HTTPS reverse proxy can be connected directly. Plain remote Ollama does not provide bridge hardware
identity, so Dairack leaves backend execution knobs automatic and uses a conservative context profile. Generic HTTP is
rejected by default; `--allow-http` exists only for a deliberately trusted private network. Never expose unauthenticated
Ollama directly to the public internet.

The client owns the working directory and every action. The compute server sees model request content, including
prompts, selected conversation context, approved tool results, and attached image bytes, but it receives no general
filesystem or shell endpoint from Dairack.

## Convenience Installer

`scripts/install.sh` selects `uv`, `pipx`, or an isolated user virtual environment in that order. Set `DAIRACK_BIN_DIR`
to choose the fallback command directory and `DAIRACK_VENV` to choose its environment path.

```bash
./scripts/install.sh
```

On Debian-derived systems, the fallback requires the package providing `venv` for the active Python version. The
installer reports the needed action rather than invoking `sudo`.

## Windows PowerShell

The Windows installer uses `uv`, then `pipx`, then an isolated per-user virtual environment. It does not require
administrator privileges. The fallback adds only that environment's `Scripts` directory to the user PATH.
Private state relies on the ACL inherited from the current user's profile directories; POSIX `0600`/`0700` mode bits
do not provide an additional Windows access-control boundary.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
dairack setup
dairack doctor
dairack
```

Ollama must be installed and running separately. Git for Windows provides the edit backend used by agent patches.
The terminal UI, model API, chats, and indexing are native Python; shell actions use the Windows command processor,
with PowerShell invoked explicitly when needed.

## Non-Interactive Setup

Setup never downloads multi-gigabyte models without displaying a plan. For managed installs, choose an optional
profile or an arbitrary model explicitly:

```bash
dairack setup --profile balanced --yes
dairack setup --model qwen3.5:9b --yes
dairack setup --profile manual
```

`minimal`, `balanced`, and `complete` are recommendations, not compatibility modes. `--no-pull` previews and stores
settings without downloading.

## Development Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Do not point an editable development install at a production state tree when testing migrations. Use:

```bash
DAIRACK_HOME="$(mktemp -d)" dairack setup --profile manual
```

## Release Channel

Dairack does not guess a project identity. A distributor must own and configure an HTTPS manifest or PyPI JSON URL:

```bash
dairack update channel https://example.org/dairack/releases.json
```

The selected endpoint and cache interval are stored in normal configuration. `DAIRACK_UPDATE_INDEX_URL` can seed a
new configuration during managed provisioning. Run `dairack update channel off` to disable checks. Application updates
do not modify Ollama models, chats, indexes, profiles, or checkpoints.
Plain HTTP is accepted only for a loopback release endpoint during local development. Update installation honors the
package index configured for the owning `uv`, `pipx`, or Python environment; review that environment when using a
private package mirror.
