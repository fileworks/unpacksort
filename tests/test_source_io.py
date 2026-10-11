"""NEW-3: a source swapped for a link after inventory is never read through."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from unpacksort import source_io
from unpacksort.engine import Processor
from unpacksort.mail import is_confirmed_mbox, iter_mbox
from unpacksort.models import ExitOutcome
from unpacksort.policy import Policy
from unpacksort.source_io import UnsafeSourceError, open_source, strip_extended_prefix

posix_only = pytest.mark.skipif(os.name != "posix", reason="openat descriptor walk is POSIX")


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    (root / "album").mkdir(parents=True)
    (root / "album" / "photo.txt").write_bytes(b"inside the source")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "photo.txt").write_bytes(b"SECRET outside the source")
    return root, outside


def test_a_regular_file_below_the_root_is_read(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    with open_source(root / "album" / "photo.txt", root) as stream:
        assert stream.read() == b"inside the source"


def test_a_path_that_climbs_out_of_the_root_is_refused(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    with pytest.raises(UnsafeSourceError, match="escapes"):
        open_source(root / "album" / ".." / "album" / "photo.txt", root)


@posix_only
def test_an_ancestor_replaced_by_a_link_is_not_traversed(tmp_path: Path) -> None:
    root, outside = _tree(tmp_path)
    (root / "album").rename(tmp_path / "parked")
    (root / "album").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeSourceError, match="ancestor"):
        open_source(root / "album" / "photo.txt", root)


@posix_only
def test_a_leaf_replaced_by_a_link_is_not_followed(tmp_path: Path) -> None:
    root, outside = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    leaf.unlink()
    leaf.symlink_to(outside / "photo.txt")
    with pytest.raises(UnsafeSourceError, match="not a plain file"):
        open_source(leaf, root)


@posix_only
def test_a_fifo_swapped_in_is_refused_without_blocking(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    leaf.unlink()
    posix: Any = os  # mkfifo exists only where this test runs
    posix.mkfifo(leaf)
    with pytest.raises(UnsafeSourceError, match="not a regular file"):
        open_source(leaf, root)


@posix_only
def test_a_swap_after_inventory_fails_closed_and_publishes_nothing_outside(
    tmp_path: Path,
) -> None:
    root, outside = _tree(tmp_path)
    destination = tmp_path / "output"
    processor = Processor(root, destination, Policy())
    # Inventory has accepted album/photo.txt; now the ancestor becomes a link.
    (root / "album").rename(tmp_path / "parked")
    (root / "album").symlink_to(outside, target_is_directory=True)

    manifest, _report, outcome = processor.run()

    assert outcome is not ExitOutcome.SUCCESS
    published = [path.read_bytes() for path in destination.rglob("*.txt")]
    assert all(b"SECRET" not in payload for payload in published)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    reasons = {
        record.get("reason") for record in records if record.get("record_type") == "occurrence"
    }
    assert "unsafe_entry" in reasons
    assert (outside / "photo.txt").read_bytes() == b"SECRET outside the source"


# The identity opener is the Windows strategy; it is plain Python over
# lstat/open/fstat, so its refusals are proven on every platform.


def _fake_stat(
    template: os.stat_result, *, mode: int | None = None, ino: int | None = None
) -> os.stat_result:
    fields = list(template)
    if mode is not None:
        fields[stat.ST_MODE] = mode
    if ino is not None:
        fields[stat.ST_INO] = ino
    return os.stat_result(fields)


def test_identity_opener_reads_the_inspected_file(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    descriptor = source_io._open_by_identity(leaf, root, leaf.relative_to(root))
    with os.fdopen(descriptor, "rb") as stream:
        assert stream.read() == b"inside the source"


def test_identity_opener_refuses_a_file_swapped_between_inspection_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    real_fstat = os.fstat

    def swapped(descriptor: int) -> os.stat_result:
        actual = real_fstat(descriptor)
        return _fake_stat(actual, ino=actual.st_ino + 1)

    monkeypatch.setattr(os, "fstat", swapped)
    with pytest.raises(UnsafeSourceError, match="changed while it was opened"):
        source_io._open_by_identity(leaf, root, leaf.relative_to(root))


def test_identity_opener_refuses_a_handle_that_resolves_outside_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    monkeypatch.setattr(source_io, "_final_path", lambda _descriptor: str(outside / "photo.txt"))
    with pytest.raises(UnsafeSourceError, match="outside its root"):
        source_io._open_by_identity(leaf, root, leaf.relative_to(root))


def test_identity_opener_refuses_a_link_ancestor_and_a_non_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _tree(tmp_path)
    leaf = root / "album" / "photo.txt"
    real_lstat = os.lstat

    def album_is_a_link(path: os.PathLike[str] | str) -> os.stat_result:
        result = real_lstat(path)
        if Path(path) == root / "album":
            return _fake_stat(result, mode=stat.S_IFLNK | 0o777)
        return result

    monkeypatch.setattr(os, "lstat", album_is_a_link)
    with pytest.raises(UnsafeSourceError, match="ancestor is a link"):
        source_io._open_by_identity(leaf, root, leaf.relative_to(root))
    monkeypatch.undo()

    with pytest.raises(UnsafeSourceError, match="not a plain file"):
        source_io._open_by_identity(root / "album", root, Path("album"))


@pytest.mark.parametrize(
    ("final", "expected"),
    [
        ("\\\\?\\C:\\data\\a.txt", "C:\\data\\a.txt"),
        ("\\\\?\\UNC\\nas\\share\\a.txt", "\\\\nas\\share\\a.txt"),
        ("C:\\data\\a.txt", "C:\\data\\a.txt"),
    ],
)
def test_extended_length_prefixes_are_removed(final: str, expected: str) -> None:
    assert strip_extended_prefix(final) == expected


def test_boundary_check_requires_a_real_child(tmp_path: Path) -> None:
    root, _ = _tree(tmp_path)
    assert source_io._is_below(str(root / "album" / "photo.txt"), root)
    assert not source_io._is_below(str(tmp_path / "source-sibling" / "x"), root)
    assert not source_io._is_below(str(root), root)


def test_a_mailbox_that_is_no_longer_the_inspected_file_is_not_parsed(
    tmp_path: Path, mbox_path: Path
) -> None:
    other = tmp_path / "other.txt"
    other.write_bytes(b"something else")
    stranger = other.stat()

    assert is_confirmed_mbox(mbox_path, identity=mbox_path.stat())
    assert not is_confirmed_mbox(mbox_path, identity=stranger)
    with pytest.raises(OSError, match="changed after it was confirmed"):
        list(iter_mbox(mbox_path, identity=stranger))
