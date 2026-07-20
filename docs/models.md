# Models and Coordinator

## Compatibility

Dairack does not require a fixed Ollama stack. Every installed chat model is available in direct mode. Coordinator
discovers models from the Ollama API and builds a profile from declared capabilities, context, parameter metadata,
hardware fit, and optional catalog or user priors.

Vision and tool support are hard capabilities. Quality scores are routing estimates, not guarantees. Unfamiliar
models are labeled `INFERRED` with lower confidence; catalog matches are labeled `CURATED`; explicit capability
overrides are labeled `CALIBRATED`.

## Lifecycle

```bash
dairack models recommend [--profile minimal|balanced|complete]
dairack models pull MODEL
dairack models update MODEL
dairack models update --all --yes
dairack models remove MODEL
dairack models refresh
```

`pull` and `update` use Ollama's HTTP API. Re-pulling a mutable tag updates its manifest and reuses unchanged layers.
Versioned tags coexist until removed. Removal requires confirmation unless `--yes` is supplied.

In the terminal UI, `F6` or `/library` opens the complete lifecycle surface. `/models` remains a compatibility alias;
the `dairack models ...` command namespace is unchanged.

## Automatic Routing

Coordinator uses task signals for coding, agent work, reasoning, general synthesis, research, vision, risk, and
complexity. Policy changes how much efficiency, residency, planning, review, and delegation cost matter. It normally
chooses one executor; additional model passes require a planning, review, or bounded specialist decision.

There is no required coordinator model. When semantic arbitration is warranted, Coordinator selects an efficient
suitable model from the installed registry and requests strict structured requirements. Deterministic transport facts,
capability gates, and policy limits remain authoritative. Local outcome learning is role-specific, evidence-gated, and
capped; it adjusts ranking slightly rather than replacing capability profiles.

Missing roles do not make the stack invalid. Coordinator uses the best available fallback and suppresses handoffs
that offer no material gain. Missing vision is reported because a non-vision model cannot safely accept image input.

Compute preferences expressed in conversation are typed, confidence-gated, and scoped to one turn. A request for a
larger executor must select a materially larger model that remains a credible task and hardware fit; otherwise the
baseline is retained. Quality and efficiency requests adjust the same provider-neutral ranking rather than selecting
models by name. Questions about model tradeoffs and adjectives describing the requested content remain automatic.
Semantic arbitration may promote a specialized task signal only when the current or referenced user task provides
corroborating evidence, so an inferred label cannot independently grant tools or force a specialist route.

## Advanced Configuration

<p align="center">
  <img src="assets/coordinator-policy.png" alt="Dairack coordinator operating policy selector" width="614">
</p>

```bash
dairack coordinator show
dairack coordinator enable
dairack coordinator disable
dairack coordinator policy adaptive|quality|efficient
dairack coordinator set planning|review|delegation|semantic|learning on|off
dairack coordinator prefer general|coding|agent|reasoning|research|vision|planner|reviewer MODEL
dairack coordinator prefer ROLE auto
```

Preferences are soft score bonuses and never bypass capability checks. Capability and runtime overrides remain in
the model registry and survive refreshes:

```bash
dairack models set MODEL coding 0.90
dairack models set MODEL reasoning 0.84
dairack models set MODEL num_ctx 8192
dairack models reset MODEL
```
