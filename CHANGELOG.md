# Changelog

<p align="center">
  <a href="README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="docs/release-checklist.md">Release Checklist</a>
</p>

Notable user-visible changes are recorded here. Pre-1.0 work remains under `Unreleased`; versioned releases will follow
semantic versioning after 1.0.

## [Unreleased]

### Added

- Distributable `src` package and stable console entry point.
- Hardware and Ollama model discovery with generated runtime profiles.
- User-configurable model capability and runtime overrides.
- `dairack init`, `doctor`, `hardware`, and `models` lifecycle commands.
- XDG path support and portable `DAIRACK_HOME` state.
- Independently testable permission-policy and provider boundaries.
- Source installer, package metadata, CI, architecture, contribution, and security documentation.
- Optional hardware-fitted model sets plus unrestricted Ollama model install, update, removal, and transfer UX.
- Confidence-labeled model priors, local profile calibration, and soft Coordinator role preferences.
- Native Coordinator, model-library, profile editor, visual-input, approval, and cancellable transfer workflows.
- Cached HTTPS software release checks with `uv`, `pipx`, and managed-environment update commands.
- Windows state layout, hardware probes, installer, command fallbacks, and CI coverage.
- Animated responsive startup identity, transient fresh sessions, configurable recent-chat resume, and native
  image-path attachment.
- Native Ollama function tools with validated fallback parsing, resumable tool history, and approval-modal recovery.
- Route history, explicit route feedback, bounded model-role learning, and repository-only grouped coordinator
  evaluation.
- Registry-owned action presentation, typed transcript records, real process-tree cancellation, and explicit atomic-work
  feedback across agent and direct commands.
- Canonical `/library` command, curated primary help, typo recovery, reduced-motion support, and compact-terminal
  command discovery.
- Structured transcript severity, governed prose widths, horizontal code/diff review, and content-aware modal families.
- One-package client/compute roles with an authenticated inference-only bridge, Tailscale Serve integration, private
  pairing credentials, verified server hardware profiles, and native live compute switching.
- Confidence-gated per-turn quality, capacity, and efficiency controls with resolved-task execution and route feedback.
- Authoritative client/compute hardware identity, bounded cross-platform path discovery, and a deterministic
  `/hardware` surface.
- Restartable Linux user-service lifecycle for the remote compute bridge, including native status and removal commands.
- Exact-string `edit_file` action for targeted single-occurrence edits, checkpointed for `/undo`, scoped to the
  working directory, and previewed as a diff during permission review alongside `patch`.
- Frontend-agnostic turn decision core (`turn.py`) that owns the agent turn's repair, completion, review, and
  finalization ladder as pure tested functions; the agent-capable paths in Textual, the fallback terminal, and the
  plain CLI drive on it, replacing their duplicated decision ladders while retaining frontend-specific state adapters.
- Batched execution of multiple auto-approvable project reads returned in one response under `read-auto`, so
  independent lookups run in a single turn instead of failing the one-action check; each call still passes the same
  loop-guard, action-budget, and scope enforcement, and any non-auto-approvable member disqualifies the whole batch.
- Agent-invocable `grep` content search backed by ripgrep with an isolated, cancellable Python fallback, scoped and
  auto-approvable under `read-auto` exactly like other project reads, and eligible for batched execution.
- Coordinator learning conditioned on the classified task kind: a bounded conditional estimate interpolates from the
  shared model/role estimate as kind evidence matures, without adding duplicate influence from the same outcomes.
- Prompts typed while a response is active queue and send automatically when the turn completes; interrupting a turn
  returns queued input to the composer, while action approvals hold the queue until explicitly resolved. `Ctrl+C` now
  interrupts the active turn in both terminal interfaces instead of being unbound or exiting mid-stream.
- Context-aware file windows with explicit line continuations, plus a compact task-evidence ledger that preserves the
  useful head and tail of older tool results when their raw exchanges no longer fit the active request.
