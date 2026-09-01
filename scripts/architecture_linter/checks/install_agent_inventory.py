"""Agent source admission and package-inventory architecture guard."""

from __future__ import annotations

from scripts.architecture_linter.checks.install_deployment_shared import (
    _facts_for,
    _present,
    _python_paths,
    _summary,
)
from scripts.architecture_linter.facts import FactsProvider
from scripts.architecture_linter.models import Violation

_GUARD_AGENT_SOURCE_INVENTORY = "install-deployment-agent-source-inventory"
_OWNER = "src/apm_cli/integration/agent_integrator.py"
_PREPARATION = "src/apm_cli/install/primitive_integration.py"
_SERVICES = "src/apm_cli/install/services.py"
_OWNER_DEFINITIONS = frozenset({"_is_plain_md_agent", "prepare_agent_files"})


def check_agent_source_inventory(provider: FactsProvider) -> tuple[Violation, ...]:
    """Agent admission and inventory must route through AgentIntegrator."""
    rule_id = _GUARD_AGENT_SOURCE_INVENTORY
    owner, owner_fail = _facts_for(provider, _OWNER, rule_id)
    preparation, preparation_fail = _facts_for(provider, _PREPARATION, rule_id)
    services, services_fail = _facts_for(provider, _SERVICES, rule_id)
    if owner_fail or preparation_fail or services_fail:
        return tuple(list(owner_fail) + list(preparation_fail) + list(services_fail))

    definition_counts = dict.fromkeys(_OWNER_DEFINITIONS, 0)
    for path in _python_paths(provider, "src/apm_cli/"):
        facts, failures = _facts_for(provider, path, rule_id)
        if failures:
            return tuple(failures)
        for definition in facts.definitions:
            if definition.name in definition_counts:
                definition_counts[definition.name] += 1

    required_owner_fragments = (
        "files, _ignored = self._classify_agent_files(package_path)",
        "agent_files, ignored_resources = self._classify_agent_files(package_path)",
        "frontmatter = load_frontmatter(str(source)).metadata",
        'name = frontmatter.get("name")',
        'description = frontmatter.get("description")',
        "and bool(name.strip())",
        "and bool(description.strip())",
        "if agent_files is None:",
    )
    if (
        any(count != 1 for count in definition_counts.values())
        or any(not _present(owner, fragment) for fragment in required_owner_fragments)
        or not _present(preparation, '"agent_files": integrator.prepare_agent_files(')
        or not _present(services, "prepare_primitive_inputs as _prepare_primitive_inputs")
    ):
        return (
            _summary(
                rule_id,
                _OWNER,
                "Agent admission and inventory must route through AgentIntegrator",
            ),
        )
    return ()


__all__ = ["_GUARD_AGENT_SOURCE_INVENTORY", "check_agent_source_inventory"]
