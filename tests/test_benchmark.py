"""Benchmark harness unit tests (no model, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mqtt_spec_mcp.benchmark import conditions, report
from mqtt_spec_mcp.benchmark.cases import Case, parse_cases
from mqtt_spec_mcp.benchmark.runs import (
    RunRecord,
    Usage,
    append_record,
    build_plan,
    completed_keys,
    load_records,
)
from mqtt_spec_mcp.benchmark.scorer import (
    SCORER_VERSION,
    cited_sections,
    matches_fact,
    score_answer,
)


def make_case(**overrides: Any) -> Case:
    data: dict[str, Any] = {
        "id": "demo-retained-flag",
        "category": "normative",
        "prompt": "Does the broker keep a retained copy when RETAIN is one?",
        "facts": [
            {"id": "stores", "any": ["stor(e|es|ed)"], "section": "3.3.1.3"},
            {
                "id": "not-session",
                "any": ["part of the session state"],
                "polarity": "negate",
                "section": "4.1",
            },
        ],
        "citations": [["3.3.1.3"], ["4.1", "4"]],
        "forbidden": [
            {"id": "qos3", "any": ["qos ?3"], "note": "no QoS 3", "section": "4.3"}
        ],
        "search_query": "retain flag storage",
        "relevant_sections": ["3.3.1.3"],
        "word_limit": 40,
        "provenance": "From 3.3.1.3 and 4.1.",
        "reference_answer": "Stored (3.3.1.3); not part of the session state (4.1).",
    }
    data.update(overrides)
    return parse_cases([json.dumps(data)])[0]


def test_negation_blocks_affirmed_fact() -> None:
    assert matches_fact("The server stores it.", ["stores"], "affirm")
    assert not matches_fact("The server does not store it.", ["store"], "affirm")
    assert matches_fact("It is not stored. Later it stores it.", ["stores"], "affirm")


def test_negated_fact_requires_negation() -> None:
    patterns = ["part of the session state"]
    assert matches_fact(
        "Retained messages are not part of the Session State.", patterns, "negate"
    )
    assert not matches_fact(
        "Retained messages are part of the Session State.", patterns, "negate"
    )


def test_citation_detection_ignores_version_numbers() -> None:
    cited = cited_sections(
        "In MQTT 5.0 (unlike MQTT 3.1.1) see section 3.3.1.3 and §4."
    )
    assert "3.3.1.3" in cited
    assert "4" in cited
    assert "5.0" not in cited
    assert "3.1.1" not in cited


def test_score_formula_and_pass() -> None:
    case = make_case()
    perfect = score_answer(
        case,
        "The Server stores it (3.3.1.3); it is not part of the session state (4.1).",
    )
    assert perfect.passed
    assert perfect.quality == 1.0
    assert perfect.scorer_version == SCORER_VERSION
    partial = score_answer(case, "The Server stores it (3.3.1.3) using QoS 3.")
    assert not partial.passed
    assert partial.fact_recall == 0.5
    assert partial.citation_recall == 0.5
    assert partial.violation_rate == 1.0
    assert partial.quality == 0.0  # 0.8*0.5 + 0.2*0.5 - 1 clamps at zero
    descendant = score_answer(
        case, "Stored (3.3.1.3); not part of the session state (4.1.2)."
    )
    assert descendant.citation_recall == 1.0


def test_concision() -> None:
    case = make_case()
    long_answer = "stores " * 80 + "(3.3.1.3) (4.1) not part of the session state"
    result = score_answer(case, long_answer)
    assert result.words > case.word_limit
    assert result.concision < 1.0


def test_case_schema_is_strict() -> None:
    with pytest.raises(ValueError, match="invalid benchmark cases"):
        make_case(unexpected=True)
    with pytest.raises(ValueError, match="invalid benchmark cases"):
        make_case(category="trivia")


def test_plan_is_seeded_and_rotates_conditions() -> None:
    cases = [make_case(id=f"case-n{i}") for i in range(4)]
    plan = build_plan(cases, ["none", "full_spec", "grep", "mcp"], epochs=2, seed=3)
    assert plan == build_plan(
        cases, ["none", "full_spec", "grep", "mcp"], epochs=2, seed=3
    )
    assert len(plan) == 32
    firsts = [plan[i].condition for i in range(0, 16, 4)]
    assert len(set(firsts)) == 4
    other = build_plan(cases, ["none", "full_spec", "grep", "mcp"], epochs=2, seed=4)
    assert [p.case_id for p in other] != [p.case_id for p in plan]


def test_grep_and_read_tools() -> None:
    text, error = conditions._grep({"pattern": "Will Delay Interval", "max_results": 3})
    assert not error
    assert text.count("\n") >= 2
    first_line = int(text.split(":")[0])
    page, error = conditions._read({"start_line": first_line, "max_lines": 80})
    assert not error
    assert page.splitlines()[-1].startswith("next_line:")
    assert len(page.splitlines()) == 81
    assert conditions._read({"start_line": 1, "max_lines": 81})[1]
    assert conditions._grep({"pattern": "("})[0]  # invalid regex falls back to literal


def test_tool_budget_is_enforced() -> None:
    session = conditions.ToolSession(conditions.tools_for("mcp"), max_calls=1)
    args = {"version": "5.0", "kind": "packet", "query": "PUBACK"}
    assert not session.invoke("mqtt_spec_lookup", args)[1]
    text, error = session.invoke("mqtt_spec_lookup", args)
    assert error
    assert "budget" in text


def test_mcp_condition_is_pinned_to_50() -> None:
    session = conditions.ToolSession(conditions.tools_for("mcp"))
    text, error = session.invoke("mqtt_spec_outline", {"version": "3.1.1"})
    assert error
    assert "not loaded" in text


def test_system_prompts_share_base() -> None:
    prompts = {c: conditions.system_prompt(c, 120) for c in conditions.CONDITIONS}
    base = conditions.BASE_PROMPT.format(word_limit=120)
    assert all(p.startswith(base) for p in prompts.values())
    assert (
        "## 3.1.2.4 Clean Start" in prompts["full_spec"]
        or "3.1.2.4 Clean Start" in (prompts["full_spec"])
    )
    assert len(prompts["full_spec"]) > 100_000
    assert len(prompts["mcp"]) < 5_000


def _record(
    condition: str, case_id: str, quality: float, tokens: int, latency: float
) -> RunRecord:
    return RunRecord(
        runner="test",
        model="m",
        condition=condition,
        case_id=case_id,
        epoch=0,
        seed=1,
        answer="a",
        latency_s=latency,
        usage=Usage(input_tokens=tokens),
        hashes={"prompt": "p", "benchmark": "b", "server": "s"},
        score={"quality": quality, "passed": quality == 1.0, "concision": 1.0},
    )


def test_report_pairs_bootstrap_and_gates(tmp_path: Path) -> None:
    records = []
    for i in range(20):
        records.append(_record("full_spec", f"c{i}", 1.0, 60_000, 10.0))
        records.append(_record("mcp", f"c{i}", 1.0 if i % 10 else 0.9, 6_000, 5.0))
    comparisons = report.compare(records, "full_spec", "mcp")
    row = comparisons[0]
    assert row["pairs"] == 20
    assert row["token_reduction"]["point"] == pytest.approx(0.9)
    assert row["speedup"]["point"] == pytest.approx(2.0)
    low, high = row["quality_delta"]["ci95"]
    assert low <= row["quality_delta"]["point"] <= high
    assert report.compare(records, "full_spec", "mcp") == comparisons  # deterministic
    path = tmp_path / "runs.jsonl"
    for record in records:
        append_record(path, record)
    assert len(load_records([path])) == 40
    assert len(completed_keys(path)) == 40
    assert (
        report.main(
            [str(path), "--gate-token-reduction", "0.5", "--gate-speedup", "1.2"]
        )
        == 0
    )
    assert report.main([str(path), "--gate-speedup", "3"]) == 1


def test_report_refuses_mixed_hashes(tmp_path: Path) -> None:
    a = _record("mcp", "c1", 1.0, 10, 1.0)
    b = _record("full_spec", "c1", 1.0, 10, 1.0)
    b.hashes = {**b.hashes, "server": "different"}
    path = tmp_path / "mixed.jsonl"
    append_record(path, a)
    append_record(path, b)
    assert report.main([str(path)]) == 1
    with pytest.raises(report.ReportError):
        report.check_compatible([a, b])


def test_forbidden_claim_negated_inside_match_is_not_a_violation() -> None:
    case = make_case(
        forbidden=[
            {
                "id": "timer-retry",
                "any": [r"should\s+(?:\w+\s+)?retransmit"],
                "note": "retransmission only on reconnect",
                "section": "4.4",
            }
        ]
    )
    ok = score_answer(case, "Clients should not retransmit on a timer (4.4).")
    assert ok.violations == []
    bad = score_answer(case, "Clients should retransmit on a timer (4.4).")
    assert bad.violations == ["timer-retry"]


def test_bundled_corpus_is_valid() -> None:
    from mqtt_spec_mcp.benchmark.cases import load_cases, validate_cases

    cases = load_cases()
    assert len(cases) == 120
    assert validate_cases(cases) == []


def test_retrieval_benchmark_runs() -> None:
    from mqtt_spec_mcp.benchmark.cases import load_cases
    from mqtt_spec_mcp.benchmark.retrieval import evaluate

    result = evaluate(load_cases())
    assert result["lookup_success"] == 1.0
    assert result["hit_rate"] >= 0.9
