"""AI judge for corpus-format-2 answers, and the score computed from its verdicts.

The judge sees the question, the answer key (facts and wrong claims, each with its
supporting quote) and one answer: never the condition, model, tool calls or other
answers. For each fact it returns present, absent or contradicted, and for each
wrong claim made or not_made, copying the passage of the answer behind every
positive verdict. A reply with malformed JSON or missing ids, or whose evidence is
not found in the answer, is sent back with the problem (at most three attempts);
evidence still unverified after that is recorded and reported.

Judgments are cached by judge model, judge prompt, the judged content of the case
and the answer, so reports never re-judge, and the formula below can change
without re-judging:

    fact recall = facts present, essential facts weighing 2 and supporting facts 1
    citation    = share of facts with a cited section (or subsection) of theirs
    errors      = wrong claims made + facts contradicted
    quality     = clamp(0.8 * fact recall + 0.2 * citation - 0.5 * errors, 0, 1)
    passed      = every essential fact present and cited, and no errors
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .corpus import CorpusCase, words_of
from .runs import Usage, append_record, digest
from .scorer import (
    CITATION_WEIGHT,
    FACT_WEIGHT,
    cited_sections,
    count_words,
    fold,
    group_cited,
    plain,
)

JUDGE_PROMPT_VERSION = "1"
SCORING_VERSION = "2.0"
ESSENTIAL_WEIGHT = 2.0
SUPPORTING_WEIGHT = 1.0
ERROR_PENALTY = 0.5
MAX_ATTEMPTS = 3
POSITIVE = frozenset({"present", "contradicted", "made"})

JUDGE_SYSTEM = """\
You grade one answer to a question about the OASIS MQTT specification against an \
answer key. Judge only what the answer says: do not add requirements from your own \
knowledge, and ignore everything outside the listed facts and wrong claims (length, \
style, extra detail).

Facts. For each fact decide:
- "present": the answer states the fact, in any wording, with the same meaning. The \
meaning includes the requirement strength (MUST, MUST NOT, SHOULD, MAY), conditions \
and exceptions, the MQTT version it is about, and any values, names or codes. A \
partial, vaguer or weaker statement that loses part of that meaning is not present.
- "contradicted": the answer says something incompatible with the fact anywhere, \
even if it also states the fact elsewhere.
- "absent": otherwise.
Some facts say that the specification does not define or require something. Such a \
fact is present only if the answer says or clearly conveys that the specification \
leaves it undefined or open; it is contradicted if the answer says the \
specification requires or defines a particular behaviour for it.

Wrong claims. For each wrong claim decide "made" if the answer asserts it anywhere, \
in any wording, even if it later says the opposite; otherwise "not_made". \
Mentioning a claim only to reject it is "not_made".

Versions. Facts and claims are labelled with the MQTT version they are about. When \
an answer covers two versions, a statement counts only for the version the answer \
attributes it to.

Evidence. For every "present", "contradicted" or "made" verdict, copy the passage \
of the answer that shows it, verbatim and at most 40 words; if it is spread over \
separate places, join the passages with " ... ". Use "" for "absent" and \
"not_made".

