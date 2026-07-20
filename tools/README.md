# Coordinator Lab

This directory contains development and release-validation tooling. It is not
included in the Dairack wheel and never runs during a chat.

Run from the repository root with the project environment active:

```bash
.venv/bin/python tools/coordinator_lab.py --profile quick
.venv/bin/python tools/coordinator_lab.py --profile semantic
.venv/bin/python tools/coordinator_lab.py --profile full
.venv/bin/python tools/coordinator_lab.py --profile semantic --optimize --candidates 96
```

Profiles are deliberately bounded:

- `quick` runs 1,116 unique deterministic cases without model inference.
- `semantic` runs the curated archetypes and caches classifier responses.
- `full` adds semantic prompt variants under an explicit inference budget.

The optimizer searches five bounded, interpretable policy parameters. It uses
scenario-family leave-one-out validation, rejects hard safety or modality
regressions, regularizes toward the checked-in baseline, and never applies a
candidate automatically.

Scenario data lives in `tools/data/coordinator-scenarios.json`. External JSON
datasets can be supplied with `--dataset`. Keep paraphrases in the same family
as their source archetype so they cannot leak across grouped validation folds.

The harness only calls coordinator route selection. It does not execute shell,
file, patch, web, or package-management tools.

Runs use a reproducible cold model pool by default, regardless of what Ollama
happens to have loaded. Use `--resident-model MODEL` (repeatable) to run an
explicit warm-state sensitivity check without changing production policy.
