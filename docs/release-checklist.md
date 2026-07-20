# Release Checklist

## Identity and Legal

- Confirm the repository and package names are available at release time.
- Confirm the right to use the project name and any future logo; retain the non-affiliation notice.
- Review the MIT license choice and copyright holder wording.
- Audit dependency licenses from the final lock or installation report.

## Quality

- Run compile, Ruff lint/format, all tests, and `python -m build` from a clean checkout.
- Run the repository coordinator lab in `quick` mode and the budgeted semantic holdout profile for routing changes.
- Install the wheel into a new environment and run `dairack --version`, `init`, `doctor`, and the Textual UI.
- Test desktop and narrow terminal layouts, modal escape behavior, action-specific cancellation, atomic-action labeling,
  approvals, direct command evidence, and patch undo.
- Test initialization on CPU-only hardware and at least one NVIDIA, ROCm, or Apple Silicon host before claiming support.
- Verify migration using a copy of configuration and chats from the previous release.
- Test cached, forced, offline, malformed, and successful update checks against the production HTTPS endpoint.

## Security and Privacy

- Re-run negative permission tests for shell composition, network auto-approval, path traversal, and patch targets.
- Confirm package archives contain no chats, indexes, checkpoints, credentials, machine paths, or model files.
- Review `SECURITY.md`, enable private vulnerability reporting, and document supported versions.

## Distribution

- Update `CHANGELOG.md` and remove the release's entries from Unreleased.
- Tag the exact tested commit and build artifacts from that tag.
- Validate sdist and wheel metadata with `twine check` or an equivalent validator.
- Publish to a test index before the production package index.
- Attach checksums and release notes; do not publish generated local state.
- Set `DEFAULT_UPDATE_INDEX_URL` to an endpoint owned by the project, and verify its version matches the published
  package exactly. Never point it at an unclaimed package or repository name.
- Confirm `uv`, `pipx`, and managed-venv update commands in clean installs; verify the Textual flow saves and exits.
