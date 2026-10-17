"""Completeness gate, generator CLI and strict loader behaviour."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mqtt_spec_mcp.loader import IndexLoadError, load_index, parse_index
from mqtt_spec_mcp.schema import Code, Packet, SpecIndex
from mqtt_spec_mcp.versions import PINS, Pin
from scripts import generate_index
from scripts.indexgen.canonical import canonicalize, sha256
from scripts.indexgen.completeness import CompletenessError, check_completeness

DATA = Path(__file__).resolve().parent.parent / "src" / "mqtt_spec_mcp" / "data"


@pytest.fixture(scope="module")
def v311() -> SpecIndex:
    return load_index("3.1.1")


@pytest.fixture(scope="module")
def v50() -> SpecIndex:
    return load_index("5.0")


def problems(index: SpecIndex, version: str) -> list[str]:
    with pytest.raises(CompletenessError) as info:
        check_completeness(index, version)
    return info.value.problems


# Completeness gate -----------------------------------------------------------------


def test_rejects_partial_document(v50: SpecIndex) -> None:
    partial = v50.model_copy(update={"sections": v50.sections[:100]})
    found = problems(partial, "5.0")
    assert any("expected 291 sections" in p for p in found)


def test_rejects_dangling_catalog_section(v50: SpecIndex) -> None:
    prop = v50.properties[0].model_copy(update={"sections": ("9.9.9",)})
    broken = v50.model_copy(update={"properties": (prop, *v50.properties[1:])})
    assert any("missing section 9.9.9" in p for p in problems(broken, "5.0"))


def test_rejects_return_code_for_unknown_packet(v311: SpecIndex) -> None:
    code = v311.return_codes[0].model_copy(update={"packets": ("BOGUS",)})
    broken = v311.model_copy(update={"return_codes": (code, *v311.return_codes[1:])})
    assert any("unknown packet BOGUS" in p for p in problems(broken, "3.1.1"))


def test_rejects_duplicate_return_code_replacing_missing_one(v311: SpecIndex) -> None:
    codes = list(v311.return_codes)
    suback_two = next(i for i, c in enumerate(codes) if c.packets == ("SUBACK",))
    codes[suback_two + 2] = codes[suback_two]  # SUBACK 0 twice, SUBACK 2 missing
    broken = v311.model_copy(update={"return_codes": tuple(codes)})
    assert len(broken.return_codes) == 10
    assert any("return codes must be exactly" in p for p in problems(broken, "3.1.1"))


def test_rejects_reserved_311_packet_type(v311: SpecIndex) -> None:
    reserved = Packet(
        name="Reserved",
        value=15,
        direction="Forbidden",
        description="Reserved",
        fixed_header_flags="0000",
        section="3.14",
    )
    broken = v311.model_copy(update={"packets": (*v311.packets[1:], reserved)})
    assert any(
        "packet values must be exactly 1..14" in p for p in problems(broken, "3.1.1")
    )


def test_rejects_unknown_flags_and_duplicate_labels(v50: SpecIndex) -> None:
    packet = v50.packets[0].model_copy(update={"fixed_header_flags": ""})
    reqs = (*v50.requirements, v50.requirements[0])
    broken = v50.model_copy(
        update={"packets": (packet, *v50.packets[1:]), "requirements": reqs}
    )
    found = problems(broken, "5.0")
    assert any("unknown fixed-header flags" in p for p in found)
    assert any("duplicate requirement label" in p for p in found)


def test_rejects_requirement_missing_from_its_section(v50: SpecIndex) -> None:
    req = v50.requirements[0].model_copy(update={"section": "1"})
    broken = v50.model_copy(update={"requirements": (req, *v50.requirements[1:])})
    assert any("missing from section 1" in p for p in problems(broken, "5.0"))


def test_rejects_bad_parent_and_duplicate_section(v50: SpecIndex) -> None:
    orphan = v50.sections[1].model_copy(update={"parent": "42"})
    sections = (v50.sections[0], orphan, *v50.sections[2:], v50.sections[3])
    found = problems(v50.model_copy(update={"sections": sections}), "5.0")
    assert any("unknown parent 42" in p for p in found)
    assert any("duplicate section id" in p for p in found)


def test_rejects_duplicates_replacing_missing_catalog_entries(v50: SpecIndex) -> None:
    props = (v50.properties[0], v50.properties[0], *v50.properties[2:])
    found = problems(v50.model_copy(update={"properties": props}), "5.0")
    assert any("duplicate property id 1" in p for p in found)
    assert any("property identifiers differ" in p for p in found)
    codes = (v50.reason_codes[0], v50.reason_codes[0], *v50.reason_codes[2:])
    found = problems(v50.model_copy(update={"reason_codes": codes}), "5.0")
    assert any("duplicate reason code row" in p for p in found)
    terms = (v50.terms[0], v50.terms[0], *v50.terms[2:])
    found = problems(v50.model_copy(update={"terms": terms}), "5.0")
    assert any("duplicate glossary term" in p for p in found)
    assert any("glossary terms differ" in p for p in found)


def test_rejects_property_without_section_in_packet_subtree(v50: SpecIndex) -> None:
    index = next(i for i, p in enumerate(v50.properties) if p.hex == "0x02")
    prop = v50.properties[index]
    trimmed = prop.model_copy(
        update={"sections": tuple(s for s in prop.sections if not s.startswith("3.3."))}
    )
    props = (*v50.properties[:index], trimmed, *v50.properties[index + 1 :])
    found = problems(v50.model_copy(update={"properties": props}), "5.0")
    assert any("has no section under PUBLISH" in p for p in found)


def test_reason_code_for_unknown_packet(v50: SpecIndex) -> None:
    code = Code(value=1, hex="0x01", name="x", packets=("NOPE",), section="2.4")
    broken = v50.model_copy(update={"reason_codes": (*v50.reason_codes[1:], code)})
    assert any("unknown packet NOPE" in p for p in problems(broken, "5.0"))


# Generator -------------------------------------------------------------------------


def test_generator_rejects_unknown_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        generate_index.main(["--version", "3.1"])
    assert info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


class _Explodes:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError("the other version's pin must not be consulted")


def test_generator_checks_only_selected_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.html"
    source.write_bytes(b"<html><body><h1>1 Intro</h1></body></html>")
    output = tmp_path / "out.json"
    monkeypatch.setitem(PINS, "5.0", _Explodes())
    code = generate_index.main(
        ["--version", "3.1.1", "--source", str(source), "--output", str(output)]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "MQTT 3.1.1 source checksum mismatch" in err
    assert "Nothing was written" in err
    assert not output.exists()


def test_generator_writes_nothing_on_completeness_failure(tmp_path: Path) -> None:
    raw = (
        b"<html><body><h1>1 Introduction</h1><h2>1.2 Terminology</h2>"
        b"<p>Client: A program.</p><h1>2 Format</h1><h2>2.2 Fixed</h2>"
        b"<h3>2.2.1 Type</h3><table><tr><td>Name</td><td>Value</td>"
        b"<td>Direction of flow</td><td>Description</td></tr>"
        b"<tr><td>CONNECT</td><td>1</td><td>C to S</td><td>Connect</td></tr></table>"
        b"<h3>2.2.2 Flags</h3><table><tr><td>Control Packet</td><td>Flags</td>"
        b"<td>Bit 3</td><td>Bit 2</td><td>Bit 1</td><td>Bit 0</td></tr>"
        b"<tr><td>CONNECT</td><td>Reserved</td><td>0</td><td>0</td><td>0</td>"
        b"<td>0</td></tr></table><h1>3 Packets</h1><h2>3.1 CONNECT</h2>"
        b"<h2>3.2 CONNACK</h2><h3>3.2.2 Var</h3><h4>3.2.2.3 Return</h4>"
        b"<table><tr><td>Value</td><td>Return Code Response</td></tr>"
        b"<tr><td>0</td><td>0x00 Connection Accepted</td></tr></table>"
        b"<h2>3.9 SUBACK</h2><h3>3.9.3 Payload</h3><p>0x80 - Failure</p>"
        b"</body></html>"
    )
    pin = Pin(**{**PINS["3.1.1"].__dict__, "source_sha256": sha256(canonicalize(raw))})
    output = tmp_path / "partial.json"
    with pytest.raises(CompletenessError):
        generate_index.generate(pin, raw, output)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


# Loader ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["format_version", "title", "version", "published", "source_url", "source_sha256"],
)
def test_loader_rejects_changed_pinned_metadata(field: str) -> None:
    data = json.loads((DATA / "mqtt-v5.0.json").read_text("utf-8"))
    original = data["metadata"][field]
    data["metadata"][field] = (
        original + 1 if isinstance(original, int) else original + "x"
    )
    with pytest.raises(IndexLoadError):
        parse_index(json.dumps(data), "5.0")


def test_loader_rejects_other_versions_index() -> None:
    raw = (DATA / "mqtt-v3.1.1.json").read_bytes()
    parse_index(raw, "3.1.1")
    with pytest.raises(IndexLoadError, match="pinned publication"):
        parse_index(raw, "5.0")


def test_loader_rejects_unknown_fields_and_unsupported_version() -> None:
    data = json.loads((DATA / "mqtt-v3.1.1.json").read_text("utf-8"))
    data["sections"][0]["extra"] = True
    with pytest.raises(IndexLoadError, match="validation"):
        parse_index(json.dumps(data), "3.1.1")
    with pytest.raises(IndexLoadError, match="unsupported"):
        load_index("3.1")
    with pytest.raises(IndexLoadError, match="unsupported"):
        parse_index((DATA / "mqtt-v3.1.1.json").read_bytes(), "4.0")


def test_loaded_index_is_immutable(v50: SpecIndex) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        v50.sections[0].title = "changed"
