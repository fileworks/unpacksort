"""Content-addressed private staging with bounded atomic writes."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Protocol

from unpacksort.journal import Journal
from unpacksort.models import Blob, Reason
from unpacksort.policy import LimitExceededError

CHUNK_SIZE = 1024 * 1024


class PublicationError(Exception):
    """A blob could not be published safely, so it was not published at all."""


class BlobStore:
    """Private content-addressed storage owned by one destination."""

    def __init__(self, journal: Journal) -> None:
        """Create private blob and temporary directories."""
        self.root = journal.state_dir / "blobs"
        self.temp = journal.state_dir / "tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.temp.mkdir(parents=True, exist_ok=True)
        self.journal = journal
        self.cleanup_incomplete()

    def cleanup_incomplete(self) -> None:
        """Remove only private, unmistakably incomplete staging files."""

        for candidate in self.temp.glob("blob-*.tmp"):
            if candidate.is_file():
                candidate.unlink()

    def ingest_path(
        self,
        path: Path,
        *,
        max_bytes: int,
        limit_reason: Reason = Reason.MEMBER_SIZE_LIMIT,
        limit_owner: str | None = None,
    ) -> Blob:
        """Stream a regular source path into content-addressed storage."""

        with path.open("rb") as stream:
            return self.ingest_stream(
                stream,
                max_bytes=max_bytes,
                limit_reason=limit_reason,
                limit_owner=limit_owner,
            )

    def ingest_bytes(
        self,
        payload: bytes,
        *,
        max_bytes: int,
        limit_reason: Reason = Reason.MEMBER_SIZE_LIMIT,
        limit_owner: str | None = None,
    ) -> Blob:
        """Stage an in-memory parser payload through the same bounded path."""

        return self.ingest_stream(
            BytesIO(payload),
            max_bytes=max_bytes,
            limit_reason=limit_reason,
            limit_owner=limit_owner,
        )

    def ingest_stream(
        self,
        stream: ReadableStream,
        *,
        max_bytes: int,
        limit_reason: Reason = Reason.MEMBER_SIZE_LIMIT,
        limit_owner: str | None = None,
    ) -> Blob:
        """Hash, bound, fsync, and atomically commit one stream."""

        temporary = self.temp / f"blob-{os.getpid()}-{id(stream):x}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as destination:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise LimitExceededError(limit_reason, owner=limit_owner)
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            hexadecimal = digest.hexdigest()
            final = self.root / hexadecimal[:2] / hexadecimal
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                temporary.unlink()
            else:
                temporary.replace(final)
                _fsync_directory(final.parent)
            blob = Blob(digest=hexadecimal, size=size, path=final)
            self.journal.record_blob(blob)
            return blob
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @contextmanager
    def temporary_member(self, identifier: str) -> Iterator[Path]:
        """Provide a bounded private member path and remove it afterward."""

        safe_identifier = hashlib.sha256(identifier.encode()).hexdigest()[:16]
        path = self.temp / f"member-{safe_identifier}-{os.getpid()}.tmp"
        path.unlink(missing_ok=True)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    def publish(self, blob: Blob, destination: Path) -> None:
        """Copy one blob to a public path, refusing to overwrite, proving the copy.

        `C-13`. The engine checks the destination before calling this, and that
        check is a different thing from this one: it can only speak for the
        moment it ran. `os.replace` is atomic but not *conditional*, so anything
        that appeared in the window between the check and the write was
        destroyed without a word — and this is the one method in the program
        that writes into a directory the user owns.

        The copy is also re-hashed before it is published, for the same reason
        `ingest_stream` hashes what it writes: bytes that have travelled through
        memory, a filesystem cache and a disk are not proven correct by the fact
        that a write call returned.
        """

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.unpacksort.tmp")
        temporary.unlink(missing_ok=True)
        try:
            digest = hashlib.sha256()
            with blob.path.open("rb") as source, temporary.open("xb") as target:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            written = digest.hexdigest()
            if written != blob.digest:
                mismatch = (
                    f"published copy does not match its blob: expected "
                    f"{blob.digest}, wrote {written}"
                )
                raise PublicationError(mismatch)
            _commit_without_clobbering(temporary, destination)
            _fsync_directory(destination.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _commit_without_clobbering(temporary: Path, destination: Path) -> None:
    """Move *temporary* onto *destination*, refusing if something is there.

    `os.link` is the race-free form: it creates the name or fails, with no
    window in between. Filesystems that cannot hard-link (exFAT, some network
    mounts) fall back to a check and a replace, which narrows the window without
    closing it — stated plainly rather than papered over, because the fallback
    is the weaker guarantee and a reader deserves to know which one they got.
    """
    occupied = f"refusing to overwrite an existing file: {destination}"
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise PublicationError(occupied) from None
    except OSError:
        if destination.exists():
            raise PublicationError(occupied) from None
        temporary.replace(destination)
        return
    temporary.unlink()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ReadableStream(Protocol):
    """The narrow third-party parser stream boundary."""

    def read(self, size: int = -1) -> bytes:
        """Read at most ``size`` bytes."""
