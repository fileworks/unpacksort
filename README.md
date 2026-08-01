<img src=".github/icon.svg" alt="" width="72" height="72" align="left">

# unpacksort

`unpacksort` safely recovers files from an mbox or a directory, recursively opens
supported archives and attached messages, removes byte-identical duplicates, and
publishes a deterministic type-grouped result with complete provenance.

It supports Python 3.12+, ZIP/ZIP64, TAR (plain, gzip, bzip2, xz, and zstandard),
7z, parser-validated PDFs, and common ZIP application packages. RAR is detected
and retained as unprocessed; source links and archive links are never followed.

## Status

**Published.** Version 1.1.0 is available from PyPI and the official GitHub
Release, including the unsigned Windows x64 portable ZIP. The Homebrew formula
is available from `fileworks/tap`. The initial WinGet manifest is
[under Microsoft review](https://github.com/microsoft/winget-pkgs/pull/410897);
the catalog identity is not reserved until that PR is accepted.

## Overview

`unpacksort` recovers files from a mailbox or a directory tree: it opens nested
archives and attached messages, removes byte-identical duplicates, and publishes
a deterministic, type-grouped result with a manifest recording where every file
came from.

Deterministic means the same input produces the same output, every time — which
is what makes a recovery run something you can check rather than something you
have to trust.

## Install

```console
pipx install unpacksort
```

Alternatively run `brew install fileworks/tap/unpacksort`, or download the
Windows x64 portable ZIP from the official GitHub Release. The executable is
unsigned and can trigger an operating-system trust prompt. Verify the published
SHA-256 checksum, while remembering that a checksum detects damage but does not
independently prove who published a file. `winget install fileworks.unpacksort`
will become available only after Microsoft accepts
[the initial manifest](https://github.com/microsoft/winget-pkgs/pull/410897).

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

## Usage

```console
unpacksort SOURCE DESTINATION [options]
```

| Option | Effect |
|---|---|
| `--flatten` | One directory per type instead of mirroring the source layout |
| `--pdf-only` | Extract and validate PDFs, ignore everything else |
| `--log-file PATH` | Write bounded rotating progress and diagnostics (default: beside destination) |
| `--verbose` | Include debug diagnostics in the logfile |

`unpacksort --help` is authoritative.

## Configuration

There is no configuration file. Behaviour and all seven safety limits are set by
explicit command-line flags; run `unpacksort --help` for their names, defaults,
and minimum values. Raising a limit is an operator decision and expands the
resource budget for untrusted input, so scheduled jobs should pin reviewed
values rather than accept input-controlled arguments.

## Troubleshooting

**A RAR archive was not extracted.** RAR is detected and retained unprocessed;
`unpacksort` does not bundle a RAR implementation.

**The run stopped at a safety limit.** The manifest names which limit and which
container tripped it. That is the intended behaviour for a suspicious archive.

**A PDF was rejected.** PDFs are parser-validated; a file that cannot be parsed
is retained as-is rather than published as a valid document.

**The output differs between two runs of the same input.** It should not. That is
a bug worth reporting, with the two manifests.

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Parsers run in-process: `unpacksort` bounds extraction and never follows links,
but it is not a malware scanner or a sandbox. Process hostile data inside an
additional operating-system sandbox.

## License

MIT — see [LICENSE](LICENSE).
