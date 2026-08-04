from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import cast

import py7zr
import pytest
from click import unstyle
from typer.testing import CliRunner

from unpacksort.cli import app
from unpacksort.engine import Processor, fingerprint_source
from unpacksort.journal import Journal, StateConflictError
from unpacksort.models import ExitOutcome
from unpacksort.policy import Policy
from unpacksort.storage import BlobStore

runner = CliRunner()


def _manifest(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_directory_e2e_hierarchy_dedup_and_resume(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_bytes(b"duplicate")
    nested = source / "nested"
    nested.mkdir()
    (nested / "b.txt").write_bytes(b"duplicate")
    with zipfile.ZipFile(source / "archive.zip", "w") as archive:
        archive.writestr("inside/data.json", '{"value": 1}')
    destination = tmp_path / "output"
    manifest, report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.SUCCESS
    assert (destination / "documents" / "input" / "a.txt").read_bytes() == b"duplicate"
    assert not (destination / "documents" / "input" / "nested" / "b.txt").exists()
    assert (destination / "data" / "input" / "archive.zip" / "inside" / "data.json").exists()
    records = [record for record in _manifest(manifest) if record["record_type"] == "occurrence"]
    assert {record["status"] for record in records} >= {"published", "duplicate"}
    before_manifest = manifest.read_bytes()
    before_report = report.read_bytes()
    second_manifest, second_report, second_outcome = Processor(source, destination, Policy()).run()
    assert second_outcome is ExitOutcome.SUCCESS
    assert second_manifest.read_bytes() == before_manifest
    assert second_report.read_bytes() == before_report


def test_flatten_collision_numbers_distinct_content(tmp_path: Path) -> None:
    source = tmp_path / "input"
    (source / "one").mkdir(parents=True)
    (source / "two").mkdir()
    (source / "one" / "same.txt").write_text("one", encoding="utf-8")
    (source / "two" / "same.txt").write_text("two", encoding="utf-8")
    destination = tmp_path / "output"
    Processor(source, destination, Policy(layout="flatten")).run()
    assert (destination / "documents" / "same.txt").read_text(encoding="utf-8") == "one"
    assert (destination / "documents" / "same_1.txt").read_text(encoding="utf-8") == "two"


def test_pdf_only_publishes_only_valid_pdf(tmp_path: Path, valid_pdf: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "note.txt").write_text("skip", encoding="utf-8")
    (source / "document.bin").write_bytes(valid_pdf.read_bytes())
    destination = tmp_path / "output"
    manifest, _report, outcome = Processor(source, destination, Policy(pdf_only=True)).run()
    assert outcome is ExitOutcome.SUCCESS
    assert list((destination / "pdf").rglob("*.pdf"))
    assert not (destination / "documents").exists()
    occurrences = [row for row in _manifest(manifest) if row["record_type"] == "occurrence"]
    assert any(
        row["status"] == "skipped" and row["reason"] == "policy_non_pdf" for row in occurrences
    )


def test_unsupported_and_corrupt_containers_yield_partial(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    payload = b"Rar!\x1a\x07\x01\x00payload"
    (source / "first.rar").write_bytes(payload)
    (source / "second.rar").write_bytes(payload)
    destination = tmp_path / "output"
    manifest, report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.PARTIAL
    retained = list((destination / "archives" / "unprocessed").rglob("*.rar"))
    assert len(retained) == 1
    assert "unsupported_rar" in report.read_text(encoding="utf-8")
    occurrences = [row for row in _manifest(manifest) if row["record_type"] == "occurrence"]
    assert len(occurrences) == 2
    assert {row["status"] for row in occurrences} == {"unprocessed", "duplicate"}


def test_policy_and_source_changes_conflict_with_resume(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    file = source / "a.txt"
    file.write_text("first", encoding="utf-8")
    destination = tmp_path / "output"
    Processor(source, destination, Policy()).run()
    with pytest.raises(StateConflictError, match="policy_fingerprint"):
        Processor(source, destination, Policy(pdf_only=True)).run()
    file.write_text("changed", encoding="utf-8")
    with pytest.raises(StateConflictError, match="source_fingerprint"):
        Processor(source, destination, Policy()).run()


def test_interrupted_publication_resumes_same_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "b.txt").write_text("b", encoding="utf-8")
    destination = tmp_path / "output"
    original_publish = BlobStore.publish
    calls = 0

    def interrupt_once(self: BlobStore, blob: object, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        original_publish(self, blob, target)  # type: ignore[arg-type]

    monkeypatch.setattr(BlobStore, "publish", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        Processor(source, destination, Policy()).run()
    assert not list(destination.rglob("*.unpacksort.tmp"))
    monkeypatch.setattr(BlobStore, "publish", original_publish)
    _manifest_path, _report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.SUCCESS
    assert len(list((destination / "documents").rglob("*.txt"))) == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation needs runner policy")
def test_source_symlink_is_not_followed(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("secret", encoding="utf-8")
    (source / "link.txt").symlink_to(target)
    destination = tmp_path / "output"
    manifest, _report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.PARTIAL
    text = manifest.read_text(encoding="utf-8")
    assert "unsafe_entry" in text
    assert "secret" not in text


def test_fingerprint_is_content_based_and_order_independent(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "b").write_bytes(b"b")
    (source / "a").write_bytes(b"a")
    first = fingerprint_source(source)
    second = fingerprint_source(source)
    assert first == second
    expected_length = len(hashlib.sha256().hexdigest())
    assert len(first.fingerprint) == expected_length


def test_cli_help_version_and_usage_errors(tmp_path: Path) -> None:
    help_result = runner.invoke(app, ["--help"], terminal_width=160)
    assert help_result.exit_code == 0
    help_text = unstyle(help_result.stdout)
    for option in (
        "--max-depth",
        "--max-members-per-container",
        "--max-members-run",
        "--max-member-bytes",
        "--max-container-bytes",
        "--max-run-bytes",
        "--max-expansion-ratio",
        "--log-file",
        "--verbose",
    ):
        assert option in help_text
    assert "--dry-run" not in help_text
    # The three CLIs are meant to be usable from one script; `-h` is documented
    # nowhere but works in the other two, so a reader carries the habit over.
    short_result = runner.invoke(app, ["-h"], terminal_width=160)
    assert short_result.exit_code == 0
    assert unstyle(short_result.stdout) == help_text
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "--dry-run" not in readme
    assert "all seven safety limits" in readme
    version_result = runner.invoke(app, ["--version"])
    assert version_result.exit_code == 0
    assert version_result.stdout.startswith("unpacksort ")
    missing = runner.invoke(app, [str(tmp_path / "missing"), str(tmp_path / "out")])
    assert missing.exit_code == ExitOutcome.USAGE
    assert "input error" in missing.stderr


def test_cli_reports_partial_on_stderr(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "archive.rar").write_bytes(b"Rar!\x1a\x07\x00payload")
    destination = tmp_path / "output"
    result = runner.invoke(app, [str(source), str(destination)])
    assert result.exit_code == ExitOutcome.PARTIAL
    assert "partial success" in result.stderr
    assert "manifest=" in result.stdout


def test_regular_non_mbox_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "ordinary.txt"
    source.write_text("ordinary", encoding="utf-8")
    result = runner.invoke(app, [str(source), str(tmp_path / "output")])
    assert result.exit_code == ExitOutcome.USAGE
    assert "parser-confirmed mbox" in result.stderr


def test_mbox_input_and_standalone_message_in_directory(
    tmp_path: Path,
    mbox_path: Path,
) -> None:
    mbox_destination = tmp_path / "mbox-output"
    _manifest_path, _report, outcome = Processor(mbox_path, mbox_destination, Policy()).run()
    assert outcome is ExitOutcome.SUCCESS
    assert list((mbox_destination / "other").rglob("a.bin"))

    source = tmp_path / "messages"
    source.mkdir()
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["To"] = "b@example.test"
    message["Subject"] = "fixture"
    message.set_content("body")
    message.add_attachment(b"document", maintype="text", subtype="plain", filename="note.txt")
    (source / "message.data").write_bytes(message.as_bytes())
    directory_destination = tmp_path / "message-output"
    Processor(source, directory_destination, Policy()).run()
    assert list((directory_destination / "documents").rglob("note.txt"))


def test_corrupt_and_depth_blocked_archives_are_partial(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "broken.zip").write_bytes(b"PK\x03\x04truncated")
    inner_bytes = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner_bytes, "w") as inner:
        inner.writestr("deep.txt", "payload")
    with zipfile.ZipFile(source / "outer.zip", "w") as outer:
        outer.writestr("inner.zip", inner_bytes.read_bytes())
    destination = tmp_path / "output"
    _manifest_path, report, outcome = Processor(
        source,
        destination,
        Policy(max_depth=1),
    ).run()
    assert outcome is ExitOutcome.PARTIAL
    text = report.read_text(encoding="utf-8")
    assert "corrupt_archive" in text
    assert "depth_limit" in text


def test_parent_container_subtree_limit_discards_tentative_descendants(
    tmp_path: Path,
) -> None:
    inner_buffer = io.BytesIO()
    with zipfile.ZipFile(inner_buffer, "w") as inner:
        inner.writestr("leaf.txt", b"123456")
    inner_bytes = inner_buffer.getvalue()
    source = tmp_path / "input"
    source.mkdir()
    with zipfile.ZipFile(source / "outer.zip", "w") as outer:
        outer.writestr("one.zip", inner_bytes)
        outer.writestr("two.zip", inner_bytes)
    direct_bytes = len(inner_bytes) * 2
    policy = Policy(
        max_member_bytes=10_000,
        max_container_bytes=direct_bytes + 5,
        max_run_bytes=100_000,
    )
    destination = tmp_path / "output"
    manifest, report, outcome = Processor(source, destination, policy).run()
    assert outcome is ExitOutcome.PARTIAL
    occurrences = [row for row in _manifest(manifest) if row["record_type"] == "occurrence"]
    assert len(occurrences) == 1
    assert occurrences[0]["reason"] == "container_size_limit"
    assert not list(destination.rglob("leaf.txt"))
    assert "container_size_limit" in report.read_text(encoding="utf-8")


def test_message_inside_archive_is_traversed_with_full_provenance(tmp_path: Path) -> None:
    message = EmailMessage()
    message["From"] = "a@example.test"
    message["To"] = "b@example.test"
    message["Subject"] = "nested"
    message.set_content("display body")
    message.add_attachment(
        b"nested document",
        maintype="text",
        subtype="plain",
        filename="note.txt",
    )
    source = tmp_path / "input"
    source.mkdir()
    with zipfile.ZipFile(source / "mail.zip", "w") as archive:
        archive.writestr("messages/nested.eml", message.as_bytes())
    destination = tmp_path / "output"
    manifest, _report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.SUCCESS
    assert list((destination / "documents").rglob("note.txt"))
    occurrences = [row for row in _manifest(manifest) if row["record_type"] == "occurrence"]
    assert len(occurrences) == 1
    metadata = cast("dict[str, object]", occurrences[0]["metadata"])
    assert metadata["archive_member_name"] == "messages/nested.eml"
    ancestry = cast("list[dict[str, object]]", occurrences[0]["ancestry"])
    ancestry_kinds = [node["kind"] for node in ancestry]
    assert ancestry_kinds.count("archive") == 1
    assert ancestry_kinds.count("message") == 1


def test_nested_zip_tar_and_seven_zip_are_processed_in_one_chain(tmp_path: Path) -> None:
    leaf = tmp_path / "deep.txt"
    leaf.write_bytes(b"deep payload")
    seven_path = tmp_path / "inner.7z"
    with py7zr.SevenZipFile(seven_path, "w") as seven:
        seven.write(leaf, arcname="deep.txt")
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        seven_bytes = seven_path.read_bytes()
        info = tarfile.TarInfo("inner.7z")
        info.size = len(seven_bytes)
        tar.addfile(info, io.BytesIO(seven_bytes))
    source = tmp_path / "input"
    source.mkdir()
    with zipfile.ZipFile(source / "outer.zip", "w") as outer:
        outer.writestr("middle.tar", tar_buffer.getvalue())
    destination = tmp_path / "output"
    manifest, _report, outcome = Processor(source, destination, Policy()).run()
    assert outcome is ExitOutcome.SUCCESS
    assert list((destination / "documents").rglob("deep.txt"))
    containers = [row for row in _manifest(manifest) if row["record_type"] == "container"]
    assert {row["kind"] for row in containers} == {"zip", "tar", "7z"}


def test_cli_complete_state_conflict_fatal_and_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    destination = tmp_path / "output"
    complete = runner.invoke(app, [str(source), str(destination)])
    assert complete.exit_code == ExitOutcome.SUCCESS
    assert "outcome=complete" in complete.stdout
    conflict = runner.invoke(app, [str(source), str(destination), "--pdf-only"])
    assert conflict.exit_code == ExitOutcome.CONFLICT
    assert "state conflict" in conflict.stderr

    def fatal(_self: object) -> object:
        message = "failure"
        raise RuntimeError(message)

    monkeypatch.setattr("unpacksort.cli.Processor.run", fatal)
    fatal_result = runner.invoke(app, [str(source), str(tmp_path / "fatal")])
    assert fatal_result.exit_code == ExitOutcome.FATAL
    assert "fatal error" in fatal_result.stderr

    def interrupted(_self: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr("unpacksort.cli.Processor.run", interrupted)
    interrupted_result = runner.invoke(app, [str(source), str(tmp_path / "interrupted")])
    assert interrupted_result.exit_code == ExitOutcome.INTERRUPTED
    assert "can be resumed" in interrupted_result.stderr


def test_cli_rejects_unsafe_destination_relationships(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    inside = runner.invoke(app, [str(source), str(source / "output")])
    assert inside.exit_code == ExitOutcome.USAGE
    assert "inside the source" in inside.stderr
    output_file = tmp_path / "output-file"
    output_file.write_text("not a directory", encoding="utf-8")
    wrong_kind = runner.invoke(app, [str(source), str(output_file)])
    assert wrong_kind.exit_code == ExitOutcome.USAGE
    assert "not a directory" in wrong_kind.stderr


def test_digest_ancestor_cycle_is_reported_without_expansion(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    archive_path = tmp_path / "cycle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("leaf.txt", "never expanded")
    destination = tmp_path / "output"
    processor = Processor(source, destination, Policy())
    with Journal(destination) as journal:
        journal.prepare(processor.source_identity, processor.policy, tool_version="0.1.0")
        store = BlobStore(journal)
        blob = store.ingest_path(archive_path, max_bytes=10_000)
        processor._process_blob(
            blob,
            logical_name="cycle.zip",
            source_path="cycle.zip",
            ancestry=(),
            depth=0,
            ancestor_digests=frozenset({blob.digest}),
            journal=journal,
            store=store,
            metadata={},
            diagnostics=(),
        )
        occurrences = journal.occurrence_records()
        containers = journal.container_records()
    assert occurrences[0]["reason"] == "cycle"
    assert containers[0]["reason"] == "cycle"
