# Release and clean-machine checklist

Automated 1.1.0 evidence was recorded from release run
[`30696525252`](https://github.com/fileworks/unpacksort/actions/runs/30696525252)
and Homebrew PR
[`fileworks/homebrew-tap#21`](https://github.com/fileworks/homebrew-tap/pull/21).
Unchecked items still require an external clean host or WinGet acceptance.

Record the release version, tag, workflow run URLs, operator, and results.

## Reservation and protection

- [x] Recheck exact names on GitHub, PyPI, Homebrew, WinGet, npm, crates.io,
      RubyGems, and Go discovery.
- [x] Confirm GitHub, PyPI, and Homebrew are reserved and the active PyPI
      publisher still matches.
- [x] Confirm required protected-main checks and protected release environments.
- [x] Confirm no credential value appears in tracked files or workflow output.

## Source and wheel

- [x] Ruff format/lint, strict mypy, pytest/Hypothesis, and at least 90% coverage.
- [x] Linux, macOS, and Windows pass Python 3.12 and current Python.
- [x] Build the exact sdist/wheel once and install the wheel alone.
- [x] Run `unpacksort --help` and deterministic hierarchy/flatten/PDF-only
      fixtures through the installed command.
- [x] Confirm ZIP/ZIP64, TAR compression families, 7z, RAR, encryption,
      corruption, unsafe links/names, limit boundaries, PDF parsing, duplicate
      content, and interruption/resume fixtures.
- [ ] Install the published version with pipx in a clean environment and repeat
      the representative fixture.

## Windows portable

- [x] Build on a clean Windows x64 runner with the locked PyInstaller version.
- [x] Confirm the portable ZIP contains only the expected nested executable.
- [x] Run help and the representative fixture through the unpacked executable.
- [x] Confirm the unsigned/SmartScreen warning is in README, manual, and notes.

## Assets and immutable publication

- [x] Verify filenames, versions, magic types, portable layout, and checksums.
- [x] Confirm PyPI OIDC publication and no long-lived token.
- [x] Confirm GitHub has sdist, wheel, portable ZIP, and `SHA256SUMS`.
- [ ] Download every asset from the public release and re-run verification.
- [ ] Confirm no asset was replaced after publication.

## Catalogs

- [x] Homebrew formula has pinned/hash-verified resources and no install network.
- [x] `brew audit --strict`, `brew style`, source install, and meaningful
      extraction/manifest formula test pass on a clean machine.
- [x] WinGet templates validate the immutable URL/SHA, nested portable path,
      x64 architecture, identifier, and `unpacksort` alias.
- [x] Record the Homebrew PR and WinGet bootstrap as the visible external
      follow-up.
