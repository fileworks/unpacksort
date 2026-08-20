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
import os
import platform
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from unpacksort.journal import Journal
from unpacksort.models import Blob
from unpacksort.storage import BlobStore, PublicationError, _commit_without_clobbering


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


#: Resolved to an absolute path so the subprocess calls below name an
#: executable rather than trusting `PATH` at call time.
_HDIUTIL = shutil.which("hdiutil")


def _exfat_mount(tmp_path: Path) -> Path | None:
    """Mount a small exFAT image, or return None where that is not possible.

    Skipping on other platforms is deliberate: the point of this class is to run
    against a filesystem that genuinely cannot hard-link, and a simulated one is
    already covered above. macOS can build one with `hdiutil` and no privileges.
    """
    # `platform.system()` rather than `sys.platform`: mypy narrows the latter to
    # the checking platform, so on Linux it proves this function's body
    # unreachable and fails the type gate. The runtime behaviour is identical.
    if platform.system() != "Darwin" or _HDIUTIL is None:
        return None
    image = tmp_path / "exfat.dmg"
    mountpoint = tmp_path / "exfat"
    mountpoint.mkdir()
    create = subprocess.run(  # noqa: S603
        [
            _HDIUTIL,
            "create",
            "-size",
            "20m",
            "-fs",
            "exFAT",
            "-volname",
            "UPSTEST",
            "-o",
            str(image),
            "-quiet",
        ],
        capture_output=True,
        check=False,
    )
    if create.returncode != 0:
        return None
    attach = subprocess.run(  # noqa: S603
        [_HDIUTIL, "attach", str(image), "-mountpoint", str(mountpoint), "-nobrowse"],
        capture_output=True,
        check=False,
    )
    if attach.returncode != 0:
        return None
    return mountpoint


@pytest.fixture(scope="module")
def exfat(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Mounted once for the module: attaching an image per test is slow enough to
    make the suite flaky under contention, and these tests do not share state."""
    mountpoint = _exfat_mount(tmp_path_factory.mktemp("exfat-image"))
    if mountpoint is None:
        pytest.skip("no exFAT filesystem can be created on this host")
    try:
        yield mountpoint
    finally:
        assert _HDIUTIL is not None  # the mount above could not have succeeded otherwise
        subprocess.run(  # noqa: S603
            [_HDIUTIL, "detach", str(mountpoint), "-force"],
            capture_output=True,
            check=False,
        )


class TestTheFallbackOnARealFilesystemThatCannotHardLink:
    """The same fallback, against exFAT itself rather than a patched `os.link`.

    The class above proves the fallback behaves correctly *given* that hard
    links fail. It cannot prove the premise: that exFAT is a filesystem where
    they actually do, and that they fail in the way the `except OSError` clause
    catches. A monkeypatch raising `EPERM` would pass either way — including if
    the real error arrived as something the handler never sees.
    """

    def test_hard_links_really_are_unsupported_there(self, exfat: Path) -> None:
        source = exfat / "unsupported-source.bin"
        source.write_bytes(b"payload")

        with pytest.raises(OSError) as raised:  # noqa: PT011 - the errno is the assertion
            os.link(source, exfat / "unsupported-link.bin")

        # ENOTSUP (45 on Darwin), not EPERM — which is why the handler catches
        # OSError broadly rather than one errno it guessed.
        assert raised.value.errno == errno.ENOTSUP

    def test_it_publishes_and_leaves_no_temporary_behind(self, exfat: Path) -> None:
        temporary = exfat / "publish-staged.bin"
        temporary.write_bytes(b"payload")
        destination = exfat / "publish-destination.bin"

        _commit_without_clobbering(temporary, destination)

        assert destination.read_bytes() == b"payload"
        assert not temporary.exists()

    def test_it_still_refuses_an_existing_destination(self, exfat: Path) -> None:
        destination = exfat / "refuse-destination.bin"
        destination.write_bytes(b"somebody else's work")
        temporary = exfat / "refuse-staged.bin"
        temporary.write_bytes(b"the new bytes")

        with pytest.raises(PublicationError, match="refusing to overwrite"):
            _commit_without_clobbering(temporary, destination)

        assert destination.read_bytes() == b"somebody else's work"
