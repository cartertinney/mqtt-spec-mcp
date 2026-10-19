"""Benchmark report: per-condition summaries, paired comparisons and release gates.

Records are paired by (model, case, epoch). Confidence intervals come from a
seeded (deterministic) paired bootstrap over those pairs. Runs whose prompt,
benchmark or server hashes differ are never compared: the report refuses to mix
them. ``--rescore`` re-applies the current scorer to stored answers offline.
"""

from __future__ import annotations

import argparse
import importlib
import json
import random
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .cases import Case, load_cases
from .runs import RunRecord, case_hash, latest_records, load_records
from .scorer import SCORER_VERSION, score_answer

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 1729


class ReportError(RuntimeError):
    pass


# Loading ---------------------------------------------------------------------------


def rescore(records: list[RunRecord], cases: dict[str, Case]) -> None:
    """Re-apply the current scorer and answer keys to stored answers."""
    for record in records:
        if record.error is not None:
            continue
        case = cases.get(record.case_id)
        if case is None:
            raise ReportError(f"cannot rescore {record.case_id}: not in the corpus")
        if record.hashes.get("case") != case_hash(case):
            raise ReportError(
                f"cannot rescore {record.case_id}: its question changed since the run"
            )
        record.score = score_answer(case, record.answer).to_dict()
        record.scorer_version = SCORER_VERSION


def check_compatible(records: list[RunRecord]) -> dict[str, Any]:
    """Refuse to compare runs that asked different questions or used different
    shared instructions, prompts, or reference material for the same condition."""
    problems: list[str] = []
    bases = {r.hashes.get("base") for r in records}
    if len(bases) > 1:
        problems.append(f"different shared instructions {sorted(map(str, bases))}")
    per_condition: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    per_case: dict[str, set[str]] = defaultdict(set)
    for r in records:
        for key in ("prompt", "reference"):
            per_condition[r.condition][key].add(str(r.hashes.get(key)))
        per_case[r.case_id].add(str(r.hashes.get("case")))
    for condition, keys in sorted(per_condition.items()):
        for key, values in keys.items():
            if len(values) > 1:
                problems.append(f"{condition}: different {key} hashes {sorted(values)}")
    changed = sorted(case for case, values in per_case.items() if len(values) > 1)
    if changed:
        problems.append(f"questions changed between runs: {changed[:5]}")
    if problems:
        raise ReportError(
            "refusing to compare incompatible runs: " + "; ".join(problems)
        )
    versions = {r.scorer_version for r in records if r.score is not None}
    if len(versions) > 1:
        raise ReportError(
            f"records were scored by different scorer versions {sorted(versions)}; "
            "use --rescore"
        )
    return {
        "base": next(iter(bases), None),
        "conditions": {
            c: {k: next(iter(v)) for k, v in keys.items()}
            for c, keys in sorted(per_condition.items())
        },
    }


# Statistics ------------------------------------------------------------------------


