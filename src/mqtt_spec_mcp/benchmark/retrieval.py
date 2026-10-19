"""Retrieval benchmark: ranks reference queries with no model in the loop.

Metrics over the case corpus (MQTT 3.1.1 and 5.0, search limit 5). Each case's
reference query is ranked against every version the case is about; comparison
cases therefore contribute one query per version. Reported overall and per
version:

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mqtt_spec_mcp.server import MqttSpecService

from .cases import Case, load_cases, lookup_version, run_lookup, section_refs

SEARCH_LIMIT = 5


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class _Tally:
    queries: int = 0
    recall: dict[int, float] = field(default_factory=lambda: {1: 0.0, 3: 0.0, 5: 0.0})
    hits: int = 0
    reciprocal_ranks: float = 0.0

    def add(self, ranked: list[str], relevant: set[str]) -> int | None:
        self.queries += 1
        for k in self.recall:
            self.recall[k] += len(relevant & set(ranked[:k])) / min(len(relevant), k)
        first = next((i for i, s in enumerate(ranked, start=1) if s in relevant), None)
        self.hits += first is not None
        self.reciprocal_ranks += 1 / first if first else 0.0
        return first

    def summary(self) -> dict[str, Any]:
        n = self.queries or 1
        return {
            "queries": self.queries,
            "recall_at_1": round(self.recall[1] / n, 4),
            "recall_at_3": round(self.recall[3] / n, 4),
            "recall_at_5": round(self.recall[5] / n, 4),
            "hit_rate": round(self.hits / n, 4),
            "mrr": round(self.reciprocal_ranks / n, 4),
        }


def evaluate(
    cases: list[Case], service: MqttSpecService | None = None
) -> dict[str, Any]:
    """Rank each case's reference query against each version it is about."""
    service = service or MqttSpecService()
    overall = _Tally()
    by_version: dict[str, _Tally] = {}
    latencies: list[float] = []
    sizes: list[int] = []
    lookup_total = 0
    lookup_ok = 0
    per_case: list[dict[str, Any]] = []
    for case in cases:
        relevant_by_version: dict[str, set[str]] = {}
        for version, section in section_refs(case, case.relevant_sections):
            relevant_by_version.setdefault(version, set()).add(section)
        entry: dict[str, Any] = {"id": case.id, "version": case.version, "ranks": {}}
        for version in case.versions:
            relevant = relevant_by_version.get(version)
            if not relevant:
                continue
            args = {
                "version": version,
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
            first = overall.add(ranked, relevant)
            by_version.setdefault(version, _Tally()).add(ranked, relevant)
            entry["ranks"][version] = {"ranked": ranked, "first_relevant": first}
        lookup_in = lookup_version(case)
        if case.lookup is not None and lookup_in is not None:
            lookup_total += 1
            start = time.perf_counter()
            ok, _ = run_lookup(service, case.lookup, lookup_in)
            latencies.append((time.perf_counter() - start) * 1000)
            lookup_ok += ok
            entry["lookup_ok"] = ok
        per_case.append(entry)
    return {
        "cases": len(cases),
        **overall.summary(),
        "by_version": {v: t.summary() for v, t in sorted(by_version.items())},
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
        description="MQTT 3.1.1 and 5.0 retrieval benchmark (no model)."
    )
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    parser.add_argument("--per-case", action="store_true", help="print per-case ranks")
    parser.add_argument(
        "--versions", nargs="+", choices=("3.1.1", "5.0", "both"), help="case scopes"
    )
    for option, _ in THRESHOLDS:
        parser.add_argument(f"--{option.replace('_', '-')}", type=float, dest=option)
    args = parser.parse_args(argv)
    report = evaluate(load_cases(args.cases, versions=args.versions))
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
