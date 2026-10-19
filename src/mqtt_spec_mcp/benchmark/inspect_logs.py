"""Convert Inspect AI eval logs into benchmark run records (needs the extra)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from inspect_ai.log import read_eval_log

from .runs import RunRecord, ToolCallRecord, Usage


def load_inspect_logs(paths: Sequence[Path]) -> list[RunRecord]:
    """Convert Inspect AI eval logs into run records."""
    records: list[RunRecord] = []
    for path in paths:
        log = read_eval_log(str(path))
        meta = log.eval.metadata or {}
        condition = str(
            log.eval.task_args.get("condition") or meta.get("condition", "?")
        )
        seed = int(log.eval.task_args.get("seed") or meta.get("seed", 0))
        for sample in log.samples or []:
            usage = Usage()
            for model_usage in (sample.model_usage or {}).values():
                usage.input_tokens += model_usage.input_tokens
                usage.output_tokens += model_usage.output_tokens
                usage.cache_read_tokens += model_usage.input_tokens_cache_read or 0
                usage.cache_write_tokens += model_usage.input_tokens_cache_write or 0
                usage.cost += model_usage.total_cost or 0.0
            calls = (sample.metadata or {}).get("tool_calls", [])
            records.append(
                RunRecord(
                    runner="inspect-ai",
                    model=log.eval.model,
                    condition=condition,
                    case_id=str(sample.id),
                    version=str((sample.metadata or {}).get("version", "?")),
                    epoch=sample.epoch - 1,
                    seed=seed,
                    answer=sample.output.completion if sample.output else "",
                    error=str(sample.error.message) if sample.error else None,
                    latency_s=float(sample.total_time or 0.0),
                    usage=usage,
                    tool_calls=[ToolCallRecord(**c) for c in calls],
                    hashes={
                        **dict(meta.get("hashes", {})),
                        "case": str((sample.metadata or {}).get("case_hash", "")),
                    },
                )
            )
    return records
