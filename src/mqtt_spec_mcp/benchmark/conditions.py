"""The four reference-access conditions compared by the agent benchmark.

Every condition shares the same base prompt, case prompts, loop and limits; they
differ only in how the model reaches the specification text for the version(s) a
case is about (MQTT 3.1.1, MQTT 5.0, or both for comparison cases):

- ``none``: no retrieval at all.
- ``full_spec``: the whole text of the case's specification(s) in the system prompt.
- ``grep``: a generic plain-text line search plus a bounded line reader over the
  same text (at most 80 lines per read, explicit next-line pagination, no MQTT
  catalogs).
- ``mcp``: this server's four tools, served by an instance holding only the case's
  version(s), so every condition sees the same material (calls for any other
  version fail).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.loader import load_index
from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS, MqttSpecService
from mqtt_spec_mcp.versions import SUPPORTED_VERSIONS, VersionId

Condition = Literal["none", "full_spec", "grep", "mcp"]
CONDITIONS: tuple[Condition, ...] = ("none", "full_spec", "grep", "mcp")
MAX_TOOL_CALLS = 12
MAX_READ_LINES = 80
MAX_GREP_RESULTS = 30
GREP_LINE_CHARS = 200

BASE_PROMPT = """\
You answer questions about the OASIS MQTT specifications - MQTT Version 3.1.1 \
(OASIS Standard, 29 October 2014) and MQTT Version 5.0 (OASIS Standard, 7 March \
2019) - for engineers implementing MQTT clients and servers.