- Enforced generation reserve in request fitting: a response floor that binds when a raised budget ratio, small
  window, thinking mode, or final synthesis would otherwise starve the answer, with `num_predict` capped at the true
  window residual on every executor request so the provider can never context-shift the prompt away.
- Context-posture directive matched to the executor's window tier: tight windows instruct incremental windowed work
  with `start_line` continuations and section-by-section audits; generous windows get whole-file latitude.
- Reload-aware executor continuity: a resident incumbent survives a cold challenger whose lead is within a margin
  scaled to model size and policy stance, recorded in the route report; a validated `model_keep_alive` setting
  extends provider residency between turns.
- In-process semantic-assessment cache: identical routing turns reuse the classifier verdict instead of paying a
  model inference, with caller mutations isolated from the cached copy; each specialist consultation now ranks the
  model pool exactly once.
- Coordinator delegation and semantic assessment extracted into `coordinator/delegation.py` and
  `coordinator/semantic.py`, continuing the module split begun with analysis and ranking while preserving the full
  runtime API surface.
- Searchable model-library sections with concise installed-model rows and progressively disclosed profile details.

### Changed

- Native tool requests no longer embed the prose tool catalog in the system prompt; the schemas are authoritative
  there, keeping native function tools active within the default 4096-token context window. The compatibility text
  protocol retains the catalog.
- Read batching now fails closed when agent mode is disabled or final synthesis has begun, including provider responses
  that return tools which were not offered.
- Search exclusions are canonical across ripgrep and the fallback backend; fallback scans are file-, output-, time-,
  symlink-, and cancellation-bounded. Search approvals display both the root and expression.
- Task-kind calibration reports role evidence, kind evidence, and interpolation weight in route diagnostics.
- Renamed the product, package, command, state roots, and compute protocol to Dairack, with non-destructive migration
  of existing user state and narrow compatibility aliases for previous installations.
- Coordinator routing reads generated model metadata instead of fixed model-name tables.
- Adaptive routing accounts for hardware fit, residency, task demand, capability confidence, and user preferences.
- First run opens consumer model setup directly; no-model and unavailable-Ollama states provide recovery actions.
- Configuration, chat, and checkpoint metadata use atomic private writes.
- `read-auto` is restricted to project-scoped reads and safe machine-status commands; network tools require approval.
- Short self-contained turns no longer inherit prior-chat complexity; semantic routing is reserved for genuine
  discourse dependencies.
- Model-library actions now precede inventory, with fitted sets nested separately and bulk installed-tag refresh
  available natively.
- Welcome removal is atomic with first-message insertion, preventing animation refreshes from querying a detached
  widget.
- Terminal chrome now uses measured content breakpoints, event-driven motion, readable quiet-text contrast, and
  compact 32-column modal layouts. Short transitions render at 20 fps, active waits at 10 fps, and idle schedules no
  animation work.
- Coordinator semantic routing is independent of warm-model state and now resolves short contextual turns without
  contaminating fresh greetings.
- `/run`, `/test`, `/search`, `/index`, `/web`, and `/url` now share one timed action lifecycle and retain bounded
  structured evidence for subsequent turns; maintenance output remains outside conversation context.
- Approval focus is risk-aware, destructive actions default to Deny, and persistent read trust is restricted to
  verified read-only calls at the modal boundary.
- Coordinator ranking now suppresses unused capability headroom and learned residuals for trivial independent turns,
  while preserving specialist routing for tasks with real coding, reasoning, research, or vision evidence.
- Mode, activity, input, and archive state now have one owner each; completion throughput is transient, startup motion
  settles once, interruption takes display precedence, and session selectors no longer expose internal identifiers.
- Streaming transcript rendering is coalesced independently of token throughput, and composer sizing uses terminal-cell
  width for multilingual input.
- Coordinator evaluation now uses a reproducible cold model pool by default and accepts explicit resident-model inputs
  for controlled warm-state sensitivity checks.
- Remote model registries are tuned against verified compute hardware; plain remote Ollama leaves backend execution
  settings automatic instead of inheriting the client machine's hardware profile.
