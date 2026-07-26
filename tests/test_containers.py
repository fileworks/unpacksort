from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import py7zr
import pytest
import zstandard

from unpacksort.containers import (
    ContainerFailureError,
    SevenZipAdapter,
    TarAdapter,
    ZipAdapter,
    adapter_for,
)
from unpacksort.journal import Journal
from unpacksort.models import Reason
from unpacksort.policy import Accounting, Policy
from unpacksort.storage import BlobStore


def _store(tmp_path: Path) -> tuple[Journal, BlobStore]:
    journal = Journal(tmp_path / "destination")
    return journal, BlobStore(journal)


def test_zip_adapter_stages_members_in_portable_order(tmp_path: Path) -> None:
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.writestr("B.txt", b"second")
        archive.writestr("a.txt", b"first")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        members = ZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert [member.name for member in members] == ["a.txt", "B.txt"]
    assert [member.blob.path.read_bytes() for member in members] == [b"first", b"second"]


def test_zip64_member_uses_the_same_safe_adapter(tmp_path: Path) -> None:
    path = tmp_path / "zip64.zip"
    with (
        zipfile.ZipFile(path, "w", allowZip64=True) as archive,
        archive.open("forced.bin", "w", force_zip64=True) as member,
    ):
        member.write(b"zip64")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=10_000)
        members = ZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert members[0].blob.path.read_bytes() == b"zip64"


@pytest.mark.parametrize("name", ["../escape", "/absolute", r"C:\drive", "file:stream"])
def test_zip_rejects_unsafe_paths_atomically(tmp_path: Path, name: str) -> None:
    path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("safe.txt", b"tentative")
        archive.writestr(name, b"unsafe")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        with pytest.raises(ContainerFailureError) as caught:
            ZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason in {Reason.UNSAFE_PATH, Reason.UNSAFE_ENTRY}


def test_zip_rejects_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, "../target")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        with pytest.raises(ContainerFailureError) as caught:
            ZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason is Reason.UNSAFE_ENTRY


def test_zip_encryption_flag_blocks_the_complete_subtree(tmp_path: Path) -> None:
    path = tmp_path / "encrypted-flag.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload.txt", b"payload")
    payload = bytearray(path.read_bytes())
    local = payload.index(b"PK\x03\x04")
    central = payload.index(b"PK\x01\x02")
    payload[local + 6] |= 0x01
    payload[central + 8] |= 0x01
    path.write_bytes(payload)
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=10_000)
        with pytest.raises(ContainerFailureError) as caught:
            ZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason is Reason.ENCRYPTED_ARCHIVE


def test_zip_declared_limits_are_checked_before_read(tmp_path: Path) -> None:
    path = tmp_path / "large.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("large.bin", b"x" * 20)
    policy = Policy(max_member_bytes=10, max_container_bytes=100, max_run_bytes=100)
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        with pytest.raises(ContainerFailureError) as caught:
            ZipAdapter().members(blob, store, policy, Accounting(policy))
    finally:
        journal.close()
    assert caught.value.reason is Reason.MEMBER_SIZE_LIMIT


def test_zip_global_declared_limit_is_checked_before_read(tmp_path: Path) -> None:
    path = tmp_path / "global.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload.bin", b"12345")
    policy = Policy(max_member_bytes=10, max_container_bytes=10, max_run_bytes=10)
    accounting = Accounting(policy, members=1, expanded_bytes=6)
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        with pytest.raises(ContainerFailureError) as caught:
            ZipAdapter().members(blob, store, policy, accounting)
    finally:
        journal.close()
    assert caught.value.reason is Reason.GLOBAL_SIZE_LIMIT
    assert accounting.expanded_bytes == 6


@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_tar_compression_families(tmp_path: Path, mode: str) -> None:
    suffix = mode.replace("w", "").replace(":", "") or "tar"
    path = tmp_path / f"fixture.{suffix}"
    payload = b"tar payload"
    with tarfile.open(path, mode) as archive:
        info = tarfile.TarInfo("nested/a.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=100_000)
        members = TarAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert len(members) == 1
    assert members[0].blob.path.read_bytes() == payload


def test_tar_zstandard_is_in_process(tmp_path: Path) -> None:
    raw = tmp_path / "fixture.tar"
    compressed = tmp_path / "fixture.tar.zst"
    with tarfile.open(raw, "w") as archive:
        payload = b"zstandard"
        info = tarfile.TarInfo("a.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    compressed.write_bytes(zstandard.ZstdCompressor().compress(raw.read_bytes()))
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(compressed, max_bytes=10_000)
        members = TarAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert members[0].blob.path.read_bytes() == b"zstandard"


@pytest.mark.parametrize(("kind", "linkname"), [("symlink", "target"), ("hardlink", "target")])
def test_tar_rejects_links(tmp_path: Path, kind: str, linkname: str) -> None:
    path = tmp_path / "link.tar"
    with tarfile.open(path, "w") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        info.linkname = linkname
        archive.addfile(info)
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=100_000)
        with pytest.raises(ContainerFailureError) as caught:
            TarAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason is Reason.UNSAFE_ENTRY


def test_seven_zip_adapter_and_encryption(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"seven")
    archive_path = tmp_path / "fixture.7z"
    with py7zr.SevenZipFile(archive_path, "w") as archive:
        archive.write(source, arcname="a.txt")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(archive_path, max_bytes=100_000)
        members = SevenZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert members[0].blob.path.read_bytes() == b"seven"

    encrypted = tmp_path / "encrypted.7z"
    with py7zr.SevenZipFile(encrypted, "w", password="secret", header_encryption=True) as archive:
        archive.write(source, arcname="a.txt")
    journal, store = _store(tmp_path / "encrypted-state")
    try:
        blob = store.ingest_path(encrypted, max_bytes=100_000)
        with pytest.raises(ContainerFailureError) as caught:
            SevenZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason is Reason.ENCRYPTED_ARCHIVE


def test_corrupt_seven_zip_has_stable_reason(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.7z"
    path.write_bytes(b"7z\xbc\xaf'\x1cnot-a-valid-container")
    journal, store = _store(tmp_path)
    try:
        blob = store.ingest_path(path, max_bytes=1_000)
        with pytest.raises(ContainerFailureError) as caught:
            SevenZipAdapter().members(blob, store, Policy(), Accounting(Policy()))
    finally:
        journal.close()
    assert caught.value.reason is Reason.CORRUPT_ARCHIVE


def test_unsupported_adapter_has_stable_reason() -> None:
    with pytest.raises(ContainerFailureError) as caught:
        adapter_for("rar")
    assert caught.value.reason is Reason.UNSUPPORTED_CONTAINER
