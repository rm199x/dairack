# Architecture

<p align="center">
  <a href="README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="CONTRIBUTING.md">Contributing</a> &nbsp;&middot;&nbsp;
  <a href="SECURITY.md">Security</a>
</p>

Dairack separates model judgment from deterministic authority. Models can propose work; typed runtime boundaries own
execution, persistence, hardware policy, routing constraints, and presentation state.

## Operating Principles

- The client owns files, actions, approvals, chats, indexes, and checkpoints.
- Providers own inference transport, not client authority.
- Capability gates and permission policy remain deterministic.
- Generated machine policy belongs in user configuration, not source tables keyed to one model collection.
- New models normally enter through metadata and local profiling rather than name-specific routing branches.

The current source tree is an extraction from a mature single-file installation. New lifecycle and policy code is
modular. `runtime.py` remains a compatibility core for the established conversation, agent, indexing, and fallback UI
behavior while those domains are separated behind tests. It should shrink over time; new independent features must
not be added to it by default.

## System Map

```text
CLIENT MACHINE                                      COMPUTE MACHINE

Terminal UI -> runtime -> provider transport -----> Dairack bridge -> Ollama
                  |                                      |
                  +-> permission policy                  +-> model lifecycle
                  +-> local tools                        +-> hardware identity
                  +-> chats / index / checkpoints
```

Local inference collapses the provider and Ollama path onto the client machine. The ownership boundaries do not
change.

## Modules

| Module | Ownership |
| --- | --- |
| `cli.py` | Stable console entry point and lifecycle subcommands |
| `paths.py` | XDG, Windows, and portable filesystem layout |
| `config.py` | Versioned validation and atomic private writes |
| `hardware.py` | Cross-platform hardware probes and conservative runtime tuning |
| `machine.py` | Authoritative client/compute identity map and user-facing hardware status |
| `file_discovery.py` | Bounded cross-platform discovery of named client paths |
| `search.py` | Canonical search exclusions and isolated bounded fallback when ripgrep is unavailable |
| `models.py` | Provider-neutral metadata, inferred capabilities, registry, and overrides |
| `catalog.py` + `data/` | Optional versioned recommendations and known-model routing priors |
| `model_ops.py` | Validated pull/remove operations and transport-neutral progress state |
| `permissions.py` | Tool classification, project path scope, and interactive credential guards |
| `network.py` | Resolve-validate-pin HTTP transport, redirect policy, and bounded cancellation |
| `providers/` | Inference transport contracts and Ollama HTTP adapter |
| `compute.py` | Compute endpoint policy, private credentials, identity probing, and connection state |
| `bridge.py` | Authenticated inference-only Ollama proxy and verified server hardware metadata |
| `messages.py` | Canonical provider messages, discourse references, attachments, and separable system sections |
| `text.py` | Shared bounded plain-text output helpers |
| `coordinator/analysis.py` | Deterministic task signals, action contracts, semantic gates, and assessment merging |
| `coordinator/ranking.py` | Provider-neutral executor ranking, learned influence, and bounded stage selection |
| `coordinator/policy.py` | Named quality/cost policy controls |
| `coordinator/tuning.py` | Small validated baseline tuning vector |
| `coordinator/calibration.py` | Bounded model/role outcome residuals, refined per task kind with evidence backoff |
| `ui/textual_app.py` | Textual presentation and interaction layer |
| `turn.py` | Frontend-agnostic turn decision ladder (repair, completion, review, finalization) as pure functions |
| `runtime.py` | Compatibility application core pending further domain extraction |
| `bootstrap.py` | First-run initialization and environment diagnostics |
| `updates.py` | Cached release discovery and install-owner-aware update commands |

## Initialization Flow

