"""Deterministic atomic manifest and human report rendering."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from unpacksort.models import ExitOutcome, Status
from unpacksort.policy import Accounting, Policy


def outcome_for(plan: list[dict[str, Any]]) -> ExitOutcome:
    """Derive success versus durable partial success."""

    if any(record["status"] == Status.UNPROCESSED.value for record in plan):
        return ExitOutcome.PARTIAL
    return ExitOutcome.SUCCESS


def write_outputs(
    destination: Path,
    *,
    source: dict[str, str],
    policy: Policy,
    accounting: Accounting,
    containers: list[dict[str, Any]],
    plan: list[dict[str, Any]],
) -> tuple[Path, Path, ExitOutcome]:
    """Atomically publish deterministic JSONL and text result contracts."""

    outcome = outcome_for(plan)
    run_record = {
        "record_type": "run",
        "schema_version": 1,
        "tool": "unpacksort",
        "source": source,
        "policy": {
            "detector_version": policy.detector_version,
            "layout": policy.layout,
            "max_container_bytes": policy.max_container_bytes,
            "max_depth": policy.max_depth,
            "max_expansion_ratio": policy.max_expansion_ratio,
            "max_member_bytes": policy.max_member_bytes,
            "max_members_per_container": policy.max_members_per_container,
            "max_members_run": policy.max_members_run,
            "max_run_bytes": policy.max_run_bytes,
            "pdf_only": policy.pdf_only,
            "policy_version": policy.policy_version,
        },
    }
    manifest_lines = [json.dumps(run_record, sort_keys=True, separators=(",", ":"))]
    for container in sorted(
        containers,
        key=lambda record: (tuple(record["sort_key"]), record["container_id"]),
    ):
        manifest_lines.append(
            json.dumps(
                {"record_type": "container", **container},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    for occurrence in plan:
        manifest_lines.append(
            json.dumps(
                {"record_type": "occurrence", **occurrence},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    manifest = destination / "manifest.jsonl"
    _atomic_text(manifest, "\n".join(manifest_lines) + "\n")

    statuses = Counter(str(record["status"]) for record in plan)
    groups = Counter(str(record["detection"]["group"]) for record in plan)
    reasons = Counter(str(record["reason"]) for record in plan if record.get("reason"))
    report_lines = [
        "unpacksort report",
        f"outcome: {'partial' if outcome == ExitOutcome.PARTIAL else 'complete'}",
        f"layout: {policy.layout}",
        f"pdf_only: {str(policy.pdf_only).lower()}",
        f"logical_members: {accounting.members}",
        f"logical_expanded_bytes: {accounting.expanded_bytes}",
        "",
        "status counts:",
        *[f"  {key}: {statuses[key]}" for key in sorted(statuses)],
        "",
        "group counts:",
        *[f"  {key}: {groups[key]}" for key in sorted(groups)],
        "",
        "reason counts:",
        *([f"  {key}: {reasons[key]}" for key in sorted(reasons)] or ["  none: 0"]),
        "",
        "limits:",
        f"  depth: {policy.max_depth}",
        f"  members_per_container: {policy.max_members_per_container}",
        f"  members_run: {policy.max_members_run}",
        f"  member_bytes: {policy.max_member_bytes}",
        f"  container_bytes: {policy.max_container_bytes}",
        f"  run_bytes: {policy.max_run_bytes}",
        f"  expansion_ratio: {policy.max_expansion_ratio:g}",
    ]
    report = destination / "report.txt"
    _atomic_text(report, "\n".join(report_lines) + "\n")
    return manifest, report, outcome


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.unpacksort.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
