"""Inspect AI tasks for the four-condition agent benchmark.

Registered through the ``inspect_ai`` entry point, so after installing the
``benchmark`` extra::

    inspect eval mqtt_spec_mcp/mqtt_mcp --model openai/gpt-5
    inspect eval mqtt_spec_mcp/mqtt_reference -T condition=full_spec -T versions=3.1.1

Each sample runs a fresh ReAct-style tool loop (no submit tool; the loop ends when
the model answers without calling a tool) with the same base prompt and the shared
tool-call budget. Convert logs for the common report with
``mqtt-spec-bench-report --inspect-log <log>``.
"""

from __future__ import annotations

from typing import Any

from inspect_ai import Task, task
from inspect_ai.agent import AgentPrompt, as_solver, react
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.scorer import Score, Target, mean, scorer, stderr
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolDef, ToolError, ToolParam, ToolParams

from .cases import Case, load_cases
from .conditions import (
    Condition,
    ToolSession,
    ToolSpec,
    system_prompt,
    tools_for,
    user_message,
)
from .runs import case_hash, condition_hashes
from .scorer import score_answer

DEFAULT_SEED = 20261016
MESSAGE_LIMIT = 40
_SCHEMA_KEYS = set(ToolParam.model_fields)


def _cases() -> dict[str, Case]:
    return {c.id: c for c in load_cases()}


def _params(schema: dict[str, Any]) -> ToolParams:
    properties = {
        name: ToolParam(**{k: v for k, v in prop.items() if k in _SCHEMA_KEYS})
        for name, prop in schema.get("properties", {}).items()
    }
    return ToolParams(properties=properties, required=list(schema.get("required", [])))


def _tool_def(spec: ToolSpec, session: ToolSession) -> ToolDef:
    async def execute(**kwargs: Any) -> str:
        text, is_error = session.invoke(spec.name, kwargs)
        if is_error:
            raise ToolError(text)
        return text

    return ToolDef(
        execute,
        name=spec.name,
        description=spec.description,
        parameters=_params(spec.parameters),
    )


@solver
def condition_agent(condition: Condition) -> Solver:
    cases = _cases()

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        case = cases[str(state.sample_id)]
        session = ToolSession(tools_for(condition, case.versions))
        agent = react(
            prompt=AgentPrompt(
                instructions=system_prompt(condition, case.word_limit, case.versions),
                assistant_prompt=None,
                handoff_prompt=None,
                submit_prompt=None,
            ),
            tools=[_tool_def(spec, session) for spec in session.tools],
            submit=False,
        )
        state = await as_solver(agent)(state, generate)
        state.metadata["condition"] = condition
        state.metadata["tool_calls"] = [
            {
                "name": c.name,
                "arguments": c.arguments,
                "chars": c.chars,
                "latency_ms": round(c.latency_ms, 3),
                "is_error": c.is_error,
            }
            for c in session.calls
        ]
        return state

    return solve


@scorer(metrics={"quality": [mean(), stderr()], "passed": [mean()]})
def mqtt_answer_scorer() -> Any:
    cases = _cases()

    async def score(state: TaskState, target: Target) -> Score:
        case = cases[str(state.sample_id)]
        result = score_answer(case, state.output.completion)
        return Score(
            value={"quality": result.quality, "passed": float(result.passed)},
            answer=state.output.completion,
            metadata=result.to_dict(),
        )

    return score


def _cases_for(versions: str | float) -> list[Case]:
    # Inspect parses "-T versions=5.0" as a number, so accept that form too.
    scopes = [v.strip() for v in str(versions).split(",") if v.strip()]
    return load_cases(versions=None if scopes == ["all"] else scopes)


def _dataset(cases: list[Case], seed: int, limit: int | None) -> MemoryDataset:
    samples = [
        Sample(
            id=case.id,
            input=user_message(case.prompt, case.versions),
            target=[f.id for f in case.facts],
            metadata={
                "category": case.category,
                "version": case.version,
                "case_hash": case_hash(case),
            },
        )
        for case in cases
    ]
    dataset = MemoryDataset(samples, name="mqtt-reference")
    dataset.shuffle(seed)
    if limit:
        dataset = MemoryDataset(list(dataset)[:limit], name=dataset.name)
    return dataset


@task
def mqtt_reference(
    condition: str = "mcp",
    versions: str = "all",
    seed: int = DEFAULT_SEED,
    limit: int | None = None,
) -> Task:
    """versions: "all", or a comma-separated subset of "3.1.1", "5.0", "both"."""
    if condition not in ("none", "full_spec", "grep", "mcp"):
        raise ValueError(f"unknown condition {condition!r}")
    cases = _cases_for(versions)
    return Task(
        dataset=_dataset(cases, seed, limit),
        solver=condition_agent(condition),  # type: ignore[arg-type]
        scorer=mqtt_answer_scorer(),
        message_limit=MESSAGE_LIMIT,
        metadata={
            "condition": condition,
            "seed": seed,
            "hashes": condition_hashes(condition),
        },
    )


@task
def mqtt_none(
    versions: str = "all", seed: int = DEFAULT_SEED, limit: int | None = None
) -> Task:
    return mqtt_reference("none", versions, seed, limit)


@task
def mqtt_full_spec(
    versions: str = "all", seed: int = DEFAULT_SEED, limit: int | None = None
) -> Task:
    return mqtt_reference("full_spec", versions, seed, limit)


@task
def mqtt_grep(
    versions: str = "all", seed: int = DEFAULT_SEED, limit: int | None = None
) -> Task:
    return mqtt_reference("grep", versions, seed, limit)


@task
def mqtt_mcp(
    versions: str = "all", seed: int = DEFAULT_SEED, limit: int | None = None
) -> Task:
    return mqtt_reference("mcp", versions, seed, limit)