1. Resolve paths and validate `config.json`.
2. Query Ollama `/api/version`, `/api/tags`, and `/api/show`.
3. Detect CPU topology, available memory, and supported accelerators.
4. Normalize provider results into `ModelDescriptor` values.
5. Infer capability priors from parameter scale and declared features.
6. Enrich known catalog matches; unknown models remain confidence-labeled inferred profiles.
7. Generate a conservative runtime profile from model size, context limit, and local memory.
8. Merge explicit user overrides from the previous registry.
9. Atomically write `hardware.json`, `models.json`, and validated configuration.

Models that exceed the conservative memory budget are marked `constrained` and are not selected as automatic defaults
when a recommended model exists. Ollama remains responsible for backend-specific layer placement.

## Request Flow

The coordinator first derives deterministic task signals. Adaptive and quality policies may ask the most efficient
suitable installed model for a schema-validated semantic assessment when the request warrants it. That assessment
also declares whether the turn requires a real runtime action; the execution contract rejects prose that merely claims
an action is underway. Action workflows receive one bounded semantic completion check before they settle. Capability
scores, hardware fit, complexity-discounted residency, grounded-research cost, task quality demand, profile confidence,
soft role preferences, and a small bounded learned estimate produce an executor ranking. Learning shares evidence at
the model/role level, then interpolates toward a task-kind estimate as its own evidence matures. A confident semantic
assessment may promote task signals past the keyword layer and lower — never remove — the deterministic support
floors for planning and review, so paraphrased and non-English requests are routed on meaning. Semantic output cannot
invent an image, bypass a capability or vision gate, erase deterministic risk evidence, or create action authority.

Planning, independent review, and specialist delegation are separate bounded stages, each visible in route state and
interruptible by the user. The planner receives indexed project context so execution briefs name real files rather
than guesses. Review verdicts apply only with usable grounded feedback, revisions are announced in the transcript,
and the revised answer is re-checked once; a still-disputed answer is kept and marked unresolved in route state.
Review receives the same recent tool-result evidence and deterministic completion assessment used by the action
arbiter, while keeping those internal logs out of a concise user-facing answer.

The action loop is bounded and self-correcting: model-emitted action requests are parsed generously (including
call-style near-misses and unescaped Windows path backslashes), malformed requests get one correction pass and then a
plain explanation, and a validated text-form call may recover a turn only when a native or compatibility tool surface
was actually offered. An identical read repeated with no intervening state change is refused with its prior result,
persistent repetition forces final synthesis, bounded tool output keeps its beginning and end, a dropped model
connection retries once silently, and an over-budget request omits project retrieval before failing. A blank or
structurally incomplete continuation retries once on the same executor; Coordinator may then move once to the next
pre-ranked eligible executor, while direct mode never switches implicitly. The decision
ladder that chooses each agent turn's next action lives in one frontend-agnostic core (`turn.py`) used by each
interface's agent-capable path; direct response paths do not need that ladder. Frontends still own presentation,
history mutation, and action execution, with conformance tests pinning those adapters to the shared ordering. Under
`read-auto`, a response that returns several independently auto-approvable
in-scope reads runs them together in one turn. Batching is disabled when agent mode is off or final synthesis has begun,
and it cannot execute anything that would not auto-run on its own. A ripgrep-backed `grep` tool gives the agent live
content search under the same scope and auto-approval rules as other reads. Its no-ripgrep fallback runs as a bounded,
cancellable child process and shares the same generated-state exclusions. Prompts submitted while a turn is active are
queued and dispatched when it ends. A pending approval holds that queue until the user allows or denies the action; an
interrupted turn returns queued input to the composer instead of sending it.

Writes remain exact and previewed. Structured-file edits are parsed or compiled before checkpointing, obvious
undefined Python self-references are rejected, and an exact-string mismatch can return a bounded unique anchor from
the current file without applying a fuzzy change. Failed writes do not reset no-progress accounting. Unix shell
pipelines run with `pipefail` where Bash is available, so output limiting cannot turn a failed test command into exit
zero.

