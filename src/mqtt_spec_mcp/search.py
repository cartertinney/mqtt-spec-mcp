"""Lexical section search with BM25-style scoring and windowed snippets."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from .textutil import (
    LABEL_PATTERN,
    canonical_label,
    looks_like_label,
    normalize,
    search_tokens,
    tokenize,
)

if TYPE_CHECKING:
    from .engine import SectionInfo, SpecEngine

K1 = 1.6
B_TITLE = 0.3
B_BODY = 0.5
TITLE_WEIGHT = 5.0
PATH_WEIGHT = 1.0
NORMATIVE_BONUS = 0.5
NORMATIVE_BONUS_CAP = 4
BOOST_SECTION_ID = 1000.0
BOOST_LABEL_HOME = 600.0
BOOST_LABEL_LISTED = 400.0
BOOST_TITLE_EQUAL = 300.0
BOOST_TITLE_CONTAINS = 100.0
BOOST_BODY_PHRASE = 10.0
BOOST_TITLE_PAIR = 6.0
BOOST_HEX_IDENTIFIER = 8.0
BOOST_BODY_PAIR = 2.0
FIRST_SNIPPET_CHARS = 1600
OTHER_SNIPPET_CHARS = 320
PARENT_SNIPPET_CHARS = 700
MAX_SNIPPET_LABELS = 5

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "then",
        "there",
        "these",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)
_DASHES = str.maketrans(
    dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-") | {"\n": " "}
)


def match_form(text: str) -> str:
    """Length-preserving lowercase form used to locate matches in original text."""
    return "".join(c if len(c.lower()) != 1 else c.lower() for c in text).translate(
        _DASHES
    )


def phrase_form(text: str) -> str:
    return " ".join(match_form(text).split())


@dataclass(frozen=True)
class _Doc:
    info: SectionInfo
    title_tf: Counter[str]
    path_tf: Counter[str]
    body_tf: Counter[str]
    title_len: int
    body_len: int
    body_match: str
    title_joined: str
    body_joined: str


class SearchIndex:
    def __init__(self, engine: SpecEngine) -> None:
        self.engine = engine
        docs: list[_Doc] = []
        for info in engine.ordered:
            title_tokens = search_tokens(info.title)
            body_tokens = search_tokens(info.body)
            ancestors: list[str] = []
            parent = info.section.parent
            while parent is not None:
                ancestors.extend(search_tokens(engine.sections[parent].title))
                parent = engine.sections[parent].section.parent
            doc = _Doc(
                info=info,
                title_tf=Counter(title_tokens),
                path_tf=Counter(ancestors),
                body_tf=Counter(body_tokens),
                title_len=len(title_tokens),
                body_len=len(body_tokens),
                body_match=match_form(info.body),
                title_joined=" " + " ".join(title_tokens) + " ",
                body_joined=" " + " ".join(body_tokens) + " ",
            )
            docs.append(doc)
        self.docs = tuple(docs)
        n = len(docs)
        any_df: Counter[str] = Counter()
        for doc in docs:
            any_df.update(set(doc.title_tf) | set(doc.body_tf) | set(doc.path_tf))
        self.idf = {t: _idf(n, df) for t, df in any_df.items()}
        self.avg_title = sum(d.title_len for d in docs) / n
        self.avg_body = sum(d.body_len for d in docs) / n

    # Scoring -----------------------------------------------------------------------

    def _bm25f(self, doc: _Doc, terms: list[str]) -> float:
        """BM25F: field-weighted term frequency with a single saturation per term."""
        title_norm = 1 - B_TITLE + B_TITLE * doc.title_len / self.avg_title
        body_norm = 1 - B_BODY + B_BODY * doc.body_len / self.avg_body
        score = 0.0
        for term in terms:
            tf = (
                TITLE_WEIGHT * doc.title_tf.get(term, 0) / title_norm
                + PATH_WEIGHT * min(doc.path_tf.get(term, 0), 1)
                + doc.body_tf.get(term, 0) / body_norm
            )
            if tf:
                score += self.idf.get(term, 0.0) * tf * (K1 + 1) / (K1 + tf)
        return score

    def rank(
        self,
        query: str,
        chapter: str | None,
        normative_only: bool,
    ) -> list[tuple[float, _Doc]]:
        engine = self.engine
        q_norm = normalize(query)
        q_phrase = phrase_form(query)
        label = (
            canonical_label(query, engine.version) if looks_like_label(query) else None
        )
        req = engine.requirements.get(label) if label is not None else None
        label_home = req.section if req is not None else None
        # Identifier queries are matched exactly; their digits are noise for BM25.
        identifier = label is not None or SECTION_ID.fullmatch(q_norm) is not None
        terms = [] if identifier else query_terms(query)
        pairs = [f" {a} {b} " for a, b in pairwise(terms)]
        hex_terms = [t for t in terms if HEX_TOKEN.fullmatch(t)]
        stems = search_tokens(query)
        q_stems = " " + " ".join(stems) + " " if stems and not identifier else ""
        scored: list[tuple[float, _Doc]] = []
        for doc in self.docs:
            info = doc.info
            if chapter is not None and not engine.in_subtree(info.id, chapter):
                continue
            if normative_only and not info.section.requirements:
                continue
            score = self._bm25f(doc, terms)
            if score > 0:
                normative = min(len(info.section.requirements), NORMATIVE_BONUS_CAP)
                score += NORMATIVE_BONUS * normative
            if q_norm == info.id:
                score += BOOST_SECTION_ID
            if label is not None and label in info.section.requirements:
                score += (
                    BOOST_LABEL_HOME if info.id == label_home else BOOST_LABEL_LISTED
                )
            if q_stems and q_stems == doc.title_joined:
                score += BOOST_TITLE_EQUAL
            elif q_stems and q_stems in doc.title_joined:
                score += BOOST_TITLE_CONTAINS
            if len(terms) > 1 and q_phrase and q_phrase in doc.body_match:
                score += BOOST_BODY_PHRASE
            for term in hex_terms:
                if term in doc.body_tf or term in doc.title_tf:
                    score += BOOST_HEX_IDENTIFIER
            for pair in pairs:
                if pair in doc.title_joined:
                    score += BOOST_TITLE_PAIR
                if pair in doc.body_joined:
                    score += BOOST_BODY_PAIR
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], item[1].info.order))
        return scored

    # Snippets ----------------------------------------------------------------------

    def anchor(self, text: str, query: str) -> int | None:
        haystack = match_form(text)
        phrase = phrase_form(query)
        if phrase:
            pos = haystack.find(phrase)
            if pos >= 0:
                return pos
        best: int | None = None
        for term in query_terms(query):
            found = re.search(rf"(?<![0-9a-z]){re.escape(term)}", haystack)
            if found and (best is None or found.start() < best):
                best = found.start()
        return best


HEX_TOKEN = re.compile(r"0x[0-9a-f]+")
SECTION_ID = re.compile(r"\d+(?:\.\d+)*")


def _idf(n: int, df: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def query_terms(query: str) -> list[str]:
    tokens = search_tokens(query)
    content = [t for t in tokens if t not in STOPWORDS]
    chosen = content or tokens
    return list(dict.fromkeys(chosen))


def window(text: str, anchor: int | None, budget: int) -> str:
    """Cut ``text`` to at most ``budget`` chars around ``anchor`` at word boundaries."""
    if len(text) <= budget:
        return text
    ellipsis = "..."
    start = 0
    if anchor is not None and anchor > budget // 3:
        line_start = text.rfind("\n", 0, anchor) + 1
        start = (
            line_start if anchor - line_start <= budget // 3 else anchor - budget // 5
        )
        if start > 0 and not text[start - 1].isspace():
            nxt = re.search(r"\s", text[start:])
            start = start + nxt.end() if nxt else start
    prefix = ellipsis if start > 0 else ""
    room = budget - len(prefix)
    if len(text) - start <= room:
        return prefix + text[start:].lstrip()
    room -= len(ellipsis)
    end = start + room
    cut = text.rfind(" ", start, end + 1)
    nl = text.rfind("\n", start, end + 1)
    cut = max(cut, nl)
    if cut > start + room // 2:
        end = cut
    return prefix + text[start:end].strip() + ellipsis


def snippet_labels(
    engine: SpecEngine, snippet: str, query_label: str | None
) -> list[str]:
    labels: list[str] = []
    if query_label is not None:
        labels.append(query_label)
    for match in LABEL_PATTERN.finditer(snippet):
        label = canonical_label(match.group(1), engine.version)
        if label not in labels:
            labels.append(label)
    return labels[:MAX_SNIPPET_LABELS]


def search(
    engine: SpecEngine,
    query: str,
    chapter: str | None,
    limit: int,
    normative_only: bool,
) -> dict[str, Any]:
    engine.guard("query", query)
    query = query.strip()
    if not tokenize(query):
        raise engine.error(
            "The query has no letters or digits to search for. Pass words such as "
            "'Session Expiry Interval' or a requirement label such as 'MQTT-3.1.2-4'."
        )
    root = engine.resolve_section(chapter, "chapter") if chapter is not None else None
    index = engine.search_index
    ranked = index.rank(query, root.id if root else None, normative_only)[:limit]
    label = canonical_label(query, engine.version) if looks_like_label(query) else None
    if label is not None and label not in engine.requirements:
        label = None

    results: list[dict[str, Any]] = []
    for position, (_, doc) in enumerate(ranked):
        info = doc.info
        budget = FIRST_SNIPPET_CHARS if position == 0 else OTHER_SNIPPET_CHARS
        in_section = label is not None and label in info.section.requirements
        if in_section and engine.version == "3.1.1" and label is not None:
            excerpt = engine.requirements[label].excerpt
            if engine.requirements[label].section != info.id:
                excerpt = _block_with_label(info, label, engine) or excerpt
            snippet = window(excerpt, _label_anchor(excerpt, label, engine), budget)
        else:
            snippet = window(info.body, index.anchor(info.body, query), budget)
        entry: dict[str, Any] = {
            "section": info.id,
            "title": info.title,
            "snippet": snippet,
            "requirements": snippet_labels(
                engine, snippet, label if in_section else None
            ),
        }
        if position == 0 and info.section.parent is not None:
            parent = engine.sections[info.section.parent]
            if parent.body.strip():
                entry["parent_context"] = {
                    "section": parent.id,
                    "title": parent.title,
                    "snippet": window(
                        parent.body,
                        index.anchor(parent.body, query),
                        PARENT_SNIPPET_CHARS,
                    ),
                }
        results.append(entry)

    v = engine.version
    best = results[0]["section"] if results else None
    if results:
        next_step = (
            "Answer from the first snippet if it contains the facts you need. "
            f"Otherwise call mqtt_spec_get once with version='{v}' and "
            f"section='{best}'. Do not search again for this question."
        )
    else:
        next_step = (
            f"Nothing in MQTT {v} matched this query. This is not proof that the topic "
            f"is absent from MQTT {v}, and it says nothing about any other version. "
            "Do not repeat the same search; rephrase with different terms or browse "
            f"with mqtt_spec_outline (version='{v}')."
        )
    result = engine.envelope()
    result.update(
        {
            "query": query,
            "chapter": root.id if root else None,
            "normative_only": normative_only,
            "results": results,
            "best_section": best,
            "next_step": next_step,
        }
    )
    return result


def _label_anchor(text: str, label: str, engine: SpecEngine) -> int | None:
    for match in LABEL_PATTERN.finditer(text):
        if canonical_label(match.group(1), engine.version) == label:
            return match.start()
    return None


def _block_with_label(info: SectionInfo, label: str, engine: SpecEngine) -> str | None:
    for line in info.body.split("\n"):
        if _label_anchor(line, label, engine) is not None:
            return line
    return None
