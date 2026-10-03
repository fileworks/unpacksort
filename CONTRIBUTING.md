# Contributing

Open an issue before changing the public manifest, naming, safety-limit, or
resume contracts. Use a focused branch and a Conventional Commit subject.

Start at [AGENTS.md](AGENTS.md); maintain affected instructions and decisions in
the same change. Public clones do not need private workspace context. Documentation
changes do not publish a package or move an existing release tag.

Install with `uv sync --locked --all-groups`, then run:

```console
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

Tests must use compact generated fixtures. Do not commit credentials, personal
mail, malicious samples, archive bombs, or licensed documents.

Linux CI uses Ubuntu 24.04 while retaining existing logical matrix/check names.
The full OS/Python matrix and installed-wheel checks remain required. Dependency
caches do not bypass locked installs, and newer commits cancel obsolete CI runs.
