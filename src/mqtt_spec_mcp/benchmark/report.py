"""Benchmark report: per-condition summaries, paired comparisons and decisions.

Records are paired by (model, case, epoch). Confidence intervals come from a seeded
(deterministic) bootstrap that resamples cases, keeping each case's pairs (epochs,
models) together; with several models a pooled row combines their pairs. Runs whose
prompt, benchmark or server hashes differ are never compared: the report refuses to
mix them. ``--rescore`` re-applies the current scorer to stored answers of the
bundled corpus offline.

Corpus-format-2 runs are scored from cached judge verdicts (``judge.py``): the
report never calls a model, and refuses answers without a judgment. The sealed
test split is reported only under its analysis plan, as aggregates, with the plan's
decision: PASS, FAIL, or INCOMPLETE while planned runs are missing or failed.
"""

from __future__ import annotations

import argparse
import importlib
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .cases import Case, load_cases
from .corpus import CorpusCase, CorpusError
from .judge import judgment_key, load_judgments, panel_score, scorer_id
from .plan import (
    LoadedPlan,
    Plan,
    PlanError,
    corpus_cases,
    corpus_problems,
    judge_problems,
    judgments_path,
    load_plan,
)
from .runs import RunRecord, case_hash, latest_records, load_records
from .scorer import SCORER_VERSION, score_answer

BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 1729
POOLED = "all models"
Pair = tuple[RunRecord, RunRecord]


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


def score_judged(
    records: list[RunRecord],
    cases: dict[str, CorpusCase],
    judge_models: str | Sequence[str],
    judgments: Path | Sequence[Path],
) -> None:
    """Score format-2 answers from cached judgments: one judge's, or the mean of a
    panel's."""
    models = [judge_models] if isinstance(judge_models, str) else list(judge_models)
    paths = [judgments] if isinstance(judgments, Path) else list(judgments)
    found = load_judgments(paths)
    missing: Counter[str] = Counter()
    for record in records:
        if record.error is not None:
            continue
        case = cases[record.case_id]
        panel = [found.get(judgment_key(m, case, record.answer)) for m in models]
        absent = [m for m, j in zip(models, panel, strict=True) if j is None]
        missing.update(absent)
        if absent:
            continue
        record.score = panel_score(case, record.answer, [j for j in panel if j])
        record.scorer_version = scorer_id(models)
    if missing:
        raise ReportError(
            "answers without a judgment ("
            + ", ".join(f"{m}: {n}" for m, n in sorted(missing.items()))
            + "): run mqtt-spec-bench-judge first"
        )


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
    pairs: Sequence[Pair],
    statistic: Callable[[Sequence[Pair]], float],
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """Point estimate and 95% interval, resampling cases: a case's pairs from
    several epochs or models are not independent, so they are drawn together."""
    point = statistic(pairs)
    by_case: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        by_case[pair[1].case_id].append(pair)
    clusters = list(by_case.values())
    if len(clusters) < 2:
        return point, point, point
    rng = random.Random(seed)  # noqa: S311 - deterministic resampling, not crypto
    values = sorted(
        statistic([p for _ in clusters for p in clusters[rng.randrange(len(clusters))]])
        for _ in range(samples)
    )
    low = values[int(0.025 * (samples - 1))]
    high = values[int(0.975 * (samples - 1))]
    return point, low, high


def _quality(r: RunRecord) -> float:
    return float(r.score["quality"]) if r.score else 0.0


def _passed(r: RunRecord) -> float:
    """1 or 0; with a panel of judges, the share of judges that pass the run."""
    return float(r.score["passed"]) if r.score else 0.0


def _errors(r: RunRecord) -> int:
    """Wrong claims made, plus facts contradicted (judged runs)."""
    if not r.score:
        return 0
    return len(r.score.get("violations", ())) + len(
        r.score.get("facts_contradicted", ())
    )


def _error_share(r: RunRecord) -> float:
    """Whether the run made an error; with a panel, the share of judges who say so."""
    if r.score and "error_share" in r.score:
        return float(r.score["error_share"])
    return float(_errors(r) > 0)


