"""Inspect AI tasks for the four-condition agent benchmark.

Registered through the ``inspect_ai`` entry point, so after installing the
``benchmark`` extra::

    inspect eval mqtt_spec_mcp/mqtt5_mcp --model openai/gpt-5
    inspect eval mqtt_spec_mcp/mqtt5_reference -T condition=full_spec -T seed=7

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
from .conditions import Condition, ToolSession, ToolSpec, system_prompt, tools_for
from .runs import run_hashes
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
        session = ToolSession(tools_for(condition))
        agent = react(
            prompt=AgentPrompt(
                instructions=system_prompt(condition, case.word_limit),
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


def _dataset(seed: int, limit: int | None) -> MemoryDataset:
    samples = [
        Sample(
            id=case.id,
            input=case.prompt,
            target=[f.id for f in case.facts],
            metadata={"category": case.category},
        )
        for case in load_cases()
    ]
    dataset = MemoryDataset(samples, name="mqtt-5.0-reference")
    dataset.shuffle(seed)
    if limit:
        dataset = MemoryDataset(list(dataset)[:limit], name=dataset.name)
    return dataset


@task
def mqtt5_reference(
    condition: str = "mcp", seed: int = DEFAULT_SEED, limit: int | None = None
) -> Task:
    if condition not in ("none", "full_spec", "grep", "mcp"):
        raise ValueError(f"unknown condition {condition!r}")
    return Task(
        dataset=_dataset(seed, limit),
        solver=condition_agent(condition),  # type: ignore[arg-type]
        scorer=mqtt_answer_scorer(),
        message_limit=MESSAGE_LIMIT,
        metadata={"condition": condition, "seed": seed, "hashes": run_hashes()},
    )


@task
def mqtt5_none(seed: int = DEFAULT_SEED, limit: int | None = None) -> Task:
    return mqtt5_reference("none", seed, limit)


@task
def mqtt5_full_spec(seed: int = DEFAULT_SEED, limit: int | None = None) -> Task:
    return mqtt5_reference("full_spec", seed, limit)


@task
def mqtt5_grep(seed: int = DEFAULT_SEED, limit: int | None = None) -> Task:
    return mqtt5_reference("grep", seed, limit)


@task
def mqtt5_mcp(seed: int = DEFAULT_SEED, limit: int | None = None) -> Task:
    return mqtt5_reference("mcp", seed, limit)