Rules for every answer:
- Answer for the MQTT version(s) the question names, and never apply one version's \
rules, fields or codes to the other.
- Be concise: at most {word_limit} words.
- Keep normative strength exactly (MUST, MUST NOT, SHOULD, MAY) and state the \
conditions that apply.
- Cite the specification section number for each claim, for example "(section \
3.1.2.4)"; when the question involves both versions, name the version with each \
citation.
- If you are unsure, say so rather than guessing.
"""

CONDITION_PROMPTS: dict[Condition, str] = {
    "none": "You have no reference material or tools; answer from your own knowledge.",
    "full_spec": (
        "The complete text of the relevant MQTT specification(s) follows between the "
        "markers. Use it as your reference.\n<<<SPECIFICATION\n{spec}\nSPECIFICATION>>>"
    ),
    "grep": (
        "You can consult the text of the relevant MQTT specification(s) with two "
        "tools: spec_grep finds matching lines, and spec_read returns up to 80 lines "
        "starting at a line number. Each specification starts with a line '# MQTT "
        "Version ...', and section headings look like '## 3.1.2.4 Clean Start'. Use "
        f"at most {MAX_TOOL_CALLS} tool calls, then answer."
    ),
    "mcp": (
        "You can consult the MQTT specifications through the mqtt_spec_* tools. Pass "
        "the version the question asks about ('3.1.1' or '5.0') on every call; for a "
        "question about both versions, query each version separately. Use at most "
        f"{MAX_TOOL_CALLS} tool calls, then answer.\n\nTool guidance:\n{INSTRUCTIONS}"
    ),
}


def user_message(prompt: str, versions: Sequence[str]) -> str:
    """The question as sent: single-version questions are labelled with their
    version, as a user of a multi-version tool would; comparisons name both."""
    if len(versions) == 1:
        return f"[MQTT {versions[0]}] {prompt}"
    return prompt


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    chars: int
    latency_ms: float
    is_error: bool


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], tuple[str, bool]]


@dataclass
class ToolSession:
    """Per-run tool state: shared call budget and telemetry for one case."""

    tools: list[ToolSpec]
    max_calls: int = MAX_TOOL_CALLS
    calls: list[ToolCall] = field(default_factory=list)

    def invoke(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        start = time.perf_counter()
        spec = next((t for t in self.tools if t.name == name), None)
        if len(self.calls) >= self.max_calls:
            text, is_error = (
                f"Tool budget exhausted ({self.max_calls} calls). Answer now with the "
                "evidence you already have.",
                True,
            )
        elif spec is None:
            text, is_error = f"Unknown tool {name!r}.", True
        else:
            text, is_error = spec.handler(arguments)
        latency = (time.perf_counter() - start) * 1000
        self.calls.append(ToolCall(name, dict(arguments), len(text), latency, is_error))
        return text, is_error


def _versions(versions: Sequence[str]) -> tuple[VersionId, ...]:
    """Normalize to supported versions in a stable order."""
    chosen = tuple(v for v in SUPPORTED_VERSIONS if v in set(versions))
    if not chosen or len(chosen) != len(set(versions)):
        raise ValueError(f"unsupported versions {list(versions)!r}")
    return chosen


# Specification text ----------------------------------------------------------------


@cache
def _engine(version: VersionId) -> SpecEngine:
    return SpecEngine(load_index(version))


@cache
def render_spec_text(version: VersionId) -> str:
    """Plain-text rendering of every section of one version."""
    engine = _engine(version)
    parts = [f"# {engine.pin.title} ({engine.pin.source_url})"]
    for info in engine.ordered:
        parts.append(f"{'#' * min(6, info.section.level + 1)} {info.id} {info.title}")
        if info.body:
            parts.append(info.body)
    return "\n".join(parts)


@cache
def spec_text(versions: tuple[VersionId, ...]) -> str:
    return "\n\n".join(render_spec_text(v) for v in versions)


def system_prompt(
    condition: Condition, word_limit: int, versions: Sequence[str]
) -> str:
    extra = CONDITION_PROMPTS[condition]
    if condition == "full_spec":
        extra = extra.format(spec=spec_text(_versions(versions)))
    return BASE_PROMPT.format(word_limit=word_limit) + "\n" + extra


# Generic text tools (grep condition) -----------------------------------------------


def _grep(lines: Sequence[str], arguments: dict[str, Any]) -> tuple[str, bool]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return "pattern must be a non-empty string.", True
    limit = arguments.get("max_results", 20)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "max_results must be an integer from 1 to 50.", True
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error:
        regex = re.compile(re.escape(pattern), re.IGNORECASE)
    hits: list[str] = []
    total = 0
    for number, line in enumerate(lines, start=1):
        if regex.search(line):
            total += 1
            if len(hits) < min(limit, MAX_GREP_RESULTS):
                hits.append(f"{number}: {line[:GREP_LINE_CHARS]}")
    if not hits:
        return "No matching lines.", False
    more = (
        f"\n({total - len(hits)} more matches not shown)" if total > len(hits) else ""
    )
    return "\n".join(hits) + more, False


def _read(lines: Sequence[str], arguments: dict[str, Any]) -> tuple[str, bool]:
    start = arguments.get("start_line")
    count = arguments.get("max_lines", MAX_READ_LINES)
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return "start_line must be a positive integer.", True
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 80:
        return f"max_lines must be an integer from 1 to {MAX_READ_LINES}.", True
    if start > len(lines):
        return f"start_line is past the end (document has {len(lines)} lines).", True
    end = min(len(lines), start + count - 1)
    body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
    tail = f"next_line: {end + 1}" if end < len(lines) else "end of document"
    return f"{body}\n{tail}", False


GREP_PARAMETERS: dict[str, dict[str, Any]] = {
    "spec_grep": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression."},
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum matching lines to return (default 20).",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    "spec_read": {
        "type": "object",
        "properties": {
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "First line number to read.",
            },
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "maximum": 80,
                "description": "Lines to read (default and maximum 80).",
            },
        },
        "required": ["start_line"],
        "additionalProperties": False,
    },
}


def grep_tools(versions: Sequence[str]) -> list[ToolSpec]:
    lines = tuple(spec_text(_versions(versions)).split("\n"))
    return [
        ToolSpec(
            name="spec_grep",
            description=(
                "Search the specification text line by line with a case-insensitive "
                "regular expression; returns matching line numbers."
            ),
            parameters=GREP_PARAMETERS["spec_grep"],
            handler=lambda arguments: _grep(lines, arguments),
        ),
        ToolSpec(
            name="spec_read",
            description=(
                "Read up to 80 lines of the specification text starting at start_line. "
                "The result ends with next_line for continuation."
            ),
            parameters=GREP_PARAMETERS["spec_read"],
            handler=lambda arguments: _read(lines, arguments),
        ),
    ]


# MCP tools (pinned to the case's versions) -------------------------------------------


@cache
def _pinned_service(versions: tuple[VersionId, ...]) -> MqttSpecService:
    return MqttSpecService(versions)


def mcp_tools(versions: Sequence[str]) -> list[ToolSpec]:
    service = _pinned_service(_versions(versions))

    def handler(name: str) -> Callable[[dict[str, Any]], tuple[str, bool]]:
        def call(arguments: dict[str, Any]) -> tuple[str, bool]:
            outcome = service.call(name, arguments)
            return outcome.text, outcome.is_error

        return call

    return [
        ToolSpec(
            name=tool.name,
            description=tool.description or "",
            parameters=dict(tool.input_schema),
            handler=handler(tool.name),
        )
        for tool in TOOLS
    ]


def tools_for(condition: Condition, versions: Sequence[str]) -> list[ToolSpec]:
    if condition == "grep":
        return grep_tools(versions)
    if condition == "mcp":
        return mcp_tools(versions)
    return []
