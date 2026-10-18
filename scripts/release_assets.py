"""Verify immutable release candidates and generate their checksums."""

from __future__ import annotations

import argparse
import hashlib
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


def _candidate_files(directory: Path) -> list[Path]:
    """Return release files, skipping hidden entries such as uv's `dist/.gitignore`."""
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(directory).parts)
    )


def verify_assets(directory: Path, version: str) -> None:
    files = _candidate_files(directory)
    wheels = [path for path in files if path.suffix == ".whl"]
    source_archives = [path for path in files if path.name.endswith(".tar.gz")]
    if (
        len(wheels) != 1
        or len(source_archives) != 1
        or len(files) != len(wheels) + len(source_archives)
    ):
        raise SystemExit("release requires exactly one wheel and one sdist")
    expected = f"unpacksort-{version}"
    if not wheels[0].name.startswith(f"{expected}-") or expected not in source_archives[0].name:
        raise SystemExit("Python distribution version does not match the release")
    if wheels[0].read_bytes()[:2] != b"PK" or source_archives[0].read_bytes()[:2] != b"\x1f\x8b":
        raise SystemExit("Python release artifact type mismatch")


def write_checksums(directory: Path, output: Path) -> None:
    lines: list[str] = []
    for path in _candidate_files(directory):
        if path == output:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
