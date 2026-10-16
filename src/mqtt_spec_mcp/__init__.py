"""Offline MCP server for the OASIS MQTT 3.1.1 and MQTT 5.0 specifications."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mqtt-spec-mcp")
except PackageNotFoundError:  # pragma: no cover - running from an unpacked tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
