"""server.json describes the package to the MCP Registry and must match it."""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mqtt_spec_mcp.server import SERVER_TITLE

ROOT = Path(__file__).resolve().parents[1]


def _server() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / "server.json").read_text("utf-8"))
    return data


def test_registry_versions_match_the_package() -> None:
    server = _server()
    expected = version("mqtt-spec-mcp")
    hint = "set both version fields in server.json to the pyproject.toml version"
    assert server["version"] == expected, hint
    (package,) = server["packages"]
    assert package["version"] == expected, hint


def test_registry_entry_points_at_this_pypi_package() -> None:
    server = _server()
    (package,) = server["packages"]
    assert server["title"] == SERVER_TITLE
    assert package["registryType"] == "pypi"
    assert package["identifier"] == "mqtt-spec-mcp"
    assert package["transport"] == {"type": "stdio"}
    # The registry verifies PyPI ownership by finding this line in the README.
    readme = (ROOT / "README.md").read_text("utf-8")
    assert f"mcp-name: {server['name']}" in readme
