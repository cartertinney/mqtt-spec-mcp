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


def parse_search(text: str) -> dict[str, Any]:
    """Read a Markdown evidence pack back into its parts (scope, source, sections
    with their excerpts as ``snippet``, best_section, next_step)."""
    header, _, body = text.partition("\n\n")
    scope, _, source = header.rpartition(" Source: ")
    chunks = ("\n\n" + body).split("\n\n## ")
    results: list[dict[str, Any]] = []
    next_step = body.strip() if len(chunks) == 1 else ""
    for position, chunk in enumerate(chunks[1:], start=1):
        heading, _, excerpt = chunk.partition("\n")
        if position == len(chunks) - 1:
            excerpt, _, next_step = excerpt.rpartition("\n\n")
        section, _, title = heading.partition(" ")
        results.append({"section": section, "title": title, "snippet": excerpt})
    return {
        "version": "5.0" if "/v5.0/" in source else "3.1.1",
        "scope": scope,
        "source": source,
        "results": results,
        "best_section": results[0]["section"] if results else None,
        "next_step": next_step,
    }


@pytest.fixture(scope="session")
def ok(call: Call) -> JsonCall:
    """Call a tool and return its decoded result, failing on tool errors. Search
    results (Markdown evidence packs) are parsed with ``parse_search``."""

    def _ok(tool: str, **arguments: Any) -> dict[str, Any]:
        outcome = call(tool, **arguments)
        assert not outcome.is_error, outcome.text
        if tool == "mqtt_spec_search":
            return parse_search(outcome.text)
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
