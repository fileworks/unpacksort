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

Follow the matching route below; do not load the entire documentation set.
If `../_local/AGENT-ROUTER.md` exists and the task involves owner-specific,
security, or cross-repository decisions, follow its relevant route. It is
optional private context: a standalone clone must work without it. Do not ask
for private notes to perform ordinary work. Never import them from CLAUDE.md.

| Working on | First read |
|---|---|
| Installation or updates | [public install guide](docs/install.md) |
| Extraction, classification, resume or provenance | [operating manual](docs/manual.md), then the affected module and its tests |
| Packaging or release | [release procedure](docs/release.md), [release checklist](docs/release-checklist.md) |
| Architecture or lasting tradeoffs | [decisions](docs/decisions.md) |
| Security or development | [SECURITY](SECURITY.md), [CONTRIBUTING](CONTRIBUTING.md) |

## Before finishing

- Update the affected user/developer instructions in the same change as behavior.
- For a lasting decision, update [docs/decisions.md](docs/decisions.md): date,
  choice, reason, and a link to the owning contract. Replace superseded choices;
  do not add session transcripts or repeat facts already owned elsewhere.
- Update this routing table when a new maintained topic needs an entry point.
- Keep credentials, personal data, security setup and private operational notes
  out of public commits. If the optional private workspace exists, put those
  decisions there; otherwise report the missing context only when it blocks work.
- Run the checks appropriate to the change; report actual output and skips.
  Documentation-only changes need link/routing checks, not a new product release.
- Use the owner's configured Git identity; no AI authors/co-author trailers.
  Keep the README's AI-first disclosure. Publishing requires task authorization.
