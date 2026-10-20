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
from mqtt_spec_mcp.versions import VersionId, as_version

from .scorer import fold, score_answer

Scope = Literal["3.1.1", "5.0", "both"]
Category = Literal["lookup", "normative", "applied", "synthesis", "edge", "compare"]
# Each scope lives in its own bundled file and has its own required mix.
CATEGORY_TARGETS: dict[str, dict[str, int]] = {
    "5.0": {"lookup": 35, "normative": 30, "applied": 25, "synthesis": 15, "edge": 15},
    "3.1.1": {"lookup": 18, "normative": 15, "applied": 12, "synthesis": 8, "edge": 7},
    "both": {"compare": 20},
}
CASE_FILES: dict[str, str] = {
    "5.0": "cases-v5.0.jsonl",
    "3.1.1": "cases-v3.1.1.jsonl",
    "both": "cases-compare.jsonl",
}
DEFAULT_WORD_LIMIT = 180
COPY_SHINGLE = 8
CASE_ID = r"^[a-z0-9]+(?:-[a-z0-9]+)+$"
SECTION = r"^\d+(?:\.\d+)*$"
# A section reference, optionally qualified by version: "3.1.2.4" or "3.1.1:3.1.2.4".
SECTION_REF = r"^(?:(?:3\.1\.1|5\.0):)?\d+(?:\.\d+)*$"
_MENTIONS_311 = re.compile(r"3\.1\.1")
_MENTIONS_50 = re.compile(r"\b5(?:\.0)?\b")

# Scenarios exercised by the unit tests; benchmark cases must not reuse them.
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
        "clean session",
        "keep alive",
    }
)
ACCEPTANCE_LOOKUPS: dict[str, frozenset[tuple[str, str, str | None]]] = {
    "5.0": frozenset(
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
            ("requirement", "mqtt-3.1.2-4", None),
            ("term", "session", None),
            ("term", "packet identifier", None),
        }
    ),
    "3.1.1": frozenset(
        {
            ("packet", "auth", None),
            ("packet", "15", None),
            ("packet", "connack", None),
            ("packet", "publish", None),
            ("return_code", "0x80", "suback"),
            ("return_code", "0x80", "connack"),
            ("return_code", "0", "connack"),
            ("return_code", "0", "suback"),
            ("return_code", "1", "connack"),
            ("term", "shared subscription", None),
            ("requirement", "mqtt-3.1.2-4", None),
            ("requirement", "[ mqtt-3 .1.2-5]", None),
            ("requirement", "mqtt-3.3.1.-1", None),
            ("requirement", "mqtt-3.1.2.7", None),
            ("requirement", "mqtt-3-8.3-4", None),
        }
    ),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FactPattern(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
    any: Annotated[tuple[str, ...], Field(min_length=1)]
    polarity: Literal["affirm", "negate"] = "affirm"
    section: Annotated[str, Field(pattern=SECTION)]
    # Required in "both" (comparison) cases; otherwise the case's version.
    version: VersionId | None = None


class ForbiddenClaim(_Strict):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_-]+$")]
    any: Annotated[tuple[str, ...], Field(min_length=1)]
    note: Annotated[str, Field(min_length=1)]
    section: Annotated[str, Field(pattern=SECTION)]
    version: VersionId | None = None


class LookupExpectation(_Strict):
    kind: Literal[
        "packet", "property", "reason_code", "return_code", "requirement", "term"
    ]
    query: Annotated[str, Field(min_length=1)]
    packet: str | None = None
    expect: dict[str, str | int]
    version: VersionId | None = None


SectionRef = Annotated[str, Field(pattern=SECTION_REF)]


class Case(_Strict):
    id: Annotated[str, Field(pattern=CASE_ID)]
    version: Scope
    category: Category
    prompt: Annotated[str, Field(min_length=10)]
    facts: Annotated[tuple[FactPattern, ...], Field(min_length=1)]
    citations: Annotated[
        tuple[Annotated[tuple[SectionRef, ...], Field(min_length=1)], ...],
        Field(min_length=1),
    ]
    forbidden: tuple[ForbiddenClaim, ...] = ()
    search_query: Annotated[str, Field(min_length=2)]
    relevant_sections: Annotated[tuple[SectionRef, ...], Field(min_length=1)]
    lookup: LookupExpectation | None = None
    word_limit: Annotated[int, Field(ge=40, le=400)] = DEFAULT_WORD_LIMIT
    provenance: Annotated[str, Field(min_length=10)]
    reference_answer: Annotated[str, Field(min_length=10)]

    @property
    def versions(self) -> tuple[VersionId, ...]:
        """The MQTT versions this case is about."""
        if self.version == "both":
            return ("3.1.1", "5.0")
        return (self.version,)

    @property
    def stratum(self) -> str:
        """The group a stratified sample spreads across (with the version)."""
        return self.category


