"""Run identity: hashes, seeded plans and checkpointed result records."""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from mqtt_spec_mcp import __version__
from mqtt_spec_mcp.server import INSTRUCTIONS, TOOLS
from mqtt_spec_mcp.versions import PINS, SUPPORTED_VERSIONS, VersionId

from .conditions import (
    BASE_PROMPT,
    CONDITION_PROMPTS,
    MAX_TOOL_CALLS,
    Condition,
    spec_text,
)
from .scorer import SCORER_VERSION

RESULT_FORMAT = 2
CACHE_READ_WEIGHT = 0.1
CACHE_WRITE_WEIGHT = 1.25
OUTPUT_WEIGHT = 5.0


class Question(Protocol):
    """What the runner needs from a case of either corpus format."""

    @property
    def id(self) -> str: ...
    @property
    def version(self) -> str: ...
    @property
    def versions(self) -> tuple[VersionId, ...]: ...
    @property
    def prompt(self) -> str: ...
    @property
    def word_limit(self) -> int: ...
    @property
    def stratum(self) -> str: ...


def digest(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def base_hash() -> str:
    """Instructions shared by every condition."""
    return digest({"base": BASE_PROMPT, "max_tool_calls": MAX_TOOL_CALLS})


def prompt_hash(condition: str) -> str:
    prompt = next(p for c, p in CONDITION_PROMPTS.items() if c == condition)
    return digest({"base": BASE_PROMPT, "condition": prompt})


def case_hash(case: Question) -> str:
    """What the model is asked for one case. Answer keys are excluded: stored
    answers are rescored offline with the current keys (``--rescore``)."""
    asked = (case.id, case.version, case.prompt, case.word_limit)
    return digest({"case": asked, "format": RESULT_FORMAT})


def _server_source() -> str:
    """Digest of the server's own modules (not this benchmark subpackage), so a
    change to ranking or snippets counts as a different server."""
    hasher = hashlib.sha256()
    for path in sorted(Path(str(resources.files("mqtt_spec_mcp"))).glob("*.py")):
        hasher.update(path.name.encode())
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def server_hash() -> str:
    tools = [t.model_dump(by_alias=True, exclude_none=True) for t in TOOLS]
    data_dir = resources.files("mqtt_spec_mcp").joinpath("data")
    indexes = {
        v: hashlib.sha256(data_dir.joinpath(p.data_file).read_bytes()).hexdigest()
        for v, p in PINS.items()
    }
    return digest(
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
        return digest({v: spec_text((v,)) for v in SUPPORTED_VERSIONS})
    return "none"


def condition_hashes(condition: str) -> dict[str, str]:
    return {
        "base": base_hash(),
        "prompt": prompt_hash(condition),
        "reference": reference_hash(condition),
    }


def run_hashes(condition: str, case: Question) -> dict[str, str]:
    return {**condition_hashes(condition), "case": case_hash(case)}


class PlanItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    epoch: int
    case_id: str
    condition: Condition


def build_plan(
    cases: Sequence[Question], conditions: Sequence[Condition], epochs: int, seed: int
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


Q = TypeVar("Q", bound=Question)


def stratified_sample(cases: Sequence[Q], size: int, seed: int) -> list[Q]:
    """A deterministic sample spread across (version, category or kind) groups."""
    groups: dict[tuple[str, str], list[Q]] = {}
    for case in sorted(cases, key=lambda c: c.id):
        groups.setdefault((case.version, case.stratum), []).append(case)
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
    for members in groups.values():
        rng.shuffle(members)
    chosen: list[Q] = []
    keys = sorted(groups)
    while len(chosen) < min(size, len(cases)):
        for key in keys:
            if groups[key] and len(chosen) < size:
                chosen.append(groups[key].pop())
    return chosen


class ToolCallRecord(BaseModel):
    name: str
    arguments: dict[str, Any]
    chars: int
    latency_ms: float
    is_error: bool


class Prices(BaseModel):
    """A model's token prices when it ran (any unit; only ratios are used)."""

    input: float
    cache_read: float
    cache_write: float
    output: float


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

    def weighted_tokens(self, prices: Prices | None = None) -> float:
        """Input-equivalent tokens: each kind weighted by its price relative to
        uncached input (input counts include cached tokens). Without the model's
        prices: cache reads 0.1, cache writes 1.25, output 5."""
        read, write, output = CACHE_READ_WEIGHT, CACHE_WRITE_WEIGHT, OUTPUT_WEIGHT
        if prices is not None and prices.input > 0:
            read = prices.cache_read / prices.input
            # A model without a cache-write price bills writes as input.
            write = prices.cache_write / prices.input if prices.cache_write else 1.0
            output = prices.output / prices.input
        cached = self.cache_read_tokens + self.cache_write_tokens
        return (
            max(0, self.input_tokens - cached)
            + read * self.cache_read_tokens
            + write * self.cache_write_tokens
            + output * self.output_tokens
        )


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
    # Corpus-format-2 runs only: answers are scored later by the judge.
    corpus: str | None = None
    corpus_version: str | None = None
    split: str | None = None
    plan: str | None = None
    prices: Prices | None = None

    @property
    def weighted_tokens(self) -> float:
        return self.usage.weighted_tokens(self.prices)

    def key(self) -> tuple[str, str, str, int]:
        return (self.model, self.condition, self.case_id, self.epoch)


def append_record(path: Path, record: BaseModel) -> None:
    """Checkpoint one run (or judgment): append a JSON line and flush it to disk."""
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


Labels = tuple[str | None, str | None, str | None, str | None]


def run_labels(record: RunRecord) -> Labels:
    """(corpus, corpus_version, split, plan): runs with different labels never
    share a results file."""
    return (record.corpus, record.corpus_version, record.split, record.plan)


def output_labels(path: Path) -> set[Labels]:
    return {run_labels(r) for r in load_records([path])}


def latest_records(records: Iterable[RunRecord]) -> list[RunRecord]:
    """One record per (model, condition, case, epoch): the last successful run,
    else the last failed one. Resumed runs append retries after earlier errors."""
    chosen: dict[tuple[str, str, str, int], RunRecord] = {}
    for record in records:
        current = chosen.get(record.key())
        if current is None or record.error is None or current.error is not None:
            chosen[record.key()] = record
    return list(chosen.values())
