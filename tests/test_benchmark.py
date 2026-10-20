"""Benchmark harness unit tests (no model, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mqtt_spec_mcp.benchmark import conditions, report
from mqtt_spec_mcp.benchmark.cases import Case, load_cases, parse_cases
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
    plain,
    score_answer,
)


def make_case(**overrides: Any) -> Case:
    data: dict[str, Any] = {
        "id": "demo-retained-flag",
        "version": "5.0",
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


def test_plain_drops_markdown_and_joins_headings() -> None:
    assert plain("it **SHOULD NOT** disconnect, *complete* `a/+`") == (
        "it SHOULD NOT disconnect, complete a/+"
    )
    assert plain("snake_case stays, _italic_ goes") == "snake_case stays, italic goes"
    assert plain("**3. Buffer full**\nThe broker MUST close it.") == (
        "3. Buffer full: The broker MUST close it."
    )
    assert plain("## Port\n\nUse 8883.") == "Port: Use 8883."
    assert plain("- **Label:**\nText") == "Label: Text"
    assert plain("**Byte 1**: type 10\nnext") == "Byte 1: type 10\nnext"
    assert plain("doesn\u2019t \u2014 x") == "doesn't - x"


def test_markdown_emphasis_does_not_hide_facts() -> None:
    case = make_case(
        facts=[
            {"id": "close", "any": ["MUST\\s+close"], "section": "4.8"},
            {
                "id": "others",
                "any": ["SHOULD\\s+NOT\\s+disconnect"],
                "polarity": "negate",
                "section": "4.8",
            },
        ],
        citations=[["4.8"]],
        forbidden=[],
    )
    answer = (
        "**Transient error**\nIt **MUST** close; "
        "it **SHOULD NOT** disconnect others (4.8)."
    )
    assert score_answer(case, answer).passed


def test_negation_is_scoped_to_the_clause() -> None:
    def affirmed(text: str, pattern: str) -> bool:
        return matches_fact(plain(text), [pattern], "affirm")

    assert affirmed("Strings MUST NOT exceed 65535 bytes.", "65535")
    assert affirmed("It allows no more than 3 retries.", "3 retries")
    assert not affirmed("The client cannot exceed it.", "exceed")
    assert affirmed("It creates one (if none exists) and resumes it.", "resumes")
    assert affirmed("It is not discarded, and resumes it.", "resumes")
    assert not affirmed("It does not resume the session.", "resume")
    assert not affirmed("The server doesn\u2019t store it.", "store")
    assert affirmed("No, values 6-255 are reserved.", "reserved")
    assert affirmed("No - 0 and 15 are reserved.", "reserved")
    assert not affirmed("There is no reserved value.", "reserved")
    assert affirmed("If no PINGRESP arrives, the client should close it.", "close")
    assert not affirmed("If the client does not close it, ...", "close")
    assert affirmed("It has no payload, only the Packet Identifier.", "identifier")
    assert affirmed("It is not stored but discarded.", "discarded")
    assert affirmed("The server must not only store it, it forwards it.", "store")
    assert affirmed("It is not a failure \u2014 the publish was accepted.", "accepted")
    assert affirmed("In 3.1.1 it cannot - it must close the connection.", "close")
    assert not affirmed("It is not-accepted.", "accepted")
    assert matches_fact(
        "The Server MUST NOT (see 3.1.4) discard it.", ["discard"], "negate"
    )
    assert not matches_fact("No, it disconnects others.", ["disconnect"], "negate")


def test_affirmed_fact_negated_inside_its_own_match() -> None:
    published = r"will\b[^.]{0,40}?(?:is|gets)\s+(?:\w+\s+){0,2}?published"
    assert matches_fact("The Will is published.", [published], "affirm")
    assert not matches_fact("The Will is not published.", [published], "affirm")
    assert matches_fact(
        "The Will is not deleted, and is published.",
        [r"will\b[^.]{0,60}?published"],
        "affirm",
    )
    # A pattern that spells out a negation handles it itself.
    assert matches_fact("It must not use them.", [r"must\s+not\s+use"], "affirm")


def test_citation_detection_ignores_version_numbers() -> None:
    cited = cited_sections(
        "In MQTT 5.0 (unlike MQTT 3.1.1) see section 3.3.1.3 and §4."
    )
    assert "3.3.1.3" in cited
    assert "4" in cited
    assert "5.0" not in cited
    assert "3.1.1" not in cited
    assert cited_sections("In 3.1.1 the broker closes; in 5.0 it sends one.") == set()
    assert "3.1.1" in cited_sections("The fixed header (section 3.1.1) holds it.")
    assert "3.1.1" in cited_sections("See sections 3.1 and 3.1.1.")
    assert "3.1.1" in cited_sections("See §3.1.1.")


def test_pattern_negation_detection() -> None:
    def spells_negation(source: str) -> bool:
        return matches_fact("The Will is not published.", [source], "affirm")

    # Patterns without a negation word get the self-negation check ...
    assert not spells_negation(r"will\s+(?:\w+\s+){0,2}published")
    assert not spells_negation(r"will(?:[- ]?another)?\s+(?:\w+\s+){0,2}published")
    # ... patterns that spell out a negation handle it themselves.
    assert spells_negation(r"will\s+is\s+not\s+published")
    assert spells_negation(r"will\s+(?:\w+\s+){0,2}?(?:\bnot\b\s+)?published")


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


def test_load_cases_rejects_duplicate_ids(tmp_path: Path) -> None:
    line = make_case().model_dump_json()
    path = tmp_path / "cases.jsonl"
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate benchmark case ids: demo-"):
        load_cases(path)
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
    grep, read = (t.handler for t in conditions.grep_tools(["5.0"]))
    text, error = grep({"pattern": "Will Delay Interval", "max_results": 3})
    assert not error
    assert text.count("\n") >= 2
    first_line = int(text.split(":")[0])
    page, error = read({"start_line": first_line, "max_lines": 80})
    assert not error
    assert page.splitlines()[-1].startswith("next_line:")
    assert len(page.splitlines()) == 81
    assert read({"start_line": 1, "max_lines": 81})[1]
    assert grep({"pattern": "("})[0]  # invalid regex falls back to literal


def test_grep_text_holds_only_the_case_versions() -> None:
    grep_311, _ = (t.handler for t in conditions.grep_tools(["3.1.1"]))
    assert "No matching lines" in grep_311({"pattern": "Session Expiry Interval"})[0]
    grep_both, _ = (t.handler for t in conditions.grep_tools(["3.1.1", "5.0"]))
    headings, _ = grep_both({"pattern": "^# MQTT Version"})
    assert "MQTT Version 3.1.1" in headings
    assert "MQTT Version 5.0" in headings


def test_tool_budget_is_enforced() -> None:
    session = conditions.ToolSession(conditions.tools_for("mcp", ["5.0"]), max_calls=1)
    args = {"version": "5.0", "kind": "packet", "query": "PUBACK"}
    assert not session.invoke("mqtt_spec_lookup", args)[1]
    text, error = session.invoke("mqtt_spec_lookup", args)
    assert error
    assert "budget" in text


@pytest.mark.parametrize(("loaded", "other"), [("5.0", "3.1.1"), ("3.1.1", "5.0")])
def test_mcp_condition_is_pinned_to_case_versions(loaded: str, other: str) -> None:
    session = conditions.ToolSession(conditions.tools_for("mcp", [loaded]))
    text, error = session.invoke("mqtt_spec_outline", {"version": other})
    assert error
    assert "not loaded" in text
    assert not session.invoke("mqtt_spec_outline", {"version": loaded})[1]
    both = conditions.ToolSession(conditions.tools_for("mcp", ["3.1.1", "5.0"]))
    assert not both.invoke("mqtt_spec_outline", {"version": other})[1]


def test_system_prompts_share_base() -> None:
    base = conditions.BASE_PROMPT.format(word_limit=120)
    for versions in (["5.0"], ["3.1.1"], ["3.1.1", "5.0"]):
        prompts = {
            c: conditions.system_prompt(c, 120, versions) for c in conditions.CONDITIONS
        }
        assert all(p.startswith(base) for p in prompts.values())
        assert len(prompts["mcp"]) < 5_000
    only_50 = conditions.system_prompt("full_spec", 120, ["5.0"])
    assert "3.1.2.4 Clean Start" in only_50
    assert "MQTT Version 3.1.1" not in only_50.split("SPECIFICATION")[1]
    both = conditions.system_prompt("full_spec", 120, ["3.1.1", "5.0"])
    assert "3.1.2.4 Clean Session" in both
    assert "3.1.2.4 Clean Start" in both


def test_user_message_labels_every_question_with_its_versions() -> None:
    assert conditions.user_message("Q?", ["3.1.1"]) == "[MQTT 3.1.1] Q?"
    assert conditions.user_message("Older vs newer?", ["3.1.1", "5.0"]) == (
        "[MQTT 3.1.1 and 5.0] Older vs newer?"
    )


def test_case_hash_covers_the_comparison_label() -> None:
    from mqtt_spec_mcp.benchmark.runs import case_hash, digest

    single = make_case()
    assert case_hash(single) == digest(
        {
            "case": (single.id, single.version, single.prompt, single.word_limit),
            "format": 2,
        }
    )
    both = make_case(version="both")
    plain = digest(
        {"case": (both.id, both.version, both.prompt, both.word_limit), "format": 2}
    )
    assert case_hash(both) != plain


def _record(
    condition: str,
    case_id: str,
    quality: float,
    tokens: int,
    latency: float,
    *,
    version: str = "5.0",
) -> RunRecord:
    return RunRecord(
        runner="test",
        model="m",
        condition=condition,
        case_id=case_id,
        version=version,
        epoch=0,
        seed=1,
        answer="a",
        latency_s=latency,
        usage=Usage(input_tokens=tokens),
        hashes={"base": "b", "prompt": condition, "reference": "r", "case": case_id},
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
    expected = {(r.condition, r.case_id): r.hashes for r in records}
    assert len(completed_keys(path, expected)) == 40
    expected[("mcp", "c0")] = {**expected[("mcp", "c0")], "reference": "changed"}
    assert ("m", "mcp", "c0", 0) not in completed_keys(path, expected)
    assert len(completed_keys(path, expected)) == 39
    assert (
        report.main(
            [str(path), "--gate-token-reduction", "0.5", "--gate-speedup", "1.2"]
        )
        == 0
    )
    assert report.main([str(path), "--gate-speedup", "3"]) == 1


def test_report_epoch_filter(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    for epoch in (0, 1):
        for condition in ("full_spec", "mcp"):
            record = _record(condition, "c0", 1.0, 1_000, 1.0)
            append_record(path, record.model_copy(update={"epoch": epoch}))
    out = tmp_path / "report.json"
    assert report.main([str(path), "--epoch", "1", "--json-output", str(out)]) == 0
    summary = json.loads(out.read_text("utf-8"))["summary"]
    assert {row["runs"] for row in summary} == {1}
    assert report.main([str(path), "--epoch", "7"]) == 1


def test_report_refuses_mixed_hashes(tmp_path: Path) -> None:
    a = _record("mcp", "c1", 1.0, 10, 1.0)
    b = _record("mcp", "c2", 1.0, 10, 1.0)
    b.hashes = {**b.hashes, "reference": "different server"}
    path = tmp_path / "mixed.jsonl"
    append_record(path, a)
    append_record(path, b)
    assert report.main([str(path)]) == 1
    with pytest.raises(report.ReportError, match="mcp: different reference"):
        report.check_compatible([a, b])
    asked = _record("full_spec", "c1", 1.0, 10, 1.0)
    asked.hashes = {**asked.hashes, "case": "changed question"}
    with pytest.raises(report.ReportError, match="questions changed"):
        report.check_compatible([a, asked])


def test_report_allows_rerunning_one_condition() -> None:
    old = [_record("full_spec", f"c{i}", 1.0, 100, 2.0) for i in range(3)]
    new = [_record("mcp", f"c{i}", 1.0, 10, 1.0) for i in range(3)]
    for record in new:
        record.hashes = {**record.hashes, "reference": "fixed server"}
    report.check_compatible(old + new)


def test_latest_records_prefer_successful_retries() -> None:
    from mqtt_spec_mcp.benchmark.runs import latest_records

    failed = _record("mcp", "c1", 0.0, 0, 0.0)
    failed.error = "TimeoutError"
    retried = _record("mcp", "c1", 1.0, 10, 1.0)
    later_failure = _record("mcp", "c1", 0.0, 0, 0.0)
    later_failure.error = "RateLimit"
    assert latest_records([failed, retried, later_failure]) == [retried]


def test_report_breaks_down_by_version() -> None:
    records = [
        _record("full_spec", "a", 1.0, 100, 2.0, version="3.1.1"),
        _record("mcp", "a", 0.0, 10, 1.0, version="3.1.1"),
        _record("full_spec", "b", 1.0, 100, 2.0, version="5.0"),
        _record("mcp", "b", 1.0, 10, 1.0, version="5.0"),
    ]
    rows = {(r["condition"], r["version"]): r for r in report.summarize(records, True)}
    assert rows[("mcp", "3.1.1")]["quality"] == 0.0
    assert rows[("mcp", "5.0")]["quality"] == 1.0
    assert report.compare(records, "full_spec", "mcp", "3.1.1")[0]["pairs"] == 1


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
    from mqtt_spec_mcp.benchmark.cases import (
        CATEGORY_TARGETS,
        load_cases,
        validate_cases,
    )

    cases = load_cases()
    expected = sum(sum(mix.values()) for mix in CATEGORY_TARGETS.values())
    assert len(cases) == expected
    assert validate_cases(cases, require_scopes=CATEGORY_TARGETS) == []


def test_retrieval_benchmark_runs() -> None:
    from mqtt_spec_mcp.benchmark.cases import load_cases
    from mqtt_spec_mcp.benchmark.retrieval import evaluate

    result = evaluate(load_cases())
    assert result["lookup_success"] == 1.0
    assert result["hit_rate"] >= 0.9
    assert set(result["by_version"]) == {"3.1.1", "5.0"}