def split_ref(ref: str, default: VersionId | None) -> tuple[VersionId | None, str]:
    """Split "3.1.1:3.1.2.4" into its version and section ("3.1.2.4" -> default)."""
    if ":" in ref:
        version, section = ref.split(":", 1)
        return (as_version(version), section)
    return (default, ref)


def section_refs(case: Case, refs: Iterable[str]) -> list[tuple[VersionId, str]]:
    """Version-qualified section references; unqualified refs in a "both" case are
    returned against both versions."""
    out: list[tuple[VersionId, str]] = []
    default = None if case.version == "both" else as_version(case.version)
    for ref in refs:
        version, section = split_ref(ref, default)
        if version is None:
            out.extend((v, section) for v in case.versions)
        else:
            out.append((version, section))
    return out


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


def bundled_case_paths() -> list[Path]:
    data = resources.files("mqtt_spec_mcp.benchmark").joinpath("data")
    return [Path(str(data.joinpath(name))) for name in CASE_FILES.values()]


def load_cases(
    path: Path | None = None, versions: Iterable[str] | None = None
) -> list[Case]:
    """Load one file, or every bundled file; optionally keep only some scopes."""
    paths = [path] if path is not None else bundled_case_paths()
    cases: list[Case] = []
    for target in paths:
        if path is None and not target.exists():
            continue
        cases.extend(parse_cases(target.read_text("utf-8").splitlines(), str(target)))
    # Runs, hashes and reports are keyed by case id, across all files.
    duplicates = sorted(k for k, n in Counter(c.id for c in cases).items() if n > 1)
    if duplicates:
        raise ValueError(f"duplicate benchmark case ids: {', '.join(duplicates)}")
    if versions is not None:
        wanted = set(versions)
        cases = [c for c in cases if c.version in wanted]
    return cases


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", fold(text).lower())
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _lookup_matches(result: dict[str, Any], expect: dict[str, str | int]) -> bool:
    target = result
    if result.get("matches"):
        target = result["matches"][0]
    return all(target.get(key) == value for key, value in expect.items())


def lookup_version(case: Case) -> VersionId | None:
    if case.lookup is None:
        return None
    if case.lookup.version is not None:
        return case.lookup.version
    return None if case.version == "both" else as_version(case.version)


def run_lookup(
    service: Any, expectation: LookupExpectation, version: VersionId
) -> tuple[bool, str]:
    args: dict[str, Any] = {
        "version": version,
        "kind": expectation.kind,
        "query": expectation.query,
    }
    if expectation.packet is not None:
        args["packet"] = expectation.packet
    outcome = service.call("mqtt_spec_lookup", args)
    if outcome.is_error:
        return False, outcome.text
    return _lookup_matches(json.loads(outcome.text), expectation.expect), outcome.text


def _check_versions(case: Case) -> list[str]:
    where = f"case {case.id}"
    problems: list[str] = []
    items: list[tuple[str, VersionId | None]] = [
        *((f"fact {f.id}", f.version) for f in case.facts),
        *((f"forbidden {f.id}", f.version) for f in case.forbidden),
    ]
    if case.lookup is not None:
        items.append(("lookup", case.lookup.version))
    for label, version in items:
        if case.version == "both" and version is None:
            problems.append(f"{where}: {label} needs a version in a comparison case")
        if case.version != "both" and version not in (None, case.version):
            problems.append(f"{where}: {label} names version {version}")
    refs = [*case.relevant_sections, *(r for g in case.citations for r in g)]
    for ref in refs:
        version, _ = split_ref(ref, None)
        if case.version == "both" and version is None:
            problems.append(f"{where}: section {ref} needs a version prefix")
        if case.version != "both" and version not in (None, case.version):
            problems.append(f"{where}: section {ref} is from another version")
    if case.version == "both" and not (
        _MENTIONS_311.search(case.prompt) and _MENTIONS_50.search(case.prompt)
    ):
        problems.append(f"{where}: a comparison prompt must name both versions")
    if case.version == "both" and case.category != "compare":
        problems.append(f"{where}: comparison cases use category 'compare'")
    if case.version != "both" and case.category == "compare":
        problems.append(f"{where}: category 'compare' needs version 'both'")
    return problems