def quality_delta(pairs: Sequence[Pair]) -> float:
    return statistics.fmean(_quality(c) - _quality(b) for b, c in pairs)


def pass_delta(pairs: Sequence[Pair]) -> float:
    return statistics.fmean(_passed(c) - _passed(b) for b, c in pairs)


def token_reduction(pairs: Sequence[Pair]) -> float:
    base = sum(b.usage.total_tokens for b, _ in pairs)
    cand = sum(c.usage.total_tokens for _, c in pairs)
    return 1 - cand / base if base else 0.0


def weighted_token_reduction(pairs: Sequence[Pair]) -> float:
    base = sum(b.weighted_tokens for b, _ in pairs)
    cand = sum(c.weighted_tokens for _, c in pairs)
    return 1 - cand / base if base else 0.0


def speedup(pairs: Sequence[Pair]) -> float:
    base = sum(b.latency_s for b, _ in pairs)
    cand = sum(c.latency_s for _, c in pairs)
    return base / cand if cand else 0.0


STATISTICS: tuple[tuple[str, Callable[[Sequence[Pair]], float]], ...] = (
    ("quality_delta", quality_delta),
    ("pass_delta", pass_delta),
    ("token_reduction", token_reduction),
    ("weighted_token_reduction", weighted_token_reduction),
    ("speedup", speedup),
)


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
                "fact_recall": _mean(
                    [
                        float(r.score["fact_recall"])
                        for r in ok
                        if r.score and "fact_recall" in r.score
                    ],
                    4,
                ),
                "error_rate": round(sum(map(_error_share, ok)) / n, 4) if ok else 0.0,
                "tool_calls": _mean([len(r.tool_calls) for r in ok], 2),
                "api_calls": _mean([r.usage.api_calls for r in ok], 2),
                "tokens_mean": _mean([r.usage.total_tokens for r in ok], 0),
                "weighted_tokens_mean": _mean([r.weighted_tokens for r in ok], 0),
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
) -> dict[str, list[Pair]]:
    index = {
        (r.model, r.condition, r.case_id, r.epoch): r
        for r in records
        if r.error is None and version in ("all", r.version)
    }
    pairs: dict[str, list[Pair]] = defaultdict(list)
    for (model, condition, case_id, epoch), cand in sorted(index.items()):
        if condition != candidate:
            continue
        base = index.get((model, baseline, case_id, epoch))
        if base is not None:
            pairs[model].append((base, cand))
    return pairs


def compare(
    records: list[RunRecord],
    baseline: str,
    candidate: str,
    version: str = "all",
    *,
    label: str | None = None,
    keep: Callable[[RunRecord], bool] | None = None,
) -> list[dict[str, Any]]:
    """Paired comparison per model, and pooled over models when there are
    several. ``version`` or ``keep`` restrict the runs; ``label`` names the slice."""
    chosen = [r for r in records if keep is None or keep(r)]
    by_model = pair_records(chosen, baseline, candidate, version)
    groups = list(by_model.items())
    if len(groups) > 1:
        groups.append((POOLED, [p for pairs in by_model.values() for p in pairs]))
    out = []
    for model, pairs in groups:
        row: dict[str, Any] = {
            "model": model,
            "slice": label or version,
            "version": version,
            "baseline": baseline,
            "candidate": candidate,
            "pairs": len(pairs),
            "cases": len({c.case_id for _, c in pairs}),
        }
        for name, fn in STATISTICS:
            point, low, high = bootstrap_ci(pairs, fn)
            row[name] = {
                "point": round(point, 4),
                "ci95": [round(low, 4), round(high, 4)],
            }
        out.append(row)
    return out


