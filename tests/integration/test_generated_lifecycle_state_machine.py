"""Bounded model-based lifecycle checks against the installed APM CLI."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from apm_cli.utils.yaml_io import load_yaml
from tests.integration.test_required_lifecycle_state_machine import (
    _INSTALL_ARGS,
    _audit,
    _new_scenario,
    _publish,
    _result_evidence,
    _run_success,
    _Scenario,
    _skill,
)
from tests.utils.artifact_snapshot import ArtifactSnapshotSet, assert_snapshot_set_unchanged
from tests.utils.local_package import LocalPackage

pytestmark = [
    pytest.mark.integration,
    pytest.mark.e2e,
    pytest.mark.lifecycle_smoke,
    pytest.mark.lifecycle_merge_group,
    pytest.mark.requires_apm_binary,
    pytest.mark.requires_e2e_mode,
]

_SKILL_NAME = "model-skill"
_PACKAGE_NAME = "model-kit"
_SKILL_BYTES = _skill(_SKILL_NAME).encode()
_TRANSITION_PROPERTIES = {
    "audit_clean": frozenset({"filesystem.open_world_observation", "outcome.status_matches_state"}),
    "audit_tampered": frozenset(
        {
            "filesystem.open_world_observation",
            "outcome.status_matches_state",
            "transaction.failed_command_preserves_state",
        }
    ),
    "install": frozenset({"routing.authorized_targets_only", "source.ref_cache_coherent"}),
    "prune_removed": frozenset({"ownership.preserve_unowned"}),
    "readd_declaration": frozenset({"transaction.failed_command_preserves_state"}),
    "reinstall": frozenset({"idempotency.byte_stable"}),
    "remove_declaration": frozenset({"ownership.preserve_unowned"}),
    "repair": frozenset({"source.ref_cache_coherent"}),
    "tamper": frozenset({"outcome.status_matches_state"}),
}
_PHASE_ONE_PROPERTIES = frozenset(
    {
        "filesystem.open_world_observation",
        "idempotency.byte_stable",
        "outcome.status_matches_state",
        "ownership.preserve_unowned",
        "routing.authorized_targets_only",
        "source.ref_cache_coherent",
        "transaction.failed_command_preserves_state",
    }
)


@dataclass(frozen=True)
class _ModelFixture:
    scenario: _Scenario
    project: LocalPackage
    dependency: dict[str, object]
    environment: dict[str, str]

    @classmethod
    def create(cls, root: Path, apm_binary_path: Path) -> _ModelFixture:
        scenario = _new_scenario(root, apm_binary_path)
        published = _publish(scenario, _PACKAGE_NAME, skill=_SKILL_NAME)
        project = scenario.consumers.create(
            "model-consumer",
            dependencies=(published.dependency,),
            targets=("copilot",),
        )
        return cls(
            scenario=scenario,
            project=project,
            dependency=published.dependency,
            environment=published.environment,
        )


class _LifecycleReferenceModel(RuleBasedStateMachine):
    """Reference state independent of lockfile deployment records."""

    def __init__(self, fixture: _ModelFixture) -> None:
        super().__init__()
        self.fixture = fixture
        self.declared = True
        self.materialized = False
        self.clean = True
        self.locked = False
        self.step = 0

    @property
    def scenario(self) -> _Scenario:
        return self.fixture.scenario

    @property
    def project(self) -> LocalPackage:
        return self.fixture.project

    @property
    def skill_path(self) -> Path:
        return self.project.root / ".agents" / "skills" / _SKILL_NAME / "SKILL.md"

    def _next_id(self, operation: str) -> str:
        self.step += 1
        return f"generated-{self.step:02d}-{operation}"

    def _capture(self) -> ArtifactSnapshotSet:
        return ArtifactSnapshotSet.capture(
            {
                "project": self.project.root,
                "user": self.scenario.isolated.home,
            }
        )

    @rule()
    @precondition(lambda self: self.declared and not self.materialized)
    def install(self) -> None:
        _run_success(
            self.scenario,
            self.project,
            _INSTALL_ARGS,
            environment=self.fixture.environment,
            scenario_id=self._next_id("install"),
        )
        self.materialized = True
        self.clean = True
        self.locked = True

    @rule()
    @precondition(lambda self: self.declared and self.materialized and self.clean)
    def reinstall(self) -> None:
        before = self._capture()
        _run_success(
            self.scenario,
            self.project,
            _INSTALL_ARGS,
            environment=self.fixture.environment,
            scenario_id=self._next_id("reinstall"),
        )
        assert_snapshot_set_unchanged(before, self._capture())

    @rule()
    @precondition(lambda self: self.materialized and self.clean)
    def tamper(self) -> None:
        self.skill_path.write_bytes(b"# user tamper\n")
        self.clean = False

    @rule()
    @precondition(lambda self: self.declared and self.materialized and not self.clean)
    def audit_tampered(self) -> None:
        before = self._capture()
        result, payload = _audit(
            self.scenario,
            self.project,
            environment=self.fixture.environment,
            expected_returncode=1,
            scenario_id=self._next_id("audit-tampered"),
        )
        assert payload["passed"] is False, _result_evidence(result)
        assert_snapshot_set_unchanged(before, self._capture())

    @rule()
    @precondition(lambda self: self.declared and self.materialized and not self.clean)
    def repair(self) -> None:
        _run_success(
            self.scenario,
            self.project,
            _INSTALL_ARGS,
            environment=self.fixture.environment,
            scenario_id=self._next_id("repair"),
        )
        self.clean = True

    @rule()
    @precondition(lambda self: self.declared and self.materialized and self.clean)
    def audit_clean(self) -> None:
        before = self._capture()
        _result, payload = _audit(
            self.scenario,
            self.project,
            environment=self.fixture.environment,
            scenario_id=self._next_id("audit-clean"),
        )
        assert payload["passed"] is True
        assert_snapshot_set_unchanged(before, self._capture())

    @rule()
    @precondition(lambda self: self.declared)
    def remove_declaration(self) -> None:
        assert self.scenario.consumers.remove_apm_dependency(
            self.project,
            self.fixture.dependency,
        )
        self.declared = False

    @rule()
    @precondition(lambda self: not self.declared)
    def readd_declaration(self) -> None:
        self.scenario.consumers.replace_apm_dependencies(
            self.project,
            (self.fixture.dependency,),
        )
        self.declared = True

    @rule()
    @precondition(lambda self: not self.declared and self.materialized)
    def prune_removed(self) -> None:
        _run_success(
            self.scenario,
            self.project,
            ("prune",),
            environment=self.fixture.environment,
            scenario_id=self._next_id("prune"),
        )
        self.materialized = False
        self.clean = True
        self.locked = False

    @invariant()
    def durable_state_matches_reference_model(self) -> None:
        manifest = load_yaml(self.project.manifest_path)
        dependencies = manifest.get("dependencies", {}).get("apm", [])
        assert bool(dependencies) is self.declared
        assert self.skill_path.exists() is self.materialized
        if self.materialized and self.clean:
            assert self.skill_path.read_bytes() == _SKILL_BYTES
        assert (self.project.root / "apm.lock.yaml").exists() is self.locked


def test_generated_lifecycle_sequences_preserve_reference_model(
    tmp_path: Path,
    apm_binary_path: Path,
) -> None:
    """Generate and shrink guarded transition sequences over a real CLI."""
    sequence = itertools.count()

    def factory() -> _LifecycleReferenceModel:
        case_root = tmp_path / f"case-{next(sequence):03d}"
        return _LifecycleReferenceModel(_ModelFixture.create(case_root, apm_binary_path))

    run_state_machine_as_test(
        factory,
        settings=settings(
            database=None,
            deadline=None,
            derandomize=True,
            max_examples=6,
            phases=(Phase.generate, Phase.shrink),
            stateful_step_count=8,
            suppress_health_check=(HealthCheck.too_slow,),
        ),
    )


def test_generated_transition_catalog_covers_phase_one_properties() -> None:
    """Keep generalized laws visibly attached to guarded model transitions."""
    rule_names = {
        name
        for name, value in vars(_LifecycleReferenceModel).items()
        if getattr(value, "hypothesis_stateful_rule", None) is not None
    }

    assert set(_TRANSITION_PROPERTIES) == rule_names
    assert set().union(*_TRANSITION_PROPERTIES.values()) == set(_PHASE_ONE_PROPERTIES)
