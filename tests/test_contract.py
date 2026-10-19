"""Public tool contract, envelopes, errors and version isolation."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from mqtt_spec_mcp.server import TOOLS, MqttSpecService
from mqtt_spec_mcp.versions import PINS
from tests.conftest import Call, JsonCall

URL: dict[str, str] = {v: pin.source_url for v, pin in PINS.items()}
MINIMAL_ARGS: dict[str, dict[str, Any]] = {
    "mqtt_spec_outline": {},
    "mqtt_spec_search": {"query": "session"},
    "mqtt_spec_get": {"section": "1"},
    "mqtt_spec_lookup": {"kind": "term", "query": "Session"},
}
BAD_VERSIONS: list[Any] = [None, "", "5", "3.1", "v3.1.1", "latest", 5, True]


def assert_envelope(result: dict[str, Any], version: str) -> None:
    assert result["specification"] == f"MQTT Version {version}"
    assert result["version"] == version
    assert result["scope"].startswith(f"MQTT {version} only.")
    assert result["source"] == URL[version]
    assert f"/v{version}/" in result["source"]


def assert_error(message: str, version: str) -> None:
    assert message.startswith(f"MQTT {version} only ({URL[version]}): ")
    assert message.endswith("No other version was queried.")


# Tool listing ----------------------------------------------------------------------


def test_tools_list_contract() -> None:
    assert [t.name for t in TOOLS] == [
        "mqtt_spec_outline",
        "mqtt_spec_search",
        "mqtt_spec_get",
        "mqtt_spec_lookup",
    ]
    for tool in TOOLS:
        ann = tool.annotations
        assert ann is not None
        assert ann.read_only_hint is True
        assert ann.destructive_hint is False
        assert ann.idempotent_hint is True
        assert ann.open_world_hint is False
        schema = tool.input_schema
        assert "version" in schema["required"]
        version = schema["properties"]["version"]
        assert version["enum"] == ["3.1.1", "5.0"]
        assert "default" not in version
    # Only the parameters agents use are advertised; the others are still accepted.
    assert {t.name: set(t.input_schema["properties"]) for t in TOOLS} == {
        "mqtt_spec_outline": {"version", "parent"},
        "mqtt_spec_search": {"version", "query"},
        "mqtt_spec_get": {"version", "section", "cursor"},
        "mqtt_spec_lookup": {"version", "kind", "query", "packet"},
    }
    props = {t.name: t.input_schema["properties"] for t in TOOLS}
    for tool in TOOLS:
        for name, prop in tool.input_schema["properties"].items():
            # An enum is self-describing; every other parameter says what it takes.
            assert prop.get("description") or prop.get("enum"), (tool.name, name)
    assert props["mqtt_spec_lookup"]["kind"]["enum"] == [
        "packet",
        "property",
        "reason_code",
        "return_code",
        "requirement",
        "term",
    ]


def test_tool_schemas_are_concise() -> None:
    size = len(
        json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in TOOLS])
    )
    assert size < 6000


@pytest.mark.parametrize("tool", list(MINIMAL_ARGS))
@pytest.mark.parametrize("bad", [*BAD_VERSIONS, "missing"])
def test_invalid_versions_rejected(call: Call, tool: str, bad: Any) -> None:
    args = dict(MINIMAL_ARGS[tool])
    if bad != "missing":
        args["version"] = bad
    outcome = call(tool, **args)
    assert outcome.is_error
    assert "version" in outcome.text


def test_unknown_arguments_are_rejected(err: Callable[..., str]) -> None:
    message = err("mqtt_spec_outline", version="5.0", section="3")
    assert "section" in message


def test_unknown_tool(call: Call) -> None:
    assert call("mqtt_spec_delete", version="5.0").is_error


# Envelopes -------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["3.1.1", "5.0"])
def test_every_json_result_carries_envelope(ok: JsonCall, version: str) -> None:
    results = [
        ok("mqtt_spec_outline", version=version),
        ok("mqtt_spec_lookup", version=version, kind="packet", query="CONNECT"),
        ok(
            "mqtt_spec_lookup",
            version=version,
            kind="requirement",
            query="MQTT-3.1.2-4",
        ),
        ok("mqtt_spec_lookup", version=version, kind="term", query="Session"),
    ]
    if version == "5.0":
        results.append(
            ok("mqtt_spec_lookup", version=version, kind="property", query="0x11")
        )
        results.append(
            ok("mqtt_spec_lookup", version=version, kind="reason_code", query="0")
        )
    else:
        results.append(
            ok(
                "mqtt_spec_lookup",
                version=version,
                kind="return_code",
                query="0",
                packet="CONNACK",
            )
        )
    for result in results:
        assert_envelope(result, version)


def test_search_miss_next_step(ok: JsonCall) -> None:
    result = ok("mqtt_spec_search", version="5.0", query="zzqx flibbertigibbet")
    assert result["results"] == []
    assert result["best_section"] is None
    step = result["next_step"]
    assert "not proof" in step
    assert "other version" in step
    assert "Do not repeat" in step


def test_results_are_compact_json(call: Call) -> None:
    text = call("mqtt_spec_outline", version="5.0").text
    assert ", " not in text[:200] or '","' in text
    assert "\n" not in text
    assert (
        json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":")) == text
    )


def test_error_format(err: Callable[..., str]) -> None:
    for version in ("3.1.1", "5.0"):
        assert_error(err("mqtt_spec_get", version=version, section="99.1"), version)


def test_single_version_server_reports_not_loaded() -> None:
    service = MqttSpecService(["5.0"])
    outcome = service.call("mqtt_spec_outline", {"version": "3.1.1"})
    assert outcome.is_error
    assert "not loaded" in outcome.text
    assert_error(outcome.text, "3.1.1")
    assert not service.call("mqtt_spec_outline", {"version": "5.0"}).is_error


# Outline ---------------------------------------------------------------------------


def test_outline_top_level_and_chapter_3(ok: JsonCall) -> None:
    top = ok("mqtt_spec_outline", version="5.0")
    assert [s["section"] for s in top["sections"]] == list("1234567")
    assert top["parent"] is None
    assert top["depth"] == 1
    chapter = ok("mqtt_spec_outline", version="5.0", parent="3")
    ids = [s["section"] for s in chapter["sections"]]
    assert ids == [f"3.{n}" for n in range(1, 16)]
    assert chapter["parent"] == {"section": "3", "title": "MQTT Control Packets"}
    assert all(s["has_children"] for s in chapter["sections"])
    assert set(chapter["sections"][0]) == {"section", "title", "parent", "has_children"}


def test_outline_interleaved_versions(ok: JsonCall) -> None:
    for _ in range(3):
        for version, count in (("3.1.1", 14), ("5.0", 15)):
            result = ok("mqtt_spec_outline", version=version, parent="3")
            assert len(result["sections"]) == count
            assert_envelope(result, version)


def test_outline_depth(ok: JsonCall) -> None:
    deep = ok("mqtt_spec_outline", version="5.0", parent="3.1", depth=2)
    levels = {s["section"].count(".") + 1 for s in deep["sections"]}
    assert levels == {3, 4}
    everything = ok("mqtt_spec_outline", version="3.1.1", depth=5)
    assert len(everything["sections"]) == 162


def test_outline_resolution_errors(err: Callable[..., str]) -> None:
    assert "matches several" in err(
        "mqtt_spec_outline", version="5.0", parent="User Property"
    )
    ambiguous = err("mqtt_spec_outline", version="5.0", parent="Reason")
    assert "ambiguous" in ambiguous
    assert "No MQTT 5.0 section matches 'Zebra'" in err(
        "mqtt_spec_outline", version="5.0", parent="Zebra"
    )


# Version isolation -----------------------------------------------------------------


def test_311_and_50_meanings_of_3124(call: Call, err: Callable[..., str]) -> None:
    assert (
        "Clean Session"
        in call("mqtt_spec_get", version="3.1.1", section="3.1.2.4").text
    )
    assert "Clean Start" in call("mqtt_spec_get", version="5.0", section="3.1.2.4").text
    assert "title does not match" in err(
        "mqtt_spec_get", version="3.1.1", section="3.1.2.4 Clean Start"
    )
    message = err("mqtt_spec_get", version="5.0", section="3.1.2.4 Clean Session")
    assert "title does not match" in message
    assert "Clean Start" in message
    assert not call(
        "mqtt_spec_get", version="5.0", section="3.1.2.4 Clean Start"
    ).is_error


def test_requirement_differs_per_version(ok: JsonCall) -> None:
    a = ok(
        "mqtt_spec_lookup", version="3.1.1", kind="requirement", query="MQTT-3.1.2-4"
    )
    b = ok("mqtt_spec_lookup", version="5.0", kind="requirement", query="MQTT-3.1.2-4")
    assert a["excerpt"] != b["excerpt"]
    assert "CleanSession" in a["excerpt"]
    assert "Clean Start" in b["excerpt"]


def test_suback_0x80_per_version(ok: JsonCall) -> None:
    a = ok(
        "mqtt_spec_lookup",
        version="3.1.1",
        kind="return_code",
        query="0x80",
        packet="SUBACK",
    )
    assert [m["name"] for m in a["matches"]] == ["Failure"]
    b = ok(
        "mqtt_spec_lookup",
        version="5.0",
        kind="reason_code",
        query="0x80",
        packet="SUBACK",
    )
    assert [m["name"] for m in b["matches"]] == ["Unspecified error"]


def test_311_specific_misses(err: Callable[..., str], ok: JsonCall) -> None:
    for query in ("AUTH", "15"):
        message = err("mqtt_spec_lookup", version="3.1.1", kind="packet", query=query)
        assert "No MQTT Control Packet matches" in message
    assert "no property catalog" in err(
        "mqtt_spec_lookup", version="3.1.1", kind="property", query="0x11"
    )
    assert "no reason_code catalog" in err(
        "mqtt_spec_lookup", version="3.1.1", kind="reason_code", query="0"
    )
    assert "No MQTT glossary term matches 'Shared Subscription'" in err(
        "mqtt_spec_lookup", version="3.1.1", kind="term", query="Shared Subscription"
    )
    assert "No MQTT 3.1.1 section" in err(
        "mqtt_spec_get", version="3.1.1", section="3.1.2.11.2"
    )
    connack = ok("mqtt_spec_lookup", version="3.1.1", kind="packet", query="CONNACK")
    assert len(connack["return_codes"]) == 6
    assert "properties" not in connack
    assert "reason_codes" not in connack
    assert "3.1.1-only" in err(
        "mqtt_spec_lookup",
        version="5.0",
        kind="return_code",
        query="0",
        packet="CONNACK",
    )


@pytest.mark.parametrize("packet", ["CONNECT", "UNSUBACK"])
def test_311_return_code_packet_filter_must_be_connack_or_suback(
    err: Callable[..., str], packet: str
) -> None:
    args = {"version": "3.1.1", "kind": "return_code", "query": "0", "packet": packet}
    assert "packet=CONNACK, packet=SUBACK or no packet" in err(
        "mqtt_spec_lookup", **args
    )


def test_311_return_code_without_filter_names_each_packet(ok: JsonCall) -> None:
    result = ok("mqtt_spec_lookup", version="3.1.1", kind="return_code", query="2")
    assert [(m["packets"], m["name"]) for m in result["matches"]] == [
        (["CONNACK"], "Connection Refused, identifier rejected"),
        (["SUBACK"], "Success - Maximum QoS 2"),
    ]


def test_311_return_code_query_naming_a_packet_lists_its_codes(
    ok: JsonCall, err: Callable[..., str]
) -> None:
    for packet in (None, "CONNACK"):
        args = {"version": "3.1.1", "kind": "return_code", "query": "CONNACK"}
        result = ok(
            "mqtt_spec_lookup", **args, **({"packet": packet} if packet else {})
        )
        assert [m["value"] for m in result["matches"]] == [0, 1, 2, 3, 4, 5]
        assert all(m["meanings"] for m in result["matches"])
    suback = ok("mqtt_spec_lookup", version="3.1.1", kind="return_code", query="suback")
    assert [m["hex"] for m in suback["matches"]] == ["0x00", "0x01", "0x02", "0x80"]
    for args in ({"query": "SUBACK", "packet": "CONNACK"}, {"query": "PUBACK"}):
        message = err("mqtt_spec_lookup", version="3.1.1", kind="return_code", **args)
        assert "is a packet name" in message


def test_311_return_code_zero_is_scoped_to_filter(
    ok: JsonCall, err: Callable[..., str]
) -> None:
    for packet in ("CONNACK", "SUBACK"):
        result = ok(
            "mqtt_spec_lookup",
            version="3.1.1",
            kind="return_code",
            query="0",
            packet=packet,
        )
        assert [m["packets"] for m in result["matches"]] == [[packet]]
    miss = err(
        "mqtt_spec_lookup",
        version="3.1.1",
        kind="return_code",
        query="0x80",
        packet="CONNACK",
    )
    assert "No MQTT 3.1.1 return code matches" in miss
    assert "5.0" in miss


CONFLICTS: list[tuple[str, dict[str, Any]]] = [
    ("mqtt_spec_search", {"query": "{other} Session"}),
    ("mqtt_spec_search", {"query": "Session in {other}."}),
    ("mqtt_spec_get", {"section": "{other_long} CONNECT"}),
    ("mqtt_spec_outline", {"parent": "{other_v} CONNECT"}),
    ("mqtt_spec_lookup", {"kind": "term", "query": "{other_tight} Session"}),
    ("mqtt_spec_search", {"query": "Session", "chapter": "{other}"}),
    (
        "mqtt_spec_lookup",
        {"kind": "reason_code", "query": "0", "packet": "{other} SUBACK"},
    ),
]


@pytest.mark.parametrize(("version", "other"), [("3.1.1", "5.0"), ("5.0", "3.1.1")])
@pytest.mark.parametrize(("tool", "template"), CONFLICTS)
def test_conflicting_version_mentions_rejected(
    call: Call, version: str, other: str, tool: str, template: dict[str, Any]
) -> None:
    names = {
        "other": f"MQTT {other}",
        "other_long": f"MQTT Version {other}",
        "other_v": f"MQTT v{other}",
        "other_tight": f"MQTT{other}",
    }
    args = {k: v.format(**names) for k, v in template.items()}
    if version == "3.1.1" and args.get("kind") == "reason_code":
        args.update(kind="return_code", packet=f"MQTT {other} SUBACK")
    outcome = call(tool, version=version, **args)
    assert outcome.is_error
    assert "selected version" in outcome.text
    assert_error(outcome.text, version)


@pytest.mark.parametrize("text", ["MQTT 5 session", "mqtt5 session", "MQTT 5, session"])
def test_mqtt_5_counts_as_50(err: Callable[..., str], text: str) -> None:
    assert "selected version" in err("mqtt_spec_search", version="3.1.1", query=text)


def test_mentioning_selected_version_is_allowed(ok: JsonCall) -> None:
    ok("mqtt_spec_search", version="5.0", query="MQTT 5 shared subscription")
    ok("mqtt_spec_search", version="3.1.1", query="MQTT 3.1.1 clean session")


@pytest.mark.parametrize(
    "printed",
    ["[ MQTT-3 .1.2-5]", "MQTT-3.3.1.-1", "MQTT-3.1.2.7", "MQTT-3-8.3-4"],
)
def test_311_printed_labels(
    ok: JsonCall, engines: dict[str, Any], printed: str
) -> None:
    looked = ok("mqtt_spec_lookup", version="3.1.1", kind="requirement", query=printed)
    canonical = {
        "[ MQTT-3 .1.2-5]": "MQTT-3.1.2-5",
        "MQTT-3.3.1.-1": "MQTT-3.3.1-1",
        "MQTT-3.1.2.7": "MQTT-3.1.2.7",
        "MQTT-3-8.3-4": "MQTT-3-8.3-4",
    }[printed]
    assert looked["id"] == canonical
    found = ok("mqtt_spec_search", version="3.1.1", query=printed, limit=1)
    first = found["results"][0]
    assert first["section"] == looked["section"]
    snippet = first["snippet"].strip(".")
    assert snippet in looked["excerpt"] or looked["excerpt"] in first["snippet"]


def test_311_only_label_spelling_misses_in_50(err: Callable[..., str]) -> None:
    message = err(
        "mqtt_spec_lookup", version="5.0", kind="requirement", query="MQTT-3.3.1.-1"
    )
    assert "No normative requirement matches" in message