def bootstrap_ci(
    pairs: Sequence[tuple[RunRecord, RunRecord]],
    statistic: Callable[[Sequence[tuple[RunRecord, RunRecord]]], float],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    point = statistic(pairs)
    if len(pairs) < 2:
        return point, point, point
    rng = random.Random(seed)  # noqa: S311 - deterministic resampling, not crypto
    values = sorted(
        statistic([pairs[rng.randrange(len(pairs))] for _ in pairs])
        for _ in range(samples)
    )
    low = values[int(0.025 * (samples - 1))]
    high = values[int(0.975 * (samples - 1))]
    return point, low, high


def _quality(r: RunRecord) -> float:
    return float(r.score["quality"]) if r.score else 0.0


def _passed(r: RunRecord) -> bool:
    return bool(r.score and r.score["passed"])


def quality_delta(pairs: Sequence[tuple[RunRecord, RunRecord]]) -> float:
    return statistics.fmean(_quality(c) - _quality(b) for b, c in pairs)


def token_reduction(pairs: Sequence[tuple[RunRecord, RunRecord]]) -> float:
    base = sum(b.usage.total_tokens for b, _ in pairs)
    cand = sum(c.usage.total_tokens for _, c in pairs)
    return 1 - cand / base if base else 0.0


def speedup(pairs: Sequence[tuple[RunRecord, RunRecord]]) -> float:
    base = sum(b.latency_s for b, _ in pairs)
    cand = sum(c.latency_s for _, c in pairs)
    return base / cand if cand else 0.0


def summarize(
    records: list[RunRecord], by_version: bool = False
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[RunRecord]] = defaultdict(list)
    for record in records:
        version = record.version if by_version else "all"
        groups[(record.model, record.condition, version)].append(record)
    rows = []
    for key, group in sorted(groups.items()):
        ok = [r for r in group if r.error is None]
        passed = sum(_passed(r) for r in ok)
        tokens = sum(r.usage.total_tokens for r in ok)
        cost = sum(r.usage.cost for r in ok)
        n = len(ok)
        rows.append(
            {
                "model": key[0],
                "condition": key[1],
                "version": key[2],
                "runs": len(group),
                "errors": len(group) - n,
                "quality": round(statistics.fmean(map(_quality, ok)), 4) if ok else 0.0,
                "pass_rate": round(passed / n, 4) if ok else 0.0,
                "tool_calls": _mean([len(r.tool_calls) for r in ok], 2),
                "api_calls": _mean([r.usage.api_calls for r in ok], 2),
                "tokens_mean": _mean([r.usage.total_tokens for r in ok], 0),
                "input_mean": _mean([r.usage.input_tokens for r in ok], 0),
                "output_mean": _mean([r.usage.output_tokens for r in ok], 0),
                "cache_read_mean": _mean([r.usage.cache_read_tokens for r in ok], 0),
                "tokens_per_correct": round(tokens / passed) if passed else None,
                "cost_mean": _mean([r.usage.cost for r in ok], 2),
                "cost_per_correct": round(cost / passed, 4) if passed else None,
                "latency_s_mean": _mean([r.latency_s for r in ok], 2),
                "latency_s_p50": round(_percentile([r.latency_s for r in ok], 50), 2),
                "latency_s_p95": round(_percentile([r.latency_s for r in ok], 95), 2),
                "concision": _mean(
                    [float(r.score["concision"]) for r in ok if r.score], 4
                ),
            }
        )
    return rows


def _mean(values: Sequence[float], digits: int) -> float:
    return round(statistics.fmean(values), digits) if values else 0.0


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def pair_records(
    records: list[RunRecord], baseline: str, candidate: str, version: str = "all"
) -> dict[str, list[tuple[RunRecord, RunRecord]]]:
    index = {
        (r.model, r.condition, r.case_id, r.epoch): r
        for r in records
        if r.error is None and version in ("all", r.version)
    }
    pairs: dict[str, list[tuple[RunRecord, RunRecord]]] = defaultdict(list)
    for (model, condition, case_id, epoch), cand in sorted(index.items()):
        if condition != candidate:
            continue
        base = index.get((model, baseline, case_id, epoch))
        if base is not None:
            pairs[model].append((base, cand))
    return pairs


def compare(
    records: list[RunRecord], baseline: str, candidate: str, version: str = "all"
) -> list[dict[str, Any]]:
    out = []
    for model, pairs in pair_records(records, baseline, candidate, version).items():
        row: dict[str, Any] = {
            "model": model,
            "version": version,
            "baseline": baseline,
            "candidate": candidate,
            "pairs": len(pairs),
        }
        for name, fn in (
            ("quality_delta", quality_delta),
            ("token_reduction", token_reduction),
            ("speedup", speedup),
        ):
            point, low, high = bootstrap_ci(pairs, fn)
            row[name] = {
                "point": round(point, 4),
                "ci95": [round(low, 4), round(high, 4)],
            }
        out.append(row)
    return out


def apply_gates(
    comparisons: list[dict[str, Any]], args: argparse.Namespace
) -> list[str]:
    failures = []
    for row in comparisons:
        who = f"{row['model']} {row['candidate']} vs {row['baseline']}"
        if args.gate_quality_regression is not None:
            low = row["quality_delta"]["ci95"][0]
            if low < -args.gate_quality_regression:
                failures.append(
                    f"{who}: quality delta lower bound {low} regresses more "
                    f"than {args.gate_quality_regression}"
                )
        if args.gate_token_reduction is not None:
            low = row["token_reduction"]["ci95"][0]
            if low < args.gate_token_reduction:
                failures.append(
                    f"{who}: token reduction lower bound {low} < "
                    f"{args.gate_token_reduction}"
                )
        if args.gate_speedup is not None:
            low = row["speedup"]["ci95"][0]
            if low < args.gate_speedup:
                failures.append(
                    f"{who}: speedup lower bound {low} < {args.gate_speedup}"
                )
    return failures


def _summary_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| model | condition | version | runs | errors | quality | pass rate | "
        "tool calls | API calls | tokens/run | input/run | output/run | "
        "tokens/correct | cost/run | latency s (mean / p95) |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['condition']} | {r['version']} | {r['runs']} | "
            f"{r['errors']} | {r['quality']:.3f} | {r['pass_rate']:.3f} | "
            f"{r['tool_calls']} | {r['api_calls']} | {r['tokens_mean']:.0f} | "
            f"{r['input_mean']:.0f} | {r['output_mean']:.0f} | "
            f"{r['tokens_per_correct']} | {r['cost_mean']} | "
            f"{r['latency_s_mean']} / {r['latency_s_p95']} |"
        )
    return lines


