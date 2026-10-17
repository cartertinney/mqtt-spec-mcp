"""Parse an OASIS MQTT HTML publication into numbered sections and blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from mqtt_spec_mcp.textutil import collapse_ws

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = [*sorted(HEADING_TAGS), "p", "li", "pre", "table"]
CONTAINER_TAGS = {"table", "li", "pre"}
SKIP_CLASS_PREFIXES = ("MsoToc", "MsoHeader", "MsoFooter")
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
CAPTION = re.compile(r"^(Table|Figure)\s*\d")
BULLETS = {"·", "•", "o", "§", "▪", "-", "–", "\uf0b7", "\uf0a7", "\uf0d8"}
ORDINAL_MARKER = re.compile(r"^(\d+[.)]|[a-zA-Z][.)]|\([0-9a-zA-Z]+\))$")
SENTINEL = "\x00"
# Footnote markers such as QoS<sup>2</sup> keep their superscript form, so they are
# not misread as part of the word ("QoS2" looks like QoS level 2).
_SUPERSCRIPT = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUPERSCRIPTABLE = set("0123456789+-=()ni")
BLOCK_LEVEL = {
    *HEADING_TAGS,
    *("p", "div", "li", "ul", "ol", "pre", "table", "tr", "td", "th", "blockquote"),
}


@dataclass
class RawParagraph:
    text: str
    kind: str = "paragraph"


@dataclass
class RawTable:
    caption: str | None
    rows: list[list[str]]


RawBlock = RawParagraph | RawTable


@dataclass
class RawSection:
    id: str
    title: str
    level: int
    parent: str | None
    blocks: list[RawBlock] = field(default_factory=list)
    # The bold-prefixed "term" spans of each paragraph, used for the glossary.
    bold_leads: dict[int, str] = field(default_factory=dict)


@dataclass
class Appendix:
    heading: str
    blocks: list[RawBlock] = field(default_factory=list)


@dataclass
class Document:
    sections: list[RawSection]
    appendices: list[Appendix]


class ParseError(ValueError):
    pass


def _is_hidden(tag: Tag) -> bool:
    style = str(tag.get("style") or "").replace(" ", "").lower()
    return "display:none" in style


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_is_hidden):
        tag.decompose()


def _has_container_ancestor(tag: Tag) -> bool:
    return any(parent.name in CONTAINER_TAGS for parent in tag.parents)


def _classes(tag: Tag) -> list[str]:
    value = tag.get("class")
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _is_superscript(tag: Tag) -> bool:
    style = str(tag.get("style") or "").replace(" ", "").lower()
    return tag.name == "sup" or "vertical-align:super" in style


def _superscript_text(tag: Tag) -> str:
    text = tag.get_text()
    stripped = text.strip()
    if stripped and set(stripped) <= _SUPERSCRIPTABLE:
        return text.translate(_SUPERSCRIPT)
    return text


def _is_spacer(tag: Tag) -> bool:
    style = str(tag.get("style") or "").replace(" ", "").lower()
    return tag.name == "span" and "font:7.0pt" in style and not tag.get_text().strip()


def element_text(tag: Tag) -> str:
    """Return whitespace-normalized text with Word list spacers as SENTINEL."""
    parts: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    parts.append(" ")
                elif _is_spacer(child):
                    parts.append(SENTINEL)
                elif _is_superscript(child):
                    parts.append(_superscript_text(child))
                elif child.name in BLOCK_LEVEL:
                    parts.append(" ")
                    walk(child)
                    parts.append(" ")
                else:
                    walk(child)

    walk(tag)
    return "".join(parts)


def split_marker(raw: str) -> tuple[str, bool]:
    """Return (text, is_list_item) after interpreting a Word list marker."""
    if SENTINEL not in raw:
        return collapse_ws(raw), False
    marker, rest = raw.split(SENTINEL, 1)
    marker = collapse_ws(marker)
    rest = collapse_ws(rest.replace(SENTINEL, " "))
    if marker in BULLETS:
        return rest, True
    if ORDINAL_MARKER.match(marker):
        return f"{marker} {rest}", True
    return collapse_ws(f"{marker} {rest}"), False


def _bold_lead(tag: Tag) -> str | None:
    """Text of the leading bold run of a paragraph, if the paragraph starts bold."""
    pieces: list[str] = []
    for node in tag.descendants:
        if not isinstance(node, NavigableString) or isinstance(node, Comment):
            continue
        text = str(node)
        if not text.strip() and not pieces:
            continue
        bold = any(p.name in ("b", "strong") for p in node.parents if p is not tag)
        if not bold:
            break
        pieces.append(text)
    lead = collapse_ws("".join(pieces))
    return lead or None


def _cell_text(cell: Tag) -> str:
    return collapse_ws(element_text(cell).replace(SENTINEL, " "))


def _own_rows(table: Tag) -> list[Tag]:
    return [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]


def _span(cell: Tag, attr: str) -> int:
    try:
        return max(1, int(str(cell.get(attr) or "1")))
    except ValueError:
        return 1


def table_rows(table: Tag) -> list[list[str]]:
    """Expand rowspan/colspan into a rectangular grid of cell strings."""
    grid: dict[tuple[int, int], str] = {}
    width = 0
    row_count = 0
    for r, tr in enumerate(_own_rows(table)):
        row_count = r + 1
        c = 0
        for cell in tr.find_all(["td", "th"]):
            if cell.find_parent("tr") is not tr:
                continue
            while (r, c) in grid:
                c += 1
            text = _cell_text(cell)
            rowspan, colspan = _span(cell, "rowspan"), _span(cell, "colspan")
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = text
            c += colspan
            width = max(width, c)
    rows: list[list[str]] = []
    for r in range(row_count):
        row = [grid.get((r, c), "") for c in range(width)]
        if any(cell for cell in row):
            rows.append(row)
    return rows


def _section_parent(section_id: str, known: dict[str, RawSection]) -> str | None:
    parts = section_id.split(".")
    while len(parts) > 1:
        parts = parts[:-1]
        candidate = ".".join(parts)
        if candidate in known:
            return candidate
    return None


def parse_document(html: str) -> Document:
    """Parse numbered sections, then any alphabetic appendices that follow.

    Body parsing stops at the first appendix heading so appendix text is never
    attributed to the last numbered section; appendix blocks are kept apart.
    """
    soup = BeautifulSoup(html, "lxml")
    _strip_noise(soup)
    body = soup.body
    if body is None:
        raise ParseError("document has no <body>")

    sections: dict[str, RawSection] = {}
    order: list[RawSection] = []
    appendices: list[Appendix] = []
    current: RawSection | None = None

    for el in body.find_all(BLOCK_TAGS):
        if _has_container_ancestor(el):
            continue
        classes = _classes(el)
        if any(c.startswith(SKIP_CLASS_PREFIXES) for c in classes):
            continue
        if current is not None and "AppendixHeading1" in classes:
            appendices.append(Appendix(heading=collapse_ws(element_text(el))))
            continue
        if appendices:
            _append_appendix_block(appendices[-1], el)
            continue
        if el.name == "table":
            if current is None:
                continue
            rows = table_rows(el)
            if not rows:
                continue
            caption = None
            prev = current.blocks[-1] if current.blocks else None
            if (
                isinstance(prev, RawParagraph)
                and prev.kind == "paragraph"
                and CAPTION.match(prev.text)
            ):
                caption = prev.text
                current.blocks.pop()
            current.blocks.append(RawTable(caption=caption, rows=rows))
            continue

        text, is_item = split_marker(element_text(el))
        if el.name in HEADING_TAGS:
            match = NUMBERED_HEADING.match(text)
            if match:
                section_id, title = match.group(1), match.group(2)
                if section_id in sections:
                    raise ParseError(f"duplicate section id {section_id}")
                if current is None and section_id != "1":
                    continue
                current = RawSection(
                    id=section_id,
                    title=title,
                    level=section_id.count(".") + 1,
                    parent=_section_parent(section_id, sections),
                )
                sections[section_id] = current
                order.append(current)
                continue
        if current is None or not text:
            continue
        kind = "list_item" if (is_item or el.name == "li") else "paragraph"
        current.blocks.append(RawParagraph(text=text, kind=kind))
        lead = _bold_lead(el)
        if lead:
            current.bold_leads[len(current.blocks) - 1] = lead

    if not order:
        raise ParseError("no numbered sections found")
    return Document(sections=order, appendices=appendices)


def _append_appendix_block(appendix: Appendix, el: Tag) -> None:
    if el.name == "table":
        rows = table_rows(el)
        if rows:
            appendix.blocks.append(RawTable(caption=None, rows=rows))
        return
    text, _ = split_marker(element_text(el))
    if text:
        appendix.blocks.append(RawParagraph(text=text))
