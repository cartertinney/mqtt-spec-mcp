"""stdio MCP server exposing four read-only MQTT specification tools."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import anyio
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import __version__
from .common import QueryError
from .engine import SpecEngine
from .loader import load_index
from .lookup import KINDS, lookup
from .render import get_section
from .search import search
from .versions import PINS, SUPPORTED_VERSIONS, VersionId, as_version

logger = logging.getLogger(__name__)

SERVER_NAME = "mqtt-spec-mcp"
SERVER_TITLE = "MQTT Specification"
SERVER_DESCRIPTION = (
    "Offline, version-isolated navigation, search and quoting of the OASIS MQTT 3.1.1 "
    "and MQTT 5.0 specifications."
)
WEBSITE_URL = "https://www.oasis-open.org/committees/mqtt/"

INSTRUCTIONS = """\
Evidence tools for the OASIS MQTT 3.1.1 and MQTT 5.0 specifications. Each call reads \
exactly one version.

Versions
- Pass version explicitly on every call ("3.1.1" or "5.0"). If the user has not said \
which version they mean, ask; do not assume the latest.
- Never carry section numbers, requirement labels, field names or numeric codes from \
one version to the other. The same number can mean different things (3.1.2.4 is Clean \
Session in 3.1.1 but Clean Start in 5.0).
- To compare versions, run the workflow once per version and cite the version next to \
every section and claim. A hit, miss, identifier or cursor from one version is never \
evidence about the other.
- Vocabulary differs: 3.1.1 has Clean Session and return codes; 5.0 has Clean Start, \
Session Expiry, properties and reason codes.

Workflow
- Pick one starting tool; avoid speculative or parallel calls.
- mqtt_spec_lookup: only for one exact, recognized entity (packet, property, \
reason_code, return_code, requirement label, glossary term).
- mqtt_spec_search: one combined query for everything else, including questions that \
involve several entities or need synthesis.
- Never invent an identifier that neither the user nor earlier evidence supplied.
- If a lookup result or the first search snippet already contains the facts, answer \
immediately. Otherwise call mqtt_spec_get once for best_section. Use mqtt_spec_outline \
only to browse or resolve genuine ambiguity. Stop as soon as the evidence is sufficient.

