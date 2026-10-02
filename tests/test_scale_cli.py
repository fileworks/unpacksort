from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCALE_PROBE = (
    "tests/test_scale_budgets.py::"
    "test_focused_scale_invocation_disables_aggregate_coverage_threshold"
)


def test_scale_target_is_parsed_after_plugin_options() -> None:
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["UNPACKSORT_SCALE_TIER"] = "20000"
    # The executable is this test environment's interpreter and every argument
    # is a repository-owned literal; no input crosses a trust boundary.
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            _SCALE_PROBE,
        ],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "1 passed" in output
