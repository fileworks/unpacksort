# Repository instructions

- Preserve deterministic output across Linux, macOS, and Windows.
- Never use an archive extract-to-directory convenience API or follow links.
- Keep parser boundaries typed and keep strict mypy enabled globally.
- Add generated, compact safety fixtures rather than archive-bomb payloads.
- Run Ruff, strict mypy, pytest, and installed-wheel E2E before release.
- Keep credentials out of files, logs, manifests, reports, and command output.
- Use Conventional Commits; do not add automated co-author trailers.
