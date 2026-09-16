"""Prove the real-exFAT safety tests reject replacement publication."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXPECTED_FAILURES = 2


def run(*, premise_mutant: bool = False) -> subprocess.CompletedProcess[str]:
    selector = "tests/test_publish_safety.py::TestNoHardLinksOnRealFilesystem"
    command = [sys.executable, "-m", "pytest"]
    if premise_mutant:
        selector += "::test_hard_links_really_are_unsupported_there"
        command = [
            sys.executable,
            "-c",
            "import os, sys, pytest; os.link = lambda *args, **kwargs: None; "
            "raise SystemExit(pytest.main(sys.argv[1:]))",
        ]
    return subprocess.run(
        [
            *command,
            "-q",
            "-rs",
            "--no-cov",
            "--tb=short",
            selector,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def main() -> int:
    baseline = run()
    sys.stdout.write(baseline.stdout + baseline.stderr)
    if (
        baseline.returncode != 0
        or "3 passed" not in baseline.stdout
        or "skipped" in baseline.stdout
    ):
        return 1
    path = Path("src/unpacksort/storage.py")
    original = path.read_bytes()
    old = b"os.link(temporary, destination)"
    if original.count(old) != 1:
        return 1
    try:
        path.write_bytes(original.replace(old, b"os.replace(temporary, destination); return"))
        mutant = run()
        sys.stdout.write(mutant.stdout + mutant.stderr)
        detected = (
            mutant.returncode == 1
            and "2 failed, 1 passed" in mutant.stdout
            and mutant.stdout.count("DID NOT RAISE") == EXPECTED_FAILURES
            and "skipped" not in mutant.stdout
        )
    finally:
        path.write_bytes(original)
    # The third case guards a kernel capability premise, not our publication
    # function. It must reject a link primitive that succeeds unexpectedly.
    premise = run(premise_mutant=True)
    sys.stdout.write(premise.stdout + premise.stderr)
    premise_detected = (
        premise.returncode == 1
        and "1 failed" in premise.stdout
        and "DID NOT RAISE" in premise.stdout
        and "skipped" not in premise.stdout
    )
    return 0 if detected and premise_detected else 1


if __name__ == "__main__":
    raise SystemExit(main())