def decide(
    plan: Plan, comparisons: list[dict[str, Any]], complete: bool
) -> dict[str, Any]:
    """The plan's decision rule over the unsliced comparisons of its pair."""
    pair = (plan.analysis.baseline, plan.analysis.candidate)
    rows = [
        r
        for r in comparisons
        if r["slice"] == "all" and (r["baseline"], r["candidate"]) == pair
    ]
    per_model = [r for r in rows if r["model"] != POOLED]
    pooled = [r for r in rows if r["model"] == POOLED] or per_model
    checks = []
    for check in plan.analysis.checks:
        chosen = pooled if check.scope == "pooled" else per_model
        outcomes = [
            {
                "model": r["model"],
                "ci_low": r[check.metric]["ci95"][0],
                "passed": r[check.metric]["ci95"][0] > check.ci_low_above,
            }
            for r in chosen
        ]
        checks.append(
            {
                "name": check.name,
                "metric": check.metric,
                "scope": check.scope,
                "ci_low_above": check.ci_low_above,
                "outcomes": outcomes,
                "passed": bool(outcomes) and all(o["passed"] for o in outcomes),
            }
        )
    passed = all(c["passed"] for c in checks)
    status = "PASS" if passed else "FAIL"
    return {"status": status if complete else "INCOMPLETE", "checks": checks}


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
        "fact recall | wrong-claim rate | tool calls | API calls | tokens/run | "
        "input/run | output/run | tokens/correct | cost/run | "
        "latency s (mean / p95) |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['model']} | {r['condition']} | {r['version']} | {r['runs']} | "
            f"{r['errors']} | {r['quality']:.3f} | {r['pass_rate']:.3f} | "
            f"{r['fact_recall']:.3f} | {r['error_rate']:.3f} | "
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
    decision: dict[str, Any] | None = None,
) -> str:
    lines = _summary_table(summary)
    if by_version:
        lines += ["", *_summary_table(by_version)]
    if comparisons:
        lines += [
            "",
            "| model | slice | candidate vs baseline | pairs | "
            "quality delta (95% CI) | pass-rate delta (95% CI) | "
            "token reduction (95% CI) | weighted token reduction (95% CI) | "
            "speedup (95% CI) |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- |",
        ]
        for c in comparisons:
            cells = [
                f"{c[k]['point']} [{c[k]['ci95'][0]}, {c[k]['ci95'][1]}]"
                for k, _ in STATISTICS
            ]
            lines.append(
                f"| {c['model']} | {c['slice']} | {c['candidate']} vs "
                f"{c['baseline']} | {c['pairs']} | " + " | ".join(cells) + " |"
            )
    if decision:
        lines += ["", f"Decision ({decision['plan']}): {decision['status']}"]
        if decision["coverage"]:
            done, planned = decision["coverage"]
            lines.append(f"- {done} of {planned} planned runs succeeded")
        for check in decision["checks"]:
            lows = ", ".join(f"{o['model']} {o['ci_low']}" for o in check["outcomes"])
            verdict = "pass" if check["passed"] else "fail"
            lines.append(
                f"- {check['name']}: {check['metric']} ({check['scope']}) CI low "
                f"{lows or 'n/a'} > {check['ci_low_above']}: {verdict}"
            )
    return "\n".join(lines)


def _slice(
    cases: dict[str, CorpusCase], field: str, value: str
) -> Callable[[RunRecord], bool]:
    return lambda r: getattr(cases[r.case_id], field) == value


