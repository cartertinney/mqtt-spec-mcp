"""Corpus format 2: independently authored benchmark corpora.

A format-2 corpus is a folder kept outside this repository holding ``dev.jsonl``,
``test.jsonl`` and ``manifest.json`` (JSON Schemas in ``benchmarks/schema/``). It is
registered by name in ``benchmarks/corpora/<name>.json``, which pins the SHA-256 of
its manifest; the manifest pins each split file. Loading a split checks both
hashes, validates every line against the case schema, and checks what the schema
cannot express: unique case, fact and claim ids and the ``split`` field.

The ``test`` split is sealed. The runner, judge and report read it only under a
committed analysis plan (``plan.py``) and report only aggregates from it; ``check``
names test cases only with ``--unseal``.

    python -m mqtt_spec_mcp.benchmark.corpus register NAME PATH   # pin a frozen corpus
    python -m mqtt_spec_mcp.benchmark.corpus check NAME           # validate it again
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mqtt_spec_mcp.loader import load_index
from mqtt_spec_mcp.versions import PINS, SUPPORTED_VERSIONS, VersionId

from .cases import Scope
from .conditions import render_spec_text
from .scorer import count_words, fold

CORPUS_FORMAT = 2
CASE_SCHEMA = "case-v2.schema.json"
MANIFEST_SCHEMA = "manifest-v2.schema.json"
Kind = Literal["scenario", "synthesis", "edge", "rule", "lookup", "silent"]
Origin = Literal["spec", "real-world"]
Split = Literal["dev", "test"]
SPLITS: tuple[Split, ...] = ("dev", "test")
SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SHA256 = r"^[0-9a-f]{64}$"
_MAX_PROBLEMS = 25
BENCH_DIR_ENV = "MQTT_SPEC_BENCH_DIR"


class CorpusError(RuntimeError):
    pass


def bench_dir() -> Path:
    """The repository's ``benchmarks/`` folder: schemas, registry, plans, runs
    (``MQTT_SPEC_BENCH_DIR`` points elsewhere)."""
    override = os.environ.get(BENCH_DIR_ENV)
    if override:
        return Path(override)
    for root in (Path(__file__).resolve().parents[3], Path.cwd()):
        if (root / "benchmarks" / "schema").is_dir():
            return root / "benchmarks"
    raise CorpusError("benchmarks/schema not found: run from a source checkout")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(_Strict):
    id: str
    statement: str
    version: VersionId
    essential: bool
    sections: tuple[str, ...]
    quote: str
    absence: bool = False


class WrongClaim(_Strict):
    id: str
    claim: str
    why_wrong: str
    version: VersionId
    sections: tuple[str, ...]
    quote: str


class CorpusCase(_Strict):
    """One format-2 case. Agents see only its version, prompt and word limit."""

    id: str
    version: Scope
    kind: Kind
    topic: str
    origin: Origin
    inspired_by: tuple[str, ...] | None = None
    prompt: str
    word_limit: int
    sections_needed: tuple[str, ...]
    facts: tuple[Fact, ...]
    forbidden: tuple[WrongClaim, ...]
    reference_answer: str
    split: Split

    @property
    def versions(self) -> tuple[VersionId, ...]:
        if self.version == "both":
            return SUPPORTED_VERSIONS
        return (self.version,)

    @property
    def stratum(self) -> str:
        return self.kind


class SourcePin(_Strict):
    url: str
    html_sha256: str
    text_file: str
    text_sha256: str


class FilePin(_Strict):
    sha256: str
    cases: int


class Manifest(_Strict):
    corpus_format: Literal[2]
    corpus_version: str
    created: str
    split_seed: int
    sources: dict[str, SourcePin]
    files: dict[str, FilePin]
    changelog: tuple[str, ...] = ()


class Registration(_Strict):
    """``benchmarks/corpora/<name>.json``: where a frozen corpus is and its pin."""

    corpus: Annotated[str, Field(pattern=SLUG)]
    path: Annotated[str, Field(min_length=1)]
    corpus_version: str
    manifest_sha256: Annotated[str, Field(pattern=SHA256)]


@dataclass(frozen=True)
class Corpus:
    name: str
    root: Path
    manifest: Manifest
    manifest_sha256: str

    @property
    def version(self) -> str:
        return self.manifest.corpus_version

    def cases(self, split: Split) -> list[CorpusCase]:
        return load_split(self, split)


# Schema validation -----------------------------------------------------------------


@cache
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((bench_dir() / "schema" / name).read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _clip(text: str, limit: int = 160) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def schema_problems(schema: str, instance: Any, *, sealed: bool = False) -> list[str]:
    """JSON Schema violations. Sealed messages omit the offending values, which
    could quote a test case."""
    errors = sorted(_validator(schema).iter_errors(instance), key=lambda e: e.json_path)
    if sealed:
        return [f"{e.json_path}: fails {e.validator!s}" for e in errors]
    return [f"{e.json_path}: {_clip(e.message)}" for e in errors]


def _duplicates(values: Sequence[str]) -> list[str]:
    return sorted(v for v, n in Counter(values).items() if n > 1)


def _raise(path: Path, problems: list[str]) -> None:
    shown = problems[:_MAX_PROBLEMS]
    more = len(problems) - len(shown)
    tail = f"\n  ... and {more} more" if more else ""
    raise CorpusError(
        f"{len(problems)} problem(s) in {path}:\n  " + "\n  ".join(shown) + tail
    )


# Loading ---------------------------------------------------------------------------


def parse_manifest(data: bytes, path: Path) -> Manifest:
    try:
        raw = json.loads(data)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{path} is not JSON: {exc}") from exc
    problems = schema_problems(MANIFEST_SCHEMA, raw)
    if problems:
        _raise(path, problems)
    return Manifest.model_validate(raw)


def parse_split(data: bytes, split: Split, path: Path) -> list[CorpusCase]:
    """Validate and parse one split file (no hash checks)."""
    sealed = split == "test"
    problems: list[str] = []
    cases: list[CorpusCase] = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not a JSON object")
            continue
        found = schema_problems(CASE_SCHEMA, raw, sealed=sealed)
        if found:
            problems.extend(f"line {number}: {p}" for p in found)
            continue
        try:
            case = CorpusCase.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"line {number}: {exc.error_count()} model error(s)")
            continue
        if case.split != split:
            problems.append(f"line {number}: split is {case.split!r} in {path.name}")
        for label, ids in (
            ("fact", [f.id for f in case.facts]),
            ("forbidden claim", [c.id for c in case.forbidden]),
        ):
            repeated = _duplicates(ids)
            if repeated:
                detail = "" if sealed else f" {repeated}"
                problems.append(f"line {number}: duplicate {label} ids{detail}")
        cases.append(case)
    repeated = _duplicates([c.id for c in cases])
    if repeated:
        detail = f"{len(repeated)}" if sealed else ", ".join(repeated)
        problems.append(f"duplicate case ids: {detail}")
    if problems:
        _raise(path, problems)
    return cases


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CorpusError(f"cannot read {path}: {exc.strerror}") from exc


def load_split(corpus: Corpus, split: Split) -> list[CorpusCase]:
    """One split of a registered corpus, checked against the manifest's pin."""
    path = corpus.root / f"{split}.jsonl"
    data = _read(path)
    pin = corpus.manifest.files[f"{split}.jsonl"]
    if sha256_hex(data) != pin.sha256:
        raise CorpusError(
            f"{path} does not match the SHA-256 in its manifest: the corpus changed "
            "after it was frozen (corrections need a new corpus_version)"
        )
    cases = parse_split(data, split, path)
    if len(cases) != pin.cases:
        raise CorpusError(
            f"{path} has {len(cases)} cases; its manifest says {pin.cases}"
        )
    return cases


