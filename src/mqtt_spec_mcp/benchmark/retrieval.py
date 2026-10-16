"""Retrieval benchmark: ranks reference queries with no model in the loop.

Metrics over the case corpus (MQTT 5.0, search limit 5):

- Recall@k: mean of |relevant ∩ top-k| / min(|relevant|, k)
- Hit rate: share of cases with any relevant section in the top 5
- MRR: mean reciprocal rank of the first relevant section (0 when absent)
- Lookup success: share of cases with a lookup expectation that it satisfies
- Response characters and p50/p95 latency of in-process tool calls
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from mqtt_spec_mcp.server import MqttSpecService

from .cases import CASE_VERSION, Case, load_cases, run_lookup

SEARCH_LIMIT = 5


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def evaluate(
    cases: list[Case], service: MqttSpecService | None = None
) -> dict[str, Any]:
    service = service or MqttSpecService([CASE_VERSION])
    recall = {1: 0.0, 3: 0.0, 5: 0.0}
    hits = 0
    rr_total = 0.0
    latencies: list[float] = []
    sizes: list[int] = []
    lookup_total = 0
    lookup_ok = 0
    per_case: list[dict[str, Any]] = []
    for case in cases:
        args = {
            "version": CASE_VERSION,
            "query": case.search_query,
            "limit": SEARCH_LIMIT,
        }
        start = time.perf_counter()
        outcome = service.call("mqtt_spec_search", args)
        latencies.append((time.perf_counter() - start) * 1000)
        sizes.append(len(outcome.text))
        ranked = (
            []
            if outcome.is_error
            else [r["section"] for r in json.loads(outcome.text)["results"]]
        )
        relevant = set(case.relevant_sections)
        for k in recall:
            recall[k] += len(relevant & set(ranked[:k])) / min(len(relevant), k)
        first = next((i for i, s in enumerate(ranked, start=1) if s in relevant), None)
        hits += first is not None
        rr_total += 1 / first if first else 0.0
        entry: dict[str, Any] = {
            "id": case.id,
            "ranked": ranked,
            "first_relevant": first,
        }
        if case.lookup is not None:
            lookup_total += 1
            start = time.perf_counter()
            ok, _ = run_lookup(service, case.lookup)
            latencies.append((time.perf_counter() - start) * 1000)
            lookup_ok += ok
            entry["lookup_ok"] = ok
        per_case.append(entry)
    n = len(cases) or 1
    return {
        "cases": len(cases),
        "recall_at_1": round(recall[1] / n, 4),
        "recall_at_3": round(recall[3] / n, 4),
        "recall_at_5": round(recall[5] / n, 4),
        "hit_rate": round(hits / n, 4),
        "mrr": round(rr_total / n, 4),
        "lookup_cases": lookup_total,
        "lookup_success": round(lookup_ok / lookup_total, 4) if lookup_total else 1.0,
        "response_chars_mean": round(statistics.fmean(sizes), 1) if sizes else 0.0,
        "response_chars_max": max(sizes, default=0),
        "latency_ms_p50": round(percentile(latencies, 50), 3),
        "latency_ms_p95": round(percentile(latencies, 95), 3),
        "per_case": per_case,
    }


THRESHOLDS = (
    ("min_recall_at_1", "recall_at_1"),
    ("min_recall_at_3", "recall_at_3"),
    ("min_recall_at_5", "recall_at_5"),
    ("min_hit_rate", "hit_rate"),
    ("min_mrr", "mrr"),
    ("min_lookup_success", "lookup_success"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MQTT 5.0 retrieval benchmark (no model)."
    )
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    parser.add_argument("--per-case", action="store_true", help="print per-case ranks")
    for option, _ in THRESHOLDS:
        parser.add_argument(f"--{option.replace('_', '-')}", type=float, dest=option)
    args = parser.parse_args(argv)
    report = evaluate(load_cases(args.cases))
    summary = {k: v for k, v in report.items() if k != "per_case"}
    print(json.dumps(summary, indent=2))
    if args.per_case:
        for entry in report["per_case"]:
            print(json.dumps(entry))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    failures = [
        f"{metric} {report[metric]} < {getattr(args, option)}"
        for option, metric in THRESHOLDS
        if getattr(args, option) is not None and report[metric] < getattr(args, option)
    ]
    for failure in failures:
        print(f"THRESHOLD FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
