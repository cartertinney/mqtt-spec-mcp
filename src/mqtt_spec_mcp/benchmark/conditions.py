"""The four reference-access conditions compared by the agent benchmark.

Every condition shares the same base prompt, case prompts, loop and limits; they
differ only in what reference material the model can reach:

- ``none``: no retrieval at all.
- ``full_spec``: the whole MQTT 5.0 text in the system prompt.
- ``grep``: a generic plain-text line search plus a bounded line reader
  (at most 80 lines per read, explicit next-line pagination, no MQTT catalogs).
- ``mcp``: this server's four tools, served by an instance holding only the 5.0
  index (calls for any other version fail).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.loader import load_index
from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS, MqttSpecService

Condition = Literal["none", "full_spec", "grep", "mcp"]
CONDITIONS: tuple[Condition, ...] = ("none", "full_spec", "grep", "mcp")
BENCH_VERSION = "5.0"
MAX_TOOL_CALLS = 12
MAX_READ_LINES = 80
MAX_GREP_RESULTS = 30
GREP_LINE_CHARS = 200

BASE_PROMPT = """\
You answer questions about the OASIS MQTT Version 5.0 specification (OASIS Standard, \
7 March 2019) for engineers implementing MQTT clients and servers.

Rules for every answer:
- Answer about MQTT 5.0 only.
- Be concise: at most {word_limit} words.
- Keep normative strength exactly (MUST, MUST NOT, SHOULD, MAY) and state the \
conditions that apply.
- Cite the specification section number for each claim, for example "(section \
3.1.2.4)".
- If you are unsure, say so rather than guessing.
"""

CONDITION_PROMPTS: dict[Condition, str] = {
    "none": "You have no reference material or tools; answer from your own knowledge.",
    "full_spec": (
        "The complete MQTT 5.0 specification text follows between the markers. Use it "
        "as your reference.\n<<<SPECIFICATION\n{spec}\nSPECIFICATION>>>"
    ),
    "grep": (
        "You can consult the MQTT 5.0 specification text with two tools: spec_grep "
        "finds matching lines, and spec_read returns up to 80 lines starting at a line "
        "number. Section headings look like '## 3.1.2.4 Clean Start'. Use at most "
        f"{MAX_TOOL_CALLS} tool calls, then answer."
    ),
    "mcp": (
        "You can consult the MQTT 5.0 specification through the mqtt_spec_* tools. "
        "Always pass version '5.0'. Use at most "
        f"{MAX_TOOL_CALLS} tool calls, then answer.\n\nTool guidance:\n{INSTRUCTIONS}"
    ),
}


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


# Full specification text --------------------------------------------------------


@cache
def _engine() -> SpecEngine:
    return SpecEngine(load_index(BENCH_VERSION))


def render_spec_text(engine: SpecEngine | None = None) -> str:
    """Plain-text rendering of every section, used for full_spec and grep."""
    engine = engine or _engine()
    parts = [f"# {engine.pin.title} ({engine.pin.source_url})"]
    for info in engine.ordered:
        parts.append(f"{'#' * min(6, info.section.level + 1)} {info.id} {info.title}")
        if info.body:
            parts.append(info.body)
    return "\n".join(parts)


@cache
def spec_lines() -> tuple[str, ...]:
    return tuple(render_spec_text().split("\n"))


def system_prompt(condition: Condition, word_limit: int) -> str:
    extra = CONDITION_PROMPTS[condition]
    if condition == "full_spec":
        extra = extra.format(spec=render_spec_text())
    return BASE_PROMPT.format(word_limit=word_limit) + "\n" + extra


# Generic text tools (grep condition) -----------------------------------------------


def _grep(arguments: dict[str, Any]) -> tuple[str, bool]:
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
    for number, line in enumerate(spec_lines(), start=1):
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


def _read(arguments: dict[str, Any]) -> tuple[str, bool]:
    start = arguments.get("start_line")
    count = arguments.get("max_lines", MAX_READ_LINES)
    lines = spec_lines()
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


GREP_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="spec_grep",
        description=(
            "Search the MQTT 5.0 specification text line by line with a "
            "case-insensitive regular expression; returns matching line numbers."
        ),
        parameters={
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
        handler=_grep,
    ),
    ToolSpec(
        name="spec_read",
        description=(
            "Read up to 80 lines of the MQTT 5.0 specification text starting at "
            "start_line. The result ends with next_line for continuation."
        ),
        parameters={
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
        handler=_read,
    ),
)


# MCP tools (pinned to 5.0) ---------------------------------------------------------


@cache
def _pinned_service() -> MqttSpecService:
    return MqttSpecService([BENCH_VERSION])


def _mcp_handler(name: str) -> Callable[[dict[str, Any]], tuple[str, bool]]:
    def handler(arguments: dict[str, Any]) -> tuple[str, bool]:
        outcome = _pinned_service().call(name, arguments)
        return outcome.text, outcome.is_error

    return handler


def mcp_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool.name,
            description=tool.description or "",
            parameters=dict(tool.input_schema),
            handler=_mcp_handler(tool.name),
        )
        for tool in TOOLS
    ]


def tools_for(condition: Condition) -> list[ToolSpec]:
    if condition == "grep":
        return list(GREP_TOOLS)
    if condition == "mcp":
        return mcp_tools()
    return []


def tool_schemas(condition: Condition) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in tools_for(condition)
    ]


def describe_conditions() -> str:
    return json.dumps(
        {
            c: {"prompt": CONDITION_PROMPTS[c], "tools": tool_schemas(c)}
            for c in CONDITIONS
        },
        sort_keys=True,
    )
