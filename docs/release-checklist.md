# Release Checklist

<p align="center">
  <a href="../README.md">Overview</a> &nbsp;&middot;&nbsp;
  <a href="README.md">Documentation</a> &nbsp;&middot;&nbsp;
  <a href="../CONTRIBUTING.md">Contributing</a> &nbsp;&middot;&nbsp;
  <a href="../CHANGELOG.md">Changelog</a>
</p>

Run every release from a clean checkout of the exact candidate commit. A checked item represents observed evidence,
not an assumption inherited from an earlier build.

## Identity and Legal

- [ ] Confirm the repository and package names are controlled by the project.
- [ ] Confirm the right to use the project name and any release artwork; retain required non-affiliation notices.
- [ ] Review the MIT license and copyright-holder wording.
- [ ] Audit dependency licenses from the final lock or installation report.

## Quality

- [ ] Run compile, Ruff lint and format, the complete test suite, and `python -m build` from a clean checkout.
- [ ] Run the Coordinator lab in `quick` mode.
- [ ] Run the budgeted semantic holdout profile when routing policy or tuning changed.
- [ ] Install the wheel into a new environment and run `dairack --version`, `dairack init`, and `dairack doctor`.
- [ ] Launch the packaged terminal interface against a temporary `DAIRACK_HOME`.
- [ ] Test desktop and narrow layouts, modal escape, approval focus, action cancellation, atomic-action labeling, direct
  command evidence, and patch recovery when those surfaces changed.
- [ ] Test CPU-only setup and each hardware family claimed by the release.
- [ ] Verify migration using a copy of configuration and chats from the previous release.
- [ ] Test cached, forced, offline, malformed, and successful update checks against the production HTTPS endpoint.

<details>
<summary>Core release gate</summary>

```bash
python -m compileall -q src tests tools
python -m ruff check src tests tools
python -m ruff format --check src tests tools
python -m pytest
.venv/bin/python tools/coordinator_lab.py --profile quick
python -m build
python -m twine check dist/*
```

</details>

## Security and Privacy

- [ ] Re-run negative tests for shell composition, network auto-approval, path traversal, redirect handling, bridge
  authorization, and patch targets.
- [ ] Confirm package archives contain no chats, indexes, checkpoints, credentials, machine paths, or model files.
- [ ] Review [Security](../SECURITY.md) and confirm private vulnerability reporting remains enabled.
- [ ] Verify compute tokens remain outside configuration, transcripts, child-process environments, and chat archives.

## Distribution

- [ ] Move the release entries out of `Unreleased` in [CHANGELOG.md](../CHANGELOG.md).
- [ ] Tag the exact tested commit and build artifacts from that tag.
- [ ] Validate wheel and source-distribution metadata with `twine check` or an equivalent validator.
- [ ] Publish to a test index before the production package index.
- [ ] Attach checksums and release notes; exclude generated local state.
- [ ] Confirm the configured update endpoint version matches the published package exactly.
- [ ] Confirm clean `uv`, `pipx`, and managed-environment update paths.
- [ ] Verify the terminal update flow saves the active chat and exits into the new version cleanly.

The update endpoint may identify a version and release-notes URL. It must never provide an executable command or an
uncontrolled package source; Dairack constructs the pinned update command locally.

## After Publication

- [ ] Install from the public source into a clean environment and run the smoke checks again.
- [ ] Verify release links, checksums, badges, and the update notice from outside the maintainer session.
- [ ] Confirm the default branch is clean and the next `Unreleased` section is ready.
