"""Content-addressed private staging with bounded atomic writes."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Protocol

from unpacksort.journal import Journal
from unpacksort.models import Blob, Reason
from unpacksort.policy import LimitExceededError

CHUNK_SIZE = 1024 * 1024


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
        """Copy one blob to a public path using atomic replacement."""

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.unpacksort.tmp")
        temporary.unlink(missing_ok=True)
        try:
            with blob.path.open("rb") as source, temporary.open("xb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_SIZE)
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(destination)
            _fsync_directory(destination.parent)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


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
