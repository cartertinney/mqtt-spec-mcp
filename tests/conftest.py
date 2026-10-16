from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.server import MqttSpecService, ToolOutcome

Call = Callable[..., ToolOutcome]
JsonCall = Callable[..., dict[str, Any]]


@pytest.fixture(scope="session")
def service() -> MqttSpecService:
    return MqttSpecService()


@pytest.fixture(scope="session")
def engines(service: MqttSpecService) -> dict[str, SpecEngine]:
    return {str(version): engine for version, engine in service.engines.items()}


@pytest.fixture(scope="session")
def call(service: MqttSpecService) -> Call:
    def _call(tool: str, **arguments: Any) -> ToolOutcome:
        return service.call(tool, arguments)

    return _call


@pytest.fixture(scope="session")
def ok(call: Call) -> JsonCall:
    """Call a JSON tool and return the decoded result, failing on tool errors."""

    def _ok(tool: str, **arguments: Any) -> dict[str, Any]:
        outcome = call(tool, **arguments)
        assert not outcome.is_error, outcome.text
        data: dict[str, Any] = json.loads(outcome.text)
        return data

    return _ok


@pytest.fixture(scope="session")
def err(call: Call) -> Callable[..., str]:
    """Call a tool expecting an MCP tool error and return the message."""

    def _err(tool: str, **arguments: Any) -> str:
        outcome = call(tool, **arguments)
        assert outcome.is_error, outcome.text
        return outcome.text

    return _err
