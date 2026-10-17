# Changelog

## [1.0.1] (2026-10-17)

### Security

- Source reads are confined to the inspected source tree: fingerprinting,
  ingestion and mailbox parsing refuse a file or ancestor replaced by a link
  after the scan (POSIX `O_NOFOLLOW` walk; Windows handle identity and final
  path, failing closed when the final path cannot be resolved).

### Fixed

- A rejected invocation no longer creates the default logfile beside a
  mistyped destination.

### Removed

- Retired PyInstaller, portable-ZIP, WinGet and semantic-release tooling;
  releases ship exactly one wheel and one sdist.

## [1.0.0] (2026-10-02)

Initial Fileworks baseline, preserving the reviewed working implementation.
Generated AI-first. Earlier beta history is retained in private recovery archives.
