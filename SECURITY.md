# Security Policy

<p align="center">
  <a href="README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="docs/permissions.md">Permissions</a> &nbsp;&middot;&nbsp;
  <a href="ARCHITECTURE.md">Architecture</a>
</p>

Dairack treats models, model output, repository content, fetched pages, and resumed conversations as untrusted input.
Typed tools and approval policy reduce authority; they do not create an operating-system sandbox.

> **Report vulnerabilities privately.** Do not publish an exploitable issue, credential, prompt, path, or sensitive
> local evidence in a public ticket.

## Supported Versions

| Channel | Security support |
| --- | --- |
| Latest release | Security fixes and release guidance |
| Default branch | Current fixes awaiting release |
| Older pre-1.0 versions | No guaranteed backports |

## Report a Vulnerability

Use the repository's [private vulnerability report](https://github.com/rm199x/dairack/security/advisories/new). Include
only the evidence needed to establish impact:

- affected Dairack version or commit;
- operating system and Python version;
- permission and Coordinator modes;
- minimal reproduction steps;
- expected and observed behavior;
- redacted logs, paths, or screenshots when needed.

Do not include live tokens, private model prompts, personal files, or unrelated repository content. If private reporting
is unavailable, open a public issue asking for a secure contact channel without disclosing the vulnerability.

## Trust Model

| Component | Assumption |
| --- | --- |
| Model output and tool requests | Untrusted; parsed into a closed schema |
| Project and resumed-chat content | Untrusted; may attempt to influence model behavior |
| Web and update metadata | Untrusted; validated and bounded before use |
| Permission engine | Enforcement boundary for Dairack actions |
| Approved shell command | Runs with the invoking user's OS privileges |
| Compute endpoint | Trusted with submitted inference content |

Keep `permission_mode` at `ask` for unfamiliar repositories or models. Review commands, paths, URLs, and diffs before
approval. Do not run Dairack as root, and protect the host account and every configured inference endpoint.

## Remote Compute Boundary

Dairack splits remote operation at the model-provider boundary:

| Client owns | Compute bridge owns |
| --- | --- |
| Files, shell, patches, and approvals | Ollama request transport |
| Chats, indexes, and checkpoints | Allowlisted model lifecycle |
| Context selection and attachment reads | Read-only hardware identity |

Inference is not private from the compute server. Prompts, system instructions, selected chat context, tool schemas,
approved tool results, retrieved text, and attached images are sent to the configured endpoint. When semantic retrieval
is enabled, project indexing also sends bounded per-file excerpts for embedding inference; the index and resulting
vectors remain on the client. Set `retrieval_embeddings` to `false` for lexical-only indexing. Use only a server and
network path you trust with that content.

The bridge binds to loopback by default and requires a high-entropy bearer token. Use HTTPS, Tailscale Serve, or another
authenticated encrypted proxy for remote access. Direct Ollama has no Dairack authentication layer and must not be
published openly. Compute connections do not inherit ambient operating-system proxy settings, keeping private routes
and bearer credentials on the explicitly configured path.

Possession of a bridge token grants chat and embedding inference plus the allowlisted model-management operations,
including model pull and delete. Rotate the server token if it is exposed. The bridge has no catch-all proxy route;
adding one requires a security review and transport tests.

## Permission Boundary

`read-auto` excludes outbound network requests, arbitrary shell-based reads, and paths outside the active project.
Patch targets are path-validated, dry-run, and checkpointed. Interactive credential prompts are kept outside the
composer or blocked when no attached terminal exists.

Treat any bypass of these controls as a security issue:

- action approval or risk-aware default focus;
- active-project and resumed-project scope;
- patch target and checkpoint validation;
- redirect and public-address validation;
- interactive credential blocking;
- compute-route allowlisting and token isolation.

## Local State

On POSIX systems, Dairack creates state directories and sensitive files with private mode bits. On Windows, privacy
relies on the ACL inherited from the current user's profile directories.

Child commands never inherit `DAIRACK_COMPUTE_TOKEN`. Other environment variables remain available because approved
development commands commonly require them; inspect the environment before approving untrusted shell work.
