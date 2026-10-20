"""GitHub Copilot SDK backend and command line for the AI judge (``judge.py``).

    mqtt-spec-bench-judge RESULTS.jsonl --judge-model MODEL
    mqtt-spec-bench-judge --plan benchmarks/plans/NAME.toml

Judges every successful corpus-format-2 answer in the results that has no cached
judgment from the same judge and prompt, in fresh sessions with no tools, and
appends each judgment to ``RESULTS.judgments.jsonl``; re-running retries failures.
The sealed test split needs its plan, which fixes the judge model; progress then
names no cases.

Requires the ``benchmark`` extra (Python 3.11+) and a logged-in Copilot CLI user.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from copilot import CopilotClient
from copilot.session import PermissionHandler

from .copilot_runner import record_usage
from .corpus import CorpusError
from .judge import JudgeSession, OpenSession, judge_all
from .plan import PlanError, corpus_cases, judgments_path, verified_plan
from .runs import Usage, latest_records, load_records

DEFAULT_TIMEOUT = 180.0


class _Session:
    def __init__(self, session: Any, timeout: float) -> None:
        self._session = session
        self._timeout = timeout
        self.usage = Usage()

    def on_event(self, event: Any) -> None:
        record_usage(self.usage, event)

    async def send(self, message: str) -> str:
        reply = await self._session.send_and_wait(message, timeout=self._timeout)
        content = getattr(getattr(reply, "data", None), "content", None)
        if not isinstance(content, str) or not content:
            raise RuntimeError("no assistant message")
        return content


def copilot_opener(client: CopilotClient, model: str, timeout: float) -> OpenSession:
    @asynccontextmanager
    async def open_session(system: str) -> AsyncIterator[JudgeSession]:
        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=model,
            tools=[],
            available_tools=[],
            system_message={"mode": "replace", "content": system},
            infinite_sessions={"enabled": False},
        )
        async with session:
            wrapper = _Session(session, timeout)
            session.on(wrapper.on_event)
            yield wrapper

    return open_session


async def run(args: argparse.Namespace) -> int:
    plan = verified_plan(args.plan) if args.plan else None
    if plan is not None:
        if args.judge_model:
            raise PlanError("the plan fixes the judge model; drop --judge-model")
        results = args.results or [plan.results_path]
        judge_model = plan.plan.judge.model
    else:
        if not args.results or not args.judge_model:
            raise PlanError("give results files and --judge-model, or --plan")
        results, judge_model = args.results, args.judge_model
    records = [r for r in latest_records(load_records(results)) if r.error is None]
    if not records:
        raise PlanError("no successful runs to judge")
    cases = corpus_cases(records, plan)
    output = args.judgments or judgments_path(results[0])
    with tempfile.TemporaryDirectory(prefix="mqtt-bench-judge-") as base:
        async with CopilotClient(mode="empty", base_directory=base) as client:
            done = await judge_all(
                [(cases[r.case_id], r.answer) for r in records],
                judge_model=judge_model,
                open_session=copilot_opener(client, judge_model, args.timeout),
                output=output,
                concurrency=args.concurrency,
                sealed=records[0].split == "test",
                log=lambda line: print(line, flush=True),
            )
    failed = sum(1 for j in done if j.error)
    unverified = sum(1 for j in done if j.unverified)
    print(
        f"judged {len(done)} into {output}: {failed} failed (re-run to retry), "
        f"{unverified} with evidence not found in the answer"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Judge corpus-format-2 answers.")
    parser.add_argument("results", nargs="*", type=Path, help="results JSONL files")
    parser.add_argument("--plan", type=Path, help="analysis plan (required for test)")
    parser.add_argument("--judge-model", help="the judge (a plan fixes it)")
    parser.add_argument(
        "--judgments", type=Path, help="default: RESULTS.judgments.jsonl"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (PlanError, CorpusError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
