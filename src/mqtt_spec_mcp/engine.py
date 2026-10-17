"""In-memory query engine for one MQTT version's index."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .common import QueryError, check_version_mentions, envelope
from .schema import ListItem, Paragraph, Section, SpecIndex, Table
from .search import SearchIndex
from .textutil import SECTION_PREFIX, normalize, tokenize
from .versions import PINS, Pin, VersionId, as_version

SECTION_ID = re.compile(r"^\d+(?:\.\d+)*$")
# "<id> <title>", also with a separator: "3.1.2.4 - Clean Start", "3.1.2.4: Clean Start"
SECTION_WITH_TITLE = re.compile(
    r"^(\d+(?:\.\d+)*)\.?(?:\s*[:\-\u2010-\u2015]\s*|\s+)(\S.*)$"
)
MAX_DEPTH = 5


def block_plain_text(block: Paragraph | ListItem | Table) -> str:
    if isinstance(block, Table):
        lines = [block.caption] if block.caption else []
        lines.extend(" | ".join(row) for row in block.rows)
        return "\n".join(lines)
    if isinstance(block, ListItem):
        return f"- {block.text}"
    return block.text


@dataclass(frozen=True)
class SectionInfo:
    section: Section
    order: int
    body: str
    title_norm: str
    title_words: tuple[str, ...]
    children: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.section.id

    @property
    def title(self) -> str:
        return self.section.title


class SpecEngine:
    def __init__(self, index: SpecIndex) -> None:
        self.index = index
        self.version: VersionId = as_version(index.metadata.version)
        self.pin: Pin = PINS[self.version]
        children: dict[str, list[str]] = {s.id: [] for s in index.sections}
        for s in index.sections:
            if s.parent is not None:
                children[s.parent].append(s.id)
        infos = {
            s.id: SectionInfo(
                section=s,
                order=i,
                body="\n".join(block_plain_text(b) for b in s.blocks),
                title_norm=normalize(s.title),
                title_words=tuple(tokenize(s.title)),
                children=tuple(children[s.id]),
            )
            for i, s in enumerate(index.sections)
        }
        self.sections: MappingProxyType[str, SectionInfo] = MappingProxyType(infos)
        self.ordered: tuple[SectionInfo, ...] = tuple(
            infos[s.id] for s in index.sections
        )
        self.requirements = MappingProxyType({r.label: r for r in index.requirements})
        self.packets = index.packets
        self.search_index = SearchIndex(self)

    # Helpers ------------------------------------------------------------------

    def error(self, message: str) -> QueryError:
        return QueryError(self.version, message)

    def guard(self, field: str, text: str | None) -> None:
        check_version_mentions(self.version, field, text)

    def envelope(self) -> dict[str, Any]:
        return envelope(self.version)

    def descendants(self, section_id: str) -> Iterator[SectionInfo]:
        for child in self.sections[section_id].children:
            yield self.sections[child]
            yield from self.descendants(child)

    def in_subtree(self, section_id: str, root: str) -> bool:
        return section_id == root or section_id.startswith(root + ".")

    # Section resolution ----------------------------------------------------------

    def _exact_reference(self, text: str) -> SectionInfo | None:
        """A section of this version named exactly by "<id> <title>" or its title."""
        words = tuple(tokenize(text))
        match = SECTION_WITH_TITLE.match(text)
        if match:
            info = self.sections.get(match.group(1))
            if info is not None and tuple(tokenize(match.group(2))) == info.title_words:
                return info
        named = [s for s in self.ordered if words and s.title_words == words]
        return named[0] if len(named) == 1 else None

    def resolve_section(self, raw: str, field: str = "section") -> SectionInfo:
        text = SECTION_PREFIX.sub("", " ".join(raw.split()).strip()).strip()
        # Exact references to this version's own sections are not version mentions,
        # even when the title itself names a version (5.0's "1.8.1 MQTT v3.1.1").
        if self._exact_reference(text) is None:
            self.guard(field, raw)
        v = self.version
        if not text:
            raise self.error(f"The {field} is empty. Pass a section id such as '3.1'.")
        candidate = text.rstrip(".")
        if SECTION_ID.match(candidate):
            info = self.sections.get(candidate)
            if info is None:
                raise self.error(
                    f"No MQTT {v} section {candidate} exists. Section numbers differ "
                    f"between versions; use mqtt_spec_outline or mqtt_spec_search with "
                    f"version='{v}' to find the right section."
                )
            return info
        match = SECTION_WITH_TITLE.match(text)
        if match:
            section_id, given = match.group(1), match.group(2)
            info = self.sections.get(section_id)
            if info is None:
                raise self.error(
                    f"No MQTT {v} section {section_id} exists (you asked for "
                    f"'{text}'). Use mqtt_spec_search with version='{v}' to find it."
                )
            if not _title_matches(tuple(tokenize(given)), info.title_words):
                raise self.error(
                    f"Section {section_id} title does not match: you gave '{given}', "
                    f"but MQTT {v} section {section_id} is '{info.title}'. Section "
                    "numbers and titles differ between versions; pass only the id, "
                    "or search for the title."
                )
            return info
        wanted = normalize(text)
        words = tuple(tokenize(text))
        # Titles compare by their words, so stray publication punctuation (such as
        # the trailing backtick in 5.0's "Message Expiry Interval`") does not matter.
        exact = [s for s in self.ordered if words and s.title_words == words]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            ids = ", ".join(s.id for s in exact)
            raise self.error(
                f"The title '{text}' matches several MQTT {v} sections ({ids}). "
                "Pass one of these section ids instead."
            )
        joined = " ".join(words)
        partial = [
            s
            for s in self.ordered
            if wanted in s.title_norm or (joined and joined in " ".join(s.title_words))
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            listed = "; ".join(f"{s.id} {s.title}" for s in partial[:5])
            more = f" (and {len(partial) - 5} more)" if len(partial) > 5 else ""
            raise self.error(
                f"'{text}' is ambiguous in MQTT {v}: it matches {listed}{more}. "
                "Pass one section id."
            )
        raise self.error(
            f"No MQTT {v} section matches '{text}'. Use mqtt_spec_search with "
            f"version='{v}' to locate the topic, or mqtt_spec_outline to browse."
        )

    # Outline ---------------------------------------------------------------------

    def outline(self, parent: str | None, depth: int) -> dict[str, Any]:
        if parent is None:
            root = None
            listed = [s for s in self.ordered if s.section.level <= depth]
        else:
            root = self.resolve_section(parent, "parent")
            limit = root.section.level + depth
            listed = [s for s in self.descendants(root.id) if s.section.level <= limit]
        result = self.envelope()
        result["parent"] = (
            None if root is None else {"section": root.id, "title": root.title}
        )
        result["depth"] = depth
        result["sections"] = [
            {
                "section": s.id,
                "title": s.title,
                "parent": s.section.parent,
                "has_children": bool(s.children),
            }
            for s in listed
        ]
        return result


def _title_matches(given: tuple[str, ...], actual: tuple[str, ...]) -> bool:
    """Same title, or one is a whole-word prefix of the other ("Clean" ~ "Clean
    Session"); compared word by word, ignoring punctuation."""
    if not given:
        return False
    shorter, longer = sorted((given, actual), key=len)
    return longer[: len(shorter)] == shorter
