"""Contracts for the gh-aw shared APM workflow boundary."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHARED_APM = ROOT / ".github" / "workflows" / "shared" / "apm.md"
TARGET_EXPRESSION = "${{ github.aw.import-inputs.target }}"
TOKEN_FALLBACK = (
    "${{ steps.token.outputs.token || secrets.GH_AW_PLUGINS_TOKEN || "
    "secrets.GH_AW_GITHUB_TOKEN || secrets.GITHUB_TOKEN }}"
)
_BUNDLE_STEP_PREFIXES = (
    "Restore APM",
    "Download APM bundle",
    "Build bundle",
    "Validate downloaded",
    "Normalise bundle",
)


def _frontmatter() -> dict:
    source = SHARED_APM.read_text(encoding="utf-8")
    _prefix, frontmatter, _body = source.split("---", 2)
    loaded = yaml.safe_load(frontmatter)
    assert isinstance(loaded, dict)
    return loaded


def _shared_apm_consumers() -> list[tuple[Path, dict]]:
    consumers: list[tuple[Path, dict]] = []
    workflows = ROOT / ".github" / "workflows"
    for path in workflows.glob("*.md"):
        source = path.read_text(encoding="utf-8")
        if "uses: shared/apm.md" not in source:
            continue
        _prefix, frontmatter, _body = source.split("---", 2)
        loaded = yaml.safe_load(frontmatter)
        for imported in loaded.get("imports", ()):
            if imported.get("uses") == "shared/apm.md":
                consumers.append((path, imported))
    return consumers


def _validate_step() -> dict:
    apm_prep = _frontmatter()["jobs"]["apm-prep"]
    return next(step for step in apm_prep["steps"] if step.get("name") == "Validate APM target")


def test_shared_apm_requires_an_explicit_target_without_a_default() -> None:
    target = _frontmatter()["import-schema"]["target"]

    assert target["type"] == "string"
    assert target["required"] is True
    assert "default" not in target
    assert "The value 'all' is not valid here" in target["description"]


def test_shared_apm_forwards_the_target_to_the_isolated_pack_action() -> None:
    apm_job = _frontmatter()["jobs"]["apm"]
    assert "apm-prep" in apm_job["needs"]
    pack = next(step for step in apm_job["steps"] if step.get("name") == "Pack APM packages")

    assert pack["uses"] == "microsoft/apm-action@v1.10.0"
    assert pack["with"]["isolated"] == "true"
    assert pack["with"]["target"] == TARGET_EXPRESSION


def test_shared_apm_fallback_token_has_current_repo_read_only() -> None:
    frontmatter = _frontmatter()
    apm_prep = frontmatter["jobs"]["apm-prep"]
    apm_job = frontmatter["jobs"]["apm"]
    pack = next(step for step in apm_job["steps"] if step.get("name") == "Pack APM packages")

    assert apm_prep["permissions"] == {}
    assert apm_job["permissions"] == {"contents": "read"}
    assert pack["env"]["GITHUB_TOKEN"] == TOKEN_FALLBACK
    token_steps = [
        step["name"] for step in apm_job["steps"] if "GITHUB_TOKEN" in step.get("env", {})
    ]
    assert token_steps == ["Pack APM packages"]
    assert all("GITHUB_TOKEN" not in step.get("env", {}) for step in frontmatter["steps"])


@pytest.mark.parametrize(
    ("target", "expected_code"),
    [
        ("copilot", 0),
        ("copilot,claude", 0),
        ("", 1),
        ("all", 1),
        ("ALL", 1),
        ("copilot, all", 1),
        ("copilot,ALL", 1),
    ],
)
def test_shared_apm_rejects_empty_or_cli_only_target(
    target: str,
    expected_code: int,
) -> None:
    validate = _validate_step()
    assert validate["env"]["AW_APM_TARGET"] == TARGET_EXPRESSION

    result = subprocess.run(
        ("bash", "-c", validate["run"]),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "AW_APM_TARGET": target},
    )

    assert result.returncode == expected_code


def test_in_repo_shared_apm_consumers_use_concrete_targets() -> None:
    for path, imported in _shared_apm_consumers():
        target = imported.get("with", {}).get("target")
        assert target, f"{path.name} omits required shared/apm target"
        targets = {item.strip().lower() for item in str(target).split(",")}
        assert "all" not in targets, path.name


def test_compiled_consumer_locks_carry_target_validation() -> None:
    for path, _imported in _shared_apm_consumers():
        lock = path.with_suffix(".lock.yml")
        source = lock.read_text(encoding="utf-8")
        compiled = yaml.safe_load(source)
        assert "Validate APM target" in source, f"{lock.name} is stale"
        assert "AW_APM_TARGET:" in source, lock.name
        assert compiled["jobs"]["apm-prep"]["permissions"] == {}
        assert compiled["jobs"]["apm"]["permissions"] == {"contents": "read"}


def test_compiled_locks_pin_every_external_action_by_sha() -> None:
    unpinned: list[str] = []
    workflows = ROOT / ".github" / "workflows"
    for lock in sorted(workflows.glob("*.lock.yml")):
        for line in lock.read_text(encoding="utf-8").splitlines():
            match = re.search(r"uses:\s*([^\s@]+)@(\S+)", line)
            if (
                match
                and "/" in match.group(1)
                and not re.fullmatch(r"[0-9a-f]{40}", match.group(2))
            ):
                unpinned.append(f"{lock.name}: {match.group(0).strip()}")

    assert not unpinned, unpinned


def test_compiled_locks_render_current_target_validation_body() -> None:
    expected = _validate_step()["run"].strip()
    for path, _imported in _shared_apm_consumers():
        lock = yaml.safe_load(path.with_suffix(".lock.yml").read_text(encoding="utf-8"))
        rendered = [
            step
            for job in lock["jobs"].values()
            for step in job.get("steps", ())
            if step.get("name") == "Validate APM target"
        ]
        assert rendered, f"{path.stem}.lock.yml is stale"
        assert all(step["run"].strip() == expected for step in rendered)


def test_compiled_locks_scope_token_cascade_to_pack_step() -> None:
    for path, _imported in _shared_apm_consumers():
        lock = yaml.safe_load(path.with_suffix(".lock.yml").read_text(encoding="utf-8"))
        apm_token_steps = [
            step.get("name")
            for step in lock["jobs"]["apm"]["steps"]
            if "GITHUB_TOKEN" in (step.get("env") or {})
        ]
        assert apm_token_steps == ["Pack APM packages"], path.name

        leaked = [
            step.get("name")
            for job in lock["jobs"].values()
            for step in job.get("steps", ())
            if str(step.get("name", "")).startswith(_BUNDLE_STEP_PREFIXES)
            and "GITHUB_TOKEN" in (step.get("env") or {})
        ]
        assert not leaked, f"{path.name}: {leaked}"
