"""stdio MCP server exposing four read-only MQTT specification tools."""

from __future__ import annotations

import json
import logging
import re
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
OASIS MQTT 3.1.1 and 5.0 specification evidence. Each call reads one version.

- Pass version ("3.1.1" or "5.0") on every call; if the user named none, ask rather \
than assume the latest.
- Never carry section numbers, labels, fields or codes between versions: 3.1.2.4 is \
Clean Session in 3.1.1 but Clean Start in 5.0. 3.1.1 has Clean Session and return \
codes; 5.0 has Clean Start, Session Expiry, properties and reason codes.
- To compare versions, run the workflow per version and cite the version with every \
section and claim; a result, miss, identifier or cursor from one version is no \
evidence about the other.
- Consult the tools before answering; never invent an identifier that neither the \
user nor the evidence gave.
- Start with one tool; no speculative or parallel calls. Lookup is only for one exact \
entity (packet, property, reason/return code, requirement label, glossary term); \
otherwise use one combined search, also for multi-entity or synthesis questions.
- Snippets are partial excerpts. If they or a lookup state every fact asked, answer; \
otherwise get best_section once. Use outline only to browse or resolve real \
ambiguity. Stop once the evidence is sufficient.
- Keep MUST/SHOULD/MAY and every condition, direction, scope and exception. Prefer \
narrow reads and cursors to large subsection pulls; excerpts are transformed and the \
OASIS publication is authoritative.
"""

Version = Literal["3.1.1", "5.0"]
NonEmpty = Annotated[str, Field(min_length=1)]

VERSION_SCHEMA: dict[str, Any] = {
    "type": "string",
    "enum": list(SUPPORTED_VERSIONS),
    "description": "MQTT version.",
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
    def _lenient_optionals(cls, data: Any) -> Any:
        """Accept the common ways LLM clients spell optional arguments.

        ``null``, blank text and the words "null"/"none"/"undefined" mean "not
        given"; ``"3"`` is accepted for an integer and ``"true"``/``"false"`` for a
        boolean. ``version``, the other required arguments and unknown keys stay
        strict.
        """
        if not isinstance(data, dict):
            return data
        cleaned = {}
        for key, value in data.items():
            field = cls.model_fields.get(key)
            if field is None or field.is_required():
                cleaned[key] = _as_text(field, value)
                continue
            if _means_omitted(value):
                continue
            cleaned[key] = _coerce_optional(field.annotation, value)
        return cleaned


_OMITTED_WORDS = frozenset({"null", "none", "undefined"})
_INTEGER_TEXT = re.compile(r"[+-]?\d{1,6}")


def _means_omitted(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and (
        not value.strip() or value.strip().lower() in _OMITTED_WORDS
    )


def _coerce_optional(kind: Any, value: Any) -> Any:
    if kind is int:
        if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
    if kind is bool and isinstance(value, str):
        word = value.strip().lower()
        if word in ("true", "false"):
            return word == "true"
    if kind == (str | None) and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def _as_text(field: Any, value: Any) -> Any:
    """Required free-text arguments accept a bare number (``"query": 128``)."""
    if (
        field is not None
        and field.annotation is str
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return str(value)
    return value


class OutlineArgs(_Args):
    parent: str | None = None
    depth: Annotated[int, Field(ge=1, le=5)] = 1


class SearchArgs(_Args):
    query: NonEmpty
    chapter: str | None = None
    limit: Annotated[int, Field(ge=1, le=5)] = 4
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
            "Section ids and titles of one MQTT version, for browsing or resolving "
            "ambiguity, not as a first step. Without parent: down to depth (1 = "
            "chapters); with parent: its descendants."
        ),
        input_schema=_schema(
            {
                "parent": {
                    **NULLABLE_STRING,
                    "description": "Section id or title.",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 1,
                    "description": "Levels to list.",
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
            "Ranked sections of one MQTT version with query-focused snippets, for a "
            "question, section id or requirement label. One combined query per "
            "question; follow next_step."
        ),
        input_schema=_schema(
            {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Question words, section id or requirement label.",
                },
                "chapter": {
                    **NULLABLE_STRING,
                    "description": "Only this section's subtree.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "default": 4,
                    "description": "Number of results.",
                },
                "normative_only": {
                    "type": "boolean",
                    "default": False,
                    "description": "Only sections with requirement labels.",
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
            "One section of one MQTT version as Markdown, at most max_chars. If the "
            "footer says 'Truncated: yes', repeat the call with its Next cursor."
        ),
        input_schema=_schema(
            {
                "section": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Section id or title.",
                },
                "include_subsections": {
                    "type": "boolean",
                    "default": False,
                    "description": "Append descendant sections.",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 12000,
                    "default": 4000,
                    "description": "Page size.",
                },
                "cursor": {
                    **NULLABLE_STRING,
                    "description": "Next cursor from the previous page.",
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
            "Exact lookup of one entity in one MQTT version: packet, property or "
            "reason_code (5.0), return_code (3.1.1; packet CONNACK or SUBACK), "
            "requirement label or glossary term. Use search for anything else."
        ),
        input_schema=_schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(KINDS),
                    "description": "Catalog.",
                },
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Exact name, decimal, 0x hex or label.",
                },
                "packet": {
                    **NULLABLE_STRING,
                    "description": "Packet filter (property, reason or return code).",
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
