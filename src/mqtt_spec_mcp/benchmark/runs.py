"""Run identity: hashes, seeded plans and checkpointed result records."""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mqtt_spec_mcp import __version__
from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS
from mqtt_spec_mcp.versions import PINS

from .cases import Case, bundled_cases_path
from .conditions import BASE_PROMPT, CONDITION_PROMPTS, MAX_TOOL_CALLS, Condition
from .scorer import SCORER_VERSION

RESULT_FORMAT = 1


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def prompt_hash() -> str:
    return _digest({"base": BASE_PROMPT, "conditions": CONDITION_PROMPTS})


def benchmark_hash(cases_path: Path | None = None) -> str:
    data = (cases_path or bundled_cases_path()).read_bytes()
    return _digest(
        {
            "cases": hashlib.sha256(data).hexdigest(),
            "max_tool_calls": MAX_TOOL_CALLS,
            "format": RESULT_FORMAT,
        }
    )


def server_hash() -> str:
    tools = [t.model_dump(by_alias=True, exclude_none=True) for t in TOOLS]
    pins = {v: p.source_sha256 for v, p in PINS.items()}
    return _digest(
        {"tools": tools, "instructions": INSTRUCTIONS, "pins": pins, "pkg": __version__}
    )


def run_hashes(cases_path: Path | None = None) -> dict[str, str]:
    return {
        "prompt": prompt_hash(),
        "benchmark": benchmark_hash(cases_path),
        "server": server_hash(),
    }


class PlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    epoch: int
    case_id: str
    condition: str


def build_plan(
    cases: Sequence[Case], conditions: Sequence[Condition], epochs: int, seed: int
) -> list[PlanItem]:
    """Seeded case order per epoch; condition order rotates from case to case."""
    plan: list[PlanItem] = []
    for epoch in range(epochs):
        order = [c.id for c in cases]
        random.Random(seed * 1000 + epoch).shuffle(order)  # noqa: S311
        for index, case_id in enumerate(order):
            shift = (index + epoch) % len(conditions)
            rotated = list(conditions[shift:]) + list(conditions[:shift])
            plan.extend(
                PlanItem(epoch=epoch, case_id=case_id, condition=c) for c in rotated
            )
    return plan


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    chars: int
    latency_ms: float
    is_error: bool


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    api_calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: int = RESULT_FORMAT
    runner: str
    model: str
    condition: str
    case_id: str
    epoch: int
    seed: int
    answer: str = ""
    error: str | None = None
    latency_s: float = 0.0
    usage: Usage = Field(default_factory=Usage)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    hashes: dict[str, str]
    score: dict[str, Any] | None = None
    scorer_version: str = SCORER_VERSION

    def key(self) -> tuple[str, str, str, int]:
        return (self.model, self.condition, self.case_id, self.epoch)


def append_record(path: Path, record: RunRecord) -> None:
    """Checkpoint one run: append a JSON line and flush it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_records(paths: Iterable[Path]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                records.append(RunRecord.model_validate_json(line))
    return records


def completed_keys(path: Path) -> set[tuple[str, str, str, int]]:
    return {r.key() for r in load_records([path]) if r.error is None}
