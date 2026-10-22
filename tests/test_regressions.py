"""Regression tests for bugs found after the first release."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.search import (
    MAX_QUERY_PARTS,
    MAX_QUERY_TERMS,
    MIN_PART_CHARS,
    MULTI_PACK_CHARS,
    PACK_CHARS,
    PACK_SECTIONS,
    RELATED_CHARS,
    _list_blocks,
    _list_context,
    _units,
    query_parts,
    query_terms,
    search,
    spec_spelling,
)
from mqtt_spec_mcp.server import MqttSpecService
from scripts.indexgen.parse import parse_document
from tests.conftest import Call, JsonCall, parse_search

GET_31 = {"section": "3.1", "include_subsections": True, "max_chars": 1000}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor",
    [_b64(b"[" * 100_000), _b64(b'{"a":' * 50_000), "A" * 600, _b64(b"[" * 300)],
)
@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
def test_hostile_cursors_are_rejected_cleanly(
    err: Callable[..., str], cursor: str, version: str
) -> None:
    message = err("mqtt_spec_get", version=version, cursor=cursor, **GET_31)
    assert "cursor is invalid" in message
    assert message.startswith(f"MQTT {version} only (")


def test_unexpected_errors_stay_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    service = MqttSpecService(["5.0"])

    def boom(raw: dict[str, Any]) -> str:
        raise KeyError("boom")

    monkeypatch.setitem(service._handlers, "mqtt_spec_outline", boom)
    outcome = service.call("mqtt_spec_outline", {"version": "5.0"})
    assert outcome.is_error
    assert outcome.text.startswith("MQTT 5.0 only (")
    assert outcome.text.endswith("No other version was queried.")
    assert "Internal error" in outcome.text
    unscoped = service.call("mqtt_spec_outline", {"version": "latest"})
    assert unscoped.is_error
    assert "Internal error" in unscoped.text


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("mqtt_spec_outline", {"parent": "", "depth": None}),
        ("mqtt_spec_outline", {"parent": None}),
        ("mqtt_spec_search", {"query": "keep alive", "chapter": " ", "limit": None}),
        ("mqtt_spec_search", {"query": "keep alive", "normative_only": None}),
        (
            "mqtt_spec_get",
            {"section": "3.1.2.4", "cursor": "", "max_chars": None},
        ),
        ("mqtt_spec_get", {"section": "3.1.2.4", "include_subsections": None}),
        ("mqtt_spec_lookup", {"kind": "packet", "query": "PUBACK", "packet": ""}),
        ("mqtt_spec_lookup", {"kind": "term", "query": "Session", "packet": None}),
    ],
)
def test_empty_optional_arguments_mean_omitted(
    call: Call, tool: str, args: dict[str, Any]
) -> None:
    outcome = call(tool, version="5.0", **args)
    assert not outcome.is_error, outcome.text


def test_required_and_unknown_arguments_stay_strict(err: Callable[..., str]) -> None:
    assert "version" in err("mqtt_spec_outline", version=None)
    assert "query" in err("mqtt_spec_search", version="5.0", query="")
    assert "bogus" in err("mqtt_spec_search", version="5.0", query="x", bogus="")
    assert "section" in err("mqtt_spec_get", version="5.0", section=None)


@pytest.mark.parametrize(
    "reference",
    [
        "Section 3.1.2.4",
        "section 3.1.2.4 Clean Start",
        "§3.1.2.4",
        "§ 3.1.2.4",
        "3.1.2.4 - Clean Start",
        "3.1.2.4: Clean Start",
        "3.1.2.4 – Clean Start",
        "3.1.2.4.",
    ],
)
def test_section_reference_forms(call: Call, reference: str) -> None:
    outcome = call("mqtt_spec_get", version="5.0", section=reference)
    assert not outcome.is_error, outcome.text
    assert outcome.text.startswith("# 3.1.2.4 Clean Start")


def test_separator_does_not_hide_title_mismatch(err: Callable[..., str]) -> None:
    message = err("mqtt_spec_get", version="5.0", section="3.1.2.4 - Clean Session")
    assert "title does not match" in message


@pytest.mark.parametrize("packet", ["CONNECT", "DISCONNECT", "AUTH", "connack"])
def test_reason_code_query_naming_a_packet(
    err: Callable[..., str], packet: str
) -> None:
    message = err("mqtt_spec_lookup", version="5.0", kind="reason_code", query=packet)
    assert "Control Packet name, not a reason code" in message
    assert "kind=packet" in message


def test_reason_code_exact_names_still_work(ok: JsonCall) -> None:
    for name in ("Malformed Packet", "Server busy", "normal disconnection"):
        result = ok("mqtt_spec_lookup", version="5.0", kind="reason_code", query=name)
        assert result["matches"]


@pytest.mark.parametrize(
    ("packet", "expected"),
    [
        ("2", "CONNACK"),
        ("9", "SUBACK"),
        ("connack packet", "CONNACK"),
        ("0x09", "SUBACK"),
    ],
)
def test_return_code_packet_by_number_or_spelling(
    ok: JsonCall, packet: str, expected: str
) -> None:
    result = ok(
        "mqtt_spec_lookup",
        version="3.1.1",
        kind="return_code",
        query="0",
        packet=packet,
    )
    assert result["matches"][0]["packets"] == [expected]


def test_packet_lookup_accepts_packet_suffix(ok: JsonCall) -> None:
    result = ok("mqtt_spec_lookup", version="5.0", kind="packet", query="PUBACK packet")
    assert result["name"] == "PUBACK"


@pytest.mark.parametrize(
    ("version", "query", "label"),
    [
        ("5.0", "MQTT-3.1.2-4.", "MQTT-3.1.2-4"),
        ("5.0", "3.1.2-4", "MQTT-3.1.2-4"),
        ("5.0", "[MQTT-3.1.2-4],", "MQTT-3.1.2-4"),
        ("3.1.1", "3.3.1.-1", "MQTT-3.3.1-1"),
    ],
)
def test_requirement_label_spellings(
    ok: JsonCall, version: str, query: str, label: str
) -> None:
    result = ok("mqtt_spec_lookup", version=version, kind="requirement", query=query)
    assert result["id"] == label
    found = ok("mqtt_spec_search", version=version, query=query, limit=1)
    assert found["results"][0]["section"] == result["section"]


@pytest.mark.parametrize("query", ["3.1.2.4.", "section 3.1.2.4", "§3.1.2.4"])
def test_search_section_id_forms(ok: JsonCall, query: str) -> None:
    result = ok("mqtt_spec_search", version="5.0", query=query)
    assert result["results"][0]["section"] == "3.1.2.4"


@pytest.mark.parametrize(
    ("version", "text"),
    [
        ("3.1.1", "MQTT-5 shared subscription"),
        ("3.1.1", "MQTT-5.0 session expiry"),
        ("5.0", "MQTT-3.1.1 clean session"),
        ("5.0", "mqtt – 3.1.1 clean session"),
    ],
)
def test_hyphenated_version_mentions(
    err: Callable[..., str], version: str, text: str
) -> None:
    assert "selected version" in err("mqtt_spec_search", version=version, query=text)


@pytest.mark.parametrize(
    ("version", "label"),
    [("3.1.1", "MQTT-3-8.3-4"), ("5.0", "MQTT-3.1.0-1"), ("3.1.1", "MQTT-3.1.2.7")],
)
def test_labels_are_not_version_mentions(
    ok: JsonCall, version: str, label: str
) -> None:
    ok("mqtt_spec_lookup", version=version, kind="requirement", query=label)


def test_bare_mqtt_3_suggests_311(err: Callable[..., str]) -> None:
    message = err("mqtt_spec_search", version="5.0", query="MQTT 3 clean session")
    assert "version='3.1.1'" in message


def test_placeholders_for_sections_without_text(call: Call) -> None:
    page = call("mqtt_spec_get", version="5.0", section="3", include_subsections=True)
    body = page.text.split("\n\n")
    assert body[2] == "_Section 3 has no text of its own; its subsections follow._"
    alone = call("mqtt_spec_get", version="5.0", section="3").text
    assert alone == page.text
    assert "include_subsections" not in alone
    nested = call(
        "mqtt_spec_get",
        version="5.0",
        section="1",
        include_subsections=True,
        max_chars=12000,
    ).text
    assert "_Section 1 has no text of its own; its subsections follow._" in nested


def test_superscript_footnote_markers() -> None:
    html = (
        "<html><body><h1>1 Intro</h1><p>QoS<sup>2</sup> and DUP<sup>1</sup>, "
        "the 20<sup>th</sup> of June, 2<span style='vertical-align:super'>16</span>"
        "</p></body></html>"
    )
    text = parse_document(html).sections[0].blocks[0]
    assert getattr(text, "text", "") == "QoS² and DUP¹, the 20th of June, 2¹⁶"


def test_superscripts_in_bundled_311_index(ok: JsonCall) -> None:
    publish = ok("mqtt_spec_lookup", version="3.1.1", kind="packet", query="PUBLISH")
    assert "QoS²" in publish["fixed_header_flags"]
    assert "QoS2" not in publish["fixed_header_flags"]
    hit = ok("mqtt_spec_search", version="3.1.1", query="session present flag SP1")
    assert any("SP¹" in r["snippet"] for r in hit["results"])


def test_long_queries_are_capped(call: Call) -> None:
    query = " ".join(f"term{i}" for i in range(5000))
    assert len(query_terms(query)) == MAX_QUERY_TERMS
    assert not call("mqtt_spec_search", version="5.0", query=query).is_error


_DETERMINISM_SCRIPT = """
import json
from mqtt_spec_mcp.server import MqttSpecService
s = MqttSpecService()
calls = [
    ("mqtt_spec_search", {"version": "5.0", "query": "retained message wildcard"}),
    ("mqtt_spec_search", {"version": "3.1.1", "query": "clean session", "limit": 5}),
    ("mqtt_spec_lookup", {"version": "5.0", "kind": "reason_code", "query": "0x87"}),
    ("mqtt_spec_get", {"version": "5.0", "section": "3.3", "max_chars": 12000}),
    ("mqtt_spec_outline", {"version": "3.1.1", "depth": 3}),
]
print(json.dumps([s.call(name, args).text for name, args in calls]))
"""


def test_output_is_identical_across_hash_seeds() -> None:
    outputs = []
    for seed in ("1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", _DETERMINISM_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.append(done.stdout)
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])


@pytest.mark.parametrize(
    ("version", "text"),
    [("3.1.1", "MQTT version: 5.0 Session"), ("5.0", "MQTT v=3.1.1 clean session")],
)
def test_version_mentions_with_separators(
    err: Callable[..., str], version: str, text: str
) -> None:
    assert "selected version" in err("mqtt_spec_search", version=version, query=text)


@pytest.mark.parametrize(
    ("label", "statement"),
    [
        ("MQTT-3.2.2-8", "MUST use one of the Connect Reason Code values"),
        ("MQTT-4.2.0-1", "underlying transport protocols"),
    ],
)
def test_reconciled_label_search_shows_the_requirement(
    ok: JsonCall, label: str, statement: str
) -> None:
    first = ok("mqtt_spec_search", version="5.0", query=label, limit=1)["results"][0]
    assert statement in first["snippet"]


@pytest.mark.parametrize(
    "args",
    [
        {"query": "session", "limit": 0},
        {"query": "session", "extra": 1},
        {"limit": 3},
    ],
)
def test_argument_errors_are_scoped_when_version_is_valid(
    err: Callable[..., str], args: dict[str, Any]
) -> None:
    message = err("mqtt_spec_search", version="5.0", **args)
    assert message.startswith("MQTT 5.0 only (")
    assert message.endswith("No other version was queried.")
    assert "Invalid arguments" in message


def test_title_with_publication_artifact_resolves(call: Call, ok: JsonCall) -> None:
    page = call(
        "mqtt_spec_get", version="5.0", section="3.3.2.3.3 Message Expiry Interval"
    )
    assert not page.is_error, page.text
    prop = ok(
        "mqtt_spec_lookup",
        version="5.0",
        kind="property",
        query="0x02",
        packet="PUBLISH",
    )
    assert [d["section"] for d in prop["details"]] == ["3.3.2.3.3"]
    outcome = call("mqtt_spec_outline", version="5.0", parent="Message Expiry Interval")
    assert outcome.is_error
    assert "3.1.3.2.4" in outcome.text
    assert "3.3.2.3.3" in outcome.text


def test_snippets_anchor_on_whole_words(ok: JsonCall) -> None:
    hits = ok("mqtt_spec_search", version="5.0", query="uri", limit=5)["results"]
    refs = [r for r in hits if r["section"] == "1.4"]
    assert refs
    assert "URI" in refs[0]["snippet"]


def test_snippet_finds_the_densest_match(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_search",
        version="5.0",
        query="PUBLISH Topic Alias greater than Topic Alias Maximum",
        chapter="3.3.2.3.4",
    )
    first = result["results"][0]
    assert first["section"] == "3.3.2.3.4"
    assert "greater than the Topic Alias Maximum" in first["snippet"]


@pytest.mark.parametrize("kind", ["packet", "property", "reason_code"])
def test_huge_numbers_are_ordinary_misses(err: Callable[..., str], kind: str) -> None:
    message = err("mqtt_spec_lookup", version="5.0", kind=kind, query="9" * 4301)
    assert "Internal error" not in message
    assert message.startswith("MQTT 5.0 only (")


def test_exact_section_titles_naming_a_version_resolve(
    call: Call, err: Callable[..., str]
) -> None:
    for reference in ("1.8.1 MQTT v3.1.1", "MQTT v3.1.1", "1.8.2 MQTT v5.0"):
        page = call("mqtt_spec_get", version="5.0", section=reference)
        assert not page.is_error, page.text
    assert "selected version" in err(
        "mqtt_spec_search", version="5.0", query="MQTT v3.1.1"
    )
    assert "selected version" in err(
        "mqtt_spec_get", version="5.0", section="MQTT Version 3.1.1 CONNECT"
    )
    assert "selected version" in err(
        "mqtt_spec_outline", version="3.1.1", parent="MQTT v5.0"
    )


def test_search_folds_word_forms(ok: JsonCall) -> None:
    from mqtt_spec_mcp.textutil import stem

    assert {stem(w) for w in ("close", "closes", "closed", "closing")} == {"close"}
    assert stem("connection") == stem("connections") == "connection"
    assert stem("connection") != stem("connect")
    top = ok(
        "mqtt_spec_search",
        version="5.0",
        query="what happens to the session when the network connection closes",
    )
    assert "3.1.2.11.2" in [r["section"] for r in top["results"]]


def test_will_is_a_search_term(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_search", version="5.0", query="when is the will message published"
    )
    assert result["results"][0]["title"].startswith("Will")


def test_next_step_says_excerpts_are_partial(ok: JsonCall) -> None:
    step = ok("mqtt_spec_search", version="5.0", query="keep alive")["next_step"]
    assert "most answers need nothing more" not in step
    assert "Sections without (excerpt: ...) are complete" in step
    assert "first get that section with mqtt_spec_get" in step
    assert "at most one section per version" in step
    assert "Search MQTT 5.0 again at most once, only for a part of the" in step
    assert "to compare versions, search MQTT 3.1.1 once" in step


def test_pages_split_at_sentences_and_pack_small_pages(call: Call) -> None:
    from mqtt_spec_mcp.render import page_summary

    args = {"version": "5.0", "section": "3.1.2.5", "max_chars": 1000}
    first = call("mqtt_spec_get", **args).text
    body = first.split("\n\n")[2:-1]
    assert sum(len(part) for part in body) > 300
    cursor = page_summary(first)["cursor"]
    second = call("mqtt_spec_get", **args, cursor=cursor).text
    for page in (first, second):
        text = page.split("\n\n")[2:]
        last = next(p for p in reversed(text) if not p.startswith(("---", "Source")))
        assert last.rstrip().endswith((".", ";", ":", ")", "]")), last[-60:]


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("mqtt_spec_search", {"query": "keep alive", "chapter": None, "limit": "null"}),
        (
            "mqtt_spec_search",
            {"query": "keep alive", "limit": "3", "normative_only": "true"},
        ),
        ("mqtt_spec_search", {"query": "keep alive", "limit": 3.0}),
        (
            "mqtt_spec_get",
            {
                "section": "3.1",
                "max_chars": "1000",
                "include_subsections": "false",
                "cursor": "None",
            },
        ),
        ("mqtt_spec_lookup", {"kind": "reason_code", "query": 128}),
        ("mqtt_spec_lookup", {"kind": "packet", "query": "3", "packet": "undefined"}),
    ],
)
def test_common_llm_argument_spellings(
    call: Call, tool: str, args: dict[str, Any]
) -> None:
    outcome = call(tool, version="5.0", **args)
    assert not outcome.is_error, outcome.text


@pytest.mark.parametrize(
    "args",
    [
        {"version": 5.0},
        {"version": "null"},
        {"version": "5.0", "depth": "lots"},
        {"version": "5.0", "depth": "9"},
        {"version": "5.0", "depth": True},
    ],
)
def test_lenience_does_not_reach_version_or_ranges(
    err: Callable[..., str], args: dict[str, Any]
) -> None:
    assert "Invalid arguments" in err("mqtt_spec_outline", **args)


def test_search_pack_fits_its_budget(call: Call) -> None:
    queries = (
        "Topic Alias Maximum protocol error",
        "shared subscription",
        "keep alive",
    )
    for version in ("3.1.1", "5.0"):
        for query in queries:
            text = call("mqtt_spec_search", version=version, query=query).text
            assert len(text) < PACK_CHARS + RELATED_CHARS + 700, (version, query)
            pack = parse_search(text)
            assert len(pack["results"]) >= 1
            assert all(r["snippet"].strip() for r in pack["results"])
            assert pack["scope"].startswith(f"MQTT {version} only.")


def test_search_limit_caps_the_sections(ok: JsonCall) -> None:
    pack = ok("mqtt_spec_search", version="5.0", query="session state", limit=2)
    assert 1 <= len(pack["results"]) <= 2


def test_evidence_pack_reaches_the_behaviour_section(ok: JsonCall) -> None:
    query = "Topic Alias value 0 greater than Topic Alias Maximum protocol error"
    pack = ok("mqtt_spec_search", version="5.0", query=query)
    sections = [r["section"] for r in pack["results"]]
    assert {"3.3.2.3.4", "3.3.4"} <= set(sections)
    text = "\n".join(r["snippet"] for r in pack["results"])
    assert "Topic Alias invalid" in text


def test_evidence_pack_shows_a_repeated_sentence_once(ok: JsonCall) -> None:
    pack = ok("mqtt_spec_search", version="5.0", query="Topic Alias Maximum")
    sections = [r["section"] for r in pack["results"]]
    assert {"3.1.2.11.5", "3.2.2.3.8"} <= set(sections)
    text = "\n".join(r["snippet"] for r in pack["results"])
    repeated = "It is a Protocol Error to include the Topic Alias Maximum value more"
    assert text.count(repeated) == 1


def test_evidence_pack_prefers_normative_sentences(ok: JsonCall) -> None:
    query = "Response Topic Correlation Data request/response"
    pack = ok("mqtt_spec_search", version="5.0", query=query)
    text = "\n".join(r["snippet"] for r in pack["results"])
    assert "[MQTT-3.3.2-14]" in text  # Response Topic MUST NOT contain wildcards


def test_evidence_pack_fills_the_best_section_first(ok: JsonCall) -> None:
    query = "Variable Byte Integer continuation bytes padding maximum value"
    pack = ok("mqtt_spec_search", version="5.0", query=query)
    assert pack["results"][0]["section"] == "1.5.5"
    assert "268,435,455" in pack["results"][0]["snippet"]


def test_evidence_pack_adds_related_requirements(ok: JsonCall) -> None:
    query = "PINGREQ PINGRESP keep alive timeout close network connection"
    pack = ok("mqtt_spec_search", version="5.0", query=query)
    sections = [r["section"] for r in pack["results"]]
    assert "3.12.4" in sections[PACK_SECTIONS - 2 :]  # ranked 17th, after the pack
    related = next(r for r in pack["results"] if r["section"] == "3.12.4")
    assert "MUST send a PINGRESP packet in response to a PINGREQ" in related["snippet"]
    limited = ok("mqtt_spec_search", version="5.0", query=query, limit=PACK_SECTIONS)
    assert "3.12.4" not in [r["section"] for r in limited["results"]]
    assert len(limited["results"]) <= PACK_SECTIONS


def test_list_item_brings_its_lead_in_and_list(
    engines: dict[str, SpecEngine],
) -> None:
    def context(section: str, sentence: str) -> list[str]:
        body = engines["5.0"].sections[section].body
        units = _units(body)
        i = next(k for k, (a, b) in enumerate(units) if body[a:b].startswith(sentence))
        return [
            body[a:b]
            for a, b in (
                units[m] for m in _list_context(units, _list_blocks(body, units), i)
            )
        ]

    pubrec = context("4.9", "- Each time a PUBREC packet is received")
    assert pubrec[0] == "The send quota is incremented by 1:"
    assert any(text.startswith("- Each time a PUBACK or PUBCOMP") for text in pubrec)
    alias = context("3.3.4", "- a) If the packet has a zero length Topic Name")
    assert alias[0].startswith("If the PUBLISH packet contains a Topic Alias")
    assert any("0x82" in text for text in alias)
    # A list longer than LIST_BLOCK_CHARS without a lead-in adds nothing.
    assert context("4.8.2", "- If there's more than one Session subscribed") == []


def test_query_parts() -> None:
    assert query_parts("keep alive") == ["keep alive"]
    assert query_parts(" keep alive ; PINGREQ ;; ") == ["keep alive", "PINGREQ"]
    assert query_parts("a b; ;  ...; c d") == ["a b", "c d"]
    many = query_parts("p1; p2; p3; p4; p5; p6")
    assert len(many) == MAX_QUERY_PARTS
    assert many[-1] == "p4 p5 p6"


def test_multi_part_search_covers_each_part(ok: JsonCall) -> None:
    bullet = (
        "Each time a PUBREC packet is received with a Return Code of 0x80 or greater"
    )
    single = ok("mqtt_spec_search", version="5.0", query="Receive Maximum")
    assert bullet not in "\n".join(r["snippet"] for r in single["results"])
    query = (
        "Receive Maximum send quota decrement increment; Receive Maximum default; "
        "QoS 0 Receive Maximum; Receive Maximum exceeded DISCONNECT"
    )
    pack = ok("mqtt_spec_search", version="5.0", query=query)
    text = "\n".join(r["snippet"] for r in pack["results"])
    assert bullet in text
    assert "65,535" in text
    assert "0x93" in text
    sections = [r["section"] for r in pack["results"]]
    assert len(sections) == len(set(sections))


def test_multi_part_search_size_and_limit(call: Call, ok: JsonCall) -> None:
    query = (
        "keep alive PINGREQ; Will Delay Interval; Session Expiry Interval; Topic Alias"
    )
    text = call("mqtt_spec_search", version="5.0", query=query).text
    largest = max(MULTI_PACK_CHARS, MIN_PART_CHARS * MAX_QUERY_PARTS)
    assert len(text) < largest + RELATED_CHARS + 1500
    limited = ok("mqtt_spec_search", version="5.0", query=query, limit=1)
    assert len(limited["results"]) <= MAX_QUERY_PARTS


def test_multi_part_search_keeps_reference_parts(ok: JsonCall) -> None:
    pack = ok("mqtt_spec_search", version="5.0", query="MQTT-3.1.2-4; Clean Start")
    assert pack["results"][0]["section"] == "3.1.2.4"
    by_id = ok("mqtt_spec_search", version="3.1.1", query="3.1.2.4; keep alive")
    assert by_id["results"][0]["section"] == "3.1.2.4"
    assert "3.1.2.10" in [r["section"] for r in by_id["results"]]


def test_everyday_spellings_are_rewritten() -> None:
    assert spec_spelling("keepalive and keep-alive") == "Keep Alive and Keep Alive"
    assert spec_spelling("LWT vs last will and testament") == "Will vs Will"
    assert (
        spec_spelling("brokers kicked QoS0 clients")
        == "Server disconnect QoS 0 clients"
    )
    assert spec_spelling("same client id or ClientID") == "same ClientID or ClientID"
    assert spec_spelling("Will Delay Interval") == "Will Delay Interval"


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
@pytest.mark.parametrize(
    ("query", "section"), [("keepalive", "3.1.2.10"), ("LWT", "3.1.2.5")]
)
def test_everyday_spellings_find_their_section(
    ok: JsonCall, version: str, query: str, section: str
) -> None:
    pack = ok("mqtt_spec_search", version=version, query=query)
    assert pack["results"][0]["section"] == section


def test_identifier_queries_are_not_rewritten(engines: dict[str, SpecEngine]) -> None:
    result = search(engines["5.0"], "MQTT-3.1.2-4")
    assert result["query"] == "MQTT-3.1.2-4"
    assert search(engines["5.0"], "keepalive")["query"] == "keepalive"


def test_311_is_the_errata_01_edition(call: Call) -> None:
    text = call("mqtt_spec_get", version="3.1.1", section="2.2.3").text
    assert "mqtt-v3.1.1-errata01-os-complete.html" in text
    # Errata 01: the range check comes before the multiplier update, once.
    check = text.index("if (multiplier > 128*128*128)")
    assert check < text.index("multiplier *= 128")
    assert text.count("multiplier = 1") == 1


def test_evidence_excerpts_are_verbatim(engines: dict[str, SpecEngine]) -> None:
    result = search(engines["5.0"], "Session Expiry Interval DISCONNECT")
    for entry in result["sections"]:
        body = engines["5.0"].sections[entry["section"]].body
        for piece in entry["excerpt"].split(" ... "):
            assert piece.strip(".").strip() in body