def _coverage(
    plan: Plan, cases: dict[str, CorpusCase], records: list[RunRecord]
) -> tuple[int, int]:
    """(successful planned runs, planned runs); refuses runs the plan never made."""
    planned = {
        (m, c, case_id, e)
        for m in plan.models
        for c in plan.conditions
        for case_id in cases
        for e in range(plan.epochs)
    }
    if any(r.key() not in planned for r in records):
        raise ReportError("the results hold runs that are not in the plan")
    return sum(r.error is None for r in records), len(planned)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize agent benchmark results.")
    parser.add_argument("results", nargs="*", type=Path, help="results JSONL files")
    parser.add_argument(
        "--plan", type=Path, help="analysis plan: its results, judge and decision"
    )
    parser.add_argument("--inspect-log", action="append", type=Path, default=[])
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument(
        "--rescore", action="store_true", help="re-score answers offline"
    )
    parser.add_argument(
        "--judge-models",
        "--judge-model",
        nargs="+",
        help="judge (or panel) whose verdicts score format 2",
    )
    parser.add_argument(
        "--judgments",
        action="append",
        type=Path,
        help="judgment files (default: RESULTS.judgments.jsonl)",
    )
    parser.add_argument("--baseline", help="default full_spec, or the plan's")
    parser.add_argument("--candidate", help="default mcp, or the plan's")
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
    try:
        return _report(args)
    except (ReportError, PlanError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _report(args: argparse.Namespace) -> int:
    loaded: LoadedPlan | None = load_plan(args.plan) if args.plan else None
    paths: list[Path] = args.results or ([loaded.results_path] if loaded else [])
    records = load_records(paths)
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
    analysis = loaded.plan.analysis if loaded else None
    if analysis is not None and (args.baseline or args.candidate):
        raise ReportError("the plan fixes the comparison; drop --baseline/--candidate")
    baseline = args.baseline or (analysis.baseline if analysis else "full_spec")
    candidate = args.candidate or (analysis.candidate if analysis else "mcp")
    slices: list[tuple[str, Callable[[RunRecord], bool]]] = []
    decision: dict[str, Any] | None = None
    if any(r.corpus is not None for r in records):
        if not all(r.corpus is not None for r in records):
            raise ReportError("results mix the bundled corpus with a format-2 corpus")
        if args.rescore:
            raise ReportError("format-2 runs are scored from judgments, not --rescore")
        if loaded is not None:
            problems = corpus_problems(loaded.plan) + judge_problems(loaded.plan)
            if any(r.plan != loaded.id for r in records):
                problems.append(f"some results were not made under {loaded.id}")
            if problems:
                raise ReportError("; ".join(problems))
        if loaded is not None and args.judge_models:
            raise ReportError("the plan fixes the judges; drop --judge-models")
        judge_models = loaded.plan.judge.models if loaded else args.judge_models
        if not judge_models:
            raise ReportError("format-2 runs need --judge-models (or --plan)")
        cases = corpus_cases(records, loaded)
        score_judged(
            records,
            cases,
            judge_models,
            args.judgments or [judgments_path(p) for p in paths],
        )
        for field in ("kind", "origin"):
            for value in sorted({str(getattr(c, field)) for c in cases.values()}):
                slices.append((f"{field}={value}", _slice(cases, field, value)))
        if loaded is not None:
            decision = {
                "plan": loaded.id,
                "coverage": _coverage(loaded.plan, cases, records),
            }
    elif loaded is not None:
        raise ReportError("analysis plans apply to format-2 corpora")
    elif args.rescore:
        rescore(records, {c.id: c for c in load_cases(args.cases)})
    hashes = check_compatible(records)
    summary = summarize(records)
    by_version = summarize(records, by_version=True)
    comparisons = compare(records, baseline, candidate)
    versions = sorted({r.version for r in records})
    per_version = [
        row
        for version in versions
        for row in compare(records, baseline, candidate, version)
    ]
    per_slice = [
        row
        for label, keep in slices
        for row in compare(records, baseline, candidate, label=label, keep=keep)
    ]
    if loaded is not None and decision is not None:
        done, planned = decision["coverage"]
        decision.update(decide(loaded.plan, comparisons, done == planned))
    print(
        to_markdown(
            summary, comparisons + per_version + per_slice, by_version, decision
        )
    )
    unverified = sum(
        1 for r in records if r.score and r.score.get("evidence_unverified")
    )
    if unverified:
        print(f"\njudge evidence not found in the answer for {unverified} run(s)")
    if args.json_output:
        payload = {
            "hashes": hashes,
            "scorer_version": next(
                (r.scorer_version for r in records if r.score), SCORER_VERSION
            ),
            "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED},
            "summary": summary,
            "summary_by_version": by_version,
            "comparisons": comparisons,
            "comparisons_by_version": per_version,
            "comparisons_by_slice": per_slice,
            "decision": decision,
        }
        args.json_output.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    failures = apply_gates(comparisons, args)
    for failure in failures:
        print(f"GATE FAILED: {failure}", file=sys.stderr)
    if decision is not None and decision["status"] != "PASS":
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
