"""GitHub Copilot SDK runner for the four-condition agent benchmark.

Each (model, case, condition, epoch) runs in a fresh Copilot session with the
built-in tools turned off, the same base prompt, a replaced system message and the
condition's custom tools. Case order is seeded per epoch, condition order rotates
between cases, and every model answers each case before the next one starts;
``--concurrency`` sessions run at once. Every finished run is appended to the
output JSONL file immediately, so an interrupted benchmark resumes where it stopped
(failed runs are retried on the next invocation).

Cases come from the bundled corpus (scored as they finish), a corpus-format-2
``--corpus`` (``dev`` split; judged afterwards with ``mqtt-spec-bench-judge``), or a
``--plan``, which fixes everything about the run and is the only way to run the
sealed ``test`` split; test progress names no cases.

Requires the ``benchmark`` extra (Python 3.11+) and a logged-in Copilot CLI user.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot import CopilotClient, ToolSet
from copilot.session import PermissionHandler
from copilot.session_events import AssistantUsageData
from copilot.tools import Tool, ToolInvocation, ToolResult

from .cases import Case, load_cases
from .conditions import (
    CONDITIONS,
    Condition,
    ToolSession,
    system_prompt,
    tools_for,
    user_message,
)
from .corpus import SPLITS, CorpusError, open_corpus
from .plan import DEFAULT_TIMEOUT, PlanError, verified_plan
from .runs import (
    PlanItem,
    Prices,
    Question,
    RunRecord,
    ToolCallRecord,
    Usage,
    append_record,
    build_plan,
    case_hash,
    completed_keys,
    condition_hashes,
    output_labels,
    stratified_sample,
)
from .scorer import score_answer

RUNNER = "copilot-sdk"
DEFAULT_SEED = 20261016


@dataclass(frozen=True)
class Labels:
    """Where format-2 cases come from; recorded on every run."""

    corpus: str | None = None
    corpus_version: str | None = None
    split: str | None = None
    plan: str | None = None


def record_usage(usage: Usage, event: Any) -> None:
    """Add one model call's token counts to ``usage``."""
    data = event.data
    if isinstance(data, AssistantUsageData):
        usage.input_tokens += data.input_tokens or 0
        usage.output_tokens += data.output_tokens or 0
        usage.cache_read_tokens += data.cache_read_tokens or 0
        usage.cache_write_tokens += data.cache_write_tokens or 0
        usage.cost += data.cost or 0.0
        usage.api_calls += 1


def _copilot_tools(session: ToolSession) -> list[Tool]:
    def make(name: str) -> Any:
        def handler(invocation: ToolInvocation) -> ToolResult:
            arguments = (
                invocation.arguments if isinstance(invocation.arguments, dict) else {}
            )
            text, is_error = session.invoke(name, arguments)
            return ToolResult(
                text_result_for_llm=text,
                result_type="failure" if is_error else "success",
                error=text if is_error else None,
            )

        return handler

    return [
        Tool(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
            handler=make(spec.name),
            skip_permission=True,
            defer="never",
        )
        for spec in session.tools
    ]


