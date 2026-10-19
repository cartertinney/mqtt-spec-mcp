"""Deterministic answer scorer for the MQTT benchmark.

quality = clamp(0.8 * fact_recall + 0.2 * citation_recall - violation_rate, 0, 1)

A case passes only when every fact is present, every citation group is cited and no
forbidden claim is made. Facts are matched with case-insensitive regular
expressions. A match does not count when the answer negates it ("is not retained"),
and facts phrased as negations require the negation to be present. A forbidden claim
is a violation only when affirmed: a negation before it or inside the matched text
("should not retransmit") means the answer is not making that claim.

Answers are normalized before matching: Unicode, dashes and quotes are folded,
markdown emphasis and code markers are removed, and a heading-only line is joined
to the paragraph it introduces. Negation is scoped to the clause: parenthetical
asides, earlier ", and"/"but"/"because" clauses, a leading "If/When ...," clause
and a sentence-initial "No," do not negate what follows, and bounds such as "must
not exceed 65535" or "no more than 3" are not negations. An affirmed fact is also
not credited when its own shortest match negates itself ("the Will is not
published"), unless the pattern spells out a negation itself.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cases import Case, FactPattern

SCORER_VERSION = "1.3.1"
FACT_WEIGHT = 0.8
CITATION_WEIGHT = 0.2
NEGATION_WINDOW = 48

# A sentence-initial "No," / "No -" answers a yes/no question; it does not negate
# the clause that follows.
_NEGATION = re.compile(
    r"\b(?:not|never|cannot|can't|mustn't|isn't|aren't|doesn't|don't|won't|"
    r"shouldn't|without|neither|nor|none)\b|n't\b|\bno\b(?!\s*(?:,|-\s))",
    re.IGNORECASE,
)
_SENTENCE_BREAK = re.compile(
    r"[.!?;:\n]\s|\n"
    r"|,\s+(?:and|but|so|whereas|while|only|just|instead|yet)\b"
    r"|\b(?:but|whereas|although|though|however|because)\b",
    re.IGNORECASE,
)
_PARENTHETICAL = re.compile(r"\([^()]*\)|^[^()]*\)")
# A leading conditional or temporal clause ("If no PINGRESP arrives, ...").
_SUBORDINATE = re.compile(
    r"\b(?:if|when|whenever|once|unless|until|after|before|since|although|though|"
    r"while|whereas)\b[^,]*,",
    re.IGNORECASE,
)
# Bounds ("must not exceed 65535", "no more than 3") and "not only" affirm.
_NOT_NEGATING = re.compile(
    r"\b(?:not|no|never|cannot|can't|mustn't|shouldn't|won't)\s+(?:\w+\s+){0,2}?"
    r"(?:exceed\w*|(?:more|longer|larger|greater|bigger|higher|less|fewer|lower|"
    r"smaller|shorter|later|earlier)\s+than|beyond|above|below)\b"
    r"|\bnot\s+(?:only|just|merely)\b",
    re.IGNORECASE,
)
# A pattern that spells out its own negation handles negation itself.
# Negation words in regex source: whole words, also after an escape such as \b or \s,
# and n't spelled with a quote class (n['’]t).
_PATTERN_NEGATION = re.compile(
    r"(?:(?<![a-z])|(?<=\\[bsW]))"
    r"(?:not|no|never|without|cannot|neither|nor|none)(?![a-z])"
    r"|n't|n\[[^\]]*'[^\]]*\]t",
    re.IGNORECASE,
)
_SECTION_REF = re.compile(
    r"(?<![\w.])(?:§\s*)?(\d{1,2}(?:\.\d{1,3}){1,5})(?![\w]|\.\d)"
)
_CHAPTER_REF = re.compile(
    r"(?:\b(?:section|chapter)|§)\s*(\d)(?!\d|\.\d)", re.IGNORECASE
)
_VERSION_PREFIX = re.compile(r"(?:mqtt|version|v)\s*$", re.IGNORECASE)
# Version numbers that read like section numbers: "5.0" is not a section in either
# specification, and a bare "3.1.1" is the version unless marked as a section.
_SECTION_MARKER = re.compile(
    r"(?:\bsections?|\bsec\.|§)\s*(?:\d+(?:\.\d+)*\s*(?:,|and|or|&|/)\s*)*$",
    re.IGNORECASE,
)
_WORD = re.compile(r"\b[\w'’-]+\b")


_PUNCT = {
    **dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-"),
    **dict.fromkeys(map(ord, "\u2018\u2019"), "'"),
}


_HEADING_LINE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+(?P<atx>[^\n]+?)|(?:(?:[-+*]|\d+[.)])[ \t]+)?"
    r"(?P<bold>\*\*[^*\n]+\*\*|__[^_\n]+__)[ \t]*:?)[ \t]*\n+",
    re.MULTILINE,
)
_EMPHASIS = re.compile(r"\*+|`+|(?<!\w)_+|_+(?!\w)")


def fold(text: str) -> str:
    """Normalize Unicode, dashes and quotes so regexes see plain ASCII punctuation."""
    return unicodedata.normalize("NFKC", text).translate(_PUNCT)


def _join_heading(match: re.Match[str]) -> str:
    heading = (match.group("atx") or match.group("bold")).rstrip()
    return heading + (" " if heading.rstrip("*_").endswith(":") else ": ")


def plain(text: str) -> str:
    """Fold, join heading-only lines to their paragraph and drop markdown emphasis."""
    return _EMPHASIS.sub("", _HEADING_LINE.sub(_join_heading, fold(text)))


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


def _last_clause(text: str) -> str:
    breaks = list(_SENTENCE_BREAK.finditer(text))
    clause = text[breaks[-1].end() :] if breaks else text
    clause = _SUBORDINATE.sub(" ", _PARENTHETICAL.sub(" ", clause))
    return _NOT_NEGATING.sub(" ", clause)


def is_negated(text: str, start: int, end: int, *, include_match: bool) -> bool:
    """True when a negation cue precedes the match in the same clause."""
    context = _last_clause(text[max(0, start - NEGATION_WINDOW) : start])
    if include_match:
        context += " " + text[start:end]
    return _NEGATION.search(context) is not None


def _match_negates_itself(rx: re.Pattern[str], text: str, start: int, end: int) -> bool:
    """True when the shortest match from ``start`` negates its own last clause
    ("the Will is not published" for ``will[^.]{0,40}published``)."""
    if _NEGATION.search(text, start, end) is None:
        return False
    stop = next((e for e in range(start + 1, end) if rx.fullmatch(text, start, e)), end)
    return _NEGATION.search(_last_clause(text[start:stop])) is not None


def claims(answer: str, patterns: list[str]) -> bool:
    """True when the answer affirms a forbidden claim (no negation before or inside)."""
    for pattern in patterns:
        for match in re.finditer(pattern, answer, re.IGNORECASE):
            if not is_negated(answer, match.start(), match.end(), include_match=True):
                return True
    return False


def matches_fact(answer: str, patterns: list[str], polarity: str) -> bool:
    for pattern in patterns:
        rx = re.compile(pattern, re.IGNORECASE)
        check_inside = polarity == "affirm" and not _PATTERN_NEGATION.search(pattern)
        for match in rx.finditer(answer):
            start, end = match.span()
            if polarity == "negate":
                if is_negated(answer, start, end, include_match=True):
                    return True
            elif not is_negated(answer, start, end, include_match=False) and not (
                check_inside and _match_negates_itself(rx, answer, start, end)
            ):
                return True
    return False


def cited_sections(answer: str) -> set[str]:
    cited: set[str] = set()
    for match in _SECTION_REF.finditer(answer):
        number = match.group(1)
        before = answer[max(0, match.start() - 40) : match.start()]
        if _VERSION_PREFIX.search(before[-12:]) or number == "5.0":
            continue
        marked = match.group(0).startswith("§") or _SECTION_MARKER.search(before)
        if number == "3.1.1" and not marked:
            continue
        cited.add(number)
    cited.update(m.group(1) for m in _CHAPTER_REF.finditer(answer))
    return cited


def _group_cited(group: list[str], cited: set[str]) -> bool:
    # Entries may be version-qualified ("3.1.1:3.1.2.4"); answers cite numbers.
    sections = [ref.rsplit(":", 1)[-1] for ref in group]
    return any(c == s or c.startswith(s + ".") for s in sections for c in cited)


def count_words(answer: str) -> int:
    return len(_WORD.findall(answer))


def _fact_found(answer: str, fact: FactPattern) -> bool:
    return matches_fact(answer, list(fact.any), fact.polarity)


def score_answer(case: Case, answer: str) -> Score:
    answer = plain(answer)
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
