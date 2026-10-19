"""Direct dependencies are exact pins so Renovate's lockfile updates stay inside the soak.

`uv lock --upgrade-package <name>` resolves the newest release a range allows.
Renovate offers only releases older than its seven-day minimum release age, so
a range lets the resolver pick a newer, still-soaking release, and Renovate
fails the branch with an artifact error. An exact pin makes the resolver
install exactly the version Renovate offered, and pipx installs get the
versions CI tested. Deliberate sub-major caps (such as `<2.5`) stay ranges.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
# `name[extras]==1.2.3`: one exact release, no wildcard, no second clause.
_EXACT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?==[0-9][0-9A-Za-z.+!]*")
# The shared Renovate policy's deliberate-cap pattern; `<9`-style major caps are routine.
_DELIBERATE_CAP = re.compile(r"<=?\s*\d+(?:\.0)*\.0*[1-9]")


def _requirements() -> list[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(pyproject["project"].get("dependencies", []))
    for group in pyproject["project"].get("optional-dependencies", {}).values():
        specs.extend(group)
    for group in pyproject.get("dependency-groups", {}).values():
        specs.extend(item for item in group if isinstance(item, str))
    return [spec.split(";", maxsplit=1)[0].replace(" ", "") for spec in specs]


def test_dependencies_are_pinned_or_deliberately_capped() -> None:
    requirements = _requirements()
    loose = [
        item
        for item in requirements
        if not _EXACT.fullmatch(item) and not _DELIBERATE_CAP.search(item)
    ]
    assert requirements, "no dependencies found; the manifest shape changed"
    assert loose == []
