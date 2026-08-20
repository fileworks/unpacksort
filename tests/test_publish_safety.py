"""C-13 — publishing must not clobber, and must prove what it wrote.

`publish` is the one method in the program that writes into a directory the
user owns. It used to `os.replace` onto the destination, which is atomic but not
conditional: anything already there was destroyed, and anything that appeared
between the engine's check and this write was destroyed silently. It also
trusted the copy, while `ingest_stream` one method above hashes everything it
writes.
"""

from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest

from unpacksort.journal import Journal
from unpacksort.models import Blob
from unpacksort.storage import BlobStore, PublicationError


@pytest.fixture
def store(tmp_path: Path) -> BlobStore:
    return BlobStore(Journal(tmp_path / "destination"))


def _staged(store: BlobStore, payload: bytes) -> Blob:
    return store.ingest_bytes(payload, max_bytes=1_000_000)


class TestItRefusesToOverwrite:
    def test_an_existing_destination_is_left_exactly_as_it_was(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        blob = _staged(store, b"the new bytes")
        destination = tmp_path / "out" / "file.txt"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"somebody else's work")

        with pytest.raises(PublicationError, match="refusing to overwrite"):
            store.publish(blob, destination)

        assert destination.read_bytes() == b"somebody else's work"

    def test_it_leaves_no_temporary_behind_when_it_refuses(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        blob = _staged(store, b"the new bytes")
        destination = tmp_path / "out" / "file.txt"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"somebody else's work")

        with pytest.raises(PublicationError):
            store.publish(blob, destination)

        assert sorted(path.name for path in destination.parent.iterdir()) == ["file.txt"]

    def test_an_empty_existing_file_still_counts_as_something(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        """Zero bytes is a file somebody made, not an absence."""
        blob = _staged(store, b"payload")
        destination = tmp_path / "out" / "file.txt"
        destination.parent.mkdir(parents=True)
        destination.touch()

        with pytest.raises(PublicationError, match="refusing to overwrite"):
            store.publish(blob, destination)


class TestTheFallbackForFilesystemsWithoutHardLinks:
    """exFAT and some network mounts cannot hard-link, so `publish` falls back to
    a check and a replace. That path never runs on this host, which is exactly
    why it needs a test: an untested fallback is where the guarantee quietly
    stops holding."""

    @staticmethod
    def _no_hard_links(monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(*_unused: object) -> None:
            raise OSError(errno.EPERM, "hard links are not supported here")

        monkeypatch.setattr("unpacksort.storage.os.link", refuse)

    def test_it_still_refuses_an_existing_destination(
        self, store: BlobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._no_hard_links(monkeypatch)
        blob = _staged(store, b"the new bytes")
        destination = tmp_path / "out" / "file.txt"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"somebody else's work")

        with pytest.raises(PublicationError, match="refusing to overwrite"):
            store.publish(blob, destination)

        assert destination.read_bytes() == b"somebody else's work"

    def test_it_still_publishes_when_the_destination_is_free(
        self, store: BlobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._no_hard_links(monkeypatch)
        blob = _staged(store, b"payload")
        destination = tmp_path / "out" / "file.txt"

        store.publish(blob, destination)

        assert destination.read_bytes() == b"payload"
        assert sorted(path.name for path in destination.parent.iterdir()) == ["file.txt"]


class TestItProvesTheCopy:
    def test_a_blob_whose_bytes_do_not_match_its_digest_is_refused(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        """Corruption between staging and publishing, which is exactly the
        window `ingest_stream`'s own hashing cannot see."""
        blob = _staged(store, b"payload")
        blob.path.write_bytes(b"corrupted after staging")
        destination = tmp_path / "out" / "file.txt"

        with pytest.raises(PublicationError, match="does not match its blob"):
            store.publish(blob, destination)

        assert not destination.exists()

    def test_nothing_is_left_at_the_destination_after_a_bad_copy(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        blob = _staged(store, b"payload")
        blob.path.write_bytes(b"corrupted after staging")
        destination = tmp_path / "out" / "file.txt"

        with pytest.raises(PublicationError):
            store.publish(blob, destination)

        assert list(destination.parent.iterdir()) == []


class TestTheHappyPathIsUnchanged:
    def test_it_publishes_the_bytes_it_staged(self, store: BlobStore, tmp_path: Path) -> None:
        payload = b"a file worth keeping" * 100
        blob = _staged(store, payload)
        destination = tmp_path / "out" / "file.txt"

        store.publish(blob, destination)

        assert destination.read_bytes() == payload
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == blob.digest

    def test_it_creates_the_parent_directory(self, store: BlobStore, tmp_path: Path) -> None:
        blob = _staged(store, b"payload")
        destination = tmp_path / "deep" / "nested" / "file.txt"

        store.publish(blob, destination)

        assert destination.is_file()

    def test_it_leaves_no_temporary_behind_on_success(
        self, store: BlobStore, tmp_path: Path
    ) -> None:
        blob = _staged(store, b"payload")
        destination = tmp_path / "out" / "file.txt"

        store.publish(blob, destination)

        assert sorted(path.name for path in destination.parent.iterdir()) == ["file.txt"]

    def test_an_empty_payload_publishes(self, store: BlobStore, tmp_path: Path) -> None:
        """The read loop must not mistake "no bytes" for "nothing to do"."""
        blob = _staged(store, b"")
        destination = tmp_path / "out" / "empty.txt"

        store.publish(blob, destination)

        assert destination.is_file()
        assert destination.read_bytes() == b""
