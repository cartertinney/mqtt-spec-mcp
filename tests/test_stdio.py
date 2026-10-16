"""End-to-end test over a real stdio subprocess using the official MCP client."""

from __future__ import annotations

import json
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import TextContent

from mqtt_spec_mcp.render import page_summary


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _text(result: object) -> str:
    content = getattr(result, "content", [])
    assert len(content) == 1
    item = content[0]
    assert isinstance(item, TextContent)
    return item.text


@pytest.mark.anyio
async def test_stdio_handshake_and_isolation() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mqtt_spec_mcp"])
    async with Client(params, mode="legacy") as client:
        info = client.server_info
        assert info is not None
        assert info.name == "mqtt-spec-mcp"
        assert info.title == "MQTT Specification"
        assert client.instructions
        assert "version" in client.instructions

        tools = await client.list_tools()
        assert [t.name for t in tools.tools] == [
            "mqtt_spec_outline",
            "mqtt_spec_search",
            "mqtt_spec_get",
            "mqtt_spec_lookup",
        ]

        prop = await client.call_tool(
            "mqtt_spec_lookup", {"version": "5.0", "kind": "property", "query": "0x11"}
        )
        assert not prop.is_error
        assert json.loads(_text(prop))["name"] == "Session Expiry Interval"
        assert prop.structured_content is None

        ret = await client.call_tool(
            "mqtt_spec_lookup",
            {
                "version": "3.1.1",
                "kind": "return_code",
                "query": "1",
                "packet": "CONNACK",
            },
        )
        assert not ret.is_error
        assert "unacceptable protocol version" in _text(ret)

        args = {
            "version": "3.1.1",
            "section": "3.1",
            "include_subsections": True,
            "max_chars": 1000,
        }
        first = await client.call_tool("mqtt_spec_get", args)
        cursor = page_summary(_text(first))["cursor"]
        assert cursor

        replay = await client.call_tool(
            "mqtt_spec_get", {**args, "version": "5.0", "cursor": cursor}
        )
        assert replay.is_error
        assert "MQTT 5.0 only" in _text(replay)

        same = await client.call_tool("mqtt_spec_get", {**args, "cursor": cursor})
        assert not same.is_error
        assert "MQTT 3.1.1 ONLY" in _text(same)

        bad = await client.call_tool("mqtt_spec_outline", {"version": "latest"})
        assert bad.is_error
        assert "version" in _text(bad)
