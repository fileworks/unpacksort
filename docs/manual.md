# Operating manual

## Installation

Python 3.12 or newer is required for Python installations.

```console
pipx install unpacksort
unpacksort --help
```

The intended release channels, once the first release is published, are:

- `brew install fileworks/tap/unpacksort`
- a Windows x64 portable ZIP from GitHub Releases
- `winget install fileworks.unpacksort`

The initial Windows executable is unsigned. Windows can display a trust warning.
Compare the file against the release's `SHA256SUMS`; that detects changed bytes
but does not independently establish the publisher's identity.

## Inputs and basic operation

Supply exactly one parser-confirmed mbox file or one directory tree, followed by
a destination:

```console
unpacksort ~/Mail/archive.mbox ~/Recovered
unpacksort ~/Documents/Imports ~/Recovered
```

A regular file that is not a structural mbox is rejected. Inside a directory,
parser-confirmed mbox and RFC 5322 files are treated as mail containers; an
`.eml` or `.mbox` extension alone is not enough. Directory discovery reads only
regular files and never follows a symlink, reparse point, device, FIFO, or
socket.

The source and destination cannot alias, contain one another in an unsafe
direction, or resolve through a link to the same location.

## Layout, filtering, and deduplication

Hierarchy mode is the default:

```text
<group>/<source-root>/<message/archive ancestry>/<filename>
```

Flatten mode omits ancestry:

```console
unpacksort INPUT DESTINATION --flatten
```

Distinct content competing for the same portable name receives `name.ext`,
`name_1.ext`, `name_2.ext`, and so on. A byte-identical duplicate refers to the
first stable SHA-256 occurrence and consumes no suffix. No duplicate hardlink,
symlink, or placeholder is published.

PDF-only mode still traverses mail and supported containers, but publishes only
unencrypted PDFs successfully parsed by pikepdf:

```console
unpacksort INPUT DESTINATION --pdf-only
```

Ordinary non-PDF leaves are intentional skips and do not make the result
partial. Encrypted, corrupt, unsupported, unsafe, or limit-blocked content is
still recorded, but its bytes are not retained in PDF-only mode.

## Fixed output groups

Every published leaf belongs to exactly one group:

```text
pdf
images
video
audio
documents
spreadsheets
presentations
ebooks
fonts
data
packages
email
archives/unprocessed
other
```

Detection uses validated structures, signatures, package profiles, and parser
evidence before a normalized extension fallback. It never consults host
`libmagic`, locale, or filesystem type metadata.

PDF signatures and extensions select candidates only. A password-protected PDF
is `encrypted_pdf`; a parser-invalid candidate is `corrupt_pdf`. Neither enters
`pdf`.

## MIME and mailbox behavior

Mbox messages stay in physical order and receive
`message-000001`, `message-000002`, and so on. MIME children retain parser order
and one-based part paths.

Only unnamed `text/plain` or `text/html` leaves without attachment disposition,
filename/name parameter, or Content-ID are display bodies. Every other leaf is
recovered, including:

- attached text;
- named or unnamed inline images, styles, and resources;
- Content-ID resources;
- unknown media types.

An unnamed emitted leaf starts as `part-NNNN.bin`; deterministic detection
replaces `.bin` with its canonical extension when known. Original filename
parameters, Content-ID, disposition, content type, transfer encoding, selected
headers, parser defects, generated name, and full ancestry remain in the
manifest.

An attached `message/rfc822` is both emitted as `.eml` and traversed for its own
non-body parts. Repeated bytes retain independent occurrence provenance.
Ancestor SHA-256 cycles and excessive combined mail/archive depth stop with a
stable reason rather than recurring indefinitely. Unattributable mailbox bytes
and failed transfer decoding make the result partial; they are never silently
claimed as extracted.

## Containers and ZIP application packages

The initial release expands these formats in-process:

- ZIP and ZIP64;
- TAR, TAR+gzip, TAR+bzip2, TAR+xz, and TAR+zstandard;
- 7z through packaged `py7zr`.

No external `7z`, `tar`, or extraction executable is required. Adapters expose
metadata and bounded streams only; they do not use extract-to-public-directory
APIs.

ZIP structure is inspected before generic expansion. Recognized atomic profiles
include OOXML documents/spreadsheets/presentations, ODF text/spreadsheet/
presentation, EPUB, JAR/WAR/EAR, APK/AAB, Python wheels, NuGet, and VSIX.
Structure takes precedence over extension. A generic ZIP named `.docx` is still
expanded; a structural document with a misleading name remains one native file.

RAR is signature-recognized but deliberately unsupported. In normal mode it is
deduplicated under `archives/unprocessed` with `unsupported_rar`. An encrypted,
corrupt, unsafe, cyclic, or limit-failed container invalidates all tentative
descendants and is retained the same way. In PDF-only mode its provenance and
reason remain report-only.

