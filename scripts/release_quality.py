"""Fail closed unless the latest Quality run tested this exact protected SHA."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def require_quality(pages: list[dict[str, Any]], sha: str, repository: str) -> int:
    """Return the accepted run ID; pending reruns supersede prior successes."""
    candidates = [
        run
        for page in pages
        for run in page["workflow_runs"]
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") in {"push", "workflow_dispatch", "schedule"}
        and run.get("head_repository", {}).get("full_name") == repository
    ]
    if not candidates:
        raise ValueError("No Quality evidence for the exact protected source SHA")
    latest = max(candidates, key=lambda run: (int(run["id"]), int(run.get("run_attempt", 1))))
    if latest.get("status") != "completed" or latest.get("conclusion") != "success":
        raise ValueError("The latest Quality run is not completed successfully")
    return int(latest["id"])


if __name__ == "__main__":
    try:
        accepted = require_quality(
            json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")), sys.argv[2], sys.argv[3]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"Quality gate refused release: {exc}") from exc
    sys.stdout.write(f"Exact source SHA passed Quality run {accepted}\n")
