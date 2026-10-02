# Release procedure

The v1.0.0 baseline has public source and private prebuilt downloads.
PyPI and WinGet publication are retired. Source tags are immutable after the restart.

1. Run locked Ruff, formatting, strict mypy, and pytest.
2. Require successful Quality checks for the exact source commit, including
   all OS/Python installed-wheel E2E jobs and real exFAT refusal evidence.
3. Build once with `uv build --build-constraints build-constraints.txt`.
4. Verify wheel/sdist identity and run `scripts/installed_e2e.py` against a
   clean environment containing only the installed wheel and its dependencies.
5. Upload the reviewed wheel/sdist and SHA256SUMS to the private release hub.
6. Generate and review the private tap formula from that wheel and `uv.lock`.
   Require its macOS installation and inventory tests before recommending it.
7. Verify downloaded bytes against SHA256SUMS and update the private ledger.

The manual Release workflow verifies builds and CLI startup; it cannot publish.
See [README](../README.md#install) for authenticated installation and updates.
