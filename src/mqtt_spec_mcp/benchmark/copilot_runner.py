"""GitHub Copilot SDK runner for the four-condition agent benchmark.

Each (case, condition, epoch) runs in a fresh Copilot session with the built-in
tools turned off, the same base prompt, a replaced system message and the
condition's custom tools. Case order is seeded per epoch and condition order
rotates between cases. Every finished run is appended to the output JSONL file
immediately, so an interrupted benchmark resumes where it stopped.

Requires the ``benchmark`` extra (Python 3.11+) and a logged-in Copilot CLI user.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from copilot import CopilotClient, ToolSet
from copilot.session import PermissionHandler
from copilot.session_events import AssistantUsageData
from copilot.tools import Tool, ToolInvocation, ToolResult

from .cases import Case, load_cases
from .conditions import CONDITIONS, Condition, ToolSession, system_prompt, tools_for
from .runs import (
    RunRecord,
    ToolCallRecord,
    Usage,
    append_record,
    build_plan,
    completed_keys,
    run_hashes,
)
from .scorer import score_answer

RUNNER = "copilot-sdk"
DEFAULT_TIMEOUT = 240.0


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
    case: Case,
    condition: Condition,
    model: str,
    epoch: int,
    seed: int,
    hashes: dict[str, str],
    timeout: float,
) -> RunRecord:
    tool_session = ToolSession(tools_for(condition))
    usage = Usage()

    def on_event(event: Any) -> None:
        data = event.data
        if isinstance(data, AssistantUsageData):
            usage.input_tokens += data.input_tokens or 0
            usage.output_tokens += data.output_tokens or 0
            usage.cache_read_tokens += data.cache_read_tokens or 0
            usage.cache_write_tokens += data.cache_write_tokens or 0
            usage.cost += data.cost or 0.0
            usage.api_calls += 1

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
                "content": system_prompt(condition, case.word_limit),
            },
            infinite_sessions={"enabled": False},
        )
        async with session:
            session.on(on_event)
            reply = await session.send_and_wait(case.prompt, timeout=timeout)
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
    )
    if error is None:
        record.score = score_answer(case, answer).to_dict()
    return record


async def run_benchmark(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases)
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]
    if args.limit:
        cases = cases[: args.limit]
    by_id = {c.id: c for c in cases}
    conditions: list[Condition] = args.conditions
    plan = build_plan(cases, conditions, args.epochs, args.seed)
    hashes = run_hashes(args.cases)
    done = completed_keys(args.output)
    todo = [
        p for p in plan if (args.model, p.condition, p.case_id, p.epoch) not in done
    ]
    print(f"{len(plan)} planned runs, {len(plan) - len(todo)} already done", flush=True)
    with tempfile.TemporaryDirectory(prefix="mqtt-bench-copilot-") as base:
        async with CopilotClient(mode="empty", base_directory=base) as client:
            for index, item in enumerate(todo, start=1):
                record = await run_one(
                    client,
                    case=by_id[item.case_id],
                    condition=item.condition,  # type: ignore[arg-type]
                    model=args.model,
                    epoch=item.epoch,
                    seed=args.seed,
                    hashes=hashes,
                    timeout=args.timeout,
                )
                append_record(args.output, record)
                quality = record.score["quality"] if record.score else "error"
                print(
                    f"[{index}/{len(todo)}] {item.condition:9s} {item.case_id} "
                    f"epoch={item.epoch} quality={quality} "
                    f"tools={len(record.tool_calls)} "
                    f"tokens={record.usage.total_tokens}",
                    flush=True,
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copilot SDK agent benchmark runner.")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS)
    )
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--limit", type=int, help="only the first N cases")
    parser.add_argument(
        "--case", action="append", help="run only this case id (repeatable)"
    )
    parser.add_argument("--cases", type=Path, help="case JSONL (default: bundled)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--output", type=Path, required=True, help="results JSONL (appended)"
    )
    return asyncio.run(run_benchmark(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
