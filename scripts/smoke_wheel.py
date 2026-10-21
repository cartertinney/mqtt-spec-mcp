"""Run with a wheel-only environment's Python from outside the checkout."""

from __future__ import annotations

import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

from mqtt_spec_mcp.loader import load_index
from mqtt_spec_mcp.versions import PINS, SUPPORTED_VERSIONS


async def smoke() -> None:
    package = distribution("mqtt-spec-mcp")
    files = [str(path) for path in package.files or ()]
    assert files, "installed distribution has no file manifest"
    assert not any("benchmark" in path for path in files)
    scripts = [e.name for e in package.entry_points if e.group == "console_scripts"]
    assert scripts == ["mqtt-spec-mcp"], scripts
    assert not any(e.group == "inspect_ai" for e in package.entry_points)
    assert not any("extra ==" in req for req in package.requires or ())
    assert Path.cwd() != Path(__file__).resolve().parents[1]
    notices = [path for path in package.files or () if path.name == "NOTICE"]
    assert len(notices) == 1, "wheel must include the OASIS notice"
    notice = package.locate_file(notices[0]).read_text()
    for version in SUPPORTED_VERSIONS:
        assert load_index(version).metadata.version == version
        assert PINS[version].source_url in notice
        assert PINS[version].copyright in notice

    executable = shutil.which("mqtt-spec-mcp", path=str(Path(sys.executable).parent))
    assert executable is not None, "wheel did not install the server command"
    params = StdioServerParameters(command=executable)
    async with Client(params, mode="legacy") as client:
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == {
            "mqtt_spec_outline",
            "mqtt_spec_search",
            "mqtt_spec_get",
            "mqtt_spec_lookup",
        }
        for version in SUPPORTED_VERSIONS:
            result = await client.call_tool("mqtt_spec_outline", {"version": version})
            assert not result.is_error
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, TextContent)
            assert json.loads(content.text)["version"] == version
        invalid = await client.call_tool("mqtt_spec_outline", {"version": "latest"})
        assert invalid.is_error
    print("Wheel smoke test passed: both indexes, console entry point, MCP isolation.")


if __name__ == "__main__":
    anyio.run(smoke)
