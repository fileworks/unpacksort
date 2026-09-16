"""Prove the real-exFAT safety tests reject replacement publication."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXPECTED_FAILURES = 2

def run() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "--no-cov",
            "--tb=short",
            "tests/test_publish_safety.py::TestNoHardLinksOnRealFilesystem",
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
        path.write_bytes(original.replace(old, b"os.replace(temporary, destination)"))
        mutant = run()
        sys.stdout.write(mutant.stdout + mutant.stderr)
        detected = (
            mutant.returncode == 1
            and "2 failed, 1 passed" in mutant.stdout
            and mutant.stdout.count("DID NOT RAISE") == EXPECTED_FAILURES
            and "skipped" not in mutant.stdout
        )
        return 0 if detected else 1
    finally:
        path.write_bytes(original)


if __name__ == "__main__":
    raise SystemExit(main())
