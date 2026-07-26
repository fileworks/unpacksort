# unpacksort

`unpacksort` safely recovers files from an mbox or a directory, recursively opens
supported archives and attached messages, removes byte-identical duplicates, and
publishes a deterministic type-grouped result with complete provenance.

It supports Python 3.12+, ZIP/ZIP64, TAR (plain, gzip, bzip2, xz, and zstandard),
7z, parser-validated PDFs, and common ZIP application packages. RAR is detected
and retained as unprocessed; source links and archive links are never followed.

## Install

```console
pipx install unpacksort
```

Future releases also provide `brew install fileworks/tap/unpacksort`, a Windows
x64 portable ZIP, and `winget install fileworks.unpacksort`. Initial executable
artifacts are unsigned and can trigger an operating-system trust prompt. Verify
the published SHA-256 checksum, while remembering that a checksum detects damage
but does not independently prove who published a file.

## Quick start

```console
unpacksort ~/Mail/archive.mbox ~/Recovered
unpacksort ~/Downloads ~/Recovered --flatten
unpacksort ~/Mail/archive.mbox ~/Recovered-PDFs --pdf-only
```

Hierarchy mode is the default. It preserves source, message, and archive ancestry
beneath fixed type groups. Flatten mode publishes directly beneath each group;
distinct collisions are named `name.ext`, `name_1.ext`, and so on. Byte-identical
occurrences reference one canonical file and do not consume suffixes.

A successful run writes `manifest.jsonl` and `report.txt`. Exit `1` means the
result is trustworthy but partial—for example because an encrypted, corrupt,
unsafe, limit-blocked, or unsupported item was retained or reported. Re-run the
same command to resume compatible committed work.

See the [operating manual](docs/manual.md), [release and channel setup](docs/release.md),
and [security policy](SECURITY.md).

## Safety model

Inputs are treated as personal but potentially malformed. Extraction is bounded,
staged privately, content-addressed, and published atomically. Archive paths
never directly control public paths. The initial release does not isolate parsers
in a process or VM and is not a malware scanner.

## Development

```console
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
```

Use an ignored `CLAUDE.local.md` at the repository root for per-clone paths,
commands, or private preferences. Never store credentials or other secrets
there.

Licensed under the [MIT License](LICENSE).
