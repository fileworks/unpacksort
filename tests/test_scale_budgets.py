"""Opt-in production-path scale tiers for the bounded runtime contract."""

from __future__ import annotations

import json
import os
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from unpacksort.engine import _SourceInventory
from unpacksort.journal import Journal
from unpacksort.models import ExitOutcome
from unpacksort.planner import iter_freeze_plan
from unpacksort.policy import Accounting, Policy
from unpacksort.reporting import write_outputs

_TIER_TEXT = os.environ.get("UNPACKSORT_SCALE_TIER")
_SUPPORTED_TIERS = {20_000, 100_000, 500_000}


def _records(total: int) -> Iterator[dict[str, Any]]:
    for index in range(total):
        yield {
            "occurrence_id": f"occurrence-{index:08d}",
            "sort_key": [f"{index:08d}"],
            "source_path": f"source/{index:08d}.txt",
            "source_root": "source",
            "ancestry": [],
            "original_name": f"{index:08d}.txt",
            "generated_name": f"{index:08d}.txt",
            "detection": {"group": "documents"},
            "status": "eligible",
            "digest": f"{index:064x}",
            "size": 0,
            "reason": None,
            "diagnostics": [],
            "metadata": {},
        }


@pytest.mark.scale
@pytest.mark.skipif(_TIER_TEXT is None, reason="set UNPACKSORT_SCALE_TIER")
def test_generated_inventory_plan_and_report_stay_disk_backed(tmp_path: Path) -> None:
    """Exercise the real inventory, planner, journal cursors, and report writer."""

    tier = int(_TIER_TEXT or "0")
    assert tier in _SUPPORTED_TIERS
    source = tmp_path / "source"
    source.mkdir()
    for index in range(tier):
        (source / f"{index:08d}.txt").touch()

    fingerprinted = 0

    def counted() -> None:
        nonlocal fingerprinted
        fingerprinted += 1

    tracemalloc.start()
    inventory = _SourceInventory(source, on_file=counted)
    assert sum(1 for _entry in inventory.entries()) == tier
    inventory.close()

    destination = tmp_path / "destination"
    policy = Policy()
    accounting = Accounting(policy)
    with Journal(destination) as journal:
        journal.freeze(iter_freeze_plan(_records(tier), policy))
        assert journal.plan_count() == tier
        manifest, report, outcome = write_outputs(
            destination,
            source={
                "fingerprint": inventory.identity.fingerprint,
                "kind": inventory.identity.kind,
                "root_name": inventory.identity.root_name,
            },
            policy=policy,
            accounting=accounting,
            containers=journal.iter_container_records(),
            plan=journal.iter_plan_records(),
        )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert fingerprinted == tier
    assert outcome is ExitOutcome.SUCCESS
    assert report.is_file()
    with manifest.open(encoding="utf-8") as stream:
        assert sum(1 for _line in stream) == tier + 1
    with manifest.open(encoding="utf-8") as stream:
        first = json.loads(stream.readline())
    assert first["record_type"] == "run"
    assert peak < 192 * 1024 * 1024
