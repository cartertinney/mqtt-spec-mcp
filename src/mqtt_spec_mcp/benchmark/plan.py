"""Analysis plans: what a benchmark run measures, fixed before any answer exists.

A plan is a TOML file under ``benchmarks/plans/``. It fixes the corpus (by manifest
hash) and split; the models, conditions, epochs and seed; what each condition gives
the model (by hash: the shared instructions, the condition's prompt, and the server
or specification text it reaches); the judge model, prompt and scoring version; and
the decision rule. The runner, judge and report read the sealed test split only
under a plan, refuse a plan whose hashes no longer match the code or the corpus,
and require a test plan to be committed and unchanged, so git history shows that
the plan predates its results.

    python -m mqtt_spec_mcp.benchmark.plan init NAME --corpus C --models M ...
    python -m mqtt_spec_mcp.benchmark.plan check benchmarks/plans/NAME.toml
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .conditions import CONDITIONS, Condition
from .corpus import (
    SHA256,
    SLUG,
    SPLITS,
    Corpus,
    CorpusCase,
    CorpusError,
    Split,
    bench_dir,
    open_corpus,
    sha256_hex,
)
from .judge import JUDGE_PROMPT_VERSION, SCORING_VERSION, judge_hash
from .runs import RunRecord, base_hash, case_hash, condition_hashes

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

Metric = Literal[
    "quality_delta",
    "pass_delta",
    "token_reduction",
    "weighted_token_reduction",
    "speedup",
]
METRICS: tuple[Metric, ...] = get_args(Metric)
DEFAULT_TIMEOUT = 240.0


class PlanError(RuntimeError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConditionPins(_Strict):
    prompt: str
    reference: str


class Pins(_Strict):
    base: str
    conditions: dict[str, ConditionPins]


class JudgeSettings(_Strict):
    """One judge, or a panel scored by the mean of its judges' scores."""

    models: Annotated[
        tuple[Annotated[str, Field(min_length=1)], ...], Field(min_length=1)
    ]
    prompt_version: str
    prompt_hash: str
    scoring_version: str


class Check(_Strict):
    """Passes when the 95% CI's lower bound of ``metric`` is above the threshold,
    for the pooled comparison or for every model's."""

    name: str
    metric: Metric
    scope: Literal["pooled", "each_model"] = "pooled"
    ci_low_above: float


class Analysis(_Strict):
    baseline: Condition
    candidate: Condition
    checks: Annotated[tuple[Check, ...], Field(min_length=1)]


class Plan(_Strict):
    name: Annotated[str, Field(pattern=SLUG)]
    description: Annotated[str, Field(min_length=1)]
    corpus: Annotated[str, Field(pattern=SLUG)]
    corpus_version: str
    manifest_sha256: Annotated[str, Field(pattern=SHA256)]
    split: Split
    models: Annotated[tuple[str, ...], Field(min_length=1)]
    conditions: Annotated[tuple[Condition, ...], Field(min_length=2)]
    epochs: Annotated[int, Field(ge=1)]
    seed: int
    timeout: Annotated[float, Field(gt=0)] = DEFAULT_TIMEOUT
    server_commit: str | None = None
    hashes: Pins
    judge: JudgeSettings
    analysis: Analysis

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        if len(set(self.models)) < len(self.models):
            raise ValueError("models repeat")
        if len(set(self.conditions)) < len(self.conditions):
            raise ValueError("conditions repeat")
        if len(set(self.judge.models)) < len(self.judge.models):
            raise ValueError("judge models repeat")
        if set(self.judge.models) & set(self.models):
            raise ValueError("a judge must not be one of the models under test")
        pair = {self.analysis.baseline, self.analysis.candidate}
        if len(pair) < 2 or not pair <= set(self.conditions):
            raise ValueError("baseline and candidate must be two of the conditions")
        if set(self.hashes.conditions) != set(self.conditions):
            raise ValueError("hashes.conditions must pin exactly the plan's conditions")
        return self


