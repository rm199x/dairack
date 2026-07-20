# Security Policy

## Supported Versions

Dairack is pre-1.0. Security fixes are applied to the latest release and the default branch.

## Reporting

Do not publish an exploitable issue or sensitive local data in a public ticket. Use the repository's private security
advisory channel when available. Include the affected version, platform, permission mode, reproduction steps, and the
minimum redacted evidence needed to confirm impact.

## Trust Model

Local models, model output, repository content, fetched web content, and resumed chat data are untrusted. Dairack parses
tool requests into a closed schema and asks before actions by default. This does not create an OS sandbox: an approved
shell command runs with the invoking user's privileges and network access.

Keep `permission_mode` at `ask` when working with unfamiliar repositories or models. Review commands and diffs before
approval. Do not run Dairack as root. Protect access to any remote Ollama endpoint and to the host account itself.

## Remote Compute Boundary

Dairack splits remote operation at the model provider boundary. The client owns files, shell execution, patches,
approvals, chats, indexes, and checkpoints. The optional compute bridge owns Ollama transport and read-only hardware
identity only. It does not expose a remote shell or filesystem API.

Inference is not data-isolated from the compute server. Prompts, system instructions, selected chat context, model tool
schemas, approved tool results, retrieved text, and attached image bytes are sent to the configured endpoint. Use only
a server and network path you trust for that content.

The bridge binds to loopback by default and requires a high-entropy bearer token. Pairing tokens are stored outside the
main configuration with private permissions and are not added to chats. Use HTTPS, Tailscale Serve, or another
authenticated encrypted proxy for remote access. Direct Ollama has no Dairack authentication layer and must not be
published openly. Possession of a bridge token grants model inference and the allowlisted model-management operations,
including pull and delete; rotate the server token if it is exposed.

The bridge route allowlist is intentionally closed. Adding a route requires a security review and transport tests.
There is no catch-all proxy behavior.

`read-auto` deliberately excludes outbound web requests, arbitrary shell-based file reads, and paths outside the active
project. A bypass of that boundary, patch path validation, approval UI, or interactive credential blocking should be
treated as a security issue.

On POSIX, state directories and sensitive files are created with private mode bits. On Windows, privacy relies on the
ACL inherited from the current user's profile directories. Child commands do not inherit `DAIRACK_COMPUTE_TOKEN`;
other environment variables remain available because approved development commands commonly require them.
