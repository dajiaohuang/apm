"""Shared auth policy across native runtime and literal-only MCP consumers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from apm_cli.adapters.client.copilot import CopilotClientAdapter
from apm_cli.adapters.client.intellij import IntelliJClientAdapter
from apm_cli.adapters.client.windsurf import WindsurfClientAdapter
from apm_cli.integration.mcp_integrator import MCPIntegrator
from apm_cli.models.dependency.mcp import MCPDependency

pytestmark = pytest.mark.component


@pytest.mark.parametrize(
    "adapter_class,automatic",
    [
        (CopilotClientAdapter, "Bearer ${GITHUB_TOKEN}"),
        (IntelliJClientAdapter, "Bearer ${env:GITHUB_TOKEN}"),
        (WindsurfClientAdapter, "Bearer ambient-sentinel"),
    ],
)
@pytest.mark.parametrize("explicit", [None, "", "static-auth", "${env:USER_PAT}"])
def test_shared_policy_preserves_registry_fallback_and_manifest_precedence(
    tmp_path: Path, adapter_class: type[CopilotClientAdapter], automatic: str, explicit: str | None
) -> None:
    """Real model/overlay/formatter consumers agree on the single header winner."""
    declaration = {"name": "github-mcp-server"}
    if explicit is not None:
        declaration["headers"] = {"authorization": explicit}
    dep = MCPDependency.from_dict(declaration)
    remote = {
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": [
            {"name": "Authorization", "value": "registry-default"},
            {"name": "X-Other", "value": "preserved"},
        ],
    }
    info = {"name": dep.name, "remotes": [remote]}
    MCPIntegrator._apply_overlay({dep.name: info}, dep)
    with patch.dict(
        os.environ,
        {
            "HOME": str(tmp_path),
            "APM_HOME": str(tmp_path / ".apm"),
            "GITHUB_TOKEN": "ambient-sentinel",
            "USER_PAT": "user-sentinel",
        },
        clear=True,
    ):
        config = adapter_class()._format_server_config(info)
    expected = automatic
    if explicit == "static-auth":
        expected = explicit
    elif explicit:
        expected = {
            CopilotClientAdapter: "${USER_PAT}",
            IntelliJClientAdapter: "${env:USER_PAT}",
            WindsurfClientAdapter: "user-sentinel",
        }[adapter_class]
    assert [
        value for name, value in config["headers"].items() if name.casefold() == "authorization"
    ] == [expected]
    assert config["headers"]["X-Other"] == "preserved"


@pytest.mark.parametrize(
    "name,url",
    [
        ("other-server", "https://api.githubcopilot.com/mcp/"),
        ("github", "https://github.com.evil.invalid/mcp/"),
        ("github", "http://api.github.com/mcp/"),
    ],
)
def test_unadmitted_servers_do_not_consult_auth_authority(
    tmp_path: Path, name: str, url: str
) -> None:
    """Both the server-name and secure-host restrictions precede credential selection."""
    with (
        patch.dict(os.environ, {"HOME": str(tmp_path)}, clear=True),
        patch("apm_cli.core.auth.AuthResolver") as resolver,
    ):
        config = CopilotClientAdapter()._format_server_config(
            {"name": name, "remotes": [{"url": url}]}
        )
    assert config.get("headers", {}) == {}
    resolver.assert_not_called()