Reply with one JSON object and nothing else, listing every fact and every wrong \
claim exactly once by its id:
{"facts": [{"id": "<fact id>", "verdict": "present", "evidence": "<passage>"}], \
"forbidden": [{"id": "<claim id>", "verdict": "not_made", "evidence": ""}]}
"""

QUESTION_TEMPLATE = "Question ({scope}):\n{prompt}\n\nFacts:\n{facts}\n\n"
FACT_TEMPLATE = '- id: {id} (MQTT {version})\n  Fact: {statement}\n  Source: "{quote}"'
CLAIMS_TEMPLATE = "Wrong claims:\n{claims}\n\n"
CLAIM_TEMPLATE = (
    "- id: {id} (MQTT {version})\n  Claim: {claim}\n  Why it is wrong: {why_wrong}"
)
NO_CLAIMS = 'Wrong claims: none (reply with "forbidden": []).\n\n'
ANSWER_TEMPLATE = "Answer to grade:\n<answer>\n{answer}\n</answer>"
RETRY_TEMPLATE = (
    "Your reply could not be used:\n{problems}\n"
    "Reply again with the complete JSON object only."
)


def judge_hash() -> str:
    """Everything that defines how the judge is asked."""
    return digest(
        {
            "version": JUDGE_PROMPT_VERSION,
            "system": JUDGE_SYSTEM,
            "templates": [
                QUESTION_TEMPLATE,
                FACT_TEMPLATE,
                CLAIMS_TEMPLATE,
                CLAIM_TEMPLATE,
                NO_CLAIMS,
                ANSWER_TEMPLATE,
                RETRY_TEMPLATE,
            ],
            "attempts": MAX_ATTEMPTS,
        }
    )


def scorer_id(judge_model: str) -> str:
    return f"judged-{SCORING_VERSION}/prompt-{JUDGE_PROMPT_VERSION}/{judge_model}"


def _scope(case: CorpusCase) -> str:
    return "MQTT " + " and ".join(case.versions)


def judge_message(case: CorpusCase, answer: str) -> str:
    """The judge's input: the question, the answer key and the answer. Whether a
    fact is essential and which sections state it are not shown."""
    facts = "\n".join(
        FACT_TEMPLATE.format(
            id=f.id, version=f.version, statement=f.statement, quote=f.quote
        )
        for f in case.facts
    )
    text = QUESTION_TEMPLATE.format(scope=_scope(case), prompt=case.prompt, facts=facts)
    if case.forbidden:
        claims = "\n".join(
            CLAIM_TEMPLATE.format(
                id=c.id, version=c.version, claim=c.claim, why_wrong=c.why_wrong
            )
            for c in case.forbidden
        )
        text += CLAIMS_TEMPLATE.format(claims=claims)
    else:
        text += NO_CLAIMS
    return text + ANSWER_TEMPLATE.format(answer=answer.strip())


def judged_content(case: CorpusCase) -> dict[str, Any]:
    """The parts of a case the judge sees; a judgment is reused while they hold."""
    return {
        "version": case.version,
        "prompt": case.prompt,
        "facts": [(f.id, f.version, f.statement, f.quote) for f in case.facts],
        "forbidden": [(c.id, c.version, c.claim, c.why_wrong) for c in case.forbidden],
    }


def judgment_key(judge_model: str, case: CorpusCase, answer: str) -> str:
    return digest(
        {
            "judge": judge_model,
            "prompt": judge_hash(),
            "case": judged_content(case),
            "answer": answer,
        }
    )


# Replies ---------------------------------------------------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class FactVerdict(_Lenient):
    id: str
    verdict: Literal["present", "absent", "contradicted"]
    evidence: str = ""


class ClaimVerdict(_Lenient):
    id: str
    verdict: Literal["made", "not_made"]
    evidence: str = ""


class Verdicts(_Lenient):
    facts: tuple[FactVerdict, ...]
    forbidden: tuple[ClaimVerdict, ...] = ()


_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def evidence_found(evidence: str, answer: str) -> bool:
    """Whether each ' ... '-separated passage occurs in the answer, ignoring case,
    punctuation and markdown."""
    pieces = [words_of(p) for p in re.split(r"\.\.\.+", fold(evidence))]
    pieces = [p for p in pieces if p]
    target = f" {words_of(answer)} "
    return bool(pieces) and all(f" {p} " in target for p in pieces)


def _id_problems(
    label: str, given: Sequence[str], expected: Sequence[str]
) -> list[str]:
    problems = []
    missing = sorted(set(expected) - set(given))
    unknown = sorted(set(given) - set(expected))
    repeated = sorted({i for i in given if list(given).count(i) > 1})
    if missing:
        problems.append(f"no verdict for {label} {', '.join(missing)}")
    if unknown:
        problems.append(f"unknown {label} ids {', '.join(unknown)}")
    if repeated:
        problems.append(f"more than one verdict for {label} {', '.join(repeated)}")
    return problems


def parse_reply(
    reply: str, case: CorpusCase, answer: str
) -> tuple[Verdicts | None, list[str], list[str]]:
    """(verdicts, problems, ids whose evidence is not in the answer). Verdicts are
    None when the reply is unusable."""
    match = _OBJECT.search(reply)
    if match is None:
        return None, ["the reply has no JSON object"], []
    try:
        verdicts = Verdicts.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        detail = str(exc).splitlines()[0]
        return None, [f"the JSON does not match the format: {detail}"], []
    problems = _id_problems(
        "fact", [v.id for v in verdicts.facts], [f.id for f in case.facts]
    ) + _id_problems(
        "wrong claim",
        [v.id for v in verdicts.forbidden],
        [c.id for c in case.forbidden],
    )
    if problems:
        return None, problems, []
    items: list[FactVerdict | ClaimVerdict] = [*verdicts.facts, *verdicts.forbidden]
    unverified = [
        v.id
        for v in items
        if v.verdict in POSITIVE and not evidence_found(v.evidence, answer)
    ]
    return verdicts, [], unverified


# Judging ---------------------------------------------------------------------------


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    case_id: str
    judge_model: str
    prompt_version: str = JUDGE_PROMPT_VERSION
    facts: tuple[FactVerdict, ...] = ()
    forbidden: tuple[ClaimVerdict, ...] = ()
    unverified: tuple[str, ...] = ()
    attempts: int = 0
    error: str | None = None
    latency_s: float = 0.0
    usage: Usage = Field(default_factory=Usage)


class JudgeSession(Protocol):
    """One judge conversation; ``usage`` accumulates as replies arrive."""

    usage: Usage

    async def send(self, message: str) -> str: ...


OpenSession = Callable[[str], AbstractAsyncContextManager[JudgeSession]]


async def judge_answer(
    open_session: OpenSession, *, judge_model: str, case: CorpusCase, answer: str
) -> Judgment:
    judgment = Judgment(
        key=judgment_key(judge_model, case, answer),
        case_id=case.id,
        judge_model=judge_model,
    )
    best: tuple[Verdicts, list[str]] | None = None
    problems: list[str] = []
    start = time.perf_counter()
    try:
        async with open_session(JUDGE_SYSTEM) as session:
            try:
                message = judge_message(case, answer)
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    judgment.attempts = attempt
                    reply = await session.send(message)
                    verdicts, problems, unverified = parse_reply(reply, case, answer)
                    if verdicts is not None:
                        best = (verdicts, unverified)
                        if not unverified:
                            break
                        problems = [
                            "this evidence is not a verbatim passage of the answer: "
                            + ", ".join(unverified)
                        ]
                    message = RETRY_TEMPLATE.format(
                        problems="\n".join(f"- {p}" for p in problems)
                    )
            finally:
                judgment.usage = session.usage
    except Exception as exc:  # recorded; the next judge run retries it
        judgment.error = f"{type(exc).__name__}: {exc}"
    judgment.latency_s = round(time.perf_counter() - start, 3)
    if best is None:
        judgment.error = judgment.error or "no usable reply: " + "; ".join(problems)
        return judgment
    judgment.error = None
    judgment.facts, judgment.forbidden = best[0].facts, best[0].forbidden
    judgment.unverified = tuple(best[1])
    return judgment


def load_judgments(paths: Iterable[Path]) -> dict[str, Judgment]:
    """Usable judgments by key; a later line replaces an earlier one."""
    found: dict[str, Judgment] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                judgment = Judgment.model_validate_json(line)
                if judgment.error is None:
                    found[judgment.key] = judgment
    return found


async def judge_all(
    items: Sequence[tuple[CorpusCase, str]],
    *,
    judge_model: str,
    open_session: OpenSession,
    output: Path,
    concurrency: int = 4,
    sealed: bool = False,
    log: Callable[[str], None] = print,
) -> list[Judgment]:
    """Judge each (case, answer) without a cached judgment; append each result."""
    cached = load_judgments([output])
    todo: dict[str, tuple[CorpusCase, str]] = {}
    for case, answer in items:
        key = judgment_key(judge_model, case, answer)
        if key not in cached:
            todo.setdefault(key, (case, answer))
    log(f"{len(items)} answers, {len(items) - len(todo)} already judged")
    gate = asyncio.Semaphore(concurrency)
    done: list[Judgment] = []

    async def worker(index: int, case: CorpusCase, answer: str) -> None:
        async with gate:
            judgment = await judge_answer(
                open_session, judge_model=judge_model, case=case, answer=answer
            )
        append_record(output, judgment)
        done.append(judgment)
        name = f"#{index}" if sealed else case.id
        status = judgment.error or (
            f"attempts={judgment.attempts}"
            + (f" unverified={len(judgment.unverified)}" if judgment.unverified else "")
        )
        if sealed and judgment.error:
            # Problems name answer-key ids; the judgments file keeps the detail.
            status = "failed: " + judgment.error.split(":", 1)[0]
        log(f"[{len(done)}/{len(todo)}] {name} {status}")

    await asyncio.gather(
        *(worker(i, c, a) for i, (c, a) in enumerate(todo.values(), 1))
    )
    return done


# Scoring ---------------------------------------------------------------------------


def judged_score(case: CorpusCase, answer: str, judgment: Judgment) -> dict[str, Any]:
    """A score dict with the keys the report reads (as ``Score.to_dict()``)."""
    verdict = {v.id: v.verdict for v in judgment.facts}
    present = [f.id for f in case.facts if verdict.get(f.id) == "present"]
    contradicted = [f.id for f in case.facts if verdict.get(f.id) == "contradicted"]
    weight = {
        f.id: ESSENTIAL_WEIGHT if f.essential else SUPPORTING_WEIGHT for f in case.facts
    }
    fact_recall = sum(weight[i] for i in present) / sum(weight.values())
    essential = [f.id for f in case.facts if f.essential]
    text = plain(answer)
    cited = cited_sections(text)
    uncited = [f.id for f in case.facts if not group_cited(list(f.sections), cited)]
    citation_recall = 1 - len(uncited) / len(case.facts)
    made = [c.id for c in judgment.forbidden if c.verdict == "made"]
    errors = len(made) + len(contradicted)
    raw = (
        FACT_WEIGHT * fact_recall
        + CITATION_WEIGHT * citation_recall
        - ERROR_PENALTY * errors
    )
    words = count_words(text)
    return {
        "scorer_version": scorer_id(judgment.judge_model),
        "fact_recall": round(fact_recall, 6),
        "essential_recall": round(
            sum(i in present for i in essential) / len(essential), 6
        ),
        "citation_recall": round(citation_recall, 6),
        "errors": errors,
        "quality": round(min(1.0, max(0.0, raw)), 6),
        "passed": errors == 0
        and all(i in present and i not in uncited for i in essential),
        "words": words,
        "word_limit": case.word_limit,
        "concision": round(min(1.0, case.word_limit / words) if words else 1.0, 6),
        "facts_found": present,
        "facts_missing": [f.id for f in case.facts if f.id not in present],
        "facts_contradicted": contradicted,
        "citations_missing": uncited,
        "violations": made,
        "evidence_unverified": list(judgment.unverified),
    }
