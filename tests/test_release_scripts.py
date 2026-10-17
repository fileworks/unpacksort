from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from scripts import release_assets


def _candidate(directory: Path, version: str) -> Path:
    directory.mkdir()
    wheel = directory / f"unpacksort-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("metadata", "wheel")
    (directory / f"unpacksort-{version}.tar.gz").write_bytes(b"\x1f\x8bsource")
    return wheel


def test_release_asset_validation_and_checksums(tmp_path: Path) -> None:
    wheel = _candidate(tmp_path / "source", "1.2.3")
    release_assets.verify_assets(tmp_path / "source", "1.2.3")
    checksum_path = tmp_path / "SHA256SUMS"
    release_assets.write_checksums(tmp_path / "source", checksum_path)
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}" in lines


def test_release_asset_validation_rejects_mismatch(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="exactly one"):
        release_assets.verify_assets(tmp_path, "1.0.0")


def test_a_retired_portable_zip_is_not_a_release_asset(tmp_path: Path) -> None:
    candidate = tmp_path / "source"
    _candidate(candidate, "1.2.3")
    (candidate / "unpacksort-1.2.3-windows-x64.zip").write_bytes(b"PK")
    with pytest.raises(SystemExit, match="exactly one wheel and one sdist"):
        release_assets.verify_assets(candidate, "1.2.3")


def test_a_version_mismatch_is_refused(tmp_path: Path) -> None:
    _candidate(tmp_path / "source", "1.2.3")
    with pytest.raises(SystemExit, match="does not match"):
        release_assets.verify_assets(tmp_path / "source", "1.2.4")
