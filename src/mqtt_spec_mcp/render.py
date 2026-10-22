"""Character-budgeted Markdown retrieval with opaque continuation cursors."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schema import ListItem, Paragraph, Table

if TYPE_CHECKING:
    from .engine import SectionInfo, SpecEngine

CURSOR_FORMAT = 2
# Cursors carry a prefix of the pinned source SHA-256: enough to tell publications
# apart while keeping cursors short (about 50 characters) and easy to copy exactly.
CURSOR_SHA_CHARS = 12
MAX_CURSOR_CHARS = 128
MAX_FRAGMENT_CHARS = 600
MIN_FRAGMENT_CHARS = 40
MAX_HEADING_DEPTH = 6
SEPARATOR = "\n\n"
_LONGEST_POSITION = 10**7
_CURSOR_TEXT = re.compile(r"[A-Za-z0-9_\-]+")
_CURSOR_FIELDS = re.compile(
    r"(?P<format>\d{1,3}),(?P<version>[0-9.]{1,10}),(?P<sha>[0-9a-f]{12}),"
    r"(?P<section>\d+(?:\.\d+)*),(?P<include>[01]),(?P<max_chars>\d{1,6}),"
    r"(?P<position>\d{1,9})"
)


def sha_prefix(sha256: str) -> str:
    return sha256[:CURSOR_SHA_CHARS]


@dataclass(frozen=True)
class CursorState:
    version: str
    sha: str
    section: str
    include_subsections: bool
    max_chars: int
    position: int

    def encode(self) -> str:
        fields = (
            CURSOR_FORMAT,
            self.version,
            sha_prefix(self.sha),
            self.section,
            int(self.include_subsections),
            self.max_chars,
            self.position,
        )
        raw = ",".join(str(field) for field in fields).encode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(engine: SpecEngine, cursor: str) -> CursorState:
    invalid = engine.error(
        "The cursor is invalid (it is not a cursor produced by this server's current "
        "format). Call mqtt_spec_get again without a cursor to restart retrieval."
    )
    text = cursor.strip()
    if not text or len(text) > MAX_CURSOR_CHARS or not _CURSOR_TEXT.fullmatch(text):
        raise invalid
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode("ascii")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise invalid from None
    fields = _CURSOR_FIELDS.fullmatch(raw)
    if fields is None or int(fields["format"]) != CURSOR_FORMAT:
        raise invalid
    position = int(fields["position"])
    if position < 1:
        raise invalid
    return CursorState(
        version=fields["version"],
        sha=fields["sha"],
        section=fields["section"],
        include_subsections=fields["include"] == "1",
        max_chars=int(fields["max_chars"]),
        position=position,
    )


# Fragments -----------------------------------------------------------------------


_SENTENCE_END = re.compile(r"[.;:?!)](?= )")


def _sentence_cut(text: str, room: int) -> int:
    """Position just after the last sentence end that leaves the piece at least
    half full, or 0 when there is none."""
    best = 0
    for match in _SENTENCE_END.finditer(text, 0, room):
        if match.end() >= room // 2:
            best = match.end()
    return best


def _split_words(
    text: str, limit: int, first_prefix: str = "", rest_prefix: str = ""
) -> list[str]:
    """Split text into pieces no longer than ``limit``, preferring sentence ends
    and otherwise cutting at a word boundary."""
    pieces: list[str] = []
    prefix = first_prefix
    remaining = text
    while remaining:
        room = limit - len(prefix)
        if len(remaining) <= room:
            pieces.append(prefix + remaining)
            break
        cut = _sentence_cut(remaining, room)
        if cut <= 0:
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


# Shorter repeated cells, such as per-bit values (0 | 0 | 0), stay in their columns.
MIN_MERGED_CELL_CHARS = 4


def _span_label(header: list[str], first: int, last: int) -> str:
    start, end = header[first].strip(), header[last].strip()
    if first > 0 and start and end:
        if header[0].strip().lower() == "bit":
            return f"bits {start}–{end}"
        return f"spans {start} to {end}"
    return f"spans {last - first + 1} columns"


def _merge_spans(header: list[str], row: list[str]) -> list[str]:
    """Write a merged cell once, labelled with the columns it spans.

    Indexes repeat a merged cell's text in every column it covers, which keeps
    rows rectangular for lookups but repeats a long field name across eight bit
    columns. A run is merged only when that shortens the row."""
    cells: list[str] = []
    start = 0
    while start < len(row):
        end = start
        while end + 1 < len(row) and row[end + 1] == row[start]:
            end += 1
        text = row[start]
        run = row[start : end + 1]
        merged = f"{text} ({_span_label(header, start, end)})"
        if (
            end > start
            and len(text) >= MIN_MERGED_CELL_CHARS
            and len(merged) < len(" | ".join(run))
        ):
            cells.append(merged)
        else:
            cells.extend(run)
        start = end + 1
    return cells


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
        line = _table_line(_merge_spans(header, row))
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
    return f"_Section {info.id} has no text of its own in the publication._"


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
    room = max_chars - overhead - len(SEPARATOR)
    # Pages hold whole fragments, so fragments are at most half a page: a long
    # fragment after a short one would otherwise leave most of a page empty.
    limit = min(MAX_FRAGMENT_CHARS, room // 2)
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
    # A section without text of its own consists of its subsections, so return them
    # instead of an empty page that needs another call.
    include_subsections = include_subsections or (
        not info.section.blocks and bool(info.children)
    )
    state = decode_cursor(engine, cursor) if cursor is not None else None
    if state is not None:
        if state.version != engine.version or state.sha != sha_prefix(
            engine.pin.source_sha256
        ):
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
            issued = (
                f"for section {state.section}"
                if state.section != info.id
                else "with different page options"
            )
            raise engine.error(
                f"The cursor does not match this request: it was issued {issued}. "
                "Repeat the request that returned it, changing only the cursor, or "
                "omit the cursor to start over."
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