def to_markdown(
    summary: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    by_version: list[dict[str, Any]] | None = None,
) -> str:
    lines = _summary_table(summary)
    if by_version:
        lines += ["", *_summary_table(by_version)]
    if comparisons:
        lines += [
            "",
            "| model | version | candidate vs baseline | pairs | "
            "quality delta (95% CI) | token reduction (95% CI) | speedup (95% CI) |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
        for c in comparisons:
            cells = [
                f"{c[k]['point']} [{c[k]['ci95'][0]}, {c[k]['ci95'][1]}]"
                for k in ("quality_delta", "token_reduction", "speedup")
            ]
            lines.append(
                f"| {c['model']} | {c['version']} | {c['candidate']} vs "
                f"{c['baseline']} | {c['pairs']} | " + " | ".join(cells) + " |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize agent benchmark results.")
    parser.add_argument("results", nargs="*", type=Path, help="results JSONL files")
    parser.add_argument("--inspect-log", action="append", type=Path, default=[])
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument(
        "--rescore", action="store_true", help="re-score answers offline"
    )
    parser.add_argument("--baseline", default="full_spec")
    parser.add_argument("--candidate", default="mcp")
    parser.add_argument("--gate-quality-regression", type=float)
    parser.add_argument("--gate-token-reduction", type=float)
    parser.add_argument("--gate-speedup", type=float)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--epoch",
        action="append",
        type=int,
        default=[],
        help="only these epochs (repeatable); compare runs made together",
    )
    args = parser.parse_args(argv)

    records = load_records(args.results)
    if args.inspect_log:
        # Inspect AI needs Python 3.11+; import it only when logs are requested.
        module = importlib.import_module("mqtt_spec_mcp.benchmark.inspect_logs")
        records += module.load_inspect_logs(args.inspect_log)
    if not records:
        print("no records found", file=sys.stderr)
        return 1
    records = latest_records(records)
    if args.epoch:
        records = [r for r in records if r.epoch in set(args.epoch)]
        if not records:
            print("no records for the selected epochs", file=sys.stderr)
            return 1
    try:
        if args.rescore:
            cases = {c.id: c for c in load_cases(args.cases)}
            rescore(records, cases)
        hashes = check_compatible(records)
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    summary = summarize(records)
    by_version = summarize(records, by_version=True)
    comparisons = compare(records, args.baseline, args.candidate)
    versions = sorted({r.version for r in records})
    per_version = [
        row
        for version in versions
        for row in compare(records, args.baseline, args.candidate, version)
    ]
    print(to_markdown(summary, comparisons + per_version, by_version))
    if args.json_output:
        payload = {
            "hashes": hashes,
            "scorer_version": SCORER_VERSION,
            "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED},
            "summary": summary,
            "summary_by_version": by_version,
            "comparisons": comparisons,
            "comparisons_by_version": per_version,
        }
        args.json_output.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    failures = apply_gates(comparisons, args)
    for failure in failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