async def run_one(
    client: CopilotClient,
    *,
    case: Question,
    condition: Condition,
    model: str,
    epoch: int,
    seed: int,
    hashes: dict[str, str],
    timeout: float,
    labels: Labels,
    prices: Prices | None = None,
) -> RunRecord:
    tool_session = ToolSession(tools_for(condition, case.versions))
    usage = Usage()

    def on_event(event: Any) -> None:
        record_usage(usage, event)

    tools = _copilot_tools(tool_session)
    available: ToolSet | list[str] = ToolSet().add_custom("*") if tools else []
    answer = ""
    error: str | None = None
    start = time.perf_counter()
    try:
        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=model,
            tools=tools,
            available_tools=available,
            system_message={
                "mode": "replace",
                "content": system_prompt(condition, case.word_limit, case.versions),
            },
            infinite_sessions={"enabled": False},
        )
        async with session:
            session.on(on_event)
            reply = await session.send_and_wait(
                user_message(case.prompt, case.versions), timeout=timeout
            )
            content = getattr(getattr(reply, "data", None), "content", None)
            answer = content if isinstance(content, str) else ""
            if not answer:
                error = "no assistant message"
    except Exception as exc:  # record failures and keep going
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - start
    record = RunRecord(
        runner=RUNNER,
        model=model,
        condition=condition,
        case_id=case.id,
        version=case.version,
        epoch=epoch,
        seed=seed,
        answer=answer,
        error=error,
        latency_s=round(latency, 3),
        usage=usage,
        tool_calls=[
            ToolCallRecord(
                name=c.name,
                arguments=c.arguments,
                chars=c.chars,
                latency_ms=round(c.latency_ms, 3),
                is_error=c.is_error,
            )
            for c in tool_session.calls
        ],
        hashes=hashes,
        corpus=labels.corpus,
        corpus_version=labels.corpus_version,
        split=labels.split,
        plan=labels.plan,
        prices=prices,
    )
    if error is None and isinstance(case, Case):
        record.score = score_answer(case, answer).to_dict()
    return record


async def model_prices(client: CopilotClient) -> dict[str, Prices]:
    """Each model's current token prices, recorded on its runs."""
    found: dict[str, Prices] = {}
    try:
        models = await client.list_models()
    except Exception:  # prices are optional; runs proceed without them
        return found
    for info in models:
        listed = getattr(info.billing, "token_prices", None) if info.billing else None
        if listed is not None and listed.input_price:
            found[info.id] = Prices(
                input=listed.input_price,
                cache_read=listed.cache_read_price or 0.0,
                cache_write=listed.cache_write_price or 0.0,
                output=listed.output_price or 0.0,
            )
    return found


@dataclass(frozen=True)
class Setup:
    cases: Sequence[Question]
    models: Sequence[str]
    conditions: Sequence[Condition]
    epochs: int
    seed: int
    timeout: float
    output: Path
    labels: Labels


_PLAN_FIXES = (
    "models",
    "conditions",
    "epochs",
    "seed",
    "timeout",
    "corpus",
    "split",
    "cases",
    "case",
    "sample",
    "versions",
)


def resolve(args: argparse.Namespace) -> Setup:
    """The cases and settings to run, from a plan or from the flags."""
    if args.plan is not None:
        fixed = [f"--{name}" for name in _PLAN_FIXES if getattr(args, name)]
        if fixed:
            raise PlanError(f"the plan fixes the run; drop {', '.join(fixed)}")
        loaded = verified_plan(args.plan)
        plan = loaded.plan
        corpus = open_corpus(plan.corpus)
        return Setup(
            cases=corpus.cases(plan.split),
            models=plan.models,
            conditions=plan.conditions,
            epochs=plan.epochs,
            seed=plan.seed,
            timeout=plan.timeout,
            output=args.output or loaded.results_path,
            labels=Labels(corpus.name, corpus.version, plan.split, loaded.id),
        )
    if not args.models or args.output is None:
        raise PlanError("--models and --output are required without --plan")
    cases: Sequence[Question]
    if args.corpus:
        if args.cases:
            raise PlanError("use --corpus or --cases, not both")
        if args.split == "test":
            raise PlanError("the test split is sealed: run it under a plan (--plan)")
        corpus = open_corpus(args.corpus)
        cases = corpus.cases("dev")
        labels = Labels(corpus.name, corpus.version, "dev")
    else:
        if args.split:
            raise PlanError("--split needs --corpus")
        cases = load_cases(args.cases)
        labels = Labels()
    seed = DEFAULT_SEED if args.seed is None else args.seed
    if args.versions:
        cases = [c for c in cases if c.version in set(args.versions)]
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]
    if args.sample:
        cases = stratified_sample(cases, args.sample, seed)
    return Setup(
        cases=cases,
        models=args.models,
        conditions=args.conditions or list(CONDITIONS),
        epochs=args.epochs or 1,
        seed=seed,
        timeout=args.timeout or DEFAULT_TIMEOUT,
        output=args.output,
        labels=labels,
    )


