# Release and distribution runbook

## Invariants

- Protected `main` accepts reviewed pull requests and required quality checks.
- Conventional Commits determine Semantic Versioning and update the changelog.
- A version, tag, PyPI files, GitHub files, checksum file, portable ZIP, formula,
  and WinGet manifest must agree exactly.
- Published versions and assets are immutable. Repair defects with a new
  version; never replace an existing release file.
- Initial executable artifacts are unsigned. Release notes must repeat the trust
  warning and checksum limitation.

The dated name screen is in [name-availability.md](name-availability.md).
Recheck GitHub, PyPI, Homebrew, WinGet, and the broader exact term immediately
before the first publication. Stop on a collision.

## GitHub repository protection

Protect `main` with:

- pull requests and conversation resolution required;
- force pushes and deletion disabled;
- linear history required;
- all six `quality (OS, Python)` and all six
  `installed wheel (OS, Python)` contexts required;
- release environments restricted to tags created from protected `main`.

The semantic-release version commit needs the owner-controlled
`SEMANTIC_RELEASE_TOKEN` as a protected Actions secret and a narrowly reviewed
administrator bypass for this workflow. Its committer is Niklas Büchel; no bot,
AI, or co-author trailer is added. The default `GITHUB_TOKEN` remains
least-privilege everywhere else.

`release.yml` first runs the complete gate, then Python Semantic Release stamps
the version, updates `CHANGELOG.md`, creates the release commit, and pushes the
tag without creating a GitHub Release yet. Source/wheel and Windows portable
jobs test their exact candidates. Only after both succeed does the workflow
assemble one immutable asset set, publish the GitHub Release through the
`github-release` environment, publish the same Python distributions through
the `pypi` environment, and dispatch the formula through `homebrew`. GitHub
therefore shows Releases for user-facing versions and Deployments for each
protected publication boundary.

## PyPI OIDC

The active trusted publisher is:

| Field | Value |
| --- | --- |
| PyPI project | `unpacksort` |
| GitHub owner | `fileworks` |
| Repository | `unpacksort` |
| Workflow | `release.yml` |
| Environment | `pypi` |

Version 1.1.0 reserved the project through this publisher. The `pypi` GitHub
environment accepts deployments from protected `main`; it has no obsolete
self-approval gate. The publish job has job-local `id-token: write` and uses the
PyPA publisher action; do not configure a long-lived PyPI token.

For every release:

1. Confirm the trusted publisher still names this repository, workflow, and
   environment.
2. Confirm the candidate wheel installs in the source-distribution E2E job.
3. Confirm the GitHub tag/version does not already exist.

After publication:

```console
pipx install unpacksort==VERSION
unpacksort --help
```

Run the representative E2E fixture and compare the installed version to the tag.

## GitHub and Windows portable assets

Every GitHub Release contains exactly:

- `unpacksort-VERSION.tar.gz`
- `unpacksort-VERSION-py3-none-any.whl`
- `unpacksort-VERSION-windows-x64.zip`
- `SHA256SUMS`

The ZIP has one nested executable:

```text
unpacksort-VERSION-windows-x64/unpacksort.exe
```

PyInstaller and development dependencies are exact in `uv.lock`. The Windows
x64 runner builds with Python 3.12, fixed hash seed and source epoch, then
unpacks and runs the real executable through the same deterministic E2E fixture.
The executable is unsigned and can trigger SmartScreen.

Verify a download:

```console
sha256sum --check SHA256SUMS
```

On PowerShell:

```powershell
(Get-FileHash .\unpacksort-VERSION-windows-x64.zip -Algorithm SHA256).Hash
```

Compare against `SHA256SUMS` obtained through a separate trusted path when
possible. Matching a checksum from the same compromised location is not proof
of authenticity.

## WinGet bootstrap and updates

The reviewed templates in `packaging/winget/` define:

- package identifier `fileworks.unpacksort`;
- `zip` installer with nested type `portable`;
- x64 nested executable path matching the release ZIP;
- command alias `unpacksort`;
- schema 1.12.

Render the first immutable version:

```console
python scripts/render_winget.py \
  --version VERSION \
  --url https://github.com/fileworks/unpacksort/releases/download/vVERSION/unpacksort-VERSION-windows-x64.zip \
  --sha256 HEX_DIGEST \
  --output build/winget
```

Validate the result with current `wingetcreate` and submit the first package
through a reviewed manual WinGet PR. Version 1.1.0 was submitted in
[`microsoft/winget-pkgs#410897`](https://github.com/microsoft/winget-pkgs/pull/410897).
The identity is not reserved until Microsoft accepts that PR.

After bootstrap acceptance, create the protected `winget` environment, add the
dedicated `WINGET_TOKEN`, and set repository variable
`WINGET_SUBMISSION_ENABLED=true`. The release workflow then uses official
WinGetCreate to generate/update and submit. A rejected or delayed WinGet PR
leaves valid GitHub/PyPI artifacts intact; the failed job is the observable,
retryable follow-up. Never replace those assets to retry the catalog.

## Homebrew bootstrap and updates

The reviewed bootstrap landed in
[`fileworks/homebrew-tap#21`](https://github.com/fileworks/homebrew-tap/pull/21).
It pins the published sdist and the complete cross-platform wheel inventory from
the immutable release lock. The tap CI audits Linux/macOS and performs a real
source install plus inventory test on macOS.

The protected `homebrew` environment and
`HOMEBREW_DISPATCH_ENABLED=true` are active. Each release sends the exact
version, source repository/run, and immutable `uv.lock` URL/SHA-256 to the tap's
durable serialized queue. Existing formulas update monotonically; an absent
formula fails closed as `bootstrap_required`.

To verify or recover the channel manually:

```console
brew audit --strict fileworks/tap/unpacksort
brew style Formula/unpacksort.rb
brew install --build-from-source fileworks/tap/unpacksort
brew test fileworks/tap/unpacksort
```

## Rollback and channel failures

Before first publication, remove or recreate unpublished environment/publisher
configuration. After publication:

- fix code or metadata in a new semantic version;
- leave valid GitHub and PyPI artifacts immutable;
- retry an external Homebrew/WinGet PR independently;
- disable or supersede a broken catalog version using that catalog's reviewed
  process;
- record every external follow-up in the release checklist and GitHub issue.

Do not delete user outputs or journals as part of a release rollback.
