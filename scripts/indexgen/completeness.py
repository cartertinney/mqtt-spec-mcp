"""Completeness gate: generation fails unless every check passes exactly."""

from __future__ import annotations

from collections import Counter

from mqtt_spec_mcp.schema import SpecIndex

EXPECTED_COUNTS: dict[str, dict[str, int]] = {
    "3.1.1": {
        "sections": 162,
        "packets": 14,
        "properties": 0,
        "reason_codes": 0,
        "return_codes": 10,
        "requirements": 141,
        "terms": 9,
    },
    "5.0": {
        "sections": 291,
        "packets": 15,
        "properties": 27,
        "reason_codes": 45,
        "return_codes": 0,
        "requirements": 252,
        "terms": 15,
    },
}

EXPECTED_RETURN_CODES = {
    ("CONNACK", 0),
    ("CONNACK", 1),
    ("CONNACK", 2),
    ("CONNACK", 3),
    ("CONNACK", 4),
    ("CONNACK", 5),
    ("SUBACK", 0),
    ("SUBACK", 1),
    ("SUBACK", 2),
    ("SUBACK", 128),
}

# Catalog identities stated by the publications themselves (glossary in 1.2,
# Property Identifier table in 5.0 section 2.2.2.2).
SHARED_TERMS = {
    "Network Connection",
    "Application Message",
    "Client",
    "Server",
    "Session",
    "Subscription",
    "Topic Name",
    "Topic Filter",
    "MQTT Control Packet",
}
EXPECTED_TERMS: dict[str, set[str]] = {
    "3.1.1": SHARED_TERMS,
    "5.0": SHARED_TERMS
    | {
        "Shared Subscription",
        "Wildcard Subscription",
        "Malformed Packet",
        "Protocol Error",
        "Will Message",
        "Disallowed Unicode code point",
    },
}
EXPECTED_PROPERTY_IDS: dict[str, set[int]] = {
    "3.1.1": set(),
    "5.0": {1, 2, 3, 8, 9, 11, 17, 18, 19, 21, 22, 23, 24, 25, 26, 28, 31, 33}
    | {34, 35, 36, 37, 38, 39, 40, 41, 42},
}

WILL_PROPERTIES = "Will Properties"
WILL_PROPERTIES_SECTION = "3.1.3.2"


class CompletenessError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            "index failed completeness checks:\n- " + "\n- ".join(problems)
        )


def _dupes(values: list[object]) -> list[object]:
    return [v for v, n in Counter(values).items() if n > 1]


def check_completeness(index: SpecIndex, version: str) -> None:
    problems: list[str] = []
    expected = EXPECTED_COUNTS.get(version)
    if expected is None:
        raise CompletenessError([f"unsupported version {version!r}"])
    if index.metadata.version != version:
        problems.append(f"metadata version {index.metadata.version!r} != {version!r}")

    actual = {
        "sections": len(index.sections),
        "packets": len(index.packets),
        "properties": len(index.properties),
        "reason_codes": len(index.reason_codes),
        "return_codes": len(index.return_codes),
        "requirements": len(index.requirements),
        "terms": len(index.terms),
    }
    for key, want in expected.items():
        if actual[key] != want:
            problems.append(f"expected {want} {key}, found {actual[key]}")

    section_ids = [s.id for s in index.sections]
    sections = set(section_ids)
    for dup in _dupes(list(section_ids)):
        problems.append(f"duplicate section id {dup}")
    for s in index.sections:
        if s.parent is not None and s.parent not in sections:
            problems.append(f"section {s.id} has unknown parent {s.parent}")

    names = [p.name for p in index.packets]
    values = [p.value for p in index.packets]
    for dup in _dupes(list(names)):
        problems.append(f"duplicate packet name {dup}")
    for dup in _dupes(list(values)):
        problems.append(f"duplicate packet value {dup}")
    if sorted(values) != list(range(1, len(values) + 1)) or (
        len(values) != expected["packets"]
    ):
        problems.append(f"packet values must be exactly 1..{expected['packets']}")
    packets = set(names)
    for p in index.packets:
        if not p.fixed_header_flags:
            problems.append(f"packet {p.name} has unknown fixed-header flags")
        if p.section not in sections:
            problems.append(f"packet {p.name} points at missing section {p.section}")

    catalogs: tuple[tuple[str, list[object]], ...] = (
        ("property id", [p.id for p in index.properties]),
        ("property name", [p.name for p in index.properties]),
        ("reason code row", [(c.value, c.name) for c in index.reason_codes]),
        ("glossary term", [t.name for t in index.terms]),
    )
    for label, entries in catalogs:
        for dup in _dupes(entries):
            problems.append(f"duplicate {label} {dup}")
    if {p.id for p in index.properties} != EXPECTED_PROPERTY_IDS[version]:
        problems.append("property identifiers differ from the publication's table")
    if {t.name for t in index.terms} != EXPECTED_TERMS[version]:
        problems.append("glossary terms differ from the publication's section 1.2")

    homes = {p.name: p.section for p in index.packets}
    homes[WILL_PROPERTIES] = WILL_PROPERTIES_SECTION
    for prop in index.properties:
        for pkt in prop.packets:
            root = homes.get(pkt)
            if root and not any(
                s == root or s.startswith(root + ".") for s in prop.sections
            ):
                problems.append(
                    f"property {prop.name} has no section under {pkt} ({root})"
                )
        for sec in prop.sections:
            if sec not in sections:
                problems.append(f"property {prop.name} points at missing section {sec}")
        for pkt in prop.packets:
            if pkt not in packets and pkt != WILL_PROPERTIES:
                problems.append(f"property {prop.name} names unknown packet {pkt}")
    for kind, codes in (("reason", index.reason_codes), ("return", index.return_codes)):
        for code in codes:
            if code.section not in sections:
                problems.append(
                    f"{kind} code {code.name} points at missing {code.section}"
                )
            for pkt in code.packets:
                if pkt not in packets:
                    problems.append(
                        f"{kind} code {code.name} names unknown packet {pkt}"
                    )

    if version == "3.1.1":
        got = [(pkt, c.value) for c in index.return_codes for pkt in c.packets]
        if len(got) != len(set(got)) or set(got) != EXPECTED_RETURN_CODES:
            problems.append(
                "3.1.1 return codes must be exactly CONNACK 0-5 and SUBACK 0, 1, 2, 128"
            )

    labels = [r.label for r in index.requirements]
    for dup in _dupes(list(labels)):
        problems.append(f"duplicate requirement label {dup}")
    known = set(labels)
    by_section = {s.id: set(s.requirements) for s in index.sections}
    for s in index.sections:
        for label in s.requirements:
            if label not in known:
                problems.append(f"section {s.id} lists unknown requirement {label}")
    for r in index.requirements:
        if r.section not in sections:
            problems.append(
                f"requirement {r.label} points at missing section {r.section}"
            )
        elif r.label not in by_section[r.section]:
            problems.append(f"requirement {r.label} missing from section {r.section}")

    for term in index.terms:
        if term.section not in sections:
            problems.append(
                f"term {term.name} points at missing section {term.section}"
            )
        if not term.definition:
            problems.append(f"term {term.name} has an empty definition")

    if problems:
        raise CompletenessError(problems)
