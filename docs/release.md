# Release procedure

Source is public; prebuilt downloads are private. PyPI and WinGet publication
are retired. Published source tags and distribution assets are immutable: never
move a tag or replace bytes; fix forward with a new version.

Publication names: public source `fileworks/unpacksort` (tag `vX.Y.Z`), private
wheel/sdist in `fileworks/private-releases` (tag `unpacksort-vX.Y.Z`, not marked
latest), private Homebrew formula `fileworks/tap/unpacksort`. Retired PyPI
filenames cannot be reused after deletion; the private release hub avoids that.

Commands are POSIX shell (Git Bash works on Windows) run from a checkout of this
repository with `gh` authenticated. Each step's output is evidence for the next.
Afterwards tick the [release checklist](release-checklist.md).

## What decides the version

Versioning is manual SemVer, chosen from the commits since the last tag:

```sh
git fetch --tags origin
PREV=$(git describe --tags --abbrev=0 origin/main)
git log "$PREV"..origin/main --oneline   # what will be released
```

Any breaking change (`!` or `BREAKING CHANGE`) means major; otherwise any
`feat` means minor; otherwise `fix`/security/`perf` means patch. Only
`docs`/`ci`/`chore`/`test` commits need no release.

## 1. Release pull request

```sh
VERSION=X.Y.Z
git switch -c "release/v$VERSION" origin/main
```

Set `version` in `pyproject.toml` and the `__version__` fallback in
`src/unpacksort/__init__.py`, add a dated `## [X.Y.Z] (YYYY-MM-DD)` section to
`CHANGELOG.md`, then refresh the lock's own entry and run the local gates:

```sh
uv lock
git diff uv.lock   # only the unpacksort entry's version may change
uv sync --locked --all-groups
uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest
git commit -am "chore(release): v$VERSION"
git push -u origin "release/v$VERSION"
gh pr create --title "chore(release): v$VERSION" --body "Release v$VERSION."
gh pr checks --watch --required
gh pr merge --squash
```

## 2. Exact-commit Quality

The Quality run on `main` for the squash commit must pass every job (all
OS/Python installed-wheel E2E jobs, dependency audit, docs links and real exFAT
refusal evidence); `scripts/release_quality.py` refuses anything else.

```sh
git fetch origin
SHA=$(git rev-parse origin/main)
git show -s --format=%s "$SHA"   # chore(release): vX.Y.Z (#N)
RUN=$(gh run list --workflow quality.yml --commit "$SHA" --json databaseId -q '.[0].databaseId')
gh run watch "$RUN" --exit-status
gh api --paginate --slurp "repos/fileworks/unpacksort/actions/workflows/quality.yml/runs?head_sha=$SHA&branch=main&per_page=100" > "${TMPDIR:-/tmp}/quality-runs.json"
python -B scripts/release_quality.py "${TMPDIR:-/tmp}/quality-runs.json" "$SHA" fileworks/unpacksort
```

## 3. Tag and Release verifier

```sh
git tag -a "v$VERSION" "$SHA" -m "UnpackSort v$VERSION"
git push origin "v$VERSION"
gh workflow run release.yml --ref "v$VERSION"
RUN=$(gh run list --workflow release.yml --branch "v$VERSION" --json databaseId -q '.[0].databaseId')
gh run watch "$RUN" --exit-status
```

The dispatch-only Release workflow re-runs quality, builds with the constraints
and smoke-tests the installed command. It has read-only permissions and cannot
publish.

## 4. Build once and verify the candidate

Build from a clean clone of the tag, and stage only the two distributions, so
the upload folder holds exactly what `verify` and `SHA256SUMS` cover:

```sh
WORK=$(mktemp -d)
git clone --quiet --branch "v$VERSION" https://github.com/fileworks/unpacksort "$WORK/src"
cd "$WORK/src"
uv build --build-constraints build-constraints.txt
mkdir "$WORK/assets" && cp dist/*.whl dist/*.tar.gz "$WORK/assets/"
python -B scripts/release_assets.py verify --directory "$WORK/assets" --version "$VERSION"
uv venv "$WORK/venv"
uv pip install --python "$WORK/venv" "$WORK/assets/unpacksort-$VERSION-py3-none-any.whl"
CMD="$WORK/venv/bin/unpacksort"   # Windows: "$WORK/venv/Scripts/unpacksort.exe"
"$CMD" --version                  # unpacksort X.Y.Z
python -B scripts/installed_e2e.py --command "$CMD"
python -B scripts/release_assets.py checksums --directory "$WORK/assets" --output "$WORK/assets/SHA256SUMS"
```

## 5. Publish to the private release hub

Write `$WORK/notes.md`: what changed, that other tools are unchanged, the source
tag/commit, the Quality and Release run IDs, the download/install command and
the Homebrew status. Publish, then download the files back and check them:

```sh
cd "$WORK/assets"
gh release create "unpacksort-v$VERSION" -R fileworks/private-releases --latest=false \
  --title "UnpackSort v$VERSION" --notes-file "$WORK/notes.md" \
  "unpacksort-$VERSION-py3-none-any.whl" "unpacksort-$VERSION.tar.gz" SHA256SUMS
mkdir "$WORK/download" && cd "$WORK/download"
gh release download "unpacksort-v$VERSION" -R fileworks/private-releases
sha256sum -c SHA256SUMS
```

## 6. Record the observation

In the private `maintenance` repository, replace the `unpacksort`
`github_release` entry in `release-observations.json` (version, `verified`,
tag/commit/run IDs/checks in `detail`, `observed_at`); the ledger's
`generated_at` follows the newest entry. From the workspace root that contains
`maintenance/`:

```sh
python -B -m maintenance.refresh_release_ledger
python -B -m maintenance.refresh_release_ledger --check
```

Commit both files in a pull request, run its tests, and merge.

## 7. Homebrew formula

In the private `homebrew-tap`, generate the formula from the verified wheel and
the tagged `uv.lock` (see its README Development section for the full checks):

```sh
python .github/scripts/private_formula.py unpacksort "$WORK/download/unpacksort-$VERSION-py3-none-any.whl" "$WORK/src/uv.lock"
python -m unittest discover -s tests -v
```

Commit `Formula/`, `Wheels/` and `Locks/` together in a pull request. Merge and
record a `homebrew` observation only after its macOS install tests pass on both
architectures (private CI). Until then the tap keeps the previous version; say
so in the hub release notes.

See the [install guide](install.md) for public source and optional authenticated
downloads. Keep README commands independent of the current release number.