Archive absolute/drive/UNC paths, `..`, NUL, alternate-data-stream syntax,
links, devices, FIFOs, sockets, and other special entries are never
materialized.

## Safety limits

Defaults are visible in `unpacksort --help` and stored in the policy fingerprint:

| Limit | Default | Option |
| --- | ---: | --- |
| Combined mail/archive depth | 10 | `--max-depth` |
| Members per container | 100,000 | `--max-members-per-container` |
| Members per run | 1,000,000 | `--max-members-run` |
| Expanded bytes per member | 2 GiB | `--max-member-bytes` |
| Expanded bytes per container subtree | 20 GiB | `--max-container-bytes` |
| Logical expanded bytes per run | 100 GiB | `--max-run-bytes` |
| Declared or observed expansion ratio | 1,000:1 | `--max-expansion-ratio` |

All overrides must be positive and internally consistent. Declared metadata is
checked before reads; observed streaming counts remain authoritative. Repeated
or deduplicated logical members are charged each time. A limit failure is
atomic for its container subtree and does not discard independent successful
work.

## Journal, staging, resume, and cleanup

Each destination owns private state:

```text
<destination>/.unpacksort/journal.sqlite
<destination>/.unpacksort/blobs/<sha-prefix>/<sha256>
<destination>/.unpacksort/tmp/
```

Discovery commits complete content-addressed blobs and provenance to the
versioned SQLite journal. Temporary blobs are fsynced and atomically renamed.
Only after discovery completes does the planner freeze canonical occurrences
and public paths. Every public file, `manifest.jsonl`, and `report.txt` is
written through atomic replacement.

After an interruption, run the identical command. Complete blobs and the frozen
plan are reused; unmistakably incomplete private temporary files are removed.
If source content, policy, detector/tool compatibility, or journal schema
changed, the command exits `3` instead of mixing states. Use a new destination
for a different policy or changed source.

After a terminal result and after retaining the manifest/report elsewhere if
needed, `.unpacksort` can be removed to reclaim staging space. Removing it
disables resume and compatibility verification for that destination; it does
not remove published grouped files.

## Manifest and report

`manifest.jsonl` is UTF-8 JSON Lines in stable provenance order:

1. one `run` record with schema, source identity, and complete policy;
2. deterministic `container` records with digest, type, ancestry, outcome,
   member count, expanded bytes, and stable failure reason;
3. one `occurrence` record for every leaf, duplicate, skip, and unprocessed
   item.

Occurrence fields include source-relative identity; message/archive ancestry;
original and generated names; MIME/member metadata; diagnostics; detected media
type, group, method, and registry version; size and SHA-256 when available;
terminal status; canonical occurrence; canonical relative path; and stable
reason.

Operational timestamps, elapsed time, random identifiers, host separators, and
worker completion order are excluded. `report.txt` deterministically summarizes
outcome, status/group/reason counts, logical safety usage, and configured
limits.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | Complete result; eligible files published/deduplicated and any skips intentional |
| `1` | Trustworthy partial result with durable manifest/report and recoverable item failures |
| `2` | Usage, input, or policy error before processing |
| `3` | Destination/journal compatibility conflict requiring user action |
| `4` | Fatal journal/runtime/publication failure without a promised complete report |
| `130` | User interruption; committed work remains resumable |

Human diagnostics go to stderr. Successful completion data on stdout identifies
the destination, manifest, report, and outcome.

Typed progress covers fingerprint, discovery/expansion, planning, publication,
reporting, and completion. It is rate-limited during active work and emits a
five-second heartbeat during silence. The same records reach a bounded rotating
log beside the destination by default; use `--log-file PATH` to relocate it and
`--verbose` for debug diagnostics. The final record is flushed on success,
partial success, interruption, and fatal failure.

## Troubleshooting

- `unsupported_rar`: extract with a trusted RAR-capable tool separately, then
  process the resulting directory, or retain the reported original.
- `encrypted_archive` / `encrypted_pdf`: passwords are intentionally not
  requested. Decrypt a copy with an appropriate trusted tool and retry into a
  new destination.
- `corrupt_archive` / `corrupt_pdf`: preserve the original and inspect the
  manifest ancestry. Tentative descendants were not published.
- `unsafe_path` / `unsafe_entry`: inspect the original in an isolated
  environment. Links and special entries are never recreated.
- `*_limit`: use the report to identify the threshold. Increase a limit only
  after evaluating the source and use a new destination because limits are part
  of the policy fingerprint.
- exit `3`: do not delete the journal reflexively. Confirm the source and exact
  options; choose a new destination when either changed.
- interrupted run: repeat the same command. There should be no partial public
  file.
