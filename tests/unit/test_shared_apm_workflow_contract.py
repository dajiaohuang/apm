"""Contracts for the gh-aw shared APM workflow boundary."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHARED_APM = ROOT / ".github" / "workflows" / "shared" / "apm.md"
VERIFY_SHARED_APM = ROOT / ".github" / "workflows" / "verify-shared-apm-matrix.yml"
TARGET_EXPRESSION = "${{ github.aw.import-inputs.target }}"
PACK_TOKEN_OUTPUT = "${{ steps.package-token.outputs.token }}"
APM_ACTION_SHA = "d723bb64ed70c135bbaf87d126b721dd2dae0439"
BUILTIN_TOKEN = "${{ matrix.group.token-source == 'github-token' && github.token || '' }}"
APP_TOKEN = "${{ matrix.group.token-source == 'app' && steps.token.outputs.token || '' }}"
CASCADE_TOKEN = (
    "${{ matrix.group.token-source == 'cascade' && secrets.GH_AW_PLUGINS_TOKEN "
    "|| matrix.group.token-source == 'cascade' && secrets.GH_AW_GITHUB_TOKEN "
    "|| matrix.group.token-source == 'cascade' && secrets.GITHUB_TOKEN || '' }}"
)
HAS_PLUGINS_TOKEN = (
    "${{ matrix.group.token-source == 'cascade' && secrets.GH_AW_PLUGINS_TOKEN != '' }}"
)
HAS_GH_AW_TOKEN = (
    "${{ matrix.group.token-source == 'cascade' && secrets.GH_AW_GITHUB_TOKEN != '' }}"
)
_BUNDLE_STEP_PREFIXES = (
    "Restore APM",
    "Download APM bundle",
    "Build bundle",
    "Validate downloaded",
    "Normalise bundle",
)
_ROUTING_KEYS = {
    "id",
    "kind",
    "index",
    "owner",
    "repositories",
    "packages",
    "has-app",
    "token-source",
}
_CREDENTIAL_STEPS = {
    "Compute APM credential-group matrix",
    "Select APM package token",
    "Pack APM packages",
}
_CREDENTIAL_MARKERS = (
    "secrets.",
    "github.token",
    "steps.token.outputs",
    "steps.package-token.outputs",
    "import-inputs.apps",
    "import-inputs.private-key",
)
_JOB_CREDENTIAL_ENV = {
    "apm": {
        "AW_APM_LEGACY_APP_ID": "${{ github.aw.import-inputs.app-id }}",
        "AW_APM_LEGACY_PRIVATE_KEY": "${{ github.aw.import-inputs.private-key }}",
        "AW_APM_APPS": "${{ github.aw.import-inputs.apps }}",
    }
}


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


def _compute_step() -> dict:
    apm_prep = _frontmatter()["jobs"]["apm-prep"]
    return next(
        step
        for step in apm_prep["steps"]
        if step.get("name") == "Compute APM credential-group matrix"
    )


def _token_source_step() -> dict:
    apm_prep = _frontmatter()["jobs"]["apm-prep"]
    return next(
        step for step in apm_prep["steps"] if step.get("name") == "Validate APM token source"
    )


def _package_token_step() -> dict:
    apm_job = _frontmatter()["jobs"]["apm"]
    return next(step for step in apm_job["steps"] if step.get("name") == "Select APM package token")


def _run_compute_step(
    tmp_path: Path,
    *,
    packages: list[str] | None = None,
    packages_literal: str | None = None,
    apps: list[dict] | None = None,
    token_source: str,
    legacy_app_id: str = "",
) -> tuple[str, dict]:
    assert packages is None or packages_literal is None
    output = tmp_path / "github-output"
    compute = _compute_step()
    result = subprocess.run(
        ("bash", "-c", compute["run"]),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "AW_APM_PACKAGES": packages_literal
            if packages_literal is not None
            else json.dumps(packages or []),
            "AW_APM_APPS": json.dumps(apps or []),
            "AW_APM_LEGACY_APP_ID": legacy_app_id,
            "AW_APM_LEGACY_OWNER": "",
            "AW_APM_LEGACY_REPOS": "",
            "AW_APM_TOKEN_SOURCE": token_source,
            "GITHUB_OUTPUT": str(output),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    matrix_line = next(
        line.removeprefix("matrix=")
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("matrix=")
    )
    return matrix_line, json.loads(matrix_line)


def _code_lines(body: str) -> list[str]:
    return [line for line in body.strip().splitlines() if not line.strip().startswith("#")]


def test_shared_apm_requires_an_explicit_target_without_a_default() -> None:
    target = _frontmatter()["import-schema"]["target"]

    assert target["type"] == "string"
    assert target["required"] is True
    assert "default" not in target
    assert "The value 'all' is not valid here" in target["description"]


def test_shared_apm_token_source_defaults_to_compatible_cascade() -> None:
    token_source = _frontmatter()["import-schema"]["token-source"]

    assert token_source["type"] == "string"
    assert token_source["required"] is False
    assert token_source["default"] == "cascade"


def test_shared_apm_forwards_the_target_to_the_isolated_pack_action() -> None:
    apm_job = _frontmatter()["jobs"]["apm"]
    assert "apm-prep" in apm_job["needs"]
    pack = next(step for step in apm_job["steps"] if step.get("name") == "Pack APM packages")

    assert pack["uses"] == "microsoft/apm-action@v1.10.0"
    assert pack["with"]["isolated"] == "true"
    assert pack["with"]["target"] == TARGET_EXPRESSION


def test_verify_workflow_exercises_apm_028_pack_and_multibundle_restore() -> None:
    workflow = yaml.safe_load(VERIFY_SHARED_APM.read_text(encoding="utf-8"))
    job = workflow["jobs"]["c-apm-028-action-compat"]
    action_ref = f"microsoft/apm-action@{APM_ACTION_SHA}"
    action_steps = [step for step in job["steps"] if step.get("uses") == action_ref]
    packs = [step for step in action_steps if step.get("with", {}).get("pack") == "true"]
    restore = next(step for step in action_steps if "bundles-file" in step.get("with", {}))

    assert len(packs) == 2
    assert {step["with"]["apm-version"] for step in action_steps} == {"0.28.0"}
    assert {step["with"]["target"] for step in packs} == {"copilot", "claude"}
    assert all(step["with"]["isolated"] == "true" for step in packs)
    assert all(step["with"]["archive"] == "true" for step in packs)
    assert restore["id"] == "restore"
    assert restore["with"]["bundles-file"] == "${{ steps.bundle-list.outputs.path }}"
    assert "steps.restore.outputs.bundles-restored" in VERIFY_SHARED_APM.read_text(encoding="utf-8")


def test_shared_apm_fallback_token_has_current_repo_read_only() -> None:
    frontmatter = _frontmatter()
    apm_prep = frontmatter["jobs"]["apm-prep"]
    apm_job = frontmatter["jobs"]["apm"]
    pack = next(step for step in apm_job["steps"] if step.get("name") == "Pack APM packages")

    assert apm_prep["permissions"] == {}
    assert apm_job["permissions"] == {"contents": "read"}
    assert pack["env"] == {
        "GH_TOKEN": PACK_TOKEN_OUTPUT,
        "GITHUB_APM_PAT": PACK_TOKEN_OUTPUT,
        "GITHUB_TOKEN": PACK_TOKEN_OUTPUT,
    }
    selector = _package_token_step()
    assert selector["env"] == {
        "ROW_TOKEN_SOURCE": "${{ matrix.group.token-source }}",
        "BUILTIN_TOKEN": BUILTIN_TOKEN,
        "APP_TOKEN": APP_TOKEN,
        "CASCADE_TOKEN": CASCADE_TOKEN,
        "HAS_PLUGINS_TOKEN": HAS_PLUGINS_TOKEN,
        "HAS_GH_AW_TOKEN": HAS_GH_AW_TOKEN,
    }
    token_steps = [
        step["name"] for step in apm_job["steps"] if "GITHUB_TOKEN" in step.get("env", {})
    ]
    assert token_steps == ["Pack APM packages"]
    assert all("GITHUB_TOKEN" not in step.get("env", {}) for step in frontmatter["steps"])


@pytest.mark.parametrize(
    (
        "token_source",
        "builtin_token",
        "app_token",
        "cascade_token",
        "expected_token",
        "expected_code",
    ),
    [
        ("cascade", "builtin", "", "cascade", "cascade", 0),
        ("github-token", "builtin", "", "cascade", "builtin", 0),
        ("app", "builtin", "app", "cascade", "app", 0),
        ("github-token", "", "", "cascade", None, 1),
        ("app", "builtin", "", "cascade", None, 1),
        ("cascade", "builtin", "", "line1\nline2", None, 1),
        ("cascade", "builtin", "", "line1\rline2", None, 1),
        ("", "builtin", "app", "cascade", None, 1),
        ("pat", "builtin", "app", "cascade", None, 1),
        ("CASCADE", "builtin", "app", "cascade", None, 1),
    ],
)
def test_shared_apm_token_selection_has_no_cross_identity_fallback(
    tmp_path: Path,
    token_source: str,
    builtin_token: str,
    app_token: str,
    cascade_token: str,
    expected_token: str | None,
    expected_code: int,
) -> None:
    output = tmp_path / "github-output"
    selector = _package_token_step()
    result = subprocess.run(
        ("bash", "-c", selector["run"]),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "ROW_TOKEN_SOURCE": token_source,
            "BUILTIN_TOKEN": builtin_token,
            "APP_TOKEN": app_token,
            "CASCADE_TOKEN": cascade_token,
            "HAS_PLUGINS_TOKEN": "false",
            "HAS_GH_AW_TOKEN": "false",
            "GITHUB_OUTPUT": str(output),
        },
    )

    assert result.returncode == expected_code
    if expected_token is None:
        assert not output.exists()
    else:
        assert output.read_text(encoding="utf-8") == f"token={expected_token}\n"
        assert f"::add-mask::{expected_token}" in result.stdout


@pytest.mark.parametrize(
    ("has_plugins", "has_gh_aw", "expected_tier"),
    [
        ("true", "true", "cascade:GH_AW_PLUGINS_TOKEN"),
        ("false", "true", "cascade:GH_AW_GITHUB_TOKEN"),
        ("false", "false", "cascade:GITHUB_TOKEN"),
    ],
)
def test_shared_apm_reports_nonsecret_cascade_tier(
    tmp_path: Path,
    has_plugins: str,
    has_gh_aw: str,
    expected_tier: str,
) -> None:
    output = tmp_path / "github-output"
    result = subprocess.run(
        ("bash", "-c", _package_token_step()["run"]),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "ROW_TOKEN_SOURCE": "cascade",
            "BUILTIN_TOKEN": "",
            "APP_TOKEN": "",
            "CASCADE_TOKEN": "selected",
            "HAS_PLUGINS_TOKEN": has_plugins,
            "HAS_GH_AW_TOKEN": has_gh_aw,
            "GITHUB_OUTPUT": str(output),
        },
    )

    assert result.returncode == 0
    assert f"APM package token source: {expected_tier}" in result.stdout
    assert "selected" not in result.stdout.replace("::add-mask::selected", "")


def test_only_selector_and_pack_steps_receive_credential_env() -> None:
    frontmatter = _frontmatter()
    steps = list(frontmatter.get("steps", ()))
    steps.extend(step for job in frontmatter["jobs"].values() for step in job.get("steps", ()))
    offenders = [
        f"{step.get('name')}:{key}"
        for step in steps
        if step.get("name") not in _CREDENTIAL_STEPS
        for key, value in (step.get("env") or {}).items()
        if any(marker in str(value) for marker in _CREDENTIAL_MARKERS)
    ]

    assert not offenders, offenders


def test_job_level_credential_relay_slots_are_frozen() -> None:
    for name, job in _frontmatter()["jobs"].items():
        assert (job.get("env") or {}) == _JOB_CREDENTIAL_ENV.get(name, {}), name


@pytest.mark.parametrize("token_source", ["cascade", "github-token"])
def test_shared_apm_routes_no_app_token_source_explicitly(
    tmp_path: Path,
    token_source: str,
) -> None:
    packages = [
        "DevExpGbb/pharmacorp-agentic-software-factory/"
        "probes/apm-workspace/packages/factory-core#abc",
        "DevExpGbb/zava-agent-config/plugins/secure-baseline#456",
    ]

    _raw, matrix = _run_compute_step(
        tmp_path,
        packages=packages,
        token_source=token_source,
    )

    assert matrix == {
        "group": [
            {
                "id": "default",
                "kind": "default",
                "index": 0,
                "owner": "",
                "repositories": "",
                "packages": packages,
                "has-app": "false",
                "token-source": token_source,
            },
        ]
    }


def test_shared_apm_app_rows_keep_minted_token_precedence(tmp_path: Path) -> None:
    packages = ["DevExpGbb/private-package#abc"]

    _raw, matrix = _run_compute_step(
        tmp_path,
        packages=packages,
        token_source="github-token",
        legacy_app_id="12345",
    )

    assert matrix["group"] == [
        {
            "id": "legacy",
            "kind": "legacy",
            "index": 0,
            "owner": "",
            "repositories": "",
            "packages": packages,
            "has-app": "true",
            "token-source": "app",
        }
    ]


def test_shared_apm_app_array_rows_always_mint_regardless_of_selector(
    tmp_path: Path,
) -> None:
    apps = [
        {
            "id": "org1",
            "owner": "org1",
            "repositories": "a,b",
            "packages": ["org1/pkg"],
            "app-id": "111",
            "private-key": "-----BEGIN RSA PRIVATE KEY-----secret",
        }
    ]

    _raw, matrix = _run_compute_step(
        tmp_path,
        apps=apps,
        token_source="github-token",
    )

    assert [row["token-source"] for row in matrix["group"]] == ["app"]


def test_shared_apm_matrix_rows_carry_no_credential_material(
    tmp_path: Path,
) -> None:
    apps = [
        {
            "id": "org1",
            "owner": "org1",
            "repositories": "a,b",
            "packages": ["org1/pkg"],
            "app-id": "111",
            "private-key": "-----BEGIN RSA PRIVATE KEY-----secret",
        }
    ]

    raw, matrix = _run_compute_step(
        tmp_path,
        apps=apps,
        token_source="cascade",
    )

    assert all(set(row) == _ROUTING_KEYS for row in matrix["group"])
    assert "private-key" not in raw
    assert "PRIVATE KEY" not in raw
    assert "111" not in raw


def test_shared_apm_repairs_go_slice_formatted_packages(tmp_path: Path) -> None:
    _raw, matrix = _run_compute_step(
        tmp_path,
        packages_literal="[microsoft/apm#main other/pkg#v1]",
        token_source="github-token",
    )

    assert matrix["group"][0]["packages"] == [
        "microsoft/apm#main",
        "other/pkg#v1",
    ]
    assert matrix["group"][0]["token-source"] == "github-token"


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


@pytest.mark.parametrize(
    ("token_source", "expected_code"),
    [
        ("cascade", 0),
        ("github-token", 0),
        ("pat", 1),
        ("auto", 1),
    ],
)
def test_shared_apm_rejects_unknown_token_source(
    token_source: str,
    expected_code: int,
) -> None:
    validate = _token_source_step()
    result = subprocess.run(
        ("bash", "-c", validate["run"]),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "AW_APM_TOKEN_SOURCE": token_source},
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


def test_compiled_locks_render_token_source_routing() -> None:
    expected_validation = _token_source_step()["run"].strip()
    for path, _imported in _shared_apm_consumers():
        lock = yaml.safe_load(path.with_suffix(".lock.yml").read_text(encoding="utf-8"))
        validation = [
            step
            for job in lock["jobs"].values()
            for step in job.get("steps", ())
            if step.get("name") == "Validate APM token source"
        ]
        assert validation, f"{path.stem}.lock.yml is stale"
        assert all(step["run"].strip() == expected_validation for step in validation)

        compute = next(
            step
            for step in lock["jobs"]["apm-prep"]["steps"]
            if step.get("name") == "Compute APM credential-group matrix"
        )
        assert compute["env"]["AW_APM_TOKEN_SOURCE"] == "cascade"
        assert _code_lines(compute["run"]) == _code_lines(_compute_step()["run"])


def test_compiled_locks_scope_token_cascade_to_pack_step() -> None:
    for path, _imported in _shared_apm_consumers():
        lock = yaml.safe_load(path.with_suffix(".lock.yml").read_text(encoding="utf-8"))
        apm_token_steps = [
            step.get("name")
            for step in lock["jobs"]["apm"]["steps"]
            if "GITHUB_TOKEN" in (step.get("env") or {})
        ]
        assert apm_token_steps == ["Pack APM packages"], path.name
        pack = next(
            step for step in lock["jobs"]["apm"]["steps"] if step.get("name") == "Pack APM packages"
        )
        assert pack["env"] == {
            "GH_TOKEN": PACK_TOKEN_OUTPUT,
            "GITHUB_APM_PAT": PACK_TOKEN_OUTPUT,
            "GITHUB_TOKEN": PACK_TOKEN_OUTPUT,
        }
        selector = next(
            step
            for step in lock["jobs"]["apm"]["steps"]
            if step.get("name") == "Select APM package token"
        )
        assert selector["run"].strip() == _package_token_step()["run"].strip()
        assert selector["env"] == _package_token_step()["env"]
        credential_env_steps = [
            step.get("name")
            for step in lock["jobs"]["apm"]["steps"]
            if any(
                marker in str(value)
                for value in (step.get("env") or {}).values()
                for marker in ("secrets.", "github.token", "steps.token.outputs.token")
            )
        ]
        assert credential_env_steps == ["Select APM package token"]

        leaked = [
            step.get("name")
            for job in lock["jobs"].values()
            for step in job.get("steps", ())
            if str(step.get("name", "")).startswith(_BUNDLE_STEP_PREFIXES)
            and (
                {"GH_TOKEN", "GITHUB_APM_PAT", "GITHUB_TOKEN"} & set(step.get("env") or {})
                or any(
                    marker in str(value)
                    for value in (step.get("env") or {}).values()
                    for marker in _CREDENTIAL_MARKERS
                )
            )
        ]
        assert not leaked, f"{path.name}: {leaked}"
