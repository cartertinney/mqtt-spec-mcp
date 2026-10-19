"""Microbenchmark: context sizes, schema size, response sizes and latencies.

Token counts use the ``o200k_base`` tokenizer from ``tiktoken`` (benchmark extra)
and are labelled as such; they approximate, not equal, any model's billing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS, MqttSpecService

from .conditions import render_spec_text
from .retrieval import percentile

TOKENIZER = "o200k_base"

REPRESENTATIVE_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("search_default", "mqtt_spec_search", {"version": "5.0", "query": "keep alive"}),
    (
        "search_limit_5",
        "mqtt_spec_search",
        {"version": "5.0", "query": "message expiry", "limit": 5},
    ),
    ("get_default", "mqtt_spec_get", {"version": "5.0", "section": "3.1.2.11.2"}),
    (
        "get_chapter_page",
        "mqtt_spec_get",
        {"version": "5.0", "section": "4.3", "include_subsections": True},
    ),
    ("outline_root", "mqtt_spec_outline", {"version": "5.0"}),
    (
        "lookup_packet",
        "mqtt_spec_lookup",
        {"version": "5.0", "kind": "packet", "query": "PUBLISH"},
    ),
    (
        "lookup_reason_code",
        "mqtt_spec_lookup",
        {"version": "5.0", "kind": "reason_code", "query": "0x87"},
    ),
    (
        "lookup_requirement",
        "mqtt_spec_lookup",
        {"version": "5.0", "kind": "requirement", "query": "MQTT-4.3.3-1"},
    ),
]


def _token_counter() -> Any:
    try:
        import tiktoken  # noqa: PLC0415 - optional benchmark extra
    except ImportError:  # pragma: no cover - optional dependency
        return None
    return tiktoken.get_encoding(TOKENIZER)


def _measure(text: str, encoder: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"chars": len(text)}
    if encoder is not None:
        result[f"tokens_{TOKENIZER}"] = len(encoder.encode(text))
    return result


def in_process(repetitions: int) -> dict[str, Any]:
    start = time.perf_counter()
    service = MqttSpecService()
    load_ms = (time.perf_counter() - start) * 1000
    calls: dict[str, Any] = {}
    for label, tool, args in REPRESENTATIVE_CALLS:
        timings = []
        text = ""
        for _ in range(repetitions):
            t0 = time.perf_counter()
            text = service.call(tool, args).text
            timings.append((time.perf_counter() - t0) * 1000)
        calls[label] = {
            "chars": len(text),
            "latency_ms_p50": round(percentile(timings, 50), 3),
            "latency_ms_p95": round(percentile(timings, 95), 3),
        }
    return {"load_both_indexes_ms": round(load_ms, 1), "calls": calls}


async def _stdio(repetitions: int) -> dict[str, Any]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "mqtt_spec_mcp"])
    args = {"version": "5.0", "query": "keep alive"}
    start = time.perf_counter()
    async with Client(params, mode="legacy") as client:
        await client.call_tool("mqtt_spec_search", args)
        cold = (time.perf_counter() - start) * 1000
        warm: list[float] = []
        for _ in range(repetitions):
            t0 = time.perf_counter()
            await client.call_tool("mqtt_spec_search", args)
            warm.append((time.perf_counter() - t0) * 1000)
    return {
        "cold_start_to_first_result_ms": round(cold, 1),
        "warm_call_ms_p50": round(percentile(warm, 50), 3),
        "warm_call_ms_p95": round(percentile(warm, 95), 3),
        "warm_call_ms_mean": round(statistics.fmean(warm), 3),
    }


def run(repetitions: int, stdio: bool) -> dict[str, Any]:
    encoder = _token_counter()
    schemas = json.dumps(
        [t.model_dump(by_alias=True, exclude_none=True) for t in TOOLS],
        separators=(",", ":"),
    )
    report: dict[str, Any] = {
        "tokenizer": TOKENIZER if encoder is not None else None,
        "full_spec_3_1_1": _measure(render_spec_text("3.1.1"), encoder),
        "full_spec_5_0": _measure(render_spec_text("5.0"), encoder),
        "tool_schemas": _measure(schemas, encoder),
        "server_instructions": _measure(INSTRUCTIONS, encoder),
        "in_process": in_process(repetitions),
    }
    service = MqttSpecService(["5.0"])
    report["responses"] = {
        label: _measure(service.call(tool, args).text, encoder)
        for label, tool, args in REPRESENTATIVE_CALLS
    }
    if stdio:
        report["stdio"] = anyio.run(_stdio, repetitions)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MQTT spec MCP microbenchmark.")
    parser.add_argument("--repetitions", type=int, default=50)
    parser.add_argument(
        "--no-stdio", action="store_true", help="skip subprocess timing"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run(args.repetitions, not args.no_stdio)
    text = json.dumps(report, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
