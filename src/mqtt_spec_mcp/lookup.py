"""Exact entity lookups over the structured catalogs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from .schema import Code, Packet, Property
from .search import PARENT_SNIPPET_CHARS, window
from .textutil import LABEL_PATTERN, canonical_label, normalize

if TYPE_CHECKING:
    from .engine import SpecEngine

KINDS = ("packet", "property", "reason_code", "return_code", "requirement", "term")
MAX_MATCHES = 10
MAX_TERM_HINTS = 8
WILL_PROPERTIES = "Will Properties"
WILL_SECTION = "3.1.3.2"
RETURN_CODE_PACKETS = ("CONNACK", "SUBACK")
PROPERTY_TABLE_SECTION = "2.2.2.2"
# Spec text returned with a lookup, so the entry can be answered without a get.
SUMMARY_CHARS = 600
FIXED_HEADER_CHARS = 400
ACTIONS_CHARS = 600
# Without a packet filter, properties defined in more sections return no excerpts.
DETAIL_SECTIONS = 3
# Reason and return codes carry their per-packet meanings for this many matches.
MEANING_MATCHES = 3
_FIXED_REMAINING_LENGTH = re.compile(r"Remaining Length \((\d+)\)")
_PACKET_SUFFIX = re.compile(r"\s+(?:mqtt\s+)?(?:control\s+)?packet$")
# Catalog values are small; longer digit strings are treated as names (no match).
_NUMBER = re.compile(r"^(?:0x([0-9a-f]{1,8})|(\d{1,9}))$", re.IGNORECASE)


def parse_number(text: str) -> int | None:
    match = _NUMBER.match(text.strip())
    if not match:
        return None
    return int(match.group(1), 16) if match.group(1) else int(match.group(2))


def _codes_out(
    codes: list[Code], meanings: list[list[dict[str, str]]] | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, c in enumerate(codes):
        entry: dict[str, Any] = {
            "value": c.value,
            "hex": c.hex,
            "name": c.name,
            "packets": list(c.packets),
            "section": c.section,
        }
        if meanings and meanings[i]:
            entry["meanings"] = meanings[i]
        out.append(entry)
    return out


class Lookup:
    def __init__(self, engine: SpecEngine) -> None:
        self.engine = engine
        self.v = engine.version

    def error(self, message: str) -> Exception:
        return self.engine.error(message)

    # Shared helpers --------------------------------------------------------------

    def find_packet(self, text: str) -> Packet | None:
        number = parse_number(text)
        wanted = _PACKET_SUFFIX.sub("", normalize(text))
        for packet in self.engine.packets:
            if number is not None and packet.value == number:
                return packet
            if normalize(packet.name) == wanted:
                return packet
        return None

    def _lines(self, root: str) -> Iterator[tuple[str, str]]:
        for section_id, info in self.engine.sections.items():
            if self.engine.in_subtree(section_id, root):
                for line in info.body.splitlines():
                    yield section_id, line

    def _meanings(self, code: Code, packets: Iterable[str]) -> list[dict[str, str]]:
        """The code's row in each packet's own code table: what it means there."""
        row = re.compile(
            rf"\s*{code.value}\s*\|\s*{re.escape(code.hex)}\b", re.IGNORECASE
        )
        out: list[dict[str, str]] = []
        for name in packets:
            packet = self.find_packet(name)
            if packet is None:
                continue
            for section_id, line in self._lines(packet.section):
                if row.match(line):
                    cells = [cell.strip() for cell in line.split("|")]
                    entry = {"packet": packet.name, "section": section_id}
                    if len(cells) == 5:  # Value | Hex | Name | Sent by | Description
                        entry["sent_by"] = cells[3]
                    entry["meaning"] = cells[-1]
                    out.append(entry)
                    break
        return out

    def _code_meanings(
        self, codes: list[Code], packet: str | None
    ) -> list[list[dict[str, str]]] | None:
        if len(codes) > MEANING_MATCHES:
            return None
        return [self._meanings(c, (packet,) if packet else c.packets) for c in codes]

    def packet_names(self) -> str:
        return ", ".join(f"{p.name} ({p.value})" for p in self.engine.packets)

    def resolve_packet_filter(self, kind: str, packet: str) -> str:
        if kind == "property" and normalize(packet) in ("will", "will properties"):
            return WILL_PROPERTIES
        found = self.find_packet(packet)
        if found is None:
            extra = (
                " For kind=property, 'Will Properties' is also accepted."
                if (kind == "property")
                else ""
            )
            raise self.error(
                f"The packet filter '{packet}' is not an MQTT {self.v} Control Packet. "
                f"Valid packets are: {self.packet_names()}.{extra}"
            )
        return found.name

    # Dispatch --------------------------------------------------------------------

    def run(self, kind: str, query: str, packet: str | None) -> dict[str, Any]:
        engine = self.engine
        engine.guard("query", query)
        engine.guard("packet filter", packet)
        query = query.strip()
        if not query:
            raise self.error("The query is empty. Pass an exact name, number or label.")
        if self.v == "3.1.1" and kind in ("property", "reason_code"):
            raise self.error(
                f"MQTT 3.1.1 has no {kind} catalog; properties and reason codes were "
                "introduced in MQTT 5.0. Use kind=return_code with packet=CONNACK or "
                "packet=SUBACK for 3.1.1 result values, or mqtt_spec_search for other "
                "topics."
            )
        if self.v == "5.0" and kind == "return_code":
            raise self.error(
                "kind=return_code is 3.1.1-only. MQTT 5.0 uses reason codes, and "
                "numeric values are not interchangeable between versions. Use "
                "kind=reason_code with version='5.0' instead."
            )
        if packet is not None and kind in ("packet", "requirement", "term"):
            raise self.error(
                f"The packet filter does not apply to kind={kind}. Omit packet and "
                "call again."
            )
        if (
            kind == "return_code"
            and packet is not None
            and (
                (found := self.find_packet(packet)) is None
                or found.name not in RETURN_CODE_PACKETS
            )
        ):
            raise self.error(
                "kind=return_code takes packet=CONNACK, packet=SUBACK or no packet "
                f"filter (got packet='{packet}'). MQTT 3.1.1 only defines return codes "
                "for those two packets."
            )
        packet_filter = self.resolve_packet_filter(kind, packet) if packet else None
        handler = getattr(self, f"_{kind}")
        result: dict[str, Any] = handler(query, packet_filter)
        out = engine.envelope()
        out.update(result)
        return out

    # Kinds -------------------------------------------------------------------------

    def _packet(self, query: str, _: str | None) -> dict[str, Any]:
        packet = self.find_packet(query)
        if packet is None:
            raise self.error(
                f"No MQTT Control Packet matches '{query}' in MQTT {self.v}. Valid "
                f"packets are: {self.packet_names()}."
            )
        result: dict[str, Any] = {
            "kind": "packet",
            "name": packet.name,
            "value": packet.value,
            "direction": packet.direction,
            "description": packet.description,
            "fixed_header_flags": packet.fixed_header_flags,
            "section": packet.section,
        }
        sections = self.engine.sections
        result["summary"] = window(sections[packet.section].body, None, SUMMARY_CHARS)
        fixed = sections.get(f"{packet.section}.1")
        if fixed is not None:
            length = _FIXED_REMAINING_LENGTH.search(fixed.body)
            if length:
                result["remaining_length"] = int(length.group(1))
            prose = " ".join(
                line.strip()
                for line in fixed.body.splitlines()
                if line.strip() and "|" not in line and not line.startswith("Figure")
            )
            if prose:
                result["fixed_header"] = window(prose, None, FIXED_HEADER_CHARS)
        actions = [
            {
                "section": child.id,
                "title": child.title,
                "text": window(child.body, None, ACTIONS_CHARS),
            }
            for child in map(sections.__getitem__, sections[packet.section].children)
            if child.title.endswith("Actions") or child.title == "Response"
        ]
        if actions:
            result["actions"] = actions
        index = self.engine.index
        if self.v == "5.0":
            result["properties"] = [
                {"id": p.hex, "name": p.name}
                for p in index.properties
                if packet.name in p.packets
            ]
            result["reason_codes"] = [
                {"code": c.hex, "name": c.name}
                for c in index.reason_codes
                if packet.name in c.packets
            ]
        else:
            result["return_codes"] = [
                {"code": c.hex, "name": c.name, "section": c.section}
                for c in index.return_codes
                if packet.name in c.packets
            ]
        return result

    def _match_named(self, items: list[Any], query: str, numeric: str) -> list[Any]:
        """Match by number, then exact normalized name, then name substring."""
        number = parse_number(query)
        if number is not None:
            return [i for i in items if getattr(i, numeric) == number]
        wanted = normalize(query)
        exact = [i for i in items if normalize(i.name) == wanted]
        if exact:
            return exact
        return [i for i in items if wanted in normalize(i.name)]

    def _property(self, query: str, packet: str | None) -> dict[str, Any]:
        props: list[Property] = self._match_named(
            list(self.engine.index.properties), query, "id"
        )
        if not props:
            raise self.error(
                f"No MQTT {self.v} property matches '{query}'. kind=property covers "
                "only Property Identifier entries (Table 2-4 in section 2.2.2.2). For "
                "header flags or options such as Will Flag, Clean Start or Retain "
                "Handling, use mqtt_spec_search instead."
            )
        if packet is not None:
            valid = [p for p in props if packet in p.packets]
            if not valid:
                if len(props) == 1:
                    allowed = ", ".join(props[0].packets)
                    raise self.error(
                        f"Property '{props[0].name}' ({props[0].hex}) exists, but not "
                        f"for {packet}. It is valid for: {allowed}."
                    )
                names = ", ".join(f"{p.name} ({p.hex})" for p in props[:MAX_MATCHES])
                raise self.error(
                    f"Properties matching '{query}' exist ({names}), but none is valid "
                    f"for {packet}."
                )
            props = valid
        if len(props) > 1:
            return {
                "kind": "property",
                "matches": [
                    {"id": p.hex, "name": p.name, "sections": list(p.sections)}
                    for p in props[:MAX_MATCHES]
                ],
            }
        prop = props[0]
        return {
            "kind": "property",
            "id": prop.id,
            "hex": prop.hex,
            "name": prop.name,
            "data_type": prop.data_type,
            "packets": list(prop.packets),
            "sections": list(prop.sections),
            "details": self._property_details(prop, packet),
        }

    def _property_details(
        self, prop: Property, packet: str | None
    ) -> list[dict[str, Any]]:
        engine = self.engine
        if packet is None:
            ids = [s for s in prop.sections if s != PROPERTY_TABLE_SECTION]
            if len(ids) > DETAIL_SECTIONS:
                return []
        else:
            if packet == WILL_PROPERTIES:
                root = WILL_SECTION
            else:
                found = self.find_packet(packet)
                root = found.section if found else ""
            ids = [s for s in prop.sections if engine.in_subtree(s, root)]
        details: list[dict[str, Any]] = []
        for section_id in ids:
            info = engine.sections[section_id]
            excerpt = window(info.body, None, PARENT_SNIPPET_CHARS)
            labels: list[str] = []
            for match in LABEL_PATTERN.finditer(excerpt):
                label = canonical_label(match.group(1), self.v)
                if label not in labels:
                    labels.append(label)
            details.append(
                {
                    "section": info.id,
                    "title": info.title,
                    "excerpt": excerpt,
                    "requirements": labels,
                }
            )
        return details

    def _reason_code(self, query: str, packet: str | None) -> dict[str, Any]:
        all_codes = list(self.engine.index.reason_codes)
        names_packet = parse_number(query) is None and not any(
            normalize(c.name) == normalize(query) for c in all_codes
        )
        if names_packet and self.find_packet(query) is not None:
            # A packet name would otherwise substring-match names such as
            # "Normal disconnection"; point the caller at the right tool instead.
            raise self.error(
                f"'{query}' is an MQTT Control Packet name, not a reason code. Use "
                f"kind=packet with query='{query}' to list the reason codes that "
                f"packet can carry, or pass packet='{query}' with a reason code value "
                "or name."
            )
        codes: list[Code] = self._match_named(all_codes, query, "value")
        if not codes:
            if self.find_packet(query) is not None and parse_number(query) is None:
                raise self.error(
                    f"'{query}' is an MQTT Control Packet name, not a reason code. Use "
                    f"kind=packet to look up the packet, or pass packet='{query}' with "
                    "a reason code value or name to filter reason codes."
                )
            raise self.error(
                f"No MQTT {self.v} reason code matches '{query}'. Pass a decimal "
                "value, a 0x hex value, or a reason code name from Table 2-6 (section "
                "2.4)."
            )
        if packet is not None:
            valid = [c for c in codes if packet in c.packets]
            if not valid:
                allowed = sorted({p for c in codes for p in c.packets})
                raise self.error(
                    f"Reason code '{query}' exists, but not for {packet}. It is valid "
                    f"for: {', '.join(allowed)}."
                )
            codes = valid
        return {
            "kind": "reason_code",
            "matches": _codes_out(codes, self._code_meanings(codes, packet)),
        }

    def _return_code(self, query: str, packet: str | None) -> dict[str, Any]:
        """Codes by value or name; a CONNACK or SUBACK query lists that packet's
        codes. Without a packet filter, matches from both packets are returned, each
        naming its packet (their values overlap)."""
        number = parse_number(query)
        named = self.find_packet(query) if number is None else None
        if named is not None:
            if named.name not in RETURN_CODE_PACKETS or packet not in (
                None,
                named.name,
            ):
                raise self.error(
                    f"'{query}' is a packet name. MQTT 3.1.1 only defines return codes "
                    "for CONNACK and SUBACK; pass query='CONNACK' or query='SUBACK' to "
                    "list them, or a return code value or name."
                )
            packet = named.name
        candidates = [
            c
            for c in self.engine.index.return_codes
            if packet is None or packet in c.packets
        ]
        if named is not None:
            codes = candidates
        elif number is not None:
            codes = [c for c in candidates if c.value == number]
        else:
            codes = self._match_named(candidates, query, "value")
        if not codes:
            valid = ", ".join(
                f"{c.hex} {c.name}" + ("" if packet else f" ({c.packets[0]})")
                for c in candidates
            )
            scope = f"for {packet}" if packet else "for CONNACK or SUBACK"
            raise self.error(
                f"No MQTT 3.1.1 return code matches '{query}' {scope}. Valid return "
                f"codes are: {valid}. Do not substitute MQTT 5.0 reason code "
                "meanings; they differ."
            )
        return {
            "kind": "return_code",
            "matches": _codes_out(codes, [self._meanings(c, c.packets) for c in codes]),
        }

    def _requirement(self, query: str, _: str | None) -> dict[str, Any]:
        label = canonical_label(query, self.v)
        req = self.engine.requirements.get(label)
        if req is None:
            raise self.error(
                f"No normative requirement matches '{query}' in MQTT {self.v}. Labels "
                "look like MQTT-3.1.2-4 and are numbered differently in each version; "
                "use mqtt_spec_search to find the statement by topic."
            )
        info = self.engine.sections[req.section]
        return {
            "kind": "requirement",
            "id": req.label,
            "section": req.section,
            "title": info.title,
            "excerpt": req.excerpt,
        }

    def _term(self, query: str, _: str | None) -> dict[str, Any]:
        terms = list(self.engine.index.terms)
        wanted = normalize(query)
        matches = [t for t in terms if normalize(t.name) == wanted]
        if not matches:
            matches = [t for t in terms if wanted in normalize(t.name)]
        if not matches:
            hints = ", ".join(t.name for t in terms[:MAX_TERM_HINTS])
            raise self.error(
                f"No MQTT glossary term matches '{query}'. The MQTT {self.v} glossary "
                f"(section 1.2) defines: {hints}. For packet fields, flags or other "
                "concepts, use mqtt_spec_search."
            )
        return {
            "kind": "term",
            "matches": [
                {"name": t.name, "definition": t.definition, "section": t.section}
                for t in matches[:MAX_MATCHES]
            ],
        }


def lookup(
    engine: SpecEngine, kind: str, query: str, packet: str | None
) -> dict[str, Any]:
    return Lookup(engine).run(kind, query, packet)
