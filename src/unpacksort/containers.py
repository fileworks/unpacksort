"""Typed, bounded container adapters that never extract to public paths."""

from __future__ import annotations

import hashlib
import stat
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import py7zr
import zstandard
from py7zr.io import Py7zIO, WriterFactory

from unpacksort.models import Blob, Reason
from unpacksort.policy import Accounting, ContainerAccounting, LimitExceededError, Policy
from unpacksort.safety import portable_text_key, unsafe_logical_path
from unpacksort.storage import BlobStore


class ContainerFailureError(Exception):
    """An all-or-nothing container subtree failure."""

    def __init__(self, reason: Reason, *, owner: str | None = None) -> None:
        """Create a container error with its stable reason."""
        super().__init__(reason.value)
        self.reason = reason
        self.owner = owner


@dataclass(frozen=True, slots=True)
class Member:
    """A safely materialized regular member."""

    name: str
    index: int
    declared_size: int
    compressed_size: int | None
    blob: Blob


class ContainerAdapter(Protocol):
    """A typed boundary exposing metadata and bounded member streams only."""

    def members(
        self,
        blob: Blob,
        store: BlobStore,
        policy: Policy,
        accounting: Accounting,
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> list[Member]:
        """Return an all-or-nothing safely staged subtree."""


def adapter_for(kind: str) -> ContainerAdapter:
    """Return the fixed in-process adapter for a supported kind."""

    adapters: dict[str, ContainerAdapter] = {
        "zip": ZipAdapter(),
        "tar": TarAdapter(),
        "7z": SevenZipAdapter(),
    }
    try:
        return adapters[kind]
    except KeyError as error:
        raise ContainerFailureError(Reason.UNSUPPORTED_CONTAINER) from error


class ZipAdapter:
    """ZIP and ZIP64 adapter."""

    def members(
        self,
        blob: Blob,
        store: BlobStore,
        policy: Policy,
        accounting: Accounting,
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> list[Member]:
        """Stage a ZIP subtree only after every member passes."""
        staged: list[Member] = []
        try:
            with zipfile.ZipFile(blob.path) as archive:
                infos = archive.infolist()
                _check_container_count(len(infos), policy)
                ordered = sorted(
                    enumerate(infos),
                    key=lambda pair: (*portable_text_key(pair[1].filename), pair[0]),
                )
                for _index, info in ordered:
                    if info.is_dir():
                        continue
                    _check_name(info.filename)
                    mode = info.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG}:
                        raise ContainerFailureError(Reason.UNSAFE_ENTRY)
                    if info.flag_bits & 0x1:
                        raise ContainerFailureError(Reason.ENCRYPTED_ARCHIVE)
                    _check_declared(info.file_size, info.compress_size, policy)
                regular = [info for _, info in ordered if not info.is_dir()]
                _preflight_declared(
                    [info.file_size for info in regular],
                    accounting,
                    budgets,
                )
                for index, info in ordered:
                    if info.is_dir():
                        continue
                    max_bytes, reason, owner = _stream_bound(
                        policy,
                        accounting,
                        budgets,
                        compressed_size=info.compress_size,
                    )
                    with archive.open(info) as stream:
                        member_blob = store.ingest_stream(
                            stream,
                            max_bytes=max_bytes,
                            limit_reason=reason,
                            limit_owner=owner,
                        )
                    _charge_observed(member_blob.size, accounting, budgets)
                    staged.append(
                        Member(
                            name=info.filename,
                            index=index,
                            declared_size=info.file_size,
                            compressed_size=info.compress_size,
                            blob=member_blob,
                        ),
                    )
                bad_member = archive.testzip()
                if bad_member is not None:
                    raise ContainerFailureError(Reason.CORRUPT_ARCHIVE)
        except ContainerFailureError:
            raise
        except LimitExceededError as error:
            raise ContainerFailureError(error.reason, owner=error.owner) from error
        except RuntimeError as error:
            reason = (
                Reason.ENCRYPTED_ARCHIVE
                if "password" in str(error).casefold() or "encrypted" in str(error).casefold()
                else Reason.CORRUPT_ARCHIVE
            )
            raise ContainerFailureError(reason) from error
        except (OSError, EOFError, ValueError, zipfile.BadZipFile) as error:
            raise ContainerFailureError(Reason.CORRUPT_ARCHIVE) from error
        _check_observed(staged, policy)
        return staged


class TarAdapter:
    """TAR adapter for plain, gzip, bzip2, xz, and zstandard streams."""

    def members(
        self,
        blob: Blob,
        store: BlobStore,
        policy: Policy,
        accounting: Accounting,
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> list[Member]:
        """Stage a TAR subtree only after every member passes."""
        try:
            with _open_tar(blob, store, policy) as archive:
                infos = archive.getmembers()
                _check_container_count(len(infos), policy)
                ordered = sorted(
                    enumerate(infos),
                    key=lambda pair: (*portable_text_key(pair[1].name), pair[0]),
                )
                for _, info in ordered:
                    _check_name(info.name)
                    if not (info.isdir() or info.isreg()):
                        raise ContainerFailureError(Reason.UNSAFE_ENTRY)
                    if info.issparse():
                        raise ContainerFailureError(Reason.UNSAFE_ENTRY)
                regular = [info for _, info in ordered if info.isreg()]
                _preflight_declared(
                    [info.size for info in regular],
                    accounting,
                    budgets,
                )
                staged: list[Member] = []
                for index, info in ordered:
                    if info.isdir():
                        continue
                    _check_declared(info.size, None, policy)
                    max_bytes, reason, owner = _stream_bound(
                        policy,
                        accounting,
                        budgets,
                    )
                    stream = archive.extractfile(info)
                    if stream is None:
                        raise ContainerFailureError(Reason.CORRUPT_ARCHIVE)
                    with stream:
                        member_blob = store.ingest_stream(
                            stream,
                            max_bytes=max_bytes,
                            limit_reason=reason,
                            limit_owner=owner,
                        )
                    _charge_observed(member_blob.size, accounting, budgets)
                    staged.append(
                        Member(
                            name=info.name,
                            index=index,
                            declared_size=info.size,
                            compressed_size=None,
                            blob=member_blob,
                        ),
                    )
        except ContainerFailureError:
            raise
        except LimitExceededError as error:
            raise ContainerFailureError(error.reason, owner=error.owner) from error
        except (OSError, EOFError, ValueError, tarfile.TarError, zstandard.ZstdError) as error:
            raise ContainerFailureError(Reason.CORRUPT_ARCHIVE) from error
        _check_observed(staged, policy)
        return staged


class SevenZipAdapter:
    """7z adapter backed by py7zr and bounded private writers."""

    def members(
        self,
        blob: Blob,
        store: BlobStore,
        policy: Policy,
        accounting: Accounting,
        budgets: tuple[ContainerAccounting, ...] = (),
    ) -> list[Member]:
        """Stage a 7z subtree through bounded private writer objects."""
        staged: list[Member] = []
        try:
            with py7zr.SevenZipFile(
                blob.path,
                mode="r",
                max_extract_size=policy.max_container_bytes,
            ) as archive:
                if archive.needs_password():
                    raise ContainerFailureError(Reason.ENCRYPTED_ARCHIVE)
                infos = archive.list()
                _check_container_count(len(infos), policy)
                ordered = sorted(
                    enumerate(infos),
                    key=lambda pair: (*portable_text_key(pair[1].filename), pair[0]),
                )
                for _, info in ordered:
                    _check_name(info.filename)
                    if info.is_symlink or not (info.is_file or info.is_directory):
                        raise ContainerFailureError(Reason.UNSAFE_ENTRY)
                    if info.is_file:
                        _check_declared(info.uncompressed, info.compressed, policy)
                regular = [info for _, info in ordered if info.is_file]
                _preflight_declared(
                    [info.uncompressed for info in regular],
                    accounting,
                    budgets,
                )
                with tempfile.TemporaryDirectory(dir=store.temp, prefix="seven-") as temp:
                    limits: dict[str, list[tuple[int, Reason, str | None]]] = {}
                    for info in regular:
                        limits.setdefault(info.filename, []).append(
                            _stream_bound(
                                policy,
                                accounting,
                                budgets,
                                compressed_size=info.compressed,
                            ),
                        )
                    factory = _BoundedWriterFactory(
                        Path(temp),
                        limits,
                        _ObservedLimits(accounting, budgets),
                    )
                    try:
                        archive.extract(
                            targets=[info.filename for _, info in ordered if info.is_file],
                            factory=factory,
                        )
                        for index, info in ordered:
                            if not info.is_file:
                                continue
                            product = factory.products[info.filename].pop(0)
                            product.flush()
                            with product.path.open("rb") as stream:
                                member_blob = store.ingest_stream(
                                    stream,
                                    max_bytes=policy.max_member_bytes,
                                )
                            product.release()
                            _charge_observed(member_blob.size, accounting, budgets)
                            staged.append(
                                Member(
                                    name=info.filename,
                                    index=index,
                                    declared_size=info.uncompressed,
                                    compressed_size=info.compressed,
                                    blob=member_blob,
                                ),
                            )
                    finally:
                        factory.release_all()
        except ContainerFailureError:
            raise
        except LimitExceededError as error:
            raise ContainerFailureError(error.reason, owner=error.owner) from error
        except Exception as error:  # py7zr exposes several backend-specific exceptions
            text = str(error).casefold()
            reason = (
                Reason.ENCRYPTED_ARCHIVE
                if "password" in text or "encrypted" in text
                else Reason.CORRUPT_ARCHIVE
            )
            raise ContainerFailureError(reason) from error
        _check_observed(staged, policy)
        return staged


class _BoundedWriter(Py7zIO):
    def __init__(
        self,
        path: Path,
        limit: int,
        reason: Reason,
        owner: str | None,
        observed: _ObservedLimits,
    ) -> None:
        self.path = path
        self.limit = limit
        self.reason = reason
        self.owner = owner
        self.observed = observed
        self._stream = path.open("w+b")
        self._size = 0

    def write(self, payload: bytes | bytearray) -> int:
        if self._size + len(payload) > self.limit:
            raise LimitExceededError(self.reason, owner=self.owner)
        self.observed.charge(len(payload))
        written = self._stream.write(payload)
        self._size += written
        return written

    def read(self, size: int | None = None) -> bytes:
        return self._stream.read(-1 if size is None else size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._stream.seek(offset, whence)

    def flush(self) -> None:
        self._stream.flush()

    def size(self) -> int:
        return self._size

    def close(self) -> None:
        self._stream.flush()

    def release(self) -> None:
        """Close the private file after its staged copy is committed."""
        if not self._stream.closed:
            self._stream.close()


class _BoundedWriterFactory(WriterFactory):
    def __init__(
        self,
        root: Path,
        limits: dict[str, list[tuple[int, Reason, str | None]]],
        observed: _ObservedLimits,
    ) -> None:
        self.root = root
        self.limits = limits
        self.observed = observed
        self.products: dict[str, list[_BoundedWriter]] = {}
        self.all_products: list[_BoundedWriter] = []

    def create(self, filename: str) -> Py7zIO:
        occurrence = len(self.products.get(filename, []))
        raw_identifier = f"{filename}\0{occurrence}".encode("utf-8", "surrogatepass")
        identifier = hashlib.sha256(raw_identifier).hexdigest()
        limit, reason, owner = self.limits[filename].pop(0)
        product = _BoundedWriter(
            self.root / identifier,
            limit,
            reason,
            owner,
            self.observed,
        )
        self.products.setdefault(filename, []).append(product)
        self.all_products.append(product)
        return product

    def release_all(self) -> None:
        """Close every private writer, including products from failed extraction."""

        for product in self.all_products:
            product.release()


class _ObservedLimits:
    def __init__(
        self,
        accounting: Accounting,
        budgets: tuple[ContainerAccounting, ...],
    ) -> None:
        self.global_remaining = accounting.remaining_bytes
        self.container_remaining = {budget.identifier: budget.remaining_bytes for budget in budgets}
        self.observed = 0

    def charge(self, size: int) -> None:
        updated = self.observed + size
        if updated > self.global_remaining:
            raise LimitExceededError(Reason.GLOBAL_SIZE_LIMIT)
        for identifier, remaining in self.container_remaining.items():
            if updated > remaining:
                raise LimitExceededError(Reason.CONTAINER_SIZE_LIMIT, owner=identifier)
        self.observed = updated


class _TarContext:
    def __init__(self, archive: tarfile.TarFile, temporary: Path | None = None) -> None:
        self.archive = archive
        self.temporary = temporary

    def __enter__(self) -> tarfile.TarFile:
        return self.archive

    def __exit__(self, *_args: object) -> None:
        self.archive.close()
        if self.temporary is not None:
            self.temporary.unlink(missing_ok=True)


def _open_tar(blob: Blob, store: BlobStore, policy: Policy) -> _TarContext:
    with blob.path.open("rb") as stream:
        magic = stream.read(4)
    if magic != b"\x28\xb5\x2f\xfd":
        return _TarContext(tarfile.open(blob.path, mode="r:*"))
    temporary = store.temp / f"tar-zstd-{blob.digest}.tmp"
    temporary.unlink(missing_ok=True)
    observed = 0
    try:
        with (
            blob.path.open("rb") as source,
            temporary.open("xb") as target,
            zstandard.ZstdDecompressor().stream_reader(source) as reader,
        ):
            while chunk := reader.read(1024 * 1024):
                observed += len(chunk)
                if observed > policy.max_container_bytes:
                    raise LimitExceededError(Reason.CONTAINER_SIZE_LIMIT)
                target.write(chunk)
        return _TarContext(tarfile.open(temporary, mode="r:"), temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _check_container_count(count: int, policy: Policy) -> None:
    if count > policy.max_members_per_container:
        raise ContainerFailureError(Reason.MEMBER_COUNT_LIMIT)


def _check_name(name: str) -> None:
    if unsafe_logical_path(name):
        raise ContainerFailureError(Reason.UNSAFE_PATH)


def _check_declared(
    size: int,
    compressed: int | None,
    policy: Policy,
) -> None:
    if size > policy.max_member_bytes:
        raise ContainerFailureError(Reason.MEMBER_SIZE_LIMIT)
    if size and compressed is not None:
        denominator = max(1, compressed)
        if size / denominator > policy.max_expansion_ratio:
            raise ContainerFailureError(Reason.EXPANSION_RATIO_LIMIT)


def _preflight_declared(
    sizes: list[int],
    accounting: Accounting,
    budgets: tuple[ContainerAccounting, ...],
) -> None:
    total = sum(sizes)
    try:
        accounting.preflight(members=len(sizes), expanded_bytes=total)
        for budget in budgets:
            budget.preflight(total)
    except LimitExceededError as error:
        raise ContainerFailureError(error.reason, owner=error.owner) from error


def _stream_bound(
    policy: Policy,
    accounting: Accounting,
    budgets: tuple[ContainerAccounting, ...],
    *,
    compressed_size: int | None = None,
) -> tuple[int, Reason, str | None]:
    candidates = [
        (policy.max_member_bytes, Reason.MEMBER_SIZE_LIMIT, None),
        (accounting.remaining_bytes, Reason.GLOBAL_SIZE_LIMIT, None),
        *[
            (budget.remaining_bytes, Reason.CONTAINER_SIZE_LIMIT, budget.identifier)
            for budget in budgets
        ],
    ]
    if compressed_size is not None:
        ratio_bytes = int(max(1, compressed_size) * policy.max_expansion_ratio)
        candidates.append((ratio_bytes, Reason.EXPANSION_RATIO_LIMIT, None))
    return min(candidates, key=lambda candidate: candidate[0])


def _charge_observed(
    size: int,
    accounting: Accounting,
    budgets: tuple[ContainerAccounting, ...],
) -> None:
    accounting.charge(size)
    for budget in budgets:
        budget.charge(size)


def _check_observed(members: list[Member], policy: Policy) -> None:
    total = sum(member.blob.size for member in members)
    if total > policy.max_container_bytes:
        raise ContainerFailureError(Reason.CONTAINER_SIZE_LIMIT)
    for member in members:
        if member.compressed_size is not None and member.blob.size:
            denominator = max(1, member.compressed_size)
            if member.blob.size / denominator > policy.max_expansion_ratio:
                raise ContainerFailureError(Reason.EXPANSION_RATIO_LIMIT)
