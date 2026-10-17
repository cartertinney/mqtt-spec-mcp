"""Character-budgeted Markdown retrieval with opaque continuation cursors."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schema import ListItem, Paragraph, Table

if TYPE_CHECKING:
    from .engine import SectionInfo, SpecEngine

CURSOR_FORMAT = 1
# Real cursors are about 200 characters; anything far longer is not one of ours.
MAX_CURSOR_CHARS = 512
MAX_FRAGMENT_CHARS = 600
MIN_FRAGMENT_CHARS = 40
MAX_HEADING_DEPTH = 6
SEPARATOR = "\n\n"
_LONGEST_POSITION = 10**7


@dataclass(frozen=True)
class CursorState:
    version: str
    sha: str
    section: str
    include_subsections: bool
    max_chars: int
    position: int

    def encode(self) -> str:
        payload = {
            "f": CURSOR_FORMAT,
            "v": self.version,
            "h": self.sha,
            "s": self.section,
            "i": self.include_subsections,
            "m": self.max_chars,
            "p": self.position,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")


_CURSOR_SCHEMA: dict[str, type] = {
    "f": int,
    "v": str,
    "h": str,
    "s": str,
    "i": bool,
    "m": int,
    "p": int,
}


def decode_cursor(engine: SpecEngine, cursor: str) -> CursorState:
    invalid = engine.error(
        "The cursor is invalid (it is not a cursor produced by this server's current "
        "format). Call mqtt_spec_get again without a cursor to restart retrieval."
    )
    text = cursor.strip()
    if (
        not text
        or len(text) > MAX_CURSOR_CHARS
        or not re.fullmatch(r"[A-Za-z0-9_\-]+", text)
    ):
        raise invalid
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError):
        raise invalid from None
    if not isinstance(data, dict) or set(data) != set(_CURSOR_SCHEMA):
        raise invalid
    for key, kind in _CURSOR_SCHEMA.items():
        value = data[key]
        if type(value) is not kind:
            raise invalid
    if data["f"] != CURSOR_FORMAT or data["p"] < 1:
        raise invalid
    return CursorState(
        version=data["v"],
        sha=data["h"],
        section=data["s"],
        include_subsections=data["i"],
        max_chars=data["m"],
        position=data["p"],
    )


# Fragments -----------------------------------------------------------------------


def _split_words(
    text: str, limit: int, first_prefix: str = "", rest_prefix: str = ""
) -> list[str]:
    """Split text at word boundaries into pieces no longer than ``limit``."""
    pieces: list[str] = []
    prefix = first_prefix
    remaining = text
    while remaining:
        room = limit - len(prefix)
        if len(remaining) <= room:
            pieces.append(prefix + remaining)
            break
        cut = remaining.rfind(" ", 0, room + 1)
        if cut <= 0:
            cut = room
        pieces.append(prefix + remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
        prefix = rest_prefix
    return pieces


def _escape_cell(cell: str) -> str:
    return cell.replace("\\", "\\\\").replace("|", "\\|")


def _table_line(cells: list[str]) -> str:
    return "| " + " | ".join(_escape_cell(c) for c in cells) + " |"


def _table_fragments(table: Table, limit: int) -> list[str]:
    rows = [list(r) for r in table.rows]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    caption = f"**{table.caption}**" if table.caption else ""
    head_lines = [_table_line(header), "| " + " | ".join(["---"] * width) + " |"]
    head = "\n".join(([caption] if caption else []) + head_lines)
    label = table.caption or "Table"
    if not body:
        return (
            [head]
            if len(head) <= limit
            else _cells_fragments(label, header, header, 0, limit)
        )
    fragments: list[str] = []
    current: list[str] = []
    for number, row in enumerate(body, start=1):
        line = _table_line(row)
        candidate = "\n".join([head, *current, line])
        if len(candidate) <= limit:
            current.append(line)
            continue
        if current:
            fragments.append("\n".join([head, *current]))
            current = []
        if len("\n".join([head, line])) <= limit:
            current.append(line)
        else:
            fragments.extend(_cells_fragments(label, header, row, number, limit))
    if current:
        fragments.append("\n".join([head, *current]))
    return fragments


def _cells_fragments(
    label: str, header: list[str], row: list[str], number: int, limit: int
) -> list[str]:
    """Render a row that cannot fit as one fragment per labelled cell."""
    fragments: list[str] = []
    for column, (name, value) in enumerate(zip(header, row, strict=False), start=1):
        cell = (
            f"**{label}, data row {number}, column {column}**\nHeader: {name}\nValue: "
        )
        fragments.extend(_split_words(cell + (value or "(empty)"), limit))
    return fragments


def _block_fragments(block: Paragraph | ListItem | Table, limit: int) -> list[str]:
    if isinstance(block, Table):
        return _table_fragments(block, limit)
    if isinstance(block, ListItem):
        return _split_words(block.text, limit, "- ", "  ")
    return _split_words(block.text, limit)


def placeholder(info: SectionInfo, *, subsections_follow: bool) -> str:
    if subsections_follow:
        return f"_Section {info.id} has no text of its own; its subsections follow._"
    if not info.children:
        return f"_Section {info.id} has no text of its own in the publication._"
    return (
        f"_Section {info.id} has no text of its own. Call mqtt_spec_get with "
        "include_subsections=true, or mqtt_spec_outline with this section as parent, "
        "to see its subsections._"
    )


def build_fragments(
    engine: SpecEngine, root: SectionInfo, include_subsections: bool, limit: int
) -> list[str]:
    fragments = _section_body(root, limit, include_subsections)
    if include_subsections:
        for info in engine.descendants(root.id):
            depth = min(MAX_HEADING_DEPTH, 1 + info.section.level - root.section.level)
            fragments.extend(
                _split_words(f"{info.id} {info.title}", limit, "#" * depth + " ")
            )
            fragments.extend(_section_body(info, limit, include_subsections))
    return fragments


def _section_body(
    info: SectionInfo, limit: int, include_subsections: bool
) -> list[str]:
    if not info.section.blocks:
        follow = include_subsections and bool(info.children)
        return _split_words(placeholder(info, subsections_follow=follow), limit)
    fragments: list[str] = []
    for block in info.section.blocks:
        fragments.extend(_block_fragments(block, limit))
    return fragments


# Pages ---------------------------------------------------------------------------


def _header(info: SectionInfo, continued: bool) -> str:
    return f"# {info.id} {info.title}" + (" (continued)" if continued else "")


def _banner(engine: SpecEngine) -> str:
    return f"MQTT {engine.version} ONLY - version-specific evidence."


def _footer(engine: SpecEngine, info: SectionInfo, cursor: str | None) -> str:
    lines = [
        "---",
        f"Source: OASIS {engine.pin.title}, section {info.id} - "
        f"{engine.pin.source_url}",
        f"Truncated: {'yes' if cursor else 'no'}",
    ]
    if cursor:
        lines.append(f"Next cursor: `{cursor}`")
    return "\n".join(lines)


def _page(head: str, banner: str, body: list[str], footer: str) -> str:
    return SEPARATOR.join([head, banner, *body, footer])


def _longest_cursor(
    engine: SpecEngine, info: SectionInfo, include_subsections: bool, max_chars: int
) -> str:
    return CursorState(
        engine.version,
        engine.pin.source_sha256,
        info.id,
        include_subsections,
        max_chars,
        _LONGEST_POSITION,
    ).encode()


def fragment_limit(
    engine: SpecEngine, info: SectionInfo, include_subsections: bool, max_chars: int
) -> int:
    """Largest fragment that always fits beside the header, banner and footer."""
    longest = _longest_cursor(engine, info, include_subsections, max_chars)
    overhead = len(
        _page(_header(info, True), _banner(engine), [], _footer(engine, info, longest))
    )
    limit = min(MAX_FRAGMENT_CHARS, max_chars - overhead - len(SEPARATOR))
    if limit < MIN_FRAGMENT_CHARS:
        raise engine.error(
            f"max_chars={max_chars} is too small to render section {info.id} with its "
            "required header and footer. Use a larger max_chars."
        )
    return limit


def get_section(
    engine: SpecEngine,
    section: str,
    include_subsections: bool,
    max_chars: int,
    cursor: str | None,
) -> str:
    info = engine.resolve_section(section, "section")
    state = decode_cursor(engine, cursor) if cursor is not None else None
    if state is not None:
        if state.version != engine.version or state.sha != engine.pin.source_sha256:
            raise engine.error(
                "This cursor belongs to a different MQTT version or pinned "
                "publication. Cursors cannot be reused across versions; restart "
                "retrieval by calling mqtt_spec_get without a cursor."
            )
        if (
            state.section != info.id
            or state.include_subsections != include_subsections
            or state.max_chars != max_chars
        ):
            raise engine.error(
                "The cursor does not match this request: it was issued for "
                f"section={state.section!r}, "
                f"include_subsections={str(state.include_subsections).lower()}, "
                f"max_chars={state.max_chars}. Reuse identical arguments with the "
                "cursor, or omit the cursor to start over."
            )

    banner = _banner(engine)
    limit = fragment_limit(engine, info, include_subsections, max_chars)
    longest_cursor = _longest_cursor(engine, info, include_subsections, max_chars)
    fragments = build_fragments(engine, info, include_subsections, limit)

    start = state.position if state is not None else 0
    if start >= len(fragments):
        raise engine.error(
            "The cursor points past the end of this section. Retrieval is already "
            "complete; do not reuse this cursor."
        )
    head = _header(info, start > 0)
    used = len(_page(head, banner, [], _footer(engine, info, longest_cursor)))
    end = start
    while end < len(fragments):
        cost = len(SEPARATOR) + len(fragments[end])
        if used + cost > max_chars:
            break
        used += cost
        end += 1
    if end == start:  # cannot happen given the reserved budget; never loop forever
        end = start + 1
    next_cursor = None
    if end < len(fragments):
        next_cursor = CursorState(
            engine.version,
            engine.pin.source_sha256,
            info.id,
            include_subsections,
            max_chars,
            end,
        ).encode()
    return _page(head, banner, fragments[start:end], _footer(engine, info, next_cursor))


def page_summary(page: str) -> dict[str, Any]:
    """Small helper for tests and benchmarks: extract footer fields from a page."""
    cursor = re.search(r"Next cursor: `([^`]+)`", page)
    return {
        "truncated": "Truncated: yes" in page,
        "cursor": cursor.group(1) if cursor else None,
    }
