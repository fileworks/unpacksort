<img src=".github/icon.svg" alt="" width="72" height="72" align="left">

# unpacksort

`unpacksort` safely recovers files from an mbox or a directory, recursively opens
supported archives and attached messages, removes byte-identical duplicates, and
publishes a deterministic type-grouped result with complete provenance.

It supports Python 3.12+, ZIP/ZIP64, TAR (plain, gzip, bzip2, xz, and zstandard),
7z, parser-validated PDFs, and common ZIP application packages. RAR is detected
and retained as unprocessed; source links and archive links are never followed.

## Status

The v1.0.0 baseline is distributed through private GitHub releases.
Use the release notes for verified artifact and platform status.

## Overview

`unpacksort` recovers files from a mailbox or a directory tree: it opens nested
archives and attached messages, removes byte-identical duplicates, and publishes
a deterministic, type-grouped result with a manifest recording where every file
came from.

Deterministic means the same input produces the same output, every time — which
is what makes a recovery run something you can check rather than something you
have to trust.

## Install

Python 3.12+ and Git are required. The public source can be installed directly:

```console
pipx install git+https://github.com/fileworks/unpacksort.git@v1.0.0
unpacksort --version
```

The owner also has private prebuilt wheels:

Download the v1.0.0 wheel from the authenticated
[private Fileworks releases](https://github.com/fileworks/private-releases/releases).
Python 3.12+ and pipx (or uv) are required on Windows, macOS and Linux.

```console
gh auth login
gh release download v1.0.0 --repo fileworks/private-releases --pattern 'unpacksort-1.0.0-*.whl'
pipx install ./unpacksort-1.0.0-py3-none-any.whl
unpacksort --version
```

PyPI publication has been retired; install the downloaded file, rather than
an unqualified package name. The private Homebrew tap is optional for authenticated users.

### Private Homebrew installation (macOS)

Your GitHub account needs access to `fileworks/homebrew-tap`. Authenticate Git
over HTTPS once; clone the tap before invoking Homebrew:

```sh
brew update
brew install gh
gh auth login
gh auth setup-git
gh repo clone fileworks/homebrew-tap "$(brew --repository)/Library/Taps/fileworks/homebrew-tap"
brew tap fileworks/tap
HOMEBREW_NO_AUTO_UPDATE=1 brew install fileworks/tap/unpacksort
unpacksort --version
```

For updates, run `git -C "$(brew --repository fileworks/tap)" pull --ff-only`,
then replace `brew install` with `brew upgrade` above. A 404 usually means the GitHub account
lacks access. Do not put tokens in URLs or commit them. The tap verifies the
bundled application wheel and installs pinned dependency wheels without an index.

For wheel checksums, Windows/macOS setup, and upgrades, see the
[private installation guide](https://github.com/fileworks/private-releases#installation).

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
never directly control public paths. The application does not isolate parsers in
a process or VM and is not a malware scanner.

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

## Exit codes

`immich-export`, `paperless-export` and `unpacksort` share one exit-code
vocabulary, so a script can branch on the code without knowing which tool it
ran. The class of outcome is the same everywhere; the specific condition is
this tool's, and the table below is what it means here.

| Code | Name | Meaning |
|---|---|---|
| 0 | `SUCCESS` | everything asked for was done |
| 1 | `PARTIAL` | some content was published and some was not; the report names each stable reason |
| 2 | `USAGE` | bad flags or an unusable input path — nothing was attempted |
| 3 | `CONFLICT` | the destination holds an incompatible journal or a frozen plan |
| 4 | `FATAL` | unexpected failure, or output that could not be written |
| 130 | `INTERRUPTED` | cancelled by the operator; committed journal work can be resumed |

## Troubleshooting

**A RAR archive was not extracted.** RAR is detected and retained unprocessed;
`unpacksort` does not bundle a RAR implementation.

**The run stopped at a safety limit.** The manifest names which limit and which
container tripped it. That is the intended behaviour for a suspicious archive.

**A PDF was rejected.** PDFs are parser-validated; a file that cannot be parsed
is retained as-is rather than published as a valid document.

**The output differs between two runs of the same input.** It should not. That is
a bug worth reporting, with the two manifests.

## Development

```console
uv sync --locked --all-groups
uv run ruff check . && uv run ruff format --check .   # lint
uv run mypy                                           # strict types
uv run pytest                                         # tests
uv build                                              # sdist + wheel
```

Renovate batches routine non-major updates into one weekly `fix(deps)` pull
request, permits only one dependency branch, and squash-merges only after all
checks pass. Major, replacement, and rollback updates require explicit
Dependency Dashboard approval and never auto-merge. Releases are created from
Conventional Commits after all source, wheel, and Windows portable checks pass;
the operator runbook is [docs/release.md](docs/release.md).

Use an ignored `CLAUDE.local.md` at the repository root for per-clone paths,
commands, or private preferences. Never store credentials or other secrets
there.

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
Parsers run in-process: `unpacksort` bounds extraction and never follows links,
but it is not a malware scanner or a sandbox. Process hostile data inside an
additional operating-system sandbox.

## License

MIT — see [LICENSE](LICENSE).

## Development approach

This project was generated AI-first, with AI assistance used throughout its implementation.