Context policy is derived from the active executor's effective runtime profile. The hard model window is divided into
an input budget and answer reserve; request accounting includes the system foundation, grounded macro memory, the live
task, tool schemas, and tokenizer/routing uncertainty. Covered history is replaced by deterministic grounded memory,
not retained beside it. Within a long active task, raw tool exchanges may be shed only after their bounded evidence is
placed in a task-local ledger, and native call/result pairs remain indivisible. Tool-result and `read_file` line-window
budgets scale with the same profile, so small contexts advance through explicit chunks while larger verified contexts
retain more source evidence. System foundations, macro memory, retrieval, and transient coordinator directives remain
separate until final request fitting; optional retrieval is shed first, macro memory can contract around current-turn
evidence, and a completed action result is narrowed again when necessary so its continuation request remains valid.
Compaction may run between model/action steps but never in the middle of a provider call.

Whole-file tasks additionally keep a route-scoped coverage ledger: completed line ranges are merged, the next unread
line is deterministic, and bounded requested evidence survives raw-message compaction. The completion arbiter cannot
accept a whole-file result until coverage reaches the known final line, and exact requested headings must come from
the preserved evidence rather than reconstruction.

The answer reserve is enforced, not advisory: request fitting protects a generation floor that binds exactly when a
raised budget ratio, a small window, thinking mode, or final synthesis would otherwise let a packed prompt starve the
response, and every executor request caps `num_predict` at the true window residual so the provider can never
context-shift the prompt away — truncation surfaces honestly through the turn ladder instead of silently corrupting
the request. Tool-enabled passes have a smaller bounded generation ceiling because one native action should not spend
the entire residual failing to terminate on constrained hardware; final synthesis retains the full safe residual. A
context-posture directive tells the executor how to work at its tier: small windows get incremental
guidance (windowed reads continued with `start_line`, narrow tool requests, section-by-section audits carrying only
conclusions forward), generous windows get whole-file latitude, and the standard tier adds nothing. This mirrors the
strategies large-context agent CLIs converged on — threshold-triggered compaction that genuinely replaces history,
recency-first retention, tool-output aging, meters that count schemas and reserves — and extends them for local
windows orders of magnitude smaller, where partitioning must be enforced rather than assumed. Routing is additionally
reload-aware: when the previous turn's executor is still resident and a cold challenger leads by less than a margin
scaled to the incumbent's size and the policy's efficiency stance, the coordinator keeps the incumbent and records the
decision in the route, so marginal score differences stop paying real model-reload latency; `model_keep_alive` extends
provider residency between turns. Identical routing turns reuse a bounded in-process cache of the semantic
classifier's verdict, and each specialist consultation ranks the model pool exactly once.

The coordinator's algorithms live in `coordinator/` as importable modules — analysis, ranking, selection, semantic,
delegation, oversight, calibration, control, policy, tuning — with the runtime module re-exporting the legacy names
so every interface, test, and the evaluation lab drive one implementation. Project memory is hybrid where hardware
allows it: `/index` stores per-file embedding vectors beside the lexical index when an embedding-capable model is
installed, and retrieval fuses bm25 with vector similarity through reciprocal-rank fusion, degrading to pure lexical
search whenever embeddings are unavailable. Embedding-only models are utility inference endpoints and are excluded
from chat defaults, direct selectors, Coordinator ranking, and delegation. With remote compute, bounded source excerpts
cross the provider boundary for embedding while the index and vectors remain client-owned.

Textual motion is event-driven and monotonic-time based. A one-shot scheduler runs at 20 fps only for short welcome,
focus, phase, and completion transitions; visible waits update at 10 fps, while settled idle screens retain no motion
timer. Signal tracks keep constant glyph geometry and interpolate true-color luminance across fractional cell
positions. Streaming output suppresses decorative activity movement, and transfer progress visually eases toward the
latest provider value without advancing beyond it. Reduced motion resolves every spatial transition immediately.

