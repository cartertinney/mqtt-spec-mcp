"""Bundled-case validator, deterministic scorer and retrieval benchmark tests
(no model, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.cases import Case, load_cases, parse_cases
from benchmarks.scorer import (
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
    from benchmarks.cases import (
        CATEGORY_TARGETS,
        load_cases,
        validate_cases,
    )

    cases = load_cases()
    expected = sum(sum(mix.values()) for mix in CATEGORY_TARGETS.values())
    assert len(cases) == expected
    assert validate_cases(cases, require_scopes=CATEGORY_TARGETS) == []


def test_retrieval_benchmark_runs() -> None:
    from benchmarks.cases import load_cases
    from benchmarks.retrieval import evaluate

    result = evaluate(load_cases())
    assert result["lookup_success"] == 1.0
    assert result["hit_rate"] >= 0.9
    assert set(result["by_version"]) == {"3.1.1", "5.0"}
