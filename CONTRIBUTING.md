# Contributing

<p align="center">
  <a href="README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="docs/README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="ARCHITECTURE.md">Architecture</a> &nbsp;&middot;&nbsp;
  <a href="SECURITY.md">Security</a>
</p>

Dairack favors narrow changes with explicit ownership, observable behavior, and tests at stable boundaries. Read the
[architecture](ARCHITECTURE.md) before moving responsibilities between modules.

## Development Setup

Use Python 3.11 or newer in an isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Use an isolated `DAIRACK_HOME` for setup, migration, or destructive lifecycle tests. Never point experimental code at
your normal chats, indexes, checkpoints, or compute credentials.

## Verification

Run the relevant focused test while developing, then the complete local gate before opening a pull request:

```bash
python -m compileall -q src tests tools
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m pytest
.venv/bin/python tools/coordinator_lab.py --profile quick
python -m build
python -m twine check dist/*
```

| Change area | Required evidence |
| --- | --- |
| UI | Textual `run_test` coverage at desktop and narrow terminal sizes |
| Permissions or tools | Positive, denial, malformed-input, and boundary tests |
| Configuration or state | Round-trip, migration, unknown-key, and failure-path tests |
| Coordinator | Labeled scenarios and grouped holdout results without safety or modality regression |
| Packaging | Clean wheel/sdist build and metadata validation |

## Change Discipline

- Keep provider transport, hardware policy, model metadata, permissions, and presentation in their owning modules.
- Do not add model-specific score branches to Coordinator. Prefer provider metadata, generic inference, catalog data,
  or explicit user overrides.
- Preserve unknown configuration keys and existing model overrides during migrations.
- Add tests at the smallest stable behavioral boundary.
- Treat model-generated tool calls, project content, network content, and persisted chats as hostile input.
- Keep paraphrased coordinator scenarios in the same family as their source so grouped validation cannot leak.
- Do not commit generated state, model weights, chats, checkpoints, indexes, credentials, or machine-specific reports.

`runtime.py` is a compatibility core being reduced over time. Move one coherent domain at a time, retain behavior
through tests, and keep broad formatting churn separate from functional changes.

## Pull Requests

Keep each pull request reviewable and free of unrelated cleanup. The description should make these points explicit:

- [ ] user-visible behavior and reason for the change;
- [ ] permission, state, provider, routing, and UI risk;
- [ ] tests and manual checks performed;
- [ ] migration and rollback behavior when state changes;
- [ ] public API, file-format, and documentation updates.

Security-sensitive reports belong in the [private disclosure channel](SECURITY.md#report-a-vulnerability), not a public
pull request or issue.
