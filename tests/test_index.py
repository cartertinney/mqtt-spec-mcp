"""Facts about the bundled indexes; expected values come from the publications."""

from __future__ import annotations

import pytest

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.loader import load_index
from mqtt_spec_mcp.schema import ListItem
from mqtt_spec_mcp.textutil import natural_key
from scripts.indexgen.completeness import EXPECTED_COUNTS, check_completeness


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
def test_bundled_index_passes_completeness_gate(version: str) -> None:
    index = load_index(version)
    check_completeness(index, version)
    counts = EXPECTED_COUNTS[version]
    assert len(index.sections) == counts["sections"]
    assert len(index.requirements) == counts["requirements"]
    assert len(index.terms) == counts["terms"]


def test_packet_values(engines: dict[str, SpecEngine]) -> None:
    v311 = {p.value: p.name for p in engines["3.1.1"].packets}
    v50 = {p.value: p.name for p in engines["5.0"].packets}
    assert sorted(v311) == list(range(1, 15))
    assert "AUTH" not in v311.values()
    assert sorted(v50) == list(range(1, 16))
    assert v50[15] == "AUTH"


def test_311_return_codes(engines: dict[str, SpecEngine]) -> None:
    codes = {(c.packets[0], c.value): c for c in engines["3.1.1"].index.return_codes}
    assert codes[("CONNACK", 0)].name == "Connection Accepted"
    assert (
        codes[("CONNACK", 1)].name
        == "Connection Refused, unacceptable protocol version"
    )
    assert codes[("SUBACK", 0)].name == "Success - Maximum QoS 0"
    assert codes[("SUBACK", 128)].name == "Failure"
    assert codes[("CONNACK", 0)].section == "3.2.2.3"
    assert codes[("SUBACK", 128)].section == "3.9.3"


def test_311_glossary(engines: dict[str, SpecEngine]) -> None:
    terms = {t.name: t.definition for t in engines["3.1.1"].index.terms}
    assert set(terms) == {
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
    assert "fourteen" in terms["MQTT Control Packet"]
    for definition in terms.values():
        for other in terms:
            assert f"{other}:" not in definition


def test_50_glossary_adds_terms(engines: dict[str, SpecEngine]) -> None:
    names = {t.name for t in engines["5.0"].index.terms}
    assert {
        "Shared Subscription",
        "Wildcard Subscription",
        "Malformed Packet",
        "Protocol Error",
        "Will Message",
        "Disallowed Unicode code point",
    } <= names
    assert len(names) == 15


@pytest.mark.parametrize(
    ("label", "section"),
    [
        ("MQTT-3.1.2-16", "3.1.2.7"),
        ("MQTT-3.1.2.7", "3.1.2.4"),
        ("MQTT-3-8.3-4", "3.8.3"),
        ("MQTT-3.3.1-1", "3.3.1.1"),
    ],
)
def test_311_requirement_placement(
    engines: dict[str, SpecEngine], label: str, section: str
) -> None:
    req = engines["3.1.1"].requirements[label]
    assert req.section == section
    assert label in engines["3.1.1"].sections[section].section.requirements


def test_311_printed_label_kept_in_excerpt(engines: dict[str, SpecEngine]) -> None:
    excerpt = engines["3.1.1"].requirements["MQTT-3.3.1-1"].excerpt
    assert "[MQTT-3.3.1.-1]" in excerpt


def test_labels_inside_list_items_are_captured(engines: dict[str, SpecEngine]) -> None:
    for engine in engines.values():
        found = False
        for req in engine.index.requirements:
            section = engine.sections[req.section].section
            items = [b for b in section.blocks if isinstance(b, ListItem)]
            if any(req.excerpt == item.text for item in items):
                found = True
                break
        assert found, engine.version


def test_requirements_sorted_naturally(engines: dict[str, SpecEngine]) -> None:
    for engine in engines.values():
        labels = [r.label for r in engine.index.requirements]
        assert labels == sorted(labels, key=natural_key)


def test_50_property_and_reason_code_facts(engines: dict[str, SpecEngine]) -> None:
    index = engines["5.0"].index
    props = {p.hex: p for p in index.properties}
    assert props["0x11"].name == "Session Expiry Interval"
    assert props["0x11"].data_type == "Four Byte Integer"
    assert "3.1.2.11.2" in props["0x11"].sections
    assert "Will Properties" in props["0x18"].packets
    zero = [c for c in index.reason_codes if c.value == 0]
    assert len(zero) == 3


def test_tables_are_rectangular(engines: dict[str, SpecEngine]) -> None:
    for engine in engines.values():
        for section in engine.index.sections:
            for block in section.blocks:
                if block.type == "table":
                    widths = {len(row) for row in block.rows}
                    assert len(widths) == 1, (engine.version, section.id)


def test_appendix_text_not_in_last_section(engines: dict[str, SpecEngine]) -> None:
    for engine in engines.values():
        last = engine.ordered[-1]
        assert "Appendix" not in last.body
        assert "Acknowledg" not in last.body
