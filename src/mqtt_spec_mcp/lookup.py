"""Exact entity lookups over the structured catalogs."""

from __future__ import annotations

import re
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
_NUMBER = re.compile(r"^(?:0x([0-9a-f]+)|(\d+))$", re.IGNORECASE)


def parse_number(text: str) -> int | None:
    match = _NUMBER.match(text.strip())
    if not match:
        return None
    return int(match.group(1), 16) if match.group(1) else int(match.group(2))


def _codes_out(codes: list[Code]) -> list[dict[str, Any]]:
    return [
        {
            "value": c.value,
            "hex": c.hex,
            "name": c.name,
            "packets": list(c.packets),
            "section": c.section,
        }
        for c in codes
    ]


class Lookup:
    def __init__(self, engine: SpecEngine) -> None:
        self.engine = engine
        self.v = engine.version

    def error(self, message: str) -> Exception:
        return self.engine.error(message)

    # Shared helpers --------------------------------------------------------------

    def find_packet(self, text: str) -> Packet | None:
        number = parse_number(text)
        wanted = normalize(text)
        for packet in self.engine.packets:
            if number is not None and packet.value == number:
                return packet
            if normalize(packet.name) == wanted:
                return packet
        return None

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
        if kind == "return_code" and (
            packet is None or normalize(packet).upper() not in RETURN_CODE_PACKETS
        ):
            given = "no packet" if packet is None else f"packet='{packet}'"
            raise self.error(
                "kind=return_code requires packet=CONNACK or packet=SUBACK (got "
                f"{given}). MQTT 3.1.1 only defines return codes for those two packets."
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
            "details": self._property_details(prop, packet) if packet else [],
        }

    def _property_details(self, prop: Property, packet: str) -> list[dict[str, Any]]:
        engine = self.engine
        if packet == WILL_PROPERTIES:
            root = WILL_SECTION
        else:
            found = self.find_packet(packet)
            root = found.section if found else ""
        details: list[dict[str, Any]] = []
        for section_id in prop.sections:
            if not engine.in_subtree(section_id, root):
                continue
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
        codes: list[Code] = self._match_named(
            list(self.engine.index.reason_codes), query, "value"
        )
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
        return {"kind": "reason_code", "matches": _codes_out(codes)}

    def _return_code(self, query: str, packet: str | None) -> dict[str, Any]:
        assert packet is not None
        candidates = [c for c in self.engine.index.return_codes if packet in c.packets]
        number = parse_number(query)
        if number is not None:
            codes = [c for c in candidates if c.value == number]
        else:
            wanted = normalize(query)
            codes = [c for c in candidates if normalize(c.name) == wanted]
        if not codes:
            valid = ", ".join(f"{c.hex} {c.name}" for c in candidates)
            raise self.error(
                f"No MQTT 3.1.1 return code matches '{query}' for {packet}. Valid "
                f"{packet} return codes are: {valid}. Do not substitute MQTT 5.0 "
                "reason code meanings; they differ."
            )
        result_codes = [
            Code(
                value=c.value,
                hex=c.hex,
                name=c.name,
                packets=(packet,),
                section=c.section,
            )
            for c in codes
        ]
        return {"kind": "return_code", "matches": _codes_out(result_codes)}

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