async def run_benchmark(args: argparse.Namespace) -> int:
    try:
        setup = resolve(args)
        labels = setup.labels
        mine = (labels.corpus, labels.corpus_version, labels.split, labels.plan)
        if output_labels(setup.output) - {mine}:
            raise PlanError(
                f"{setup.output} holds runs of another corpus, version, split or "
                "plan; write these runs to a new --output"
            )
    except (PlanError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sealed = setup.labels.split == "test"
    by_id = {c.id: c for c in setup.cases}
    number = {c.id: i for i, c in enumerate(setup.cases, 1)}
    plan = build_plan(setup.cases, setup.conditions, setup.epochs, setup.seed)
    by_condition = {c: condition_hashes(c) for c in setup.conditions}
    expected: dict[tuple[str, str], dict[str, str]] = {
        (c, case.id): {**by_condition[c], "case": case_hash(case)}
        for c in setup.conditions
        for case in setup.cases
    }
    done = completed_keys(setup.output, expected)
    todo = [
        (model, item)
        for item in plan
        for model in setup.models
        if (model, item.condition, item.case_id, item.epoch) not in done
    ]
    planned = len(plan) * len(setup.models)
    print(f"{planned} planned runs, {planned - len(todo)} already done", flush=True)
    gate = asyncio.Semaphore(args.concurrency)
    finished = 0

    async def worker(model: str, item: PlanItem) -> None:
        nonlocal finished
        async with gate:
            record = await run_one(
                client,
                case=by_id[item.case_id],
                condition=item.condition,
                model=model,
                epoch=item.epoch,
                seed=setup.seed,
                hashes=expected[(item.condition, item.case_id)],
                timeout=setup.timeout,
                labels=setup.labels,
                prices=prices.get(model),
            )
        append_record(setup.output, record)
        finished += 1
        if record.error is not None:
            status = f"error: {record.error}"
        elif record.score is not None:
            status = f"quality={record.score['quality']}"
        else:
            status = "answered"
        name = f"case #{number[item.case_id]}" if sealed else item.case_id
        print(
            f"[{finished}/{len(todo)}] {model} {item.condition:9s} {name} "
            f"epoch={item.epoch} {status} tools={len(record.tool_calls)} "
            f"tokens={record.usage.total_tokens} {record.latency_s:.0f}s",
            flush=True,
        )

    with tempfile.TemporaryDirectory(prefix="mqtt-bench-copilot-") as base:
        async with CopilotClient(mode="empty", base_directory=base) as client:
            prices = await model_prices(client)
            await asyncio.gather(*(worker(model, item) for model, item in todo))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copilot SDK agent benchmark runner.")
    parser.add_argument(
        "--plan", type=Path, help="an analysis plan: fixes everything below"
    )
    parser.add_argument(
        "--models", "--model", nargs="+", help="models to run (each answers every case)"
    )
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS)
    parser.add_argument("--epochs", type=int, help="default 1")
    parser.add_argument("--seed", type=int, help=f"default {DEFAULT_SEED}")
    parser.add_argument(
        "--corpus", help="a registered corpus-format-2 corpus (instead of --cases)"
    )
    parser.add_argument(
        "--split", choices=SPLITS, help="dev (default); test needs --plan"
    )
    parser.add_argument(
        "--sample",
        type=int,
        help="a seeded sample of N cases across versions and categories or kinds",
    )
    parser.add_argument(
        "--versions",
        nargs="+",
        choices=("3.1.1", "5.0", "both"),
        help="only cases about these versions (default: all)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=4, help="sessions run in parallel"
    )
    parser.add_argument(
        "--case", action="append", help="run only this case id (repeatable)"
    )
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument("--timeout", type=float, help=f"default {DEFAULT_TIMEOUT:.0f}")
    parser.add_argument(
        "--output",
        type=Path,
        help="results JSONL, appended (a plan defaults to benchmarks/runs/...)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_benchmark(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