Answering
- Preserve normative strength (MUST, SHOULD, MAY) and every condition, direction, \
scope and exception.
- Prefer narrow retrieval and cursors over pulling large subsections. Excerpts are \
transformed text; the OASIS publication remains authoritative.
"""

Version = Literal["3.1.1", "5.0"]
NonEmpty = Annotated[str, Field(min_length=1)]

VERSION_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(SUPPORTED_VERSIONS),
    "description": "MQTT version to read. Required on every call; there is no default.",
}
NULLABLE_STRING: dict[str, Any] = {"type": ["string", "null"], "default": None}
ANNOTATIONS = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Version

    @model_validator(mode="before")
    @classmethod
    def _omit_empty_optionals(cls, data: Any) -> Any:
        """Treat null, and blank text, in an optional argument as "not given".

        LLM clients often send ``"cursor": ""`` or ``"limit": null`` for arguments
        they mean to leave out. Required arguments and unknown keys stay strict.
        """
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            field = cls.model_fields.get(key)
            empty = value is None or (isinstance(value, str) and not value.strip())
            if field is not None and not field.is_required() and empty:
                continue
            cleaned[key] = value
        return cleaned


class OutlineArgs(_Args):
    parent: str | None = None
    depth: Annotated[int, Field(ge=1, le=5)] = 1


class SearchArgs(_Args):
    query: NonEmpty
    chapter: str | None = None
    limit: Annotated[int, Field(ge=1, le=5)] = 3
    normative_only: bool = False


class GetArgs(_Args):
    section: NonEmpty
    include_subsections: bool = False
    max_chars: Annotated[int, Field(ge=1000, le=12000)] = 4000
    cursor: str | None = None


class LookupArgs(_Args):
    kind: Literal[
        "packet", "property", "reason_code", "return_code", "requirement", "term"
    ]
    query: NonEmpty
    packet: str | None = None


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"version": VERSION_SCHEMA, **properties},
        "required": ["version", *required],
        "additionalProperties": False,
    }


TOOLS: tuple[types.Tool, ...] = (
    types.Tool(
        name="mqtt_spec_outline",
        title="MQTT spec outline",
        description=(
            "List section ids and titles (no body text) for one MQTT version. Without "
            "parent: sections down to depth (depth 1 = chapters 1-7). With parent: "
            "that section's descendants down to depth levels below it. Use for "
            "browsing or resolving ambiguity, not as a first step for ordinary "
            "questions."
        ),
        input_schema=_schema(
            {
                "parent": {
                    **NULLABLE_STRING,
                    "description": "Section id (e.g. '3') or unambiguous title.",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1,
                    "description": "Levels to list below parent (or from the top).",
                },
            },
            [],
        ),
        annotations=ANNOTATIONS,
    ),
    types.Tool(
        name="mqtt_spec_search",
        title="MQTT spec search",
        description=(
            "Rank sections of one MQTT version for a natural-language query, section "
            "id or requirement label. The first result carries a long snippet that "
            "often answers the question; follow next_step. Use one combined query per "
            "question."
        ),
        input_schema=_schema(
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Words, a section id or a requirement label.",
                },
                "chapter": {
                    **NULLABLE_STRING,
                    "description": "Restrict to this section id/title and its subtree.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 3,
                    "description": "Number of results.",
                },
                "normative_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only sections with labeled normative requirements.",
                },
            },
            ["query"],
        ),
        annotations=ANNOTATIONS,
    ),
    types.Tool(
        name="mqtt_spec_get",
        title="MQTT spec get section",
        description=(
            "Return one section of one MQTT version as Markdown, never longer than "
            "max_chars. If the footer says 'Truncated: yes', call again with the same "
            "arguments plus the Next cursor value to continue."
        ),
        input_schema=_schema(
            {
                "section": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Section id (e.g. '3.1.2.4') or unambiguous title.",
                },
                "include_subsections": {
                    "type": "boolean",
                    "default": False,
                    "description": "Append all descendant sections.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 12000,
                    "default": 4000,
                    "description": "Page size limit in characters.",
                },
                "cursor": {
                    **NULLABLE_STRING,
                    "description": "Opaque cursor from the previous page.",
                },
            },
            ["section"],
        ),
        annotations=ANNOTATIONS,
    ),
    types.Tool(
        name="mqtt_spec_lookup",
        title="MQTT spec lookup",
        description=(
            "Exact lookup of one recognized entity in one MQTT version: packet, "
            "property (5.0), reason_code (5.0), return_code (3.1.1, needs packet "
            "CONNACK or SUBACK), requirement label, or glossary term. Query by exact "
            "name, decimal, 0x hex or label. Use search for anything else."
        ),
        input_schema=_schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Entity catalog to search.",
                },
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Exact name, decimal, 0x hex value or label.",
                },
                "packet": {
                    **NULLABLE_STRING,
                    "description": "Packet filter: property, reason_code, return_code.",
                },
            },
            ["kind", "query"],
        ),
        annotations=ANNOTATIONS,
    ),
)
TOOL_NAMES = tuple(tool.name for tool in TOOLS)


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool


def _json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _validation_message(tool: str, exc: ValidationError) -> str:
    problems = []
    for err in exc.errors():
        where = ".".join(str(p) for p in err["loc"]) or "arguments"
        problems.append(f"{where}: {err['msg']}")
    hint = ""
    if any(err["loc"][:1] == ("version",) for err in exc.errors()):
        hint = (
            " Pass version='3.1.1' or version='5.0' explicitly on every call; there is "
            "no default and no other spelling is accepted."
        )
    return f"Invalid arguments for {tool}: " + "; ".join(problems) + "." + hint


class MqttSpecService:
    """Transport-independent tool dispatcher holding one engine per version."""

    def __init__(self, versions: Sequence[str] = SUPPORTED_VERSIONS) -> None:
        self.engines: dict[VersionId, SpecEngine] = {
            as_version(v): SpecEngine(load_index(v)) for v in versions
        }
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "mqtt_spec_outline": self._outline,
            "mqtt_spec_search": self._search,
            "mqtt_spec_get": self._get,
            "mqtt_spec_lookup": self._lookup,
        }

    def engine(self, version: str) -> SpecEngine:
        v = as_version(version)
        engine = self.engines.get(v)
        if engine is None:
            raise QueryError(
                v,
                f"The MQTT {v} index is not loaded in this server process "
                f"({PINS[v].title} is unavailable here).",
            )
        return engine

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutcome(
                f"Unknown tool {name!r}. Available tools: {', '.join(TOOL_NAMES)}.",
                True,
            )
        args = dict(arguments or {})
        try:
            return ToolOutcome(handler(args), False)
        except ValidationError as exc:
            message = _validation_message(name, exc)
            version = args.get("version")
            if isinstance(version, str) and version in SUPPORTED_VERSIONS:
                return ToolOutcome(str(QueryError(as_version(version), message)), True)
            return ToolOutcome(message, True)
        except QueryError as exc:
            return ToolOutcome(str(exc), True)
        except Exception as exc:  # never leak an unscoped error to the client
            logger.exception("unexpected error in %s", name)
            message = (
                f"Internal error while running {name} ({type(exc).__name__}). This is "
                "a server bug; retrying with the same arguments will fail again."
            )
            version = args.get("version")
            if isinstance(version, str) and version in SUPPORTED_VERSIONS:
                return ToolOutcome(str(QueryError(as_version(version), message)), True)
            return ToolOutcome(message, True)

    def _outline(self, raw: dict[str, Any]) -> str:
        args = OutlineArgs.model_validate(raw)
        return _json(self.engine(args.version).outline(args.parent, args.depth))

    def _search(self, raw: dict[str, Any]) -> str:
        args = SearchArgs.model_validate(raw)
        engine = self.engine(args.version)
        return _json(
            search(engine, args.query, args.chapter, args.limit, args.normative_only)
        )

    def _get(self, raw: dict[str, Any]) -> str:
        args = GetArgs.model_validate(raw)
        engine = self.engine(args.version)
        return get_section(
            engine, args.section, args.include_subsections, args.max_chars, args.cursor
        )

    def _lookup(self, raw: dict[str, Any]) -> str:
        args = LookupArgs.model_validate(raw)
        return _json(
            lookup(self.engine(args.version), args.kind, args.query, args.packet)
        )


def create_server(versions: Sequence[str] = SUPPORTED_VERSIONS) -> Server[Any]:
    service = MqttSpecService(versions)

    async def list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(TOOLS))

    async def call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        outcome = service.call(params.name, params.arguments)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=outcome.text)],
            is_error=outcome.is_error,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        title=SERVER_TITLE,
        description=SERVER_DESCRIPTION,
        instructions=INSTRUCTIONS,
        website_url=WEBSITE_URL,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    anyio.run(serve)