- Provider requests now reserve image and tool-schema context, shed optional native schemas before rejecting a turn,
  and retain runtime failures as structured context for the next user turn.
- Security boundaries now fail closed across resumed project scope, web redirects and terminal text, patch checkpoints,
  read-auto execution, stalled inference, project-index freshness, and corrupt configuration or index state.
- Semantic specialization now requires corroborating task evidence, and explicit compute preferences can change the
  executor without becoming sticky model state or granting action tools.
- Semantically identified runtime actions now require real tool calls, and a bounded completion arbiter prevents
  unexecuted commands or future-work promises from silently ending an agent turn.
- Windows shell actions now run through PowerShell consistently instead of mixing PowerShell syntax with `cmd.exe`.
- Fallback upgrades reuse a healthy isolated runtime instead of requiring system `venv` support again.
- Action requests written as `tool(field=value)` calls or containing unescaped Windows path backslashes now execute
  instead of failing as invalid protocol, and tool markup that misses the grammar never reaches the visible
  transcript; a request that stays malformed ends with a plain explanation instead of a raw parser error.
- Repeated identical reads with no state change in between are refused with the prior result, and persistent
  repetition ends in final synthesis instead of exhausting the action budget.
- Bounded tool output keeps its beginning and end so test verdicts and stack-trace roots survive truncation, and a
  dropped model connection retries once silently before surfacing an error.
- Independent review re-checks its own revision once, announces revisions in the transcript, applies only verdicts
  with usable grounded feedback, and now also runs in the fallback terminal interface.
- The planner stage receives indexed project context, and a confident semantic assessment can qualify paraphrased or
  non-English requests for planning, review, and specialist signals past the English keyword layer while
  deterministic vision, risk, and authority gates stay fixed.
- Over-budget requests omit project retrieval before failing, an unindexed project is announced to the model instead
  of silently returning no retrieval, complete answers ending in ordinary punctuation are no longer regenerated, web
  search reports a broken or rate-limited backend distinctly from an empty result set, and web page extraction skips
  navigation, forms, and footer chrome.
- Context pressure now measures the next provider payload, including durable memory, live work, tool schemas, routing
  and tokenizer headroom, and a reserved answer region. Compaction replaces covered history instead of retaining it
  beside the summary, runs between action steps, and scales its working set and tool-result budget to each executor's
  effective context profile.
- A blank or structurally incomplete continuation retries once on the same executor, then Coordinator may use one
  pre-ranked alternate executor. Repeated malformed Ollama tool output follows the same bounded recovery path; direct
  model mode never switches models implicitly.
- Automatic accelerator profiles can use 32K or 64K declared context windows on verified high-headroom hardware;
  hybrid, constrained, unverified-remote, and model-limited profiles retain conservative bounds.
- Final request fitting keeps system foundations, grounded memory, retrieval, and transient directives separable until
  budgeting is complete. Optional context is shed before current evidence, and completed tool results are dynamically
  narrowed when needed so a low-context file-analysis turn can still reach its next bounded continuation.
- The terminal shell now combines global state into one header, hides the activity row while idle, removes the prompt
  label and permanent function-key strip, and uses one contextual composer footer. Slash prefixes show several matching
  commands, connection states say `READY` or `CONNECTED`, and code fences expose horizontal overflow.
- Approval actions keep a fixed Deny / Allow Once / Auto-Allow Project Reads order. Risk controls initial focus and a
  critical rail without moving buttons, while persistent read policy now states its scope and lifetime explicitly.
- Signal feedback keeps one glyph geometry and moves a fractional linear-light luminance envelope rather than a cell
  cursor. Startup is one truthful 850 ms wordmark sweep, phase and completion transitions are shorter, activity motion
  stops on the first output token, and model-transfer frontiers ease toward but never exceed reported progress.

### Removed

- Automatic Python imports from writable user data directories.
- Developer-specific hardware and model assumptions from runtime policy.
