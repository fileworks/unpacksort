"""Render the reviewed WinGet bootstrap templates without YAML dependencies."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    replacements = {
        "{{VERSION}}": arguments.version,
        "{{URL}}": arguments.url,
        "{{SHA256}}": arguments.sha256.upper(),
    }
    source = Path("packaging/winget")
    arguments.output.mkdir(parents=True, exist_ok=True)
    for template in sorted(source.glob("*.yaml.in")):
        text = template.read_text(encoding="utf-8")
        for marker, value in replacements.items():
            text = text.replace(marker, value)
        if "{{" in text or "}}" in text:
            raise SystemExit(f"unresolved placeholder in {template}")
        (arguments.output / template.name.removesuffix(".in")).write_text(
            text,
            encoding="utf-8",
            newline="\n",
        )


if __name__ == "__main__":
    main()
