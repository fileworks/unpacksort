# UnpackSort agent instructions

Public general tool: source installation needs no private repository access.
Optional prebuilt wheels and the supporting Homebrew tap are private.
AGENTS.md is the shared authority; CLAUDE.md only imports this file.

Preserve deterministic output across Linux, macOS and Windows.
Never use archive extract-to-directory convenience APIs or follow links.
Keep parser boundaries typed; strict mypy stays enabled globally.
Use compact generated safety fixtures, not committed archive bombs or user data.
Require installed-wheel E2E as well as source tests before release.

## Read only what the task needs

Read only the matching route. Cross-repo choices may use
`../agent-context/context/ROUTER.md`; owner/security operations may use
`../_local/AGENT-ROUTER.md` when present. Both are optional: standalone
work needs neither. Never import private context.

| Working on | First read |
|---|---|
| Installation or updates | [public install guide](docs/install.md) |
| Extraction, classification, resume or provenance | [operating manual](docs/manual.md), then the affected module and its tests |
| Packaging or release | [release procedure](docs/release.md), [release checklist](docs/release-checklist.md) |
| Architecture or lasting tradeoffs | [decisions](docs/decisions.md) |
| Security or development | [SECURITY](SECURITY.md), [CONTRIBUTING](CONTRIBUTING.md) |

## Before finishing

- Update affected docs/routes with behavior. For changed lasting decisions, update
  [docs/decisions.md](docs/decisions.md) with date, reason and owning contract;
  replace superseded guidance. Keep one TODO per concern, not session logs.
- Run relevant checks and show output/skips. Doc-only edits need routing/link
  checks, not a product release. Keep credentials/personal operations out of commits.
- Preserve concurrent work. Use the configured owner identity, no AI co-authors.
  Keep the README AI-first disclosure. Remote writes/publication need authorization.
