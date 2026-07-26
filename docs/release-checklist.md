# Release and clean-machine checklist

Record the release version, tag, workflow run URLs, operator, and results.

## Reservation and protection

- [ ] Recheck exact names on GitHub, PyPI, Homebrew, WinGet, npm, crates.io,
      RubyGems, and Go discovery.
- [ ] Confirm GitHub is reserved and the PyPI pending publisher still matches.
- [ ] Confirm required protected-main checks and environment reviewers.
- [ ] Confirm no credential value appears in tracked files or workflow output.

## Source and wheel

- [ ] Ruff format/lint, strict mypy, pytest/Hypothesis, and at least 90% coverage.
- [ ] Linux, macOS, and Windows pass Python 3.12 and current Python.
- [ ] Build the exact sdist/wheel once and install the wheel alone.
- [ ] Run `unpacksort --help` and deterministic hierarchy/flatten/PDF-only
      fixtures through the installed command.
- [ ] Confirm ZIP/ZIP64, TAR compression families, 7z, RAR, encryption,
      corruption, unsafe links/names, limit boundaries, PDF parsing, duplicate
      content, and interruption/resume fixtures.
- [ ] Install the published version with pipx in a clean environment and repeat
      the representative fixture.

## Windows portable

- [ ] Build on a clean Windows x64 runner with the locked PyInstaller version.
- [ ] Confirm the portable ZIP contains only the expected nested executable.
- [ ] Run help and the representative fixture through the unpacked executable.
- [ ] Confirm the unsigned/SmartScreen warning is in README, manual, and notes.

## Assets and immutable publication

- [ ] Verify filenames, versions, magic types, portable layout, and checksums.
- [ ] Confirm PyPI OIDC environment approval and no long-lived token.
- [ ] Confirm GitHub has sdist, wheel, portable ZIP, and `SHA256SUMS`.
- [ ] Download every asset from the public release and re-run verification.
- [ ] Confirm no asset was replaced after publication.

## Catalogs

- [ ] Homebrew formula has pinned/hash-verified resources and no install network.
- [ ] `brew audit --strict`, `brew style`, source install, and meaningful
      extraction/manifest formula test pass on a clean machine.
- [ ] WinGet templates validate the immutable URL/SHA, nested portable path,
      x64 architecture, identifier, and `unpacksort` alias.
- [ ] Record Homebrew/WinGet PR links or a visible retryable external follow-up.
