"""Turn parsed sections into a validated :class:`SpecIndex`."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from mqtt_spec_mcp.schema import (
    Block,
    Code,
    ListItem,
    Packet,
    Paragraph,
    Property,
    Requirement,
    Section,
    SpecIndex,
    Table,
    Term,
)
from mqtt_spec_mcp.textutil import (
    LABEL_PATTERN,
    canonical_label,
    natural_key,
    normalize,
)
from mqtt_spec_mcp.versions import Pin

from .parse import Appendix, Document, RawBlock, RawParagraph, RawSection, RawTable


class BuildError(ValueError):
    pass


# Where each catalog lives in each publication (facts about the documents).
CATALOG_SECTIONS: dict[str, dict[str, str]] = {
    "3.1.1": {
        "packet_types": "2.2.1",
        "packet_flags": "2.2.2",
        "glossary": "1.2",
        "connack_codes": "3.2.2.3",
        "suback_codes": "3.9.3",
    },
    "5.0": {
        "packet_types": "2.1.2",
        "packet_flags": "2.1.3",
        "glossary": "1.2",
        "properties": "2.2.2.2",
        "reason_codes": "2.4",
    },
}
PACKET_CHAPTER = "3"
WILL_PROPERTIES = "Will Properties"
WILL_PROPERTIES_SECTION = "3.1.3.2"
TERM_START = re.compile(r"^([A-Z][A-Za-z0-9\-]*(?: [A-Za-z0-9\-]+){0,4}):\s*(.*)$")
SUBACK_CODE = re.compile(r"0x([0-9A-Fa-f]{2})\s*-\s*(.+?)(?=\s+0x[0-9A-Fa-f]{2}\s*-|$)")


def hex_code(value: int) -> str:
    return f"0x{value:02X}"


def _title_key(text: str) -> tuple[str, ...]:
    """Title comparison key that tolerates singular/plural spelling differences."""
    return tuple(word.removesuffix("s") for word in normalize(text).split())


def row_text(row: Iterable[str]) -> str:
    return " | ".join(cell for cell in row if cell)


def _convert_block(block: RawBlock) -> Block:
    if isinstance(block, RawTable):
        rows = tuple(tuple(row) for row in block.rows)
        return Table(type="table", caption=block.caption, rows=rows)
    if block.kind == "list_item":
        return ListItem(type="list_item", text=block.text)
    return Paragraph(type="paragraph", text=block.text)


def _block_texts(block: RawBlock) -> Iterator[str]:
    """Units of excerpt text: the paragraph itself, or each table row."""
    if isinstance(block, RawTable):
        for row in block.rows:
            yield row_text(row)
    else:
        yield block.text


def _find_table(section: RawSection, first_header: str) -> RawTable:
    for block in section.blocks:
        if (
            isinstance(block, RawTable)
            and block.rows
            and normalize(block.rows[0][0]).startswith(normalize(first_header))
        ):
            return block
    raise BuildError(f"no table starting with {first_header!r} in {section.id}")


def _column(header: list[str], name: str) -> int:
    for i, cell in enumerate(header):
        if normalize(cell).startswith(normalize(name)):
            return i
    raise BuildError(f"column {name!r} not found in {header}")


class Builder:
    def __init__(self, document: Document, pin: Pin) -> None:
        self.doc = document
        self.pin = pin
        self.version = pin.version
        self.where = CATALOG_SECTIONS[self.version]
        self.by_id = {s.id: s for s in document.sections}

    def section(self, section_id: str) -> RawSection:
        try:
            return self.by_id[section_id]
        except KeyError:
            raise BuildError(f"expected section {section_id} is missing") from None

    # Requirements -----------------------------------------------------------

    def requirements(self) -> tuple[list[Requirement], dict[str, list[str]]]:
        first: dict[str, Requirement] = {}
        per_section: dict[str, list[str]] = {s.id: [] for s in self.doc.sections}
        for section in self.doc.sections:
            for block in section.blocks:
                for text in _block_texts(block):
                    for match in LABEL_PATTERN.finditer(text):
                        label = canonical_label(match.group(1), self.version)
                        if label not in per_section[section.id]:
                            per_section[section.id].append(label)
                        if label not in first:
                            first[label] = Requirement(
                                label=label, section=section.id, excerpt=text
                            )
        for label, statement in self._appendix_statements():
            if label in first:
                continue
            section_id, excerpt = self._locate_statement(label, statement)
            first[label] = Requirement(label=label, section=section_id, excerpt=excerpt)
            per_section[section_id].append(label)
        reqs = sorted(first.values(), key=lambda r: natural_key(r.label))
        return reqs, per_section

    def _appendix_statements(self) -> Iterator[tuple[str, str]]:
        """Label/statement rows of the "Mandatory normative statement(s)" appendix."""
        appendix: Appendix | None = next(
            (
                a
                for a in self.doc.appendices
                if "normative statement" in normalize(a.heading)
            ),
            None,
        )
        if appendix is None:
            return
        for block in appendix.blocks:
            if not isinstance(block, RawTable):
                continue
            for row in block.rows:
                match = LABEL_PATTERN.fullmatch(row[0].strip()) if row else None
                if match and len(row) > 1:
                    yield canonical_label(match.group(1), self.version), row[1]

    def _locate_statement(self, label: str, statement: str) -> tuple[str, str]:
        """Find the body text that carries an appendix-only (misprinted) label."""
        needle = normalize(statement).rstrip(".")
        for section in self.doc.sections:
            for block in section.blocks:
                for text in _block_texts(block):
                    if needle and needle in normalize(text):
                        return section.id, text
        raise BuildError(f"cannot place appendix requirement {label} in the body")

    # Packets ----------------------------------------------------------------

    def packets(self) -> list[Packet]:
        types = _find_table(self.section(self.where["packet_types"]), "Name")
        header = types.rows[0]
        c_val = _column(header, "Value")
        c_dir = _column(header, "Direction")
        c_desc = _column(header, "Description")
        flags = self._flags()
        chapter = [
            s for s in self.doc.sections if s.parent == PACKET_CHAPTER and s.level == 2
        ]
        packets: list[Packet] = []
        for row in types.rows[1:]:
            name = row[0].strip()
            if normalize(name) == "reserved" or not row[c_val].strip().isdigit():
                continue
            home = next((s for s in chapter if s.title.split()[0] == name), None)
            if home is None:
                raise BuildError(f"no chapter-3 section for packet {name}")
            packets.append(
                Packet(
                    name=name,
                    value=int(row[c_val]),
                    direction=row[c_dir],
                    description=row[c_desc],
                    fixed_header_flags=flags.get(name, ""),
                    section=home.id,
                )
            )
        return packets

    def _flags(self) -> dict[str, str]:
        table = _find_table(self.section(self.where["packet_flags"]), "")
        header = table.rows[0]
        bit_cols = [_column(header, f"Bit {n}") for n in (3, 2, 1, 0)]
        flags: dict[str, str] = {}
        for row in table.rows[1:]:
            bits = [row[c].strip() for c in bit_cols]
            if all(b in ("0", "1") for b in bits):
                flags[row[0].strip()] = "".join(bits)
            else:
                labelled = ", ".join(
                    f"Bit {n}: {b}" for n, b in zip((3, 2, 1, 0), bits, strict=True)
                )
                flags[row[0].strip()] = f"{row[1]} ({labelled})"
        return flags

    # Properties and codes ---------------------------------------------------

    def properties(self) -> list[Property]:
        if "properties" not in self.where:
            return []
        table = _find_table(self.section(self.where["properties"]), "Identifier")
        chapter3 = [
            s for s in self.doc.sections if s.id.split(".")[0] == PACKET_CHAPTER
        ]
        props: list[Property] = []
        for row in table.rows:
            if not row[0].strip().isdigit():
                continue
            value = int(row[0])
            if normalize(row[1]) != normalize(hex_code(value)):
                raise BuildError(f"property {value} hex mismatch: {row[1]}")
            name = row[2].strip()
            sections = [self.where["properties"]] + [
                s.id for s in chapter3 if _title_key(s.title) == _title_key(name)
            ]
            props.append(
                Property(
                    id=value,
                    hex=hex_code(value),
                    name=name,
                    data_type=row[3].strip(),
                    packets=tuple(p.strip() for p in row[4].split(",") if p.strip()),
                    sections=tuple(sections),
                )
            )
        return props

    def reason_codes(self) -> list[Code]:
        if "reason_codes" not in self.where:
            return []
        section_id = self.where["reason_codes"]
        table = _find_table(self.section(section_id), "Reason Code")
        codes: list[Code] = []
        for row in table.rows:
            if not row[0].strip().isdigit():
                continue
            value = int(row[0])
            if normalize(row[1]) != normalize(hex_code(value)):
                raise BuildError(f"reason code {value} hex mismatch: {row[1]}")
            codes.append(
                Code(
                    value=value,
                    hex=hex_code(value),
                    name=row[2].strip(),
                    packets=tuple(p.strip() for p in row[3].split(",") if p.strip()),
                    section=section_id,
                )
            )
        return codes

    def return_codes(self) -> list[Code]:
        if "connack_codes" not in self.where:
            return []
        codes: list[Code] = []
        connack_id = self.where["connack_codes"]
        table = _find_table(self.section(connack_id), "Value")
        c_name = _column(table.rows[0], "Return Code")
        for row in table.rows[1:]:
            if not row[0].strip().isdigit():
                continue
            value = int(row[0])
            name = re.sub(r"^0x[0-9A-Fa-f]+\s*", "", row[c_name]).strip()
            codes.append(
                Code(
                    value=value,
                    hex=hex_code(value),
                    name=name,
                    packets=("CONNACK",),
                    section=connack_id,
                )
            )
        suback_id = self.where["suback_codes"]
        for block in self.section(suback_id).blocks:
            if not isinstance(block, RawParagraph) or not block.text.startswith("0x"):
                continue
            for match in SUBACK_CODE.finditer(block.text):
                value = int(match.group(1), 16)
                codes.append(
                    Code(
                        value=value,
                        hex=hex_code(value),
                        name=match.group(2).strip(),
                        packets=("SUBACK",),
                        section=suback_id,
                    )
                )
        return codes

    # Glossary ---------------------------------------------------------------

    def terms(self) -> list[Term]:
        section_id = self.where["glossary"]
        terms: list[tuple[str, list[str]]] = []
        for block in self.section(section_id).blocks:
            if isinstance(block, RawTable):
                if terms:
                    terms[-1][1].extend(row_text(r) for r in block.rows)
                continue
            match = TERM_START.match(block.text) if block.kind == "paragraph" else None
            if match:
                terms.append(
                    (match.group(1), [match.group(2)] if match.group(2) else [])
                )
            elif terms:
                terms[-1][1].append(block.text)
        return [
            Term(name=name, definition=" ".join(parts), section=section_id)
            for name, parts in terms
        ]

    # Assembly ---------------------------------------------------------------

    def build(self) -> SpecIndex:
        reqs, per_section = self.requirements()
        sections = [
            Section(
                id=s.id,
                title=s.title,
                level=s.level,
                parent=s.parent,
                blocks=tuple(_convert_block(b) for b in s.blocks),
                requirements=tuple(per_section[s.id]),
            )
            for s in self.doc.sections
        ]
        return SpecIndex(
            metadata=self.pin.metadata(),
            sections=tuple(sections),
            packets=tuple(self.packets()),
            properties=tuple(self.properties()),
            reason_codes=tuple(self.reason_codes()),
            return_codes=tuple(self.return_codes()),
            requirements=tuple(reqs),
            terms=tuple(self.terms()),
        )


def build_index(document: Document, pin: Pin) -> SpecIndex:
    return Builder(document, pin).build()
