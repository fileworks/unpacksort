from __future__ import annotations

from pathlib import Path

import yaml


def _workflow(name: str) -> dict[str, object]:
    payload = yaml.safe_load((Path(".github/workflows") / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_quality_workflow_has_cross_platform_version_and_artifact_gates() -> None:
    workflow = _workflow("quality.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    # An exact set rather than a subset: a gate that silently disappears is the
    # failure this test exists to catch, so adding one is a deliberate edit here.
    assert set(jobs) == {
        "quality",
        "build",
        "dependency-audit",
        "docs-links",
    }
    for job_name in ("quality", "build"):
        job = jobs[job_name]
        matrix = job["strategy"]["matrix"]
        assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
        assert matrix["python"] == ["3.12", "3.14"]
    text = (Path(".github/workflows") / "quality.yml").read_text(encoding="utf-8")
    assert "scripts/installed_e2e.py" in text
    assert "uv run pip-audit" in text


def test_release_workflow_publishes_only_after_artifact_e2e() -> None:
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    publish = jobs["publish"]
    assert set(publish["needs"]) == {
        "release-integrity",
        "source-e2e",
        "windows-portable",
    }
    assert publish["environment"] == "pypi"
    assert publish["permissions"]["id-token"] == "write"
    text = (Path(".github/workflows") / "release.yml").read_text(encoding="utf-8")
    assert 'vcs_release: "false"' in text
    assert "gh release create" in text
    assert text.index("scripts/installed_e2e.py") < text.index("gh release create")
    assert "fileworks.unpacksort" in text
    assert "HOMEBREW_DISPATCH_ENABLED" in text


def test_actions_use_versioned_references_and_least_privilege() -> None:
    for path in sorted(Path(".github/workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "@main" not in text
        assert "@master" not in text
        workflow = yaml.safe_load(text)
        assert workflow["permissions"]["contents"] == "read"
