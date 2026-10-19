"""Lexical section search with BM25-style scoring and windowed snippets."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from .common import OTHER_VERSION
from .textutil import (
    LABEL_PATTERN,
    SECTION_PREFIX,
    canonical_label,
    looks_like_label,
    normalize,
    search_tokens,
    stem,
    tokenize,
)

if TYPE_CHECKING:
    from .engine import SectionInfo, SpecEngine

K1 = 1.6
B_TITLE = 0.3
B_BODY = 0.5
TITLE_WEIGHT = 5.0
PATH_WEIGHT = 1.0
ANCESTOR_TERM_BONUS = 0.5
NORMATIVE_BONUS = 0.25
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
PARENT_SNIPPET_CHARS = 700
PASSAGE_PHRASE_BONUS = 5.0
# Evidence pack: excerpt characters per search, candidate sections, and how much
# a lower-ranked section's sentences are discounted (1 / (1 + prior * rank)).
PACK_CHARS = 5500
PACK_SECTIONS = 8
PACK_RANK_PRIOR = 0.15
# Every sentence of the best section scores at least this much, so spare budget fills
# its gaps (a list of conditions, say) before weak matches in lower-ranked sections.
PACK_TOP_SECTION_BASE = 3.0
# Answer keys and questions turn on normative rules: such sentences count double.
PACK_NORMATIVE_BOOST = 1.0
NORMATIVE_SENTENCE = re.compile(r"\b(?:MUST|SHOULD|MAY|REQUIRED|SHALL)\b|\[MQTT-")
LABEL_SECTIONS = 3
LABEL_FIRST_CHARS = 1600
LABEL_OTHER_CHARS = 400
SECTION_QUERY_CHARS = 3000
MIN_DEDUP_CHARS = 30
MAX_QUERY_TERMS = 32
CLUSTER_SPAN = 400

# English function words plus question fillers ("what happens when ..."). "will" is
# deliberately absent: Will Message, Will Flag and Will Delay are MQTT terms.
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "describe",
        "did",
        "do",
        "does",
        "done",
        "explain",
        "for",
        "from",
        "had",
        "happen",
        "happened",
        "happens",
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
        "mean",
        "means",
        "my",
        "occur",
        "occurred",
        "occurs",
        "of",
        "on",
        "or",
        "please",
        "should",
        "so",
        "tell",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "with",
        "work",
        "works",
        "would",
        "you",
        "your",
    }
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
    title_content: str
    body_content: str


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
                title_content=_joined(content_stems(info.title)),
                body_content=_joined(content_stems(info.body)),
            )
            docs.append(doc)
        self.docs = tuple(docs)
        n = len(docs)
        any_df: Counter[str] = Counter()
        for doc in docs:
            any_df.update(set(doc.title_tf) | set(doc.body_tf) | set(doc.path_tf))
        self.idf = {t: _idf(n, df) for t, df in any_df.items()}
        self.max_idf = _idf(n, 0)
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
        q_id = SECTION_PREFIX.sub("", q_norm).strip().rstrip(".,;:")
        q_phrase = phrase_form(query)
        label = (
            canonical_label(query, engine.version) if looks_like_label(query) else None
        )
        req = engine.requirements.get(label) if label is not None else None
        label_home = req.section if req is not None else None
        # Identifier queries are matched exactly; their digits are noise for BM25.
        identifier = label is not None or SECTION_ID.fullmatch(q_id) is not None
        terms = [] if identifier else query_terms(query)
        sequence = [] if identifier else content_stems(query)
        pairs = list(dict.fromkeys(f" {a} {b} " for a, b in pairwise(sequence)))
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
            # Own titles are weighted inside BM25F; terms named only by ancestor
            # headings get extra, unsaturated credit ("3.9.3 Payload" is under SUBACK).
            score += ANCESTOR_TERM_BONUS * sum(
                self.idf.get(t, 0.0)
                for t in terms
                if t in doc.path_tf and t not in doc.title_tf
            )
            if score > 0:
                normative = min(len(info.section.requirements), NORMATIVE_BONUS_CAP)
                score += NORMATIVE_BONUS * normative
            if q_id == info.id:
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
                if pair in doc.title_content:
                    score += BOOST_TITLE_PAIR
                if pair in doc.body_content:
                    score += BOOST_BODY_PAIR
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda item: (-item[0], item[1].info.order))
        return scored

    # Snippets ----------------------------------------------------------------------

    def evidence(
        self, docs: list[_Doc], query: str, budget: int
    ) -> list[tuple[_Doc, str]]:
        """Query-focused excerpts drawn from several ranked sections at once.

        Every sentence of the candidate sections scores the idf of the query terms
        it contains (plus a phrase bonus); sentences of the best section also get a
        base score, normative sentences count double, and every score is discounted
        by its section's rank. Sentences are added best first, each with its
        neighbours, while the excerpts plus section headings fit ``budget``.
        Sections come back in rank order, sentences in document order."""
        terms = query_terms(query)
        patterns = [
            re.compile(rf"(?<![0-9a-z]){re.escape(_prefix_form(t))}") for t in terms
        ]
        phrase = phrase_form(query) if len(terms) > 1 else ""
        per_doc: list[list[tuple[int, int]]] = []
        candidates: list[tuple[float, int, int]] = []
        for rank, doc in enumerate(docs):
            body = doc.info.body
            units = _units(body)
            haystack = match_form(body)
            per_doc.append(units)
            for i, (start, end) in enumerate(units):
                segment = haystack[start:end]
                score = sum(
                    self.idf.get(t, self.max_idf)
                    for t, p in zip(terms, patterns, strict=True)
                    if p.search(segment)
                )
                if phrase and phrase in " ".join(segment.split()):
                    score += PASSAGE_PHRASE_BONUS
                if rank == 0:
                    score += PACK_TOP_SECTION_BASE
                if score > 0:
                    if NORMATIVE_SENTENCE.search(body[start:end]):
                        score *= 1 + PACK_NORMATIVE_BOOST
                    candidates.append((score / (1 + PACK_RANK_PRIOR * rank), rank, i))
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        chosen: dict[int, set[int]] = {}
        sizes: dict[int, int] = {}
        seen: set[str] = set()  # sentences already shown in a higher-ranked section
        total = 0
        for _, rank, best in candidates:
            units = per_doc[rank]
            info = docs[rank].info
            heading = len(info.id) + len(info.title) + 6
            for j in (best, best + 1, best - 1):
                current = chosen.get(rank, set())
                if not 0 <= j < len(units) or j in current:
                    continue
                text = " ".join(info.body[units[j][0] : units[j][1]].lower().split())
                if len(text) >= MIN_DEDUP_CHARS and text in seen:
                    continue
                trial = current | {j}
                size = heading + _passage_size(units, trial)
                if total + size - sizes.get(rank, 0) > budget:
                    if j == best:
                        break
                    continue
                total += size - sizes.get(rank, 0)
                chosen[rank], sizes[rank] = trial, size
                seen.add(text)
        return [
            (
                docs[r],
                _render_passages(docs[r].info.body, per_doc[r], sorted(chosen[r])),
            )
            for r in sorted(chosen)
        ]

    def anchor(self, text: str, query: str) -> int | None:
        """Where a snippet should focus: the first whole-word phrase match, else
        the start of the densest cluster of informative query terms."""
        haystack = match_form(text)
        phrase = phrase_form(query)
        if phrase:
            found = re.search(rf"(?<![0-9a-z]){re.escape(phrase)}", haystack)
            if found:
                return found.start()
        hits = sorted(
            (match.start(), term)
            for term in query_terms(query)
            for match in re.finditer(
                rf"(?<![0-9a-z]){re.escape(_prefix_form(term))}", haystack
            )
        )
        if not hits:
            return None
        best_pos, best_score = hits[0][0], -1.0
        counts: Counter[str] = Counter()
        end = 0
        for pos, term in hits:
            while end < len(hits) and hits[end][0] - pos <= CLUSTER_SPAN:
                counts[hits[end][1]] += 1
                end += 1
            score = sum(self.idf.get(t, self.max_idf) for t, n in counts.items() if n)
            if score > best_score + 1e-9:
                best_pos, best_score = pos, score
            counts[term] -= 1
        return best_pos


HEX_TOKEN = re.compile(r"0x[0-9a-f]+")
SECTION_ID = re.compile(r"\d+(?:\.\d+)*")


def _idf(n: int, df: int) -> float:
    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def content_stems(text: str) -> list[str]:
    """Stems of the non-stopword tokens, in order (all tokens if every one is a
    stopword)."""
    tokens = tokenize(text)
    content = [t for t in tokens if t not in STOPWORDS] or tokens
    return [stem(t) for t in content]


def query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(content_stems(query)))[:MAX_QUERY_TERMS]


def _joined(stems: list[str]) -> str:
    return " " + " ".join(stems) + " "


def _prefix_form(term: str) -> str:
    """Stem form that is a prefix of its surface words ("expiri" -> "expir")."""
    return term[:-1] if len(term) > 4 and term[-1] in "ie" else term


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


ELLIPSIS = "..."
PASSAGE_GAP = " ... "
_SENTENCE = re.compile(r"[^\n]*?(?:[.;!?](?=\s)|$)")


def _units(text: str) -> list[tuple[int, int]]:
    """Sentence-sized spans of ``text``: each line, split after . ; ! or ?."""
    spans: list[tuple[int, int]] = []
    for line in re.finditer(r"[^\n]+", text):
        for match in _SENTENCE.finditer(line.group(0)):
            piece = match.group(0)
            if piece.strip():
                start = line.start() + match.start() + len(piece) - len(piece.lstrip())
                end = line.start() + match.start() + len(piece.rstrip())
                spans.append((start, end))
    return spans


def _passage_size(units: list[tuple[int, int]], chosen: set[int]) -> int:
    """Length of ``_render_passages`` for these units, without building it."""
    order = sorted(chosen)
    size = sum(units[i][1] - units[i][0] for i in order)
    for prev, i in pairwise(order):
        size += units[i][0] - units[prev][1] if i == prev + 1 else len(PASSAGE_GAP)
    size += len(ELLIPSIS) if order[0] > 0 else 0
    size += len(ELLIPSIS) if order[-1] < len(units) - 1 else 0
    return size


def _render_passages(text: str, units: list[tuple[int, int]], order: list[int]) -> str:
    parts = [ELLIPSIS] if order[0] > 0 else []
    for k, i in enumerate(order):
        if k:
            prev = order[k - 1]
            parts.append(
                text[units[prev][1] : units[i][0]] if i == prev + 1 else PASSAGE_GAP
            )
        parts.append(text[units[i][0] : units[i][1]])
    if order[-1] < len(units) - 1:
        parts.append(ELLIPSIS)
    return "".join(parts)


def _ranked(
    engine: SpecEngine, query: str, chapter: str | None, normative_only: bool
) -> tuple[str, SectionInfo | None, list[_Doc], str | None, bool]:
    """Validate a query and rank sections: (query, chapter, docs, label, identifier)."""
    engine.guard("query", query)
    query = query.strip()
    if not tokenize(query):
        raise engine.error(
            "The query has no letters or digits to search for. Pass words such as "
            "'Session Expiry Interval' or a requirement label such as 'MQTT-3.1.2-4'."
        )
    root = engine.resolve_section(chapter, "chapter") if chapter is not None else None
    ranked = engine.search_index.rank(query, root.id if root else None, normative_only)
    label = canonical_label(query, engine.version) if looks_like_label(query) else None
    if label is not None and label not in engine.requirements:
        label = None
    q_id = SECTION_PREFIX.sub("", normalize(query)).strip().rstrip(".,;:")
    identifier = label is not None or SECTION_ID.fullmatch(q_id) is not None
    return query, root, [doc for _, doc in ranked], label, identifier


def rank_sections(
    engine: SpecEngine,
    query: str,
    chapter: str | None = None,
    limit: int = PACK_SECTIONS,
    normative_only: bool = False,
) -> list[str]:
    """Section ids in rank order (the order the evidence pack draws from)."""
    return [doc.info.id for doc in _ranked(engine, query, chapter, normative_only)[2]][
        :limit
    ]


def search(
    engine: SpecEngine,
    query: str,
    chapter: str | None = None,
    limit: int | None = None,
    normative_only: bool = False,
) -> dict[str, Any]:
    """An evidence pack: query-focused excerpts from the best-ranked sections."""
    query, root, docs, label, identifier = _ranked(
        engine, query, chapter, normative_only
    )
    index = engine.search_index
    docs = docs[: limit or PACK_SECTIONS]
    sections: list[dict[str, Any]] = []
    if label is not None:
        # Requirement label: the requirement itself, then the sections that list it.
        for position, doc in enumerate(docs[:LABEL_SECTIONS]):
            if label not in doc.info.section.requirements:
                continue
            budget = LABEL_FIRST_CHARS if position == 0 else LABEL_OTHER_CHARS
            excerpt = _label_snippet(engine, doc.info, label, budget)
            sections.append(_entry(doc.info, excerpt))
    elif identifier and docs:
        info = docs[0].info
        sections.append(_entry(info, window(info.body, None, SECTION_QUERY_CHARS)))
    elif docs:
        for doc, excerpt in index.evidence(docs, query, PACK_CHARS):
            sections.append(_entry(doc.info, excerpt))
        if not sections:
            info = docs[0].info
            sections.append(
                _entry(info, window(info.body, index.anchor(info.body, query), 1600))
            )
    v = engine.version
    best = sections[0]["section"] if sections else None
    if sections:
        next_step = (
            "Answer from these excerpts: state each rule here that bears on the "
            "question, with its MUST/SHOULD/MAY and conditions, and cite its section. "
            "If a needed rule is missing, get one listed section with mqtt_spec_get "
            f"(version='{v}'). Do not search MQTT {v} again for this question (to "
            f"compare versions, search MQTT {OTHER_VERSION[v]} once)."
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
            "sections": sections,
            "best_section": best,
            "next_step": next_step,
        }
    )
    return result


def _entry(info: SectionInfo, excerpt: str) -> dict[str, Any]:
    return {"section": info.id, "title": info.title, "excerpt": excerpt}


def render_search(result: dict[str, Any]) -> str:
    """The evidence pack as Markdown: scope and source, sections, next step."""
    lines = [f"{result['scope']} Source: {result['source']}"]
    for entry in result["sections"]:
        lines += ["", f"## {entry['section']} {entry['title']}", entry["excerpt"]]
    lines += ["", result["next_step"]]
    return "\n".join(lines)


def _label_snippet(
    engine: SpecEngine, info: SectionInfo, label: str, budget: int
) -> str:
    """Snippet for a requirement-label query in a section that lists the label."""
    requirement = engine.requirements[label]
    if engine.version != "3.1.1":
        printed = _label_anchor(info.body, label, engine)
        if printed is not None:
            return window(info.body, printed, budget)
    # 3.1.1 always quotes the indexed excerpt; 5.0 falls back to it for labels the
    # body does not print recognizably (reconciled from Appendix B).
    excerpt = requirement.excerpt
    if requirement.section != info.id:
        excerpt = _block_with_label(info, label, engine) or excerpt
    return window(excerpt, _label_anchor(excerpt, label, engine), budget)


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
