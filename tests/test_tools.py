"""Search, get (pagination and cursors) and lookup behaviour."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from mqtt_spec_mcp.engine import SpecEngine
from mqtt_spec_mcp.render import build_fragments, fragment_limit, page_summary
from tests.conftest import Call, JsonCall

# Search ----------------------------------------------------------------------------


def test_search_will_delay_interval(ok: JsonCall) -> None:
    result = ok("mqtt_spec_search", version="5.0", query="Will Delay Interval")
    assert result["results"][0]["section"] == "3.1.3.2.2"
    assert result["best_section"] == "3.1.3.2.2"
    assert "not search again" in result["next_step"]
    assert "3.1.3.2.2" in result["next_step"]
    assert result["query"] == "Will Delay Interval"
    assert result["chapter"] is None
    assert result["normative_only"] is False


def test_search_label_normative_only(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_search", version="5.0", query="MQTT-3.1.2-23", normative_only=True
    )
    first = result["results"][0]
    assert first["section"] == "3.1.2.11.2"
    assert "MQTT-3.1.2-23" in first["requirements"]
    assert all(r["requirements"] is not None for r in result["results"])


def test_search_label_listed_first(ok: JsonCall) -> None:
    first = ok("mqtt_spec_search", version="5.0", query="MQTT-3.1.4-2")["results"][0]
    assert first["requirements"][0] == "MQTT-3.1.4-2"
    assert "MQTT-3.1.3-1" not in first["requirements"]
    assert len(first["requirements"]) <= 5


def test_search_shared_subscription_in_chapter_4(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_search", version="5.0", query="Shared Subscription", chapter="4"
    )
    first, *rest = result["results"]
    assert first["section"] == "4.8.2"
    assert len(first["snippet"]) > 640
    assert "$share/{ShareName}/{filter}" in first["snippet"]
    assert "only sent to one of its subscribing Sessions" in first["snippet"]
    assert all(len(r["snippet"]) <= 640 for r in rest)
    assert result["chapter"] == "4"
    assert all(r["section"].startswith("4.") for r in result["results"])


def test_search_multi_concept_request_response(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_search",
        version="5.0",
        query=(
            "How do a requester and responder use Response Topic and Correlation Data "
            "in a request/response exchange?"
        ),
    )
    first = result["results"][0]
    assert first["section"] == "4.10.1"
    context = first["parent_context"]
    assert context["section"] == "4.10"
    assert "Request Response Information" in context["snippet"]
    assert len(context["snippet"]) <= 700


def test_search_topic_alias_scoped(call: Call) -> None:
    outcome = call(
        "mqtt_spec_search", version="5.0", query="Topic Alias", chapter="3.3"
    )
    result = json.loads(outcome.text)
    assert result["results"]
    assert all(r["section"].startswith("3.3.") for r in result["results"])
    assert len(outcome.text) < 5000


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
@pytest.mark.parametrize(
    "query",
    ["keep alive", "retained message", "QoS 2 flow", "session state", "topic wildcard"],
)
def test_search_default_size_bound(call: Call, version: str, query: str) -> None:
    outcome = call("mqtt_spec_search", version=version, query=query)
    assert not outcome.is_error
    assert len(outcome.text) < 5000
    assert len(json.loads(outcome.text)["results"]) <= 4


def test_search_section_id_query(ok: JsonCall) -> None:
    result = ok("mqtt_spec_search", version="3.1.1", query="3.1.2.4")
    assert result["results"][0]["section"] == "3.1.2.4"


def test_search_rejects_query_without_tokens(err: Callable[..., str]) -> None:
    assert "no letters or digits" in err("mqtt_spec_search", version="5.0", query="?!")
    assert "query" in err("mqtt_spec_search", version="5.0", query="")


def test_search_is_deterministic(call: Call) -> None:
    first = call(
        "mqtt_spec_search", version="5.0", query="message expiry", limit=5
    ).text
    for _ in range(3):
        assert (
            call(
                "mqtt_spec_search", version="5.0", query="message expiry", limit=5
            ).text
            == first
        )


# Get -------------------------------------------------------------------------------


def test_get_page_format(call: Call) -> None:
    text = call("mqtt_spec_get", version="5.0", section="3.1.2.4").text
    lines = text.split("\n")
    assert lines[0] == "# 3.1.2.4 Clean Start"
    assert "MQTT 5.0 ONLY - version-specific evidence." in lines
    assert "---" in lines
    assert text.endswith("Truncated: no")
    assert (
        "Source: OASIS MQTT Version 5.0, section 3.1.2.4 - "
        "https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html"
    ) in text


def test_get_tables_and_lists_render_as_markdown(call: Call) -> None:
    text = call("mqtt_spec_get", version="5.0", section="2.4", max_chars=12000).text
    assert "**Table 2‑6 - Reason Codes**" in text
    assert "| --- |" in text
    assert "| 128 | 0x80 | Unspecified error |" in text
    listing = call("mqtt_spec_get", version="5.0", section="4.1").text
    assert "\n- The existence of a Session" in listing


def test_get_placeholder_for_empty_section(call: Call) -> None:
    text = call("mqtt_spec_get", version="5.0", section="1").text
    assert "has no text of its own" in text


def test_get_subsections_and_continuation(call: Call) -> None:
    first = call(
        "mqtt_spec_get",
        version="5.0",
        section="3.1",
        include_subsections=True,
        max_chars=1000,
    ).text
    summary = page_summary(first)
    assert summary["truncated"]
    assert summary["cursor"]
    second = call(
        "mqtt_spec_get",
        version="5.0",
        section="3.1",
        include_subsections=True,
        max_chars=1000,
        cursor=summary["cursor"],
    ).text
    first_line = second.split("\n")[0]
    assert first_line.startswith("# 3.1 CONNECT")
    assert "(continued)" in first_line
    assert "MQTT 5.0 ONLY" in second


def test_get_subsection_headings(engines: dict[str, SpecEngine]) -> None:
    engine = engines["5.0"]
    fragments = build_fragments(engine, engine.sections["3.1.2"], True, 600)
    assert "## 3.1.2.1 Protocol Name" in fragments
    assert "### 3.1.2.11.1 Property Length" in fragments


def _page_through(
    call: Call, version: str, section: str, include: bool, max_chars: int
) -> list[str]:
    pages: list[str] = []
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        args: dict[str, Any] = {
            "version": version,
            "section": section,
            "include_subsections": include,
            "max_chars": max_chars,
        }
        if cursor is not None:
            args["cursor"] = cursor
        outcome = call("mqtt_spec_get", **args)
        assert not outcome.is_error, outcome.text
        pages.append(outcome.text)
        cursor = page_summary(outcome.text)["cursor"]
        if cursor is None:
            return pages
        assert cursor not in seen
        seen.add(cursor)
        assert len(pages) < 2000


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
@pytest.mark.parametrize("include", [False, True])
def test_every_section_pages_within_budget(
    call: Call, engines: dict[str, SpecEngine], version: str, include: bool
) -> None:
    engine = engines[version]
    url = engine.pin.source_url
    for info in engine.ordered:
        pages = _page_through(call, version, info.id, include, 1000)
        for page in pages:
            assert len(page) <= 1000, (info.id, len(page))
            assert f"MQTT {version} ONLY" in page
            assert url in page
        assert pages[-1].endswith("Truncated: no")


def test_pages_reproduce_every_fragment_once(
    call: Call, engines: dict[str, SpecEngine]
) -> None:
    engine = engines["5.0"]
    for section in ("3.1", "2.4", "4.8.2", "3.3"):
        pages = _page_through(call, "5.0", section, True, 1000)
        bodies: list[str] = []
        for page in pages:
            parts = page.split("\n\n")
            body_end = next(i for i, p in enumerate(parts) if p.startswith("---"))
            bodies.extend(parts[2:body_end])
        joined = "\n\n".join(bodies)
        expected = list(_fragments_for(engine, section, 1000))
        assert joined == "\n\n".join(expected)


def _fragments_for(engine: SpecEngine, section: str, max_chars: int) -> list[str]:
    info = engine.sections[section]
    return build_fragments(
        engine, info, True, fragment_limit(engine, info, True, max_chars)
    )


def test_oversized_table_row_renders_labelled_cells(
    engines: dict[str, SpecEngine],
) -> None:
    from mqtt_spec_mcp.render import _table_fragments
    from mqtt_spec_mcp.schema import Table

    table = Table(
        type="table",
        caption="Table X",
        rows=(("Name", "Value"), ("long", "word " * 200)),
    )
    fragments = _table_fragments(table, 300)
    assert fragments[0].startswith("**Table X, data row 1, column 1**\nHeader: Name")
    assert any("column 2**\nHeader: Value\nValue: word" in f for f in fragments)
    assert all(len(f) <= 300 for f in fragments)
    assert sum(f.count("word") for f in fragments) == 200


def test_table_cells_are_escaped() -> None:
    from mqtt_spec_mcp.render import _table_line

    assert _table_line(["a|b", "c\\d"]) == "| a\\|b | c\\\\d |"


def _cursor_for(call: Call, version: str = "5.0") -> str:
    page = call(
        "mqtt_spec_get",
        version=version,
        section="3.1",
        include_subsections=True,
        max_chars=1000,
    ).text
    cursor: str | None = page_summary(page)["cursor"]
    assert cursor is not None
    return cursor


CURSOR_KEYS = ("format", "version", "sha", "section", "include", "max_chars", "p")


def _decode(cursor: str) -> dict[str, str]:
    raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    return dict(zip(CURSOR_KEYS, raw.split(","), strict=True))


def _encode(data: dict[str, Any]) -> str:
    raw = ",".join(str(value) for value in data.values()).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_cursor_is_short_and_url_safe(call: Call) -> None:
    cursor = _cursor_for(call)
    assert len(cursor) <= 64
    assert re.fullmatch(r"[A-Za-z0-9_-]+", cursor)
    data = _decode(cursor)
    assert data["version"] == "5.0"
    assert data["section"] == "3.1"
    assert data["include"] == "1"
    assert data["max_chars"] == "1000"


def test_cursor_changed_arguments(call: Call, err: Callable[..., str]) -> None:
    cursor = _cursor_for(call)
    base = {
        "version": "5.0",
        "section": "3.1",
        "include_subsections": True,
        "max_chars": 1000,
    }
    for change in (
        {"max_chars": 2000},
        {"include_subsections": False},
        {"section": "3.2"},
    ):
        message = err("mqtt_spec_get", **{**base, **change, "cursor": cursor})
        assert "does not match" in message


def test_cursor_wrong_version_or_hash(call: Call, err: Callable[..., str]) -> None:
    cursor = _cursor_for(call)
    data = _decode(cursor)
    tampered = _encode({**data, "sha": "0" * 12})
    base = {
        "version": "5.0",
        "section": "3.1",
        "include_subsections": True,
        "max_chars": 1000,
    }
    assert "version or pinned publication" in err(
        "mqtt_spec_get", **base, cursor=tampered
    )
    other = _cursor_for(call, "3.1.1")
    assert "version or pinned publication" in err("mqtt_spec_get", **base, cursor=other)


def test_cursor_invalid_forms(call: Call, err: Callable[..., str]) -> None:
    data = _decode(_cursor_for(call))
    base = {
        "version": "5.0",
        "section": "3.1",
        "include_subsections": True,
        "max_chars": 1000,
    }
    unversioned = {k: v for k, v in data.items() if k != "format"}
    json_v1 = json.dumps(
        {"f": 1, "v": "5.0", "h": "0" * 64, "s": "3.1", "i": True, "m": 1000, "p": 2}
    )
    bad_cursors = [
        _encode(unversioned),
        _encode({**data, "format": 1}),
        _encode({**data, "p": "two"}),
        _encode({**data, "p": 0}),
        _encode({**data, "include": "yes"}),
        base64.urlsafe_b64encode(json_v1.encode()).decode().rstrip("="),
        "not a cursor!",
        "AAAA",
        _encode({**data, "extra": 1}),
    ]
    for bad in bad_cursors:
        assert "cursor is invalid" in err("mqtt_spec_get", **base, cursor=bad)
    past = _encode({**data, "p": 10_000})
    assert "past the end" in err("mqtt_spec_get", **base, cursor=past)


# Lookup ----------------------------------------------------------------------------


def test_lookup_packet_connect(ok: JsonCall) -> None:
    result = ok("mqtt_spec_lookup", version="5.0", kind="packet", query="CONNECT")
    assert result["value"] == 1
    assert result["fixed_header_flags"] == "0000"
    assert {"id": "0x11", "name": "Session Expiry Interval"} in result["properties"]
    assert "reason_codes" in result
    assert "return_codes" not in result
    by_number = ok("mqtt_spec_lookup", version="5.0", kind="packet", query="0x01")
    assert by_number["name"] == "CONNECT"


def test_lookup_property_with_packet(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_lookup",
        version="5.0",
        kind="property",
        query="0x11",
        packet="CONNECT",
    )
    assert result["data_type"] == "Four Byte Integer"
    assert result["details"][0]["section"] == "3.1.2.11.2"
    assert len(result["details"][0]["excerpt"]) <= 700
    assert "MQTT-3.1.2-23" in json.dumps(result["details"])
    will = ok(
        "mqtt_spec_lookup",
        version="5.0",
        kind="property",
        query="Will Delay Interval",
        packet="Will",
    )
    assert [d["section"] for d in will["details"]] == ["3.1.3.2.2"]


def test_lookup_property_substring(ok: JsonCall) -> None:
    result = ok("mqtt_spec_lookup", version="5.0", kind="property", query="Interval")
    assert len(result["matches"]) > 1
    assert all(m["sections"] for m in result["matches"])
    assert all(m["id"].startswith("0x") for m in result["matches"])


def test_lookup_reason_code_suback_zero(ok: JsonCall) -> None:
    result = ok(
        "mqtt_spec_lookup",
        version="5.0",
        kind="reason_code",
        query="0x00",
        packet="SUBACK",
    )
    assert result["matches"] == [
        {
            "value": 0,
            "hex": "0x00",
            "name": "Granted QoS 0",
            "packets": ["SUBACK"],
            "section": "2.4",
        }
    ]
    everything = ok("mqtt_spec_lookup", version="5.0", kind="reason_code", query="0")
    assert len(everything["matches"]) == 3


def test_lookup_requirement_and_term(ok: JsonCall) -> None:
    req = ok(
        "mqtt_spec_lookup", version="5.0", kind="requirement", query="[MQTT-3.1.2-23]"
    )
    assert req["id"] == "MQTT-3.1.2-23"
    assert req["section"] == "3.1.2.11.2"
    assert req["title"] == "Session Expiry Interval"
    assert "MUST" in req["excerpt"]
    term = ok("mqtt_spec_lookup", version="5.0", kind="term", query="session")
    assert term["matches"][0]["name"] == "Session"
    assert term["matches"][0]["section"] == "1.2"


def test_lookup_errors(err: Callable[..., str]) -> None:
    assert "Omit packet" in err(
        "mqtt_spec_lookup",
        version="5.0",
        kind="packet",
        query="CONNECT",
        packet="CONNECT",
    )
    term = err(
        "mqtt_spec_lookup", version="5.0", kind="term", query="Packet Identifier"
    )
    assert "No MQTT glossary term matches 'Packet Identifier'" in term
    assert "mqtt_spec_search" in term
    assert "exists, but not for PUBLISH" in err(
        "mqtt_spec_lookup",
        version="5.0",
        kind="property",
        query="Reason String",
        packet="PUBLISH",
    )
    assert "Property Identifier" in err(
        "mqtt_spec_lookup", version="5.0", kind="property", query="Will Flag"
    )
    assert "not a reason code" in err(
        "mqtt_spec_lookup", version="5.0", kind="reason_code", query="UNSUBACK"
    )
    assert "exists, but not for PUBLISH" in err(
        "mqtt_spec_lookup",
        version="5.0",
        kind="reason_code",
        query="0x99",
        packet="PUBLISH",
    )
    assert "not an MQTT 5.0 Control Packet" in err(
        "mqtt_spec_lookup",
        version="5.0",
        kind="reason_code",
        query="0",
        packet="BANANA",
    )
