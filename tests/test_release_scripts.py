from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import package_portable, release_assets, render_winget


def test_portable_packaging_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "unpacksort.exe"
    executable.write_bytes(b"MZ" + b"portable payload")
    output = tmp_path / "release"
    arguments = [
        "package_portable.py",
        "--executable",
        str(executable),
        "--version",
        "1.2.3",
        "--output",
        str(output),
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    package_portable.main()
    archive_path = output / "unpacksort-1.2.3-windows-x64.zip"
    first = archive_path.read_bytes()
    monkeypatch.setattr(sys, "argv", arguments)
    package_portable.main()
    assert archive_path.read_bytes() == first
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == [
            "unpacksort-1.2.3-windows-x64/unpacksort.exe",
        ]


def test_portable_packaging_rejects_wrong_magic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "unpacksort.exe"
    executable.write_bytes(b"not an executable")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package_portable.py",
            "--executable",
            str(executable),
            "--version",
            "1.0.0",
            "--output",
            str(tmp_path / "release"),
        ],
    )
    with pytest.raises(SystemExit, match="not a Windows executable"):
        package_portable.main()


def test_release_asset_validation_and_checksums(tmp_path: Path) -> None:
    source = tmp_path / "source"
    portable = tmp_path / "portable"
    source.mkdir()
    portable.mkdir()
    wheel = source / "unpacksort-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("metadata", "wheel")
    (source / "unpacksort-1.2.3.tar.gz").write_bytes(b"\x1f\x8bsource")
    portable_path = portable / "unpacksort-1.2.3-windows-x64.zip"
    with zipfile.ZipFile(portable_path, "w") as archive:
        archive.writestr("unpacksort-1.2.3-windows-x64/unpacksort.exe", b"MZpayload")
    release_assets.verify_assets(tmp_path, "1.2.3")
    checksum_path = tmp_path / "SHA256SUMS"
    release_assets.write_checksums(tmp_path, checksum_path)
    lines = checksum_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}" in lines


def test_release_asset_validation_rejects_mismatch(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="exactly one"):
        release_assets.verify_assets(tmp_path, "1.0.0")


def test_winget_templates_render_exact_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "winget"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render_winget.py",
            "--version",
            "1.2.3",
            "--url",
            "https://example.test/unpacksort.zip",
            "--sha256",
            "ab" * 32,
            "--output",
            str(output),
        ],
    )
    render_winget.main()
    files = sorted(output.glob("*.yaml"))
    assert len(files) == 3
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "fileworks.unpacksort" in combined
    assert "PortableCommandAlias: unpacksort" in combined
    assert "{{" not in combined