def validate_cases(
    cases: Sequence[Case],
    *,
    partial: bool = False,
    require_scopes: Iterable[str] = (),
) -> list[str]:
    """Return a list of problems; empty means the corpus is valid."""
    service = MqttSpecService()
    engines = service.engines
    spec_shingles: set[tuple[str, ...]] = set()
    for engine in engines.values():
        text = "\n".join(f"{s.title}\n{s.body}" for s in engine.ordered)
        spec_shingles |= _shingles(text, COPY_SHINGLE)
    problems: list[str] = []

    for dup, n in Counter(c.id for c in cases).items():
        if n > 1:
            problems.append(f"duplicate case id {dup}")
    for case in cases:
        where = f"case {case.id}"
        problems.extend(_check_versions(case))
        default = None if case.version == "both" else as_version(case.version)
        referenced = section_refs(
            case,
            [*(s for group in case.citations for s in group), *case.relevant_sections],
        )
        referenced += [(f.version or default or "5.0", f.section) for f in case.facts]
        referenced += [
            (f.version or default or "5.0", f.section) for f in case.forbidden
        ]
        for ref_version, section in sorted(set(referenced)):
            if section not in engines[ref_version].sections:
                problems.append(
                    f"{where}: unknown MQTT {ref_version} section {section}"
                )
        for fact in case.facts:
            version = fact.version or default
            if fact.section not in case.provenance:
                problems.append(f"{where}: provenance does not name {fact.section}")
            for pattern in fact.any:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(f"{where}: bad regex {pattern!r}: {exc}")
            if version is not None and fact.section in engines[version].sections:
                info = engines[version].sections[fact.section]
                grounding = fold(f"{info.title}\n{info.body}")
                if not any(
                    re.search(p, grounding, re.IGNORECASE)
                    for p in fact.any
                    if _compiles(p)
                ):
                    problems.append(
                        f"{where}: fact {fact.id} is not checkable against MQTT "
                        f"{version} section {fact.section} (no alternative matches "
                        "its text)"
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
            problems.append(f"{where}: search_query reuses a unit-test query")
        lookup_in = lookup_version(case)
        if case.lookup is not None and lookup_in is not None:
            key = (
                case.lookup.kind,
                case.lookup.query.strip().lower(),
                case.lookup.packet.strip().lower() if case.lookup.packet else None,
            )
            if key in ACCEPTANCE_LOOKUPS[lookup_in]:
                problems.append(f"{where}: lookup reuses a unit-test scenario")
            ok, detail = run_lookup(service, case.lookup, lookup_in)
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
    scopes = {c.version for c in cases} | set(require_scopes)
    for scope in sorted(scopes):
        group = [c for c in cases if c.version == scope]
        problems.extend(_check_mix(scope, group))
        if scope != "both":
            version_id = as_version(scope)
            problems.extend(_check_coverage(version_id, group, engines[version_id]))
    return problems


def _check_mix(scope: str, cases: Sequence[Case]) -> list[str]:
    counts: Counter[str] = Counter(c.category for c in cases)
    targets = CATEGORY_TARGETS[scope]
    problems = [
        f"MQTT {scope}: expected {want} {category} cases, found {counts[category]}"
        for category, want in targets.items()
        if counts[category] != want
    ]
    problems.extend(
        f"MQTT {scope}: unexpected category {category}"
        for category in counts
        if category not in targets
    )
    return problems


def _check_coverage(
    version: VersionId, cases: Sequence[Case], engine: Any
) -> list[str]:
    covered: set[str] = set()
    for case in cases:
        refs = [*(s for g in case.citations for s in g), *case.relevant_sections]
        covered |= {section for _, section in section_refs(case, refs)}
    problems = [
        f"MQTT {version}: no case covers chapter {chapter}"
        for chapter in map(str, range(1, 8))
        if chapter not in {s.split(".")[0] for s in covered}
    ]
    packets = {
        int(s.split(".")[1]) for s in covered if s.startswith("3.") and s.count(".")
    }
    problems.extend(
        f"MQTT {version}: no case covers packet {p.name} (section {p.section})"
        for p in engine.packets
        if p.value not in packets
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
    scopes = CATEGORY_TARGETS if args.path is None and not args.partial else ()
    problems = validate_cases(cases, partial=args.partial, require_scopes=scopes)
    for problem in problems:
        print(problem)
    counts = Counter(f"{c.version}/{c.category}" for c in cases)
    print(
        f"{len(cases)} cases {dict(sorted(counts.items()))}; {len(problems)} problems"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
