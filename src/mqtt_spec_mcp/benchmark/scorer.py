"""Deterministic answer scorer for the MQTT 5.0 benchmark.

quality = clamp(0.8 * fact_recall + 0.2 * citation_recall - violation_rate, 0, 1)

A case passes only when every fact is present, every citation group is cited and no
forbidden claim is made. Facts are matched with case-insensitive regular
expressions. A match does not count when the answer negates it ("is not retained"),
and facts phrased as negations require the negation to be present. A forbidden claim
is a violation only when affirmed: a negation before it or inside the matched text
("should not retransmit") means the answer is not making that claim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cases import Case, FactPattern

SCORER_VERSION = "1.1.0"
FACT_WEIGHT = 0.8
CITATION_WEIGHT = 0.2
NEGATION_WINDOW = 48

_NEGATION = re.compile(
    r"\b(?:not|no|never|cannot|can't|mustn't|isn't|aren't|doesn't|don't|won't|"
    r"shouldn't|without|neither|nor|none)\b|n't\b",
    re.IGNORECASE,
)
_SENTENCE_BREAK = re.compile(r"[.!?;:\n]\s|\n")
_SECTION_REF = re.compile(
    r"(?<![\w.])(?:§\s*)?(\d{1,2}(?:\.\d{1,3}){1,5})(?![\w]|\.\d)"
)
_CHAPTER_REF = re.compile(
    r"(?:\b(?:section|chapter)|§)\s*(\d)(?!\d|\.\d)", re.IGNORECASE
)
_VERSION_PREFIX = re.compile(r"(?:mqtt|version|v)\s*$", re.IGNORECASE)
_WORD = re.compile(r"\b[\w'’-]+\b")


_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")


def fold(text: str) -> str:
    """Normalize Unicode and dashes so regexes see plain ASCII punctuation."""
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


@dataclass(frozen=True)
class Score:
    scorer_version: str
    fact_recall: float
    citation_recall: float
    violation_rate: float
    quality: float
    passed: bool
    words: int
    word_limit: int
    concision: float
    facts_found: list[str] = field(default_factory=list)
    facts_missing: list[str] = field(default_factory=list)
    citations_missing: list[int] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _preceding_context(text: str, start: int) -> str:
    window = text[max(0, start - NEGATION_WINDOW) : start]
    breaks = list(_SENTENCE_BREAK.finditer(window))
    return window[breaks[-1].end() :] if breaks else window


def is_negated(text: str, start: int, end: int, *, include_match: bool) -> bool:
    """True when a negation cue precedes the match in the same clause."""
    context = _preceding_context(text, start)
    if include_match:
        context += " " + text[start:end]
    return _NEGATION.search(context) is not None


def claims(answer: str, patterns: list[str]) -> bool:
    """True when the answer affirms a forbidden claim (no negation before or inside)."""
    for pattern in patterns:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            if not is_negated(answer, match.start(), match.end(), include_match=True):
                return True
    return False


def matches_fact(answer: str, patterns: list[str], polarity: str) -> bool:
    for pattern in patterns:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            negated = is_negated(
                answer, match.start(), match.end(), include_match=polarity == "negate"
            )
            if negated == (polarity == "negate"):
                return True
    return False


def cited_sections(answer: str) -> set[str]:
    cited: set[str] = set()
    for match in _SECTION_REF.finditer(answer):
        if _VERSION_PREFIX.search(answer[max(0, match.start() - 12) : match.start()]):
            continue
        cited.add(match.group(1))
    cited.update(m.group(1) for m in _CHAPTER_REF.finditer(answer))
    return cited


def _group_cited(group: list[str], cited: set[str]) -> bool:
    return any(c == s or c.startswith(s + ".") for s in group for c in cited)


def count_words(answer: str) -> int:
    return len(_WORD.findall(answer))


def _fact_found(answer: str, fact: FactPattern) -> bool:
    return matches_fact(answer, list(fact.any), fact.polarity)


def score_answer(case: Case, answer: str) -> Score:
    answer = fold(answer)
    found = [f.id for f in case.facts if _fact_found(answer, f)]
    missing = [f.id for f in case.facts if f.id not in found]
    cited = cited_sections(answer)
    groups_missing = [
        i
        for i, group in enumerate(case.citations)
        if not _group_cited(list(group), cited)
    ]
    violations = [
        claim.id for claim in case.forbidden if claims(answer, list(claim.any))
    ]
    fact_recall = len(found) / len(case.facts)
    citation_recall = 1 - len(groups_missing) / len(case.citations)
    violation_rate = len(violations) / len(case.forbidden) if case.forbidden else 0.0
    raw = FACT_WEIGHT * fact_recall + CITATION_WEIGHT * citation_recall - violation_rate
    words = count_words(answer)
    return Score(
        scorer_version=SCORER_VERSION,
        fact_recall=round(fact_recall, 6),
        citation_recall=round(citation_recall, 6),
        violation_rate=round(violation_rate, 6),
        quality=round(min(1.0, max(0.0, raw)), 6),
        passed=not missing and not groups_missing and not violations,
        words=words,
        word_limit=case.word_limit,
        concision=round(min(1.0, case.word_limit / words) if words else 1.0, 6),
        facts_found=found,
        facts_missing=missing,
        citations_missing=groups_missing,
        violations=violations,
    )
