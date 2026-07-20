# Coordinator Lab

<p align="center">
  <a href="../README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="../docs/README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="../ARCHITECTURE.md">Architecture</a> &nbsp;&middot;&nbsp;
  <a href="../CONTRIBUTING.md">Contributing</a>
</p>

The Coordinator lab is repository-only development and release tooling. It does not ship in the Dairack wheel, run
during a chat, or execute agent tools.

## Run the Lab

Use the project environment from the repository root:

```bash
.venv/bin/python tools/coordinator_lab.py --profile quick
.venv/bin/python tools/coordinator_lab.py --profile semantic
.venv/bin/python tools/coordinator_lab.py --profile full
```

| Profile | Work performed | Model inference |
| --- | --- | --- |
| `quick` | 1,116 unique deterministic routing cases | No |
| `semantic` | Curated semantic archetypes with cached classifier responses | Bounded |
| `full` | Semantic archetypes plus prompt variants | Explicit budget |

Runs use a reproducible cold model pool regardless of what Ollama currently has loaded. Add
`--resident-model MODEL` more than once when a controlled warm-state sensitivity check is part of the experiment.

## Scenario Data

The checked-in dataset lives at `tools/data/coordinator-scenarios.json`. Supply another JSON dataset with `--dataset`.
Keep every paraphrase in the same family as its source archetype so grouped validation folds cannot leak related
examples into both training and evaluation.

The harness calls Coordinator route selection only. It cannot execute shell, file, patch, web, package-management, or
other client tools.

## Bounded Optimization

```bash
.venv/bin/python tools/coordinator_lab.py --profile semantic --optimize --candidates 96
```

The optimizer searches five bounded, interpretable policy parameters. It uses scenario-family leave-one-out
validation, regularizes toward the checked-in baseline, and rejects hard safety or modality regressions. A candidate is
reported for review and never applied automatically.

Tuning changes should include grouped holdout results, per-role movement, and explicit confirmation that safety and
modality gates did not regress.
