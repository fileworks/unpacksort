"""Verify immutable release candidates and generate portable checksums."""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    verify = subcommands.add_parser("verify")
    verify.add_argument("--directory", required=True, type=Path)
    verify.add_argument("--version", required=True)
    checksums = subcommands.add_parser("checksums")
    checksums.add_argument("--directory", required=True, type=Path)
    checksums.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.command == "verify":
        verify_assets(arguments.directory, arguments.version)
    else:
        write_checksums(arguments.directory, arguments.output)


def verify_assets(directory: Path, version: str) -> None:
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    wheels = [path for path in files if path.suffix == ".whl"]
    source_archives = [path for path in files if path.name.endswith(".tar.gz")]
    portable = [path for path in files if path.name.endswith("-windows-x64.zip")]
    if len(wheels) != 1 or len(source_archives) != 1 or len(portable) != 1:
        raise SystemExit("release requires exactly one wheel, sdist, and Windows portable ZIP")
    expected = f"unpacksort-{version}"
    if not wheels[0].name.startswith(f"{expected}-") or expected not in source_archives[0].name:
        raise SystemExit("Python distribution version does not match the release")
    if portable[0].name != f"{expected}-windows-x64.zip":
        raise SystemExit("portable ZIP version does not match the release")
    if wheels[0].read_bytes()[:2] != b"PK" or source_archives[0].read_bytes()[:2] != b"\x1f\x8b":
        raise SystemExit("Python release artifact type mismatch")
    with zipfile.ZipFile(portable[0]) as archive:
        expected_member = f"{expected}-windows-x64/unpacksort.exe"
        if archive.namelist() != [expected_member]:
            raise SystemExit("portable ZIP has an unexpected layout")
        if archive.read(expected_member)[:2] != b"MZ":
            raise SystemExit("portable ZIP does not contain a Windows executable")


def write_checksums(directory: Path, output: Path) -> None:
    lines: list[str] = []
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
        if path == output:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
