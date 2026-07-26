"""Exercise a clean installed command against deterministic generated fixtures."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True, type=Path)
    arguments = parser.parse_args()
    command = arguments.command.resolve()
    if not command.is_file():
        raise SystemExit(f"installed command does not exist: {command}")
    subprocess.run([str(command), "--help"], check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory(prefix="unpacksort-e2e-") as temporary:
        root = Path(temporary)
        source = root / "input"
        destination = root / "output"
        source.mkdir()
        (source / "a.txt").write_bytes(b"duplicate")
        (source / "duplicate.txt").write_bytes(b"duplicate")
        with zipfile.ZipFile(source / "nested.zip", "w") as archive:
            archive.writestr("inside/data.json", '{"portable":true}')
        environment = dict(os.environ, PYTHONHASHSEED="0")
        first = subprocess.run(
            [str(command), str(source), str(destination), "--flatten"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if first.returncode != 0:
            raise SystemExit(f"installed E2E failed:\n{first.stdout}\n{first.stderr}")
        expected = {
            "documents/a.txt": b"duplicate",
            "data/data.json": b'{"portable":true}',
        }
        for relative, payload in expected.items():
            if (destination / relative).read_bytes() != payload:
                raise SystemExit(f"unexpected installed output: {relative}")
        if (destination / "documents" / "duplicate.txt").exists():
            raise SystemExit("duplicate content was published more than once")
        manifest = destination / "manifest.jsonl"
        report = destination / "report.txt"
        before = (manifest.read_bytes(), report.read_bytes())
        records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
        occurrences = [record for record in records if record["record_type"] == "occurrence"]
        if {record["status"] for record in occurrences} < {"published", "duplicate"}:
            raise SystemExit("manifest is missing published or duplicate provenance")
        second = subprocess.run(
            [str(command), str(source), str(destination), "--flatten"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if second.returncode != 0 or before != (manifest.read_bytes(), report.read_bytes()):
            raise SystemExit("resume changed deterministic installed-artifact output")


if __name__ == "__main__":
    main()
