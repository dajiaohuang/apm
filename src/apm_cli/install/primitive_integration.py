"""Package-scoped preparation and presentation for primitive integration."""

from __future__ import annotations

from typing import Any


def prepare_primitive_inputs(
    primitive_name: str,
    integrator: Any,
    package_info: Any,
    targets: Any,
    diagnostics: Any,
    source_plan: Any,
) -> dict[str, Any]:
    """Prepare package-scoped inputs reused across target integrations."""
    if primitive_name != "agents" or not any(
        target.primitives.get("agents") is not None for target in targets
    ):
        return {}
    return {
        "agent_files": integrator.prepare_agent_files(
            package_info.install_path,
            package_info.package.name,
            diagnostics,
            source_plan,
        )
    }


def emit_integration_hints(primitive_name: str, info: dict, log_integration) -> None:
    """Emit user actions that follow successful primitive integration."""
    if any(path.startswith("copilot-app/") for path in info["paths"]) and info["files"] > 0:
        log_integration(
            "  |-- workflows arrive disabled; enable from the Copilot App's Workflows tab"
        )
    if primitive_name == "canvas" and (info["files"] > 0 or info["adopted"] > 0):
        log_integration("  |-- reload the Copilot session (/clear) or restart to load the canvas")