@dataclass(frozen=True)
class LoadedPlan:
    plan: Plan
    path: Path
    sha256: str

    @property
    def id(self) -> str:
        """Recorded on every run made under this plan (an edit changes it)."""
        return f"{self.plan.name}@{self.sha256[:12]}"

    @property
    def results_path(self) -> Path:
        plan = self.plan
        return bench_dir() / "runs" / plan.corpus / plan.split / f"{plan.name}.jsonl"


def judgments_path(results: Path) -> Path:
    """Where judgments of the answers in a results file are cached."""
    return results.with_name(f"{results.stem}.judgments.jsonl")


def load_plan(path: Path) -> LoadedPlan:
    try:
        data = path.read_bytes()
        raw = tomllib.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PlanError(f"cannot read plan {path}: {exc}") from exc
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        raise PlanError(f"invalid plan {path}:\n{exc}") from exc
    return LoadedPlan(plan, path, sha256_hex(data))


def _git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    return subprocess.run(  # noqa: S603 - fixed git arguments
        [git, *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def commit_problems(path: Path) -> list[str]:
    """A test plan must be committed, and unchanged since, before it runs."""
    where, name = path.resolve().parent, path.name
    listed = _git(["ls-tree", "--name-only", "HEAD", "--", name], where)
    if listed is None:
        return ["git not found: a test plan must be committed before it runs"]
    if listed.returncode != 0 or not listed.stdout.strip():
        return [f"{path} is not committed: commit the plan before running it"]
    status = _git(["status", "--porcelain", "--", name], where)
    if status is None or status.stdout.strip():
        return [f"{path} has uncommitted changes"]
    return []


def corpus_problems(plan: Plan) -> list[str]:
    try:
        corpus = open_corpus(plan.corpus)
    except CorpusError as exc:
        return [str(exc)]
    if (corpus.version, corpus.manifest_sha256) != (
        plan.corpus_version,
        plan.manifest_sha256,
    ):
        return [
            f"corpus {plan.corpus} is {corpus.version} (manifest "
            f"{corpus.manifest_sha256[:12]}); the plan pins {plan.corpus_version} "
            f"({plan.manifest_sha256[:12]})"
        ]
    return []


def hash_problems(plan: Plan) -> list[str]:
    """What the conditions give the model now, against the plan's pins."""
    problems = []
    if base_hash() != plan.hashes.base:
        problems.append("the shared instructions changed (hashes.base)")
    for condition in plan.conditions:
        pins = plan.hashes.conditions[condition]
        current = condition_hashes(condition)
        for key, pinned in (("prompt", pins.prompt), ("reference", pins.reference)):
            if current[key] != pinned:
                problems.append(
                    f"{condition}: the {key} hash is {current[key]}; the plan pins "
                    f"{pinned}"
                )
    return problems


def judge_problems(plan: Plan) -> list[str]:
    problems = []
    judge = plan.judge
    current = (JUDGE_PROMPT_VERSION, judge_hash())
    if (judge.prompt_version, judge.prompt_hash) != current:
        problems.append(
            f"the judge prompt is version {current[0]} ({current[1]}); the plan pins "
            f"{judge.prompt_version} ({judge.prompt_hash})"
        )
    if judge.scoring_version != SCORING_VERSION:
        problems.append(
            f"the scoring formula is version {SCORING_VERSION}; the plan pins "
            f"{judge.scoring_version}"
        )
    return problems


def plan_problems(loaded: LoadedPlan) -> list[str]:
    """Differences between the plan and the current corpus, code and judge."""
    plan = loaded.plan
    problems = corpus_problems(plan) + hash_problems(plan) + judge_problems(plan)
    if plan.split == "test":
        problems.extend(commit_problems(loaded.path))
    return problems


def verified_plan(path: Path) -> LoadedPlan:
    loaded = load_plan(path)
    problems = plan_problems(loaded)
    if problems:
        raise PlanError(
            f"plan {path} does not match the current corpus, code or judge:\n  "
            + "\n  ".join(problems)
        )
    return loaded


def corpus_cases(
    records: Sequence[RunRecord], plan: LoadedPlan | None
) -> dict[str, CorpusCase]:
    """The cases behind corpus-format-2 records from one corpus and split. Test
    records are read only under the plan they were made with."""
    sources = {(r.corpus, r.corpus_version, r.split) for r in records}
    if len(sources) != 1:
        raise PlanError("records mix corpora or splits; use one corpus split at a time")
    name, version, recorded = sources.pop()
    if name is None or recorded not in SPLITS:
        raise PlanError("records do not name their corpus and split")
    split: Split = "test" if recorded == "test" else "dev"
    sealed = split == "test"
    if sealed:
        if plan is None:
            raise PlanError("the test split is sealed: pass its analysis plan (--plan)")
        if (plan.plan.corpus, plan.plan.split) != (name, split):
            raise PlanError(f"plan {plan.id} is not for {name} {split}")
        others = sorted({str(r.plan) for r in records} - {plan.id})
        if others:
            raise PlanError(
                f"records were made under {', '.join(others)}, not plan {plan.id}"
            )
    corpus = open_corpus(name)
    if corpus.version != version:
        raise PlanError(
            f"records are from {name} {version}; {name} is now {corpus.version}"
        )
    cases = {c.id: c for c in corpus.cases(split)}
    for record in records:
        case = cases.get(record.case_id)
        what = "a record" if sealed else record.case_id
        if case is None:
            raise PlanError(f"{what} is not a case of {name} {split}")
        if record.hashes.get("case") != case_hash(case):
            raise PlanError(f"{what}: the question changed since the run")
    return cases


# Writing a plan --------------------------------------------------------------------


def _toml(value: object) -> str:
    return json.dumps(value)


def render_plan(
    *,
    name: str,
    description: str,
    corpus: Corpus,
    split: Split,
    models: Sequence[str],
    conditions: Sequence[Condition],
    epochs: int,
    seed: int,
    judge_models: Sequence[str],
    baseline: Condition,
    candidate: Condition,
    server_commit: str | None,
) -> str:
    lines = [
        "# Analysis plan: commit it before the run it governs; edit nothing after.",
        f"name = {_toml(name)}",
        f"description = {_toml(description)}",
        f"corpus = {_toml(corpus.name)}",
        f"corpus_version = {_toml(corpus.version)}",
        f"manifest_sha256 = {_toml(corpus.manifest_sha256)}",
        f"split = {_toml(split)}",
        f"models = {_toml(list(models))}",
        f"conditions = {_toml(list(conditions))}",
        f"epochs = {epochs}",
        f"seed = {seed}",
        f"timeout = {DEFAULT_TIMEOUT}",
    ]
    if server_commit:
        lines.append(f"server_commit = {_toml(server_commit)}  # for reference")
    lines += [
        "",
        "# What each condition gives the model; the runner refuses other code.",
        "[hashes]",
        f"base = {_toml(base_hash())}",
    ]
    for condition in conditions:
        pins = condition_hashes(condition)
        lines += [
            "",
            f"[hashes.conditions.{condition}]",
            f"prompt = {_toml(pins['prompt'])}",
            f"reference = {_toml(pins['reference'])}",
        ]
    lines += [
        "",
        "[judge]",
        f"models = {_toml(list(judge_models))}",
        f"prompt_version = {_toml(JUDGE_PROMPT_VERSION)}",
        f"prompt_hash = {_toml(judge_hash())}",
        f"scoring_version = {_toml(SCORING_VERSION)}",
        "",
        "# Decision rule: every check must pass. A check compares the 95% CI's",
        "# lower bound with ci_low_above, pooled over models or for each model.",
        "# Metrics: " + ", ".join(METRICS) + ".",
        "[analysis]",
        f"baseline = {_toml(baseline)}",
        f"candidate = {_toml(candidate)}",
        "",
        "[[analysis.checks]]",
        f'name = "answers at least as good as {baseline} (2-point margin)"',
        'metric = "quality_delta"',
        'scope = "pooled"',
        "ci_low_above = -0.02",
        "",
        "[[analysis.checks]]",
        f'name = "fewer tokens than {baseline}"',
        'metric = "token_reduction"',
        'scope = "pooled"',
        "ci_low_above = 0.0",
    ]
    return "\n".join(lines) + "\n"


def _head_commit() -> str | None:
    found = _git(["rev-parse", "HEAD"], Path.cwd())
    return found.stdout.strip() if found and found.returncode == 0 else None


def describe(loaded: LoadedPlan) -> list[str]:
    plan = loaded.plan
    lines = [f"plan {loaded.id}: {plan.description}"]
    try:
        corpus = open_corpus(plan.corpus)
        cases = corpus.manifest.files[f"{plan.split}.jsonl"].cases
        runs = cases * len(plan.models) * len(plan.conditions) * plan.epochs
        lines.append(
            f"{plan.corpus} {plan.split}: {cases} cases x {len(plan.models)} "
            f"model(s) x {len(plan.conditions)} conditions x {plan.epochs} "
            f"epoch(s) = {runs} runs"
        )
    except CorpusError:
        pass
    lines.append(
        f"judges {', '.join(plan.judge.models)}; {plan.analysis.candidate} vs "
        f"{plan.analysis.baseline}: "
        + "; ".join(
            f"{c.name} ({c.metric} {c.scope} CI low > {c.ci_low_above})"
            for c in plan.analysis.checks
        )
    )
    lines.append(f"results: {loaded.results_path}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write and check analysis plans.")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="write a plan pinned to the current code")
    init.add_argument("name")
    init.add_argument("--corpus", required=True)
    init.add_argument("--split", choices=SPLITS, default="test")
    init.add_argument("--models", nargs="+", required=True)
    init.add_argument(
        "--conditions", nargs="+", choices=CONDITIONS, default=["mcp", "grep"]
    )
    init.add_argument("--epochs", type=int, default=2)
    init.add_argument("--seed", type=int, default=20261101)
    init.add_argument(
        "--judge-models",
        "--judge-model",
        nargs="+",
        required=True,
        help="one judge, or a panel (scores are averaged)",
    )
    init.add_argument("--baseline", choices=CONDITIONS, default="grep")
    init.add_argument("--candidate", choices=CONDITIONS, default="mcp")
    init.add_argument("--description", default="TODO: what this run decides")
    init.add_argument("--output", type=Path, help="default: benchmarks/plans/NAME.toml")
    check = commands.add_parser("check", help="verify a plan against the current code")
    check.add_argument("plan", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            target = args.output or bench_dir() / "plans" / f"{args.name}.toml"
            if target.exists():
                raise PlanError(f"{target} exists; plans are never rewritten")
            text = render_plan(
                name=args.name,
                description=args.description,
                corpus=open_corpus(args.corpus),
                split=args.split,
                models=args.models,
                conditions=args.conditions,
                epochs=args.epochs,
                seed=args.seed,
                judge_models=args.judge_models,
                baseline=args.baseline,
                candidate=args.candidate,
                server_commit=_head_commit(),
            )
            Plan.model_validate(tomllib.loads(text))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, "utf-8")
            print(f"wrote {target}; review the description and checks, then commit it")
            return 0
        loaded = load_plan(args.plan)
        print("\n".join(describe(loaded)))
        problems = plan_problems(loaded)
    except (PlanError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"problem: {problem}", file=sys.stderr)
    if not problems:
        print("plan matches the current corpus, code and judge")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
