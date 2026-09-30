from __future__ import annotations

import json
import re
import runpy
import sys
from pathlib import Path

import pytest
import yaml


def test_release_requires_successful_quality_for_every_entry_point() -> None:
    workflow = _workflow("release.yml")
    triggers = workflow.get("on")
    assert isinstance(triggers, dict)
    assert "push" not in triggers
    assert triggers["workflow_run"] == {
        "workflows": ["Quality"],
        "types": ["completed"],
        "branches": ["main"],
    }
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    gate = jobs["quality-gate"]
    assert "github.event.workflow_run.conclusion == 'success'" in gate["if"]
    assert jobs["release-integrity"]["needs"] == "quality-gate"
    text = (Path(".github/workflows") / "release.yml").read_text(encoding="utf-8")
    assert "scripts/release_quality.py" in text
    assert "ref: ${{ needs.quality-gate.outputs.source_sha }}" in text
    assert text.index("Recheck current main") < text.index("Determine, commit, and tag")


@pytest.mark.parametrize(
    ("status", "conclusion", "sha", "allowed"),
    [
        ("completed", "success", "current", True),
        ("completed", "failure", "current", False),
        ("completed", "cancelled", "current", False),
        ("queued", None, "current", False),
        ("in_progress", None, "current", False),
        ("completed", "success", "stale", False),
    ],
)
def test_exact_quality_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    conclusion: str | None,
    sha: str,
    allowed: bool,
) -> None:
    payload = tmp_path / "runs.json"
    payload.write_text(
        json.dumps(
            [
                {
                    "workflow_runs": [
                        {
                            "id": 42,
                            "head_sha": sha,
                            "head_branch": "main",
                            "event": "push",
                            "head_repository": {"full_name": "fileworks/unpacksort"},
                            "status": status,
                            "conclusion": conclusion,
                        }
                    ]
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["release_quality.py", str(payload), "current", "fileworks/unpacksort"]
    )
    if allowed:
        runpy.run_path("scripts/release_quality.py", run_name="__main__")
        assert "Exact source SHA passed Quality run 42" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit, match="Quality gate refused release"):
            runpy.run_path("scripts/release_quality.py", run_name="__main__")


def _workflow(name: str) -> dict[str, object]:
    payload = yaml.safe_load((Path(".github/workflows") / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    if True in payload:  # PyYAML's YAML 1.1 resolver reads the Actions key as boolean.
        payload["on"] = payload.pop(True)
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
        "exfat-evidence",
        "dependency-audit",
        "docs-links",
    }
    for job_name in ("quality", "build"):
        job = jobs[job_name]
        matrix = job["strategy"]["matrix"]
        assert matrix["os"] == ["ubuntu-latest", "macos-latest", "windows-latest"]
        # Every version the package classifies, and only those. CI used to run
        # 3.12 and 3.14 while the classifiers named 3.12 alone — the matrix
        # tested more than the package promised, and `requires-python = ">=3.12"`
        # promised a 3.13 nothing ran.
        assert matrix["python"] == ["3.12", "3.13", "3.14"]
    text = (Path(".github/workflows") / "quality.yml").read_text(encoding="utf-8")
    assert "scripts/installed_e2e.py" in text
    assert "uv run pip-audit" in text


def test_the_exfat_evidence_job_treats_a_skip_as_a_failure() -> None:
    """The no-replace refusal is proven by a real exFAT volume or not at all.

    `hdiutil` can fail transiently, and the fixture then skips all three tests.
    pytest exits 0 on a skip, so without this the run stayed green with the
    evidence missing — the same silence `maintenance` broke for its branding
    job.
    """
    jobs = _workflow("quality.yml")["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["exfat-evidence"]

    assert job["runs-on"] == "macos-latest"
    body = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "TestNoHardLinksOnRealFilesystem" in body
    assert "skipped" in body
    assert "exit 1" in body


def test_scale_workflow_is_scheduled_without_skipping_main_quality_jobs() -> None:
    workflow = _workflow("scale.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"scale"}
    scale = jobs["scale"]
    assert scale["strategy"]["matrix"]["tier"] == [20000, 100000, 500000]

    quality = (Path(".github/workflows") / "quality.yml").read_text(encoding="utf-8")
    assert "github.event_name" not in quality


def test_release_workflow_publishes_only_after_artifact_e2e() -> None:
    workflow = _workflow("release.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    prepare = jobs["prepare-release"]
    assert set(prepare["needs"]) == {
        "release-integrity",
        "source-e2e",
        "windows-portable",
    }
    assert "environment" not in prepare
    assert jobs["github-release"]["environment"] == "github-release"
    assert jobs["pypi"]["environment"] == "pypi"
    assert jobs["homebrew"]["environment"] == "homebrew"
    assert jobs["winget"]["environment"] == "winget"
    assert jobs["pypi"]["permissions"]["id-token"] == "write"
    text = (Path(".github/workflows") / "release.yml").read_text(encoding="utf-8")
    assert 'vcs_release: "false"' in text
    assert "recover_tag" in text
    assert "git merge-base --is-ancestor" in text
    assert "actions/checkout@v7" in text[text.index("github-release:") :]
    assert "gh release create" in text
    assert text.index("scripts/installed_e2e.py") < text.index("gh release create")
    pypi_publish = re.search(r"pypa/gh-action-pypi-publish@[0-9a-f]{40}", text)
    assert pypi_publish is not None
    assert text.index("gh release create") < pypi_publish.start()
    assert "fileworks.unpacksort" in text
    assert "HOMEBREW_DISPATCH_ENABLED" in text


def test_actions_use_versioned_references_and_least_privilege() -> None:
    for path in sorted(Path(".github/workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "@main" not in text
        assert "@master" not in text
        workflow = yaml.safe_load(text)
        assert workflow["permissions"]["contents"] == "read"


def test_release_build_inputs_are_pinned_and_constrained() -> None:
    for name in ("quality.yml", "release.yml"):
        workflow = _workflow(name)
        jobs = workflow["jobs"]
        assert isinstance(jobs, dict)
        commands = [
            str(step.get("run", "")) for job in jobs.values() for step in job.get("steps", [])
        ]
        builds = [
            line
            for command in commands
            for line in command.splitlines()
            if re.search(r"\buv build\b", line)
        ]
        assert builds, f"{name}: no build entry points checked"
        assert all(
            "uv build --build-constraints build-constraints.txt" in line for line in builds
        ), builds
    pyproject = Path("pyproject.toml").read_text()
    assert 'requires = ["hatchling==1.31.0"]' in pyproject
    assert "python -m pip install uv==0.11.18" in pyproject
    assert "uv build --build-constraints build-constraints.txt" in pyproject
    assert Path("build-requirements.in").read_text() == "hatchling==1.31.0\n"
    assert Path("build-constraints.txt").read_text().splitlines() == [
        "hatchling==1.31.0",
        "packaging==26.3",
        "pathspec==1.1.1",
        "pluggy==1.6.0",
        "trove-classifiers==2026.6.1.19",
    ]
