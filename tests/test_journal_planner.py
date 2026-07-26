from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from unpacksort.journal import Journal, StateConflictError
from unpacksort.models import (
    AncestryNode,
    DetectionMethod,
    DetectionResult,
    Group,
    Occurrence,
    SourceIdentity,
    Status,
)
from unpacksort.planner import freeze_plan
from unpacksort.policy import Policy
from unpacksort.storage import BlobStore


def _occurrence(
    identifier: str,
    *,
    name: str,
    digest: str | None,
    source: str = "source",
    group: Group = Group.DOCUMENTS,
    status: Status = Status.ELIGIBLE,
) -> Occurrence:
    return Occurrence(
        occurrence_id=identifier,
        sort_key=(identifier,),
        source_path=source,
        source_root="input",
        ancestry=(AncestryNode("archive", "outer.zip", "outer.zip"),),
        original_name=name,
        generated_name=name,
        detection=DetectionResult(
            "application/octet-stream",
            group,
            Path(name).suffix or ".bin",
            DetectionMethod.EXTENSION,
        ),
        status=status,
        digest=digest,
        size=1 if digest else None,
    )


def test_journal_schema_and_compatibility(tmp_path: Path) -> None:
    source = SourceIdentity("directory", "input", "abc")
    with Journal(tmp_path / "destination") as journal:
        journal.prepare(source, Policy(), tool_version="0.1.0")
        assert journal.phase == "discovery"
    with Journal(tmp_path / "destination") as journal:
        journal.prepare(source, Policy(), tool_version="0.1.0")
        with pytest.raises(StateConflictError, match="policy_fingerprint"):
            journal.prepare(source, Policy(pdf_only=True), tool_version="0.1.0")


def test_future_journal_schema_is_rejected(tmp_path: Path) -> None:
    state = tmp_path / "destination" / ".unpacksort"
    state.mkdir(parents=True)
    database = state / "journal.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA user_version = 99")
    connection.close()
    with pytest.raises(StateConflictError, match="schema 99"):
        Journal(tmp_path / "destination")


def test_blob_store_deduplicates_and_cleans_incomplete_files(tmp_path: Path) -> None:
    with Journal(tmp_path / "destination") as journal:
        store = BlobStore(journal)
        incomplete = store.temp / "blob-abandoned.tmp"
        incomplete.write_bytes(b"partial")
        store.cleanup_incomplete()
        assert not incomplete.exists()
        first = store.ingest_bytes(b"same", max_bytes=10)
        second = store.ingest_bytes(b"same", max_bytes=10)
        assert first == second
        assert first.path.read_bytes() == b"same"


def test_flatten_collisions_and_duplicates_are_exact() -> None:
    records = [
        _occurrence("1", name="invoice.pdf", digest="a").to_record(),
        _occurrence("2", name="INVOICE.pdf", digest="a").to_record(),
        _occurrence("3", name="invoice.pdf", digest="b").to_record(),
        _occurrence("4", name="invoice.pdf", digest="c").to_record(),
    ]
    plan = freeze_plan(records, Policy(layout="flatten"))
    assert [record["canonical_path"] for record in plan] == [
        "documents/invoice.pdf",
        "documents/invoice.pdf",
        "documents/invoice_1.pdf",
        "documents/invoice_2.pdf",
    ]
    assert plan[2]["metadata"]["path_adjustments"] == "name_collision_suffix"
    assert plan[1]["status"] == Status.DUPLICATE.value


def test_hierarchy_is_portable_and_pdf_only_does_not_publish_unprocessed() -> None:
    record = _occurrence(
        "1",
        name="CON.txt",
        digest="a",
        source=r"folder\file",
    ).to_record()
    hierarchy = freeze_plan([record], Policy())
    assert hierarchy[0]["canonical_path"] == "documents/input/outer.zip/_CON.txt"
    failed = _occurrence(
        "2",
        name="archive.rar",
        digest="b",
        group=Group.ARCHIVES_UNPROCESSED,
        status=Status.UNPROCESSED,
    ).to_record()
    pdf_plan = freeze_plan([failed], Policy(pdf_only=True))
    assert pdf_plan[0]["canonical_path"] is None


def test_hierarchy_directory_case_collisions_are_portable() -> None:
    upper = _occurrence("1", name="file.txt", digest="a").to_record()
    lower = _occurrence("2", name="other.txt", digest="b").to_record()
    upper["ancestry"] = [{"kind": "source_dir", "identifier": "Folder", "original_name": "Folder"}]
    lower["ancestry"] = [{"kind": "source_dir", "identifier": "folder", "original_name": "folder"}]
    plan = freeze_plan([upper, lower], Policy())
    paths = [record["canonical_path"] for record in plan]
    assert paths == [
        "documents/input/Folder/file.txt",
        "documents/input/folder_1/other.txt",
    ]


def test_frozen_plan_is_immutable_and_accounting_resumes(tmp_path: Path) -> None:
    with Journal(tmp_path / "destination") as journal:
        journal.prepare(SourceIdentity("directory", "input", "abc"), Policy(), tool_version="0.1.0")
        record = _occurrence("one", name="a.txt", digest="a")
        journal.record_occurrence(record)
        plan = freeze_plan(journal.occurrence_records(), Policy())
        journal.set_accounting(members=3, expanded_bytes=99)
        journal.freeze(plan)
        journal.freeze(plan)
        assert journal.plan_records() == plan
        assert journal.accounting() == (3, 99)
        changed = [dict(plan[0], canonical_path="different")]
        with pytest.raises(StateConflictError, match="different frozen plan"):
            journal.freeze(changed)


def test_occurrence_records_are_valid_json(tmp_path: Path) -> None:
    occurrence = _occurrence("one", name="a.txt", digest="abc")
    with Journal(tmp_path / "destination") as journal:
        journal.record_occurrence(occurrence)
        record = journal.occurrence_records()[0]
    assert json.dumps(record, sort_keys=True)
    assert record["detection"]["group"] == "documents"


@given(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=12))
def test_flatten_dedup_and_suffixes_are_ordered_by_provenance(digests: list[str]) -> None:
    records = [
        _occurrence(f"{index:04d}", name="same.txt", digest=digest).to_record()
        for index, digest in enumerate(digests)
    ]
    plan = freeze_plan(records, Policy(layout="flatten"))
    canonical_by_digest: dict[str, str] = {}
    for digest, record in zip(digests, plan, strict=True):
        path = str(record["canonical_path"])
        if digest in canonical_by_digest:
            assert path == canonical_by_digest[digest]
            assert record["status"] == "duplicate"
        else:
            canonical_by_digest[digest] = path
    assert len(set(canonical_by_digest.values())) == len(set(digests))
