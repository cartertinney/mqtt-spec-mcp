"""Benchmark case corpus: strict schema, loading and corpus-level validation.

Run ``python -m mqtt_spec_mcp.benchmark.cases`` to validate the bundled corpus, or
``... cases path/to/file.jsonl --partial`` while authoring a subset.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mqtt_spec_mcp.server import MqttSpecService
from mqtt_spec_mcp.versions import VersionId

from .scorer import fold, score_answer

Category = Literal["lookup", "normative", "applied", "synthesis", "edge"]
CATEGORY_TARGETS: dict[Category, int] = {
    "lookup": 35,
    "normative": 30,
    "applied": 25,
    "synthesis": 15,
    "edge": 15,
}
CASE_VERSION: VersionId = "5.0"
CASES_FILE = "cases-v5.0.jsonl"
DEFAULT_WORD_LIMIT = 180
COPY_SHINGLE = 8
CASE_ID = r"^[a-z0-9]+(?:-[a-z0-9]+)+$"
SECTION = r"^\d+(?:\.\d+)*$"

# Acceptance-test scenarios from the requirements; benchmark cases must not reuse them.
ACCEPTANCE_QUERIES = frozenset(
    {
        "will delay interval",
        "shared subscription",
        "topic alias",
        "mqtt-3.1.2-23",
        "mqtt-3.1.4-2",
        "session",
        "mqtt 5.0 session",
        "session in mqtt 5.0.",
    }
)
ACCEPTANCE_LOOKUPS = frozenset(
    {
        ("packet", "connect", None),
        ("property", "0x11", "connect"),
        ("property", "interval", None),
        ("property", "reason string", "publish"),
        ("property", "will flag", None),
        ("reason_code", "0x00", "suback"),
        ("reason_code", "unsuback", None),
        ("reason_code", "0x99", "publish"),
        ("requirement", "[mqtt-3.1.2-23]", None),
        ("requirement", "mqtt-3.1.2-23", None),
        ("term", "session", None),
        ("term", "packet identifier", None),
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FactPattern(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
    any: Annotated[tuple[str, ...], Field(min_length=1)]
    polarity: Literal["affirm", "negate"] = "affirm"
    section: Annotated[str, Field(pattern=SECTION)]


class ForbiddenClaim(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
    any: Annotated[tuple[str, ...], Field(min_length=1)]
    note: Annotated[str, Field(min_length=1)]
    section: Annotated[str, Field(pattern=SECTION)]


class LookupExpectation(_Strict):
    kind: Literal["packet", "property", "reason_code", "requirement", "term"]
    query: Annotated[str, Field(min_length=1)]
    packet: str | None = None
    expect: dict[str, str | int]


class Case(_Strict):
    id: Annotated[str, Field(pattern=CASE_ID)]
    category: Category
    prompt: Annotated[str, Field(min_length=10)]
    facts: Annotated[tuple[FactPattern, ...], Field(min_length=1)]
    citations: Annotated[
        tuple[
            Annotated[
                tuple[Annotated[str, Field(pattern=SECTION)], ...], Field(min_length=1)
            ],
            ...,
        ],
        Field(min_length=1),
    ]
    forbidden: tuple[ForbiddenClaim, ...] = ()
    search_query: Annotated[str, Field(min_length=2)]
    relevant_sections: Annotated[
        tuple[Annotated[str, Field(pattern=SECTION)], ...], Field(min_length=1)
    ]
    lookup: LookupExpectation | None = None
    word_limit: Annotated[int, Field(ge=40, le=400)] = DEFAULT_WORD_LIMIT
    provenance: Annotated[str, Field(min_length=10)]
    reference_answer: Annotated[str, Field(min_length=10)]


def parse_cases(lines: Iterable[str], source: str = "<cases>") -> list[Case]:
    cases: list[Case] = []
    errors: list[str] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate_json(line, strict=True))
        except ValidationError as exc:
            errors.append(f"{source}:{number}: {exc}")
    if errors:
        raise ValueError("invalid benchmark cases:\n" + "\n".join(errors))
    return cases


def bundled_cases_path() -> Path:
    return Path(
        str(
            resources.files("mqtt_spec_mcp.benchmark")
            .joinpath("data")
            .joinpath(CASES_FILE)
        )
    )


def load_cases(path: Path | None = None) -> list[Case]:
    target = path or bundled_cases_path()
    return parse_cases(target.read_text("utf-8").splitlines(), str(target))


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", fold(text).lower())
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _lookup_matches(result: dict[str, Any], expect: dict[str, str | int]) -> bool:
    target = result
    if result.get("matches"):
        target = result["matches"][0]
    return all(target.get(key) == value for key, value in expect.items())


def run_lookup(service: Any, expectation: LookupExpectation) -> tuple[bool, str]:
    args: dict[str, Any] = {
        "version": CASE_VERSION,
        "kind": expectation.kind,
        "query": expectation.query,
    }
    if expectation.packet is not None:
        args["packet"] = expectation.packet
    outcome = service.call("mqtt_spec_lookup", args)
    if outcome.is_error:
        return False, outcome.text
    return _lookup_matches(json.loads(outcome.text), expectation.expect), outcome.text


def validate_cases(cases: Sequence[Case], *, partial: bool = False) -> list[str]:
    """Return a list of problems; empty means the corpus is valid."""
    service = MqttSpecService([CASE_VERSION])
    engine = service.engines[CASE_VERSION]
    sections = engine.sections
    spec_text = "\n".join(f"{s.title}\n{s.body}" for s in engine.ordered)
    spec_shingles = _shingles(spec_text, COPY_SHINGLE)
    problems: list[str] = []

    for dup, n in Counter(c.id for c in cases).items():
        if n > 1:
            problems.append(f"duplicate case id {dup}")
    for case in cases:
        where = f"case {case.id}"
        referenced = {
            *(s for group in case.citations for s in group),
            *case.relevant_sections,
            *(f.section for f in case.facts),
            *(f.section for f in case.forbidden),
        }
        for section in sorted(referenced):
            if section not in sections:
                problems.append(f"{where}: unknown section {section}")
        for fact in case.facts:
            if fact.section not in case.provenance:
                problems.append(f"{where}: provenance does not name {fact.section}")
            for pattern in fact.any:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(f"{where}: bad regex {pattern!r}: {exc}")
            if fact.section in sections:
                info = sections[fact.section]
                grounding = fold(f"{info.title}\n{info.body}")
                if not any(
                    re.search(p, grounding, re.IGNORECASE)
                    for p in fact.any
                    if _compiles(p)
                ):
                    problems.append(
                        f"{where}: fact {fact.id} is not checkable against "
                        f"section {fact.section} (no alternative matches its text)"
                    )
        for claim in case.forbidden:
            for pattern in claim.any:
                if not _compiles(pattern):
                    problems.append(f"{where}: bad forbidden regex {pattern!r}")
        for label, text in (
            ("prompt", case.prompt),
            ("search_query", case.search_query),
        ):
            if _shingles(text, COPY_SHINGLE) & spec_shingles:
                problems.append(f"{where}: {label} copies spec text verbatim")
        if " ".join(case.search_query.lower().split()) in ACCEPTANCE_QUERIES:
            problems.append(f"{where}: search_query reuses an acceptance-test query")
        if case.lookup is not None:
            key = (
                case.lookup.kind,
                case.lookup.query.strip().lower(),
                case.lookup.packet.strip().lower() if case.lookup.packet else None,
            )
            if key in ACCEPTANCE_LOOKUPS:
                problems.append(f"{where}: lookup reuses an acceptance-test scenario")
            ok, detail = run_lookup(service, case.lookup)
            if not ok:
                problems.append(f"{where}: lookup expectation failed: {detail[:200]}")
        try:
            score = score_answer(case, case.reference_answer)
        except re.error as exc:
            problems.append(f"{where}: scoring failed: {exc}")
            continue
        if not score.passed:
            problems.append(
                f"{where}: reference answer does not pass (missing facts "
                f"{score.facts_missing}, citation groups {score.citations_missing}, "
                f"violations {score.violations})"
            )
        if score.words > case.word_limit:
            problems.append(f"{where}: reference answer exceeds word limit")

    if partial:
        return problems
    counts = Counter(c.category for c in cases)
    for category, want in CATEGORY_TARGETS.items():
        if counts[category] != want:
            problems.append(
                f"expected {want} {category} cases, found {counts[category]}"
            )
    covered = {s for c in cases for group in c.citations for s in group}
    covered |= {s for c in cases for s in c.relevant_sections}
    chapters = {s.split(".")[0] for s in covered}
    for chapter in map(str, range(1, 8)):
        if chapter not in chapters:
            problems.append(f"no case covers chapter {chapter}")
    packets = {
        int(s.split(".")[1]) for s in covered if s.startswith("3.") and s.count(".")
    }
    for packet in engine.packets:
        if packet.value not in packets:
            problems.append(
                f"no case covers packet {packet.name} (section {packet.section})"
            )
    return problems


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the benchmark case corpus.")
    parser.add_argument(
        "path", nargs="?", type=Path, help="JSONL file (default: bundled)"
    )
    parser.add_argument(
        "--partial", action="store_true", help="skip mix/coverage checks"
    )
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.path)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    problems = validate_cases(cases, partial=args.partial)
    for problem in problems:
        print(problem)
    counts = Counter(c.category for c in cases)
    print(
        f"{len(cases)} cases {dict(sorted(counts.items()))}; {len(problems)} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
