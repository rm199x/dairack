# Contributing

## Setup

Use Python 3.11 or newer and an isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the full local checks before opening a pull request:

```bash
python -m compileall -q src tests tools
python -m pytest
ruff check src tests tools
ruff format --check src tests tools
.venv/bin/python tools/coordinator_lab.py --profile quick
python -m build
```

## Change Discipline

- Keep provider transport, hardware policy, model metadata, permissions, and presentation in their owning modules.
- Do not add model-specific score tables to the coordinator. Prefer provider metadata, generic inference, or user
  overrides.
- Preserve unknown configuration keys and existing model overrides during migrations.
- Add tests at the smallest stable boundary. UI behavior should use Textual's `run_test` pilot at desktop and narrow
  terminal sizes.
- Treat model-generated tool calls as hostile input. Security-sensitive behavior needs negative tests.
- Add coordinator failures as labeled scenarios; keep paraphrases in their source family for grouped validation.
- Do not commit generated user state, model weights, chats, checkpoints, indexes, or credentials.

The compatibility runtime is intentionally being reduced. Refactors from `runtime.py` should move one coherent domain
at a time and retain behavior through tests; avoid broad formatting churn mixed with behavioral changes.

## Pull Requests

Describe the user-visible behavior, risk, test evidence, and any state/schema migration. Keep changes reviewable and
avoid unrelated cleanup. Public APIs and file formats need documentation before merge.