def registry_path(name: str) -> Path:
    """A registered name, or a path to a registration file."""
    candidate = Path(name)
    if candidate.suffix == ".json":
        return candidate
    return bench_dir() / "corpora" / f"{name}.json"


def open_corpus(name: str) -> Corpus:
    """A registered corpus whose manifest still matches its registration."""
    path = registry_path(name)
    if not path.is_file():
        raise CorpusError(
            f"no corpus registered as {name!r} ({path}); register a frozen corpus "
            f"with: python -m mqtt_spec_mcp.benchmark.corpus register {name} PATH"
        )
    try:
        registration = Registration.model_validate_json(path.read_text("utf-8"))
    except ValidationError as exc:
        raise CorpusError(f"invalid corpus registration {path}: {exc}") from exc
    if registration.corpus != path.stem:
        raise CorpusError(
            f"{path} registers {registration.corpus!r}, not {path.stem!r}"
        )
    root = Path(registration.path)
    if not root.is_absolute():
        root = (path.parent.resolve() / root).resolve()
    manifest_path = root / "manifest.json"
    data = _read(manifest_path)
    digest = sha256_hex(data)
    if digest != registration.manifest_sha256:
        raise CorpusError(
            f"{manifest_path} changed since {registration.corpus!r} was registered "
            f"(sha256 {digest[:12]}, registered {registration.manifest_sha256[:12]})"
        )
    manifest = parse_manifest(data, manifest_path)
    if manifest.corpus_version != registration.corpus_version:
        raise CorpusError(
            f"{manifest_path} is corpus_version {manifest.corpus_version}; "
            f"registered {registration.corpus_version}"
        )
    return Corpus(registration.corpus, root, manifest, digest)


# Checks the schema cannot make -------------------------------------------------------


@cache
def _section_ids(version: VersionId) -> frozenset[str]:
    return frozenset(s.id for s in load_index(version).sections)


def words_of(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text).lower()))


@cache
def _source_words(version: VersionId) -> str:
    return f" {words_of(render_spec_text(version))} "


def quote_found(quote: str, version: VersionId) -> bool:
    """Whether a quote's words appear in order in the indexed text; an ellipsis
    may stand for omitted text."""
    pieces = [words_of(p) for p in re.split(r"\.\.\.+", fold(quote))]
    return all(f" {p} " in _source_words(version) for p in pieces if p)