Natural-language compute controls are a closed, schema-validated contract separate from task intent. They are
confidence-gated, apply to one turn only, and cannot create action authority. A request for higher capacity must pass a
provider-neutral material-capacity and task-fit check; quality and efficiency controls adjust bounded ranking policy.
The resolved task is carried to the selected executor, while ordinary model discussion and content styling remain on
the automatic route.

## Client and Compute Roles

Every installation contains both roles. The ordinary Dairack process is always the client runtime and owns the current
working directory, tools, permission decisions, chat persistence, project memory, and checkpoints. `dairack serve` is
an optional compute role on an Ollama host. It exposes a fixed API allowlist and hardware identity; it never executes
agent tools or opens client paths.

| Client runtime | Compute service |
| --- | --- |
| Interface and active working directory | Ollama request transport |
| Tool execution and permission decisions | Allowlisted model lifecycle |
| Chats, project index, and checkpoints | Read-only hardware identity |
| Context selection and attachment reads | Inference over submitted request content |

The provider boundary is the network boundary. Local files and images are read by the client, and only request content
needed for inference is serialized to the configured endpoint. Model-requested actions return to the client, pass
through the normal permission engine, execute there, and may then be included as bounded evidence in a later request.
Configured compute endpoints are direct peers and do not inherit ambient operating-system proxy settings. Web access
and release discovery use their own transports and policies.

Initialization has three hardware modes:

1. Local Ollama uses detected client hardware.
2. The Dairack compute bridge supplies verified server hardware.
3. Plain remote Ollama is marked unverified and keeps backend batching, threading, and placement automatic.

Model registries record both the compute endpoint and whether hardware was verified. A remote endpoint is never tuned
against the client GPU by accident.

Declared modality support is binary. Quality is relative: supporting images does not by itself make a model the best
visual reasoner. Registry overrides exist because generic metadata cannot replace local benchmarks.

## Security Boundaries

- Model output is untrusted and parsed into a closed tool schema.
- The default `ask` policy requires user approval for external effects and reads.
- `read-auto` is limited to active-project structured reads and strictly parsed machine-status commands.
- Network tools are never included in `read-auto`.
- Web and update requests connect to the exact public address validated for each redirect hop and enforce size and
  time budgets.
- Compute credentials are stored separately from printable configuration and chat state.
- The compute bridge binds to loopback by default, uses bearer authentication, and has no catch-all proxy route.
- Patch targets are checked against the working directory, dry-run first, and checkpointed before application.
- Interactive password prompts are blocked from the embedded command runner.
- Conversations, checkpoints, configuration, and generated policy are user state and are not package data.

Tool schema and action presentation metadata share one registry. The runtime owns one lifecycle for model-requested and
direct actions: activity state, cancellation capability, timing, authority, structured history, display, persistence,
and teardown. UI layers render that contract but do not infer behavior from tool names or user-facing wording.

This is an approval boundary, not an operating-system sandbox. See [Security](SECURITY.md) and
[Permissions](docs/permissions.md).

Release metadata is also untrusted. Update feeds may provide a version and HTTPS notes link, but never an install
command or package source. Dairack constructs a pinned command locally for its owning `uv`, `pipx`, or managed Python
environment and requires confirmation before running it.

## Extension Rules

A new provider implements `providers.base.ModelProvider` and maps its metadata to `ModelDescriptor`. Provider-specific
identifiers and transport logic stay in the adapter. Routing consumes normalized capabilities only.

New model families normally require no code. Pull the model and refresh the registry. If inference is weak, improve
generic metadata handling or add an explicit user override; do not add a model-name branch to coordinator code.

Schema changes require a version increment, migration logic, round-trip tests, and preservation of unknown fields
where practical.

The repository-only coordinator lab in `tools/` runs action-free deterministic and semantic routing scenarios. It is
excluded from runtime packages. Tuning changes require grouped family holdout results and no safety/modality regression.
