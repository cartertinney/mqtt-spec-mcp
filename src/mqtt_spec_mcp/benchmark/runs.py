"""Run identity: hashes, seeded plans and checkpointed result records."""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mqtt_spec_mcp import __version__
from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS
from mqtt_spec_mcp.versions import PINS, SUPPORTED_VERSIONS

from .cases import Case
from .conditions import (
    BASE_PROMPT,
    CONDITION_PROMPTS,
    MAX_TOOL_CALLS,
    Condition,
    spec_text,
)
from .scorer import SCORER_VERSION

RESULT_FORMAT = 2


def _digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def base_hash() -> str:
    """Instructions shared by every condition."""
    return _digest({"base": BASE_PROMPT, "max_tool_calls": MAX_TOOL_CALLS})


def prompt_hash(condition: str) -> str:
    prompt = next(p for c, p in CONDITION_PROMPTS.items() if c == condition)
    return _digest({"base": BASE_PROMPT, "condition": prompt})


def case_hash(case: Case) -> str:
    """What the model is asked for one case. Answer keys are excluded: stored
    answers are rescored offline with the current keys (``--rescore``)."""
    asked = (case.id, case.version, case.prompt, case.word_limit)
    return _digest({"case": asked, "format": RESULT_FORMAT})


def _server_source() -> str:
    """Digest of the server's own modules (not this benchmark subpackage), so a
    change to ranking or snippets counts as a different server."""
    digest = hashlib.sha256()
    for path in sorted(Path(str(resources.files("mqtt_spec_mcp"))).glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def server_hash() -> str:
    tools = [t.model_dump(by_alias=True, exclude_none=True) for t in TOOLS]
    data_dir = resources.files("mqtt_spec_mcp").joinpath("data")
    indexes = {
        v: hashlib.sha256(data_dir.joinpath(p.data_file).read_bytes()).hexdigest()
        for v, p in PINS.items()
    }
    return _digest(
        {
            "tools": tools,
            "instructions": INSTRUCTIONS,
            "indexes": indexes,
            "source": _server_source(),
            "pkg": __version__,
        }
    )


def reference_hash(condition: str) -> str:
    """The reference material a condition can reach."""
    if condition == "mcp":
        return server_hash()
    if condition in ("full_spec", "grep"):
        return _digest({v: spec_text((v,)) for v in SUPPORTED_VERSIONS})
    return "none"


def condition_hashes(condition: str) -> dict[str, str]:
    return {
        "base": base_hash(),
        "prompt": prompt_hash(condition),
        "reference": reference_hash(condition),
    }


def run_hashes(condition: str, case: Case) -> dict[str, str]:
    return {**condition_hashes(condition), "case": case_hash(case)}


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
    version: str
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


def completed_keys(
    path: Path, expected: Mapping[tuple[str, str], Mapping[str, str]] | None = None
) -> set[tuple[str, str, str, int]]:
    """Keys of successful runs. Given the current hashes per (condition, case id),
    a run whose question, prompt or reference material has changed is not done."""
    return {
        r.key()
        for r in load_records([path])
        if r.error is None
        and (expected is None or expected.get((r.condition, r.case_id)) == r.hashes)
    }


def latest_records(records: Iterable[RunRecord]) -> list[RunRecord]:
    """One record per (model, condition, case, epoch): the last successful run,
    else the last failed one. Resumed runs append retries after earlier errors."""
    chosen: dict[tuple[str, str, str, int], RunRecord] = {}
    for record in records:
        current = chosen.get(record.key())
        if current is None or record.error is None or current.error is not None:
            chosen[record.key()] = record
    return list(chosen.values())
