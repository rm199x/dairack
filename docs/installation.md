# Installation

<p align="center">
  <a href="../README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="models.md">Models</a> &nbsp;&middot;&nbsp;
  <a href="permissions.md">Permissions</a>
</p>

The same Dairack package supports a local workstation, a remote-compute client, and an Ollama model server. The client
always owns the interface, project files, actions, approvals, and conversation state.

## Choose a Setup

| Setup | Dairack | Inference | Best for |
| --- | --- | --- | --- |
| **Local** | One machine | Local Ollama | The shortest path and fully local operation |
| **Remote compute** | Client and server | Trusted Dairack server | Using server hardware from another computer |
| **Direct Ollama** | Client only | Existing HTTPS Ollama endpoint | Advanced deployments with their own secure proxy |

## Install

Dairack requires Python 3.11 or newer. Install it with an isolated Python tool manager:

```bash
uv tool install git+https://github.com/rm199x/dairack.git
# or
pipx install git+https://github.com/rm199x/dairack.git
```

Then initialize the active compute endpoint and verify the environment:

```bash
dairack setup
dairack doctor
dairack
```

Ollama must already be installed locally or reachable through a configured Dairack compute server. Setup presents a
model plan before any multi-gigabyte download begins.

<details>
<summary>Install from a cloned checkout</summary>

```bash
git clone https://github.com/rm199x/dairack.git
cd dairack
./scripts/install.sh
dairack setup
dairack doctor
```

The installer selects `uv`, `pipx`, or an isolated user virtual environment in that order. Set `DAIRACK_BIN_DIR` to
choose the fallback command directory and `DAIRACK_VENV` to choose its environment path. It never installs packages
into the system Python interpreter or invokes `sudo`.

</details>

## Remote Compute

Install Dairack on both machines. The server does not need a separate edition or unrestricted remote access.

**On the machine with Ollama and the models**

```bash
dairack serve --tailscale --name "Home Server"
```

On first use, Tailscale may print a tailnet approval URL and wait. Open that URL, enable Serve, and return to the
terminal; Dairack continues automatically. Non-interactive launches stop with the equivalent setup command instead of
waiting indefinitely.

The bridge binds to `127.0.0.1:11435`, creates a private bearer token, checks local Ollama, and exposes only the model
API operations Dairack needs plus read-only hardware metadata. `--tailscale` publishes that loopback service over
tailnet HTTPS. Keep the process running or place it under the operating system's user service manager.

**On each client**

```bash
dairack connect https://server-name.tailnet-name.ts.net
dairack doctor
dairack
```

Enter the token printed by the server. Dairack stores it separately with private file permissions. For scripts, pass
the token through standard input instead of command history:

```bash
printf '%s\n' "$DAIRACK_PAIR_TOKEN" | dairack connect https://server.example --token-stdin
```

| Command | Purpose |
| --- | --- |
| `dairack connect` | Inspect and test the active endpoint |
| `dairack connect local` | Return to local Ollama |
| `dairack connect remote` | Restore the last saved server |
| `dairack connect test --json` | Print machine-readable health and hardware data |

The compute server receives prompts, selected context, approved tool results, retrieved text, and attached images used
for inference. It receives no Dairack shell, project-filesystem, chat, index, approval, or checkpoint API. See
[Security](../SECURITY.md#remote-compute-boundary) before using a server outside a trusted tailnet.

<details>
<summary>Connect to an existing Ollama endpoint</summary>

An existing Ollama HTTPS reverse proxy can be connected directly. Without the Dairack bridge, server hardware cannot
be verified, so Dairack leaves backend placement and batching automatic and uses a conservative context profile.

```bash
dairack connect https://ollama.example
```

Generic HTTP is rejected by default. `--allow-http` exists for deliberately trusted private networks only. Never expose
an unauthenticated Ollama endpoint to the public internet.

</details>

## Windows

The PowerShell installer uses `uv`, then `pipx`, then an isolated per-user virtual environment. It does not require
administrator privileges and adds only the fallback environment's `Scripts` directory to the user `PATH`.

```powershell
git clone https://github.com/rm199x/dairack.git
Set-Location dairack
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
dairack setup
dairack doctor
dairack
```

Ollama must be installed and running separately. Git for Windows supplies the patch backend used for agent edits.
Private state relies on the ACL inherited from the current user's profile directories.

## Managed Setup

For non-interactive provisioning, select a recommended profile or an explicit model:

```bash
dairack setup --profile balanced --yes
dairack setup --model qwen3.5:9b --yes
dairack setup --profile manual
```

`minimal`, `balanced`, and `complete` are recommendations rather than compatibility modes. `--no-pull` records and
previews settings without downloading a model.

## Development Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Use an isolated state tree while testing migrations or destructive setup paths:

```bash
DAIRACK_HOME="$(mktemp -d)" dairack setup --profile manual
```

## Release Channel

Source installs do not assume a package or release owner. A distributor can configure an HTTPS manifest or PyPI JSON
endpoint explicitly:

```bash
dairack update channel https://example.org/dairack/releases.json
```

`DAIRACK_UPDATE_INDEX_URL` can seed a new configuration during managed provisioning. Use
`dairack update channel off` to disable checks. Release metadata can provide a version and notes URL, but it cannot
provide an executable install command; Dairack constructs that command for the environment that owns the installation.

Plain HTTP is accepted only for a loopback endpoint during development. Updating Dairack does not modify Ollama models,
chats, indexes, profiles, or checkpoints.