def case_warnings(case: CorpusCase) -> list[str]:
    """Differences from this server's indexes, and an over-long reference answer."""
    warnings: list[str] = []
    items: list[Fact | WrongClaim] = [*case.facts, *case.forbidden]
    for item in items:
        unknown = [s for s in item.sections if s not in _section_ids(item.version)]
        if unknown:
            warnings.append(
                f"{item.id}: no section {', '.join(unknown)} in MQTT {item.version}"
            )
        if not quote_found(item.quote, item.version):
            warnings.append(
                f"{item.id}: quote not found in the MQTT {item.version} text"
            )
    for ref in case.sections_needed:
        prefix, _, number = ref.rpartition(":")
        versions = [v for v in case.versions if not prefix or v == prefix]
        if not any(number in _section_ids(v) for v in versions):
            warnings.append(f"sections_needed: no section {ref}")
    words = count_words(case.reference_answer)
    if words > case.word_limit:
        warnings.append(f"reference answer has {words} words (limit {case.word_limit})")
    return warnings


def source_warnings(manifest: Manifest) -> list[str]:
    """Source documents other than the ones this server indexes."""
    warnings = []
    for version in SUPPORTED_VERSIONS:
        source = manifest.sources.get(version)
        pin = PINS[version]
        if source is not None and source.url != pin.source_url:
            warnings.append(
                f"MQTT {version}: written from {source.url}; the server indexes "
                f"{pin.source_url}"
            )
    return warnings


def check(corpus: Corpus) -> dict[Split, list[CorpusCase]]:
    """Load both splits and check that no case id appears in both."""
    splits = {split: load_split(corpus, split) for split in SPLITS}
    shared = {c.id for c in splits["dev"]} & {c.id for c in splits["test"]}
    if shared:
        raise CorpusError(f"{len(shared)} case id(s) appear in both dev and test")
    return splits


def register(name: str, path: Path, *, force: bool = False) -> Path:
    """Validate a frozen corpus and write ``benchmarks/corpora/<name>.json``."""
    if not re.match(SLUG, name):
        raise CorpusError(f"corpus names are lowercase slugs, not {name!r}")
    root = path.resolve()
    data = _read(root / "manifest.json")
    corpus = Corpus(
        name, root, parse_manifest(data, root / "manifest.json"), sha256_hex(data)
    )
    check(corpus)
    target = bench_dir() / "corpora" / f"{name}.json"
    if target.exists() and not force:
        current = Registration.model_validate_json(target.read_text("utf-8"))
        if current.manifest_sha256 != corpus.manifest_sha256:
            raise CorpusError(
                f"{name!r} is registered with another manifest; pass --force to "
                "re-pin it (a corrected corpus has a new corpus_version)"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    registration = Registration(
        corpus=name,
        # Both ends resolved: a symlinked folder (macOS /tmp) must not skew it.
        path=Path(os.path.relpath(root, target.parent.resolve())).as_posix(),
        corpus_version=corpus.version,
        manifest_sha256=corpus.manifest_sha256,
    )
    target.write_text(registration.model_dump_json(indent=2) + "\n", "utf-8")
    return target


# Command line ----------------------------------------------------------------------


def _counts(cases: Sequence[CorpusCase], field: str) -> str:
    counts = Counter(str(getattr(c, field)) for c in cases)
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))


def describe(corpus: Corpus, *, unseal: bool = False) -> list[str]:
    splits = check(corpus)
    lines = [
        f"corpus {corpus.name} {corpus.version} at {corpus.root} "
        f"(manifest {corpus.manifest_sha256[:12]})",
        *(f"warning: {w}" for w in source_warnings(corpus.manifest)),
    ]
    for split, cases in splits.items():
        lines.append(f"{split}: {len(cases)} cases")
        for field in ("version", "kind", "origin", "topic"):
            lines.append(f"  {field}: {_counts(cases, field)}")
        found = [(c.id, w) for c in cases for w in case_warnings(c)]
        if split == "test" and not unseal:
            if found:
                lines.append(
                    f"  {len(found)} warning(s) in {len({i for i, _ in found})} "
                    "sealed case(s); --unseal lists them"
                )
            continue
        lines.extend(f"  warning: {case_id}: {w}" for case_id, w in found)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Register and check corpus-format-2 benchmark corpora."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("register", help="validate a frozen corpus and pin it")
    add.add_argument("name", help="registry name, e.g. independent")
    add.add_argument("path", type=Path, help="the corpus folder")
    add.add_argument("--force", action="store_true", help="re-pin an existing name")
    add.add_argument("--unseal", action="store_true", help="name test cases")
    show = commands.add_parser("check", help="validate a registered corpus")
    show.add_argument("name")
    show.add_argument("--unseal", action="store_true", help="name test cases")
    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            target = register(args.name, args.path, force=args.force)
            print(f"registered {args.name} in {target}")
        print("\n".join(describe(open_corpus(args.name), unseal=args.unseal)))
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
