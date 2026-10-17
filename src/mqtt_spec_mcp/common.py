"""Errors, result envelopes and the conflicting-version guard."""

from __future__ import annotations

import re
from typing import Any

from .versions import PINS, VersionId

OTHER_VERSION: dict[VersionId, VersionId] = {"3.1.1": "5.0", "5.0": "3.1.1"}

# "MQTT 5", "MQTT5.0", "MQTT v3.1.1", "MQTT Version 5.0", "MQTT-5". A trailing
# "-<digit>" means a requirement label (MQTT-3.1.2-4, MQTT-3-8.3-4), not a version.
_VERSION_MENTION = re.compile(
    r"\bmqtt(?:[\s_]*|\s*[-\u2010-\u2015]\s*)(?:version[\s_]*|v[\s_]*)?"
    r"(\d+(?:\.\d+)*)(?![\d.]*\d)(?!\s*[-\u2010-\u2015]\s*\d)",
    re.IGNORECASE,
)
_MENTION_TO_VERSION = {
    "5": "5.0",
    "5.0": "5.0",
    "5.0.0": "5.0",
    "3.1.1": "3.1.1",
    "3.1": "3.1",
    "3": "3",
}


class QueryError(Exception):
    """A query failure, rendered as an MCP tool error scoped to one version."""

    def __init__(self, version: VersionId, message: str) -> None:
        self.version = version
        self.detail = message
        pin = PINS[version]
        super().__init__(
            f"MQTT {version} only ({pin.source_url}): {message} "
            "No other version was queried."
        )


def envelope(version: VersionId) -> dict[str, Any]:
    pin = PINS[version]
    other = OTHER_VERSION[version]
    return {
        "specification": pin.title,
        "version": version,
        "scope": (
            f"MQTT {version} only. Do not apply this evidence, its section numbers, "
            f"requirement labels, fields or codes to MQTT {other} or any other version."
        ),
        "source": pin.source_url,
    }


def check_version_mentions(version: VersionId, field: str, text: str | None) -> None:
    """Reject free text that names an MQTT version other than the selected one."""
    if not text:
        return
    for match in _VERSION_MENTION.finditer(text):
        named = _MENTION_TO_VERSION.get(match.group(1).rstrip("."))
        if named is None or named == version:
            continue
        if named == "3" and version == "3.1.1":
            continue
        suggest = "3.1.1" if named == "3" else named
        if suggest in PINS:
            advice = (
                f"Remove the version from the text; if you need MQTT {suggest}, make a "
                f"separate call with version='{suggest}'."
            )
        elif named in PINS:
            advice = (
                f"Remove the version from the text; if you need MQTT {named}, make a "
                f"separate call with version='{named}'."
            )
        else:
            advice = (
                f"Remove the version from the text; MQTT {named} is not available "
                "from this server (only 3.1.1 and 5.0)."
            )
        raise QueryError(
            version,
            f"The {field} {text!r} mentions {match.group(0).strip()!r}, which "
            "conflicts "
            f"with the selected version {version}. {advice}",
        )
