# Contributing

Open an issue before changing the public manifest, naming, safety-limit, or
resume contracts. Use a focused branch and a Conventional Commit subject.

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
