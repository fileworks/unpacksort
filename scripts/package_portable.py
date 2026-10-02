"""Create a deterministic nested Windows x64 portable ZIP."""

from __future__ import annotations

import argparse
import os
import stat
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    executable = arguments.executable
    if executable.read_bytes()[:2] != b"MZ":
        raise SystemExit("portable input is not a Windows executable")
    root_name = f"unpacksort-{arguments.version}-windows-x64"
    output = arguments.output / f"{root_name}.zip"
    arguments.output.mkdir(parents=True, exist_ok=True)
    epoch = max(315_532_800, int(os.environ.get("SOURCE_DATE_EPOCH", "315532800")))
    timestamp = datetime.fromtimestamp(epoch, tz=UTC)
    date_time = (timestamp.year, timestamp.month, timestamp.day, 0, 0, 0)
    info = zipfile.ZipInfo(f"{root_name}/unpacksort.exe", date_time=date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    with zipfile.ZipFile(output, "w", strict_timestamps=True) as archive:
        archive.writestr(info, executable.read_bytes(), compresslevel=9)


if __name__ == "__main__":
    main()
