"""Corpus format 2, the AI judge, analysis plans and judged reports (no model,
no network: the judge is faked)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from mqtt_spec_mcp.benchmark import corpus, judge, plan, report
from mqtt_spec_mcp.benchmark.corpus import CorpusCase, CorpusError
from mqtt_spec_mcp.benchmark.judge import (
    ClaimVerdict,
    FactVerdict,
    JudgeSession,
    Judgment,
)
from mqtt_spec_mcp.benchmark.plan import PlanError
from mqtt_spec_mcp.benchmark.runs import (
    Prices,
    RunRecord,
    Usage,
    append_record,
    base_hash,
    case_hash,
    load_records,
    output_labels,
    stratified_sample,
)
from mqtt_spec_mcp.versions import PINS

REPO = Path(__file__).resolve().parents[1]
RETAIN_SET = (
    "If the RETAIN flag is set to 1 in a PUBLISH packet sent by a Client to a "
    "Server, the Server MUST replace any existing retained message for this topic "
    "and store the Application Message"
)
RETAIN_ZERO = (
    "If the RETAIN flag is 0 in a PUBLISH packet sent by a Client to a Server, the "
    "Server MUST NOT store the message as a retained message"
)
EXPIRY_ABSENT = "If the Session Expiry Interval is absent the value 0 is used."
GOOD = (
    "With RETAIN 1 the **Server** replaces the retained message and stores the new "
    "one (section 3.3.1.3). A RETAIN 0 publish is not stored as retained "
    "(3.3.1.3). An absent Session Expiry Interval means 0 (3.1.2.11.2)."
)
PARTIAL = "With RETAIN 1 the Server stores the message as retained (3.3.1.3)."


def make_case(case_id: str, split: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": case_id,
        "version": "5.0",
        "kind": "scenario",
        "topic": "retained",
        "origin": "spec",
        "prompt": "A client publishes with RETAIN set, then with it clear. What "
        "does the broker keep?",
        "word_limit": 150,
        "sections_needed": ["3.3.1.3"],
        "facts": [
            {
                "id": "replace",
                "statement": "With RETAIN 1 the Server MUST replace any retained "
                "message for the topic and store the new one.",
                "version": "5.0",
                "essential": True,
                "sections": ["3.3.1.3"],
                "quote": RETAIN_SET,
            },
            {
                "id": "zero-not-stored",
                "statement": "With RETAIN 0 the Server MUST NOT store the message "
                "as a retained message.",
                "version": "5.0",
                "essential": True,
                "sections": ["3.3.1.3"],
                "quote": RETAIN_ZERO,
            },
            {
                "id": "expiry-default",
                "statement": "An absent Session Expiry Interval means 0.",
                "version": "5.0",
                "essential": False,
                "sections": ["3.1.2.11.2"],
                "quote": EXPIRY_ABSENT,
            },
        ],
        "forbidden": [
            {
                "id": "zero-clears",
                "claim": "A RETAIN 0 publish clears the retained message.",
                "why_wrong": "RETAIN 0 must not remove or replace it.",
                "version": "5.0",
                "sections": ["3.3.1.3"],
                "quote": RETAIN_ZERO,
            }
        ],
        "reference_answer": "RETAIN 1 replaces the retained message and stores the "
        "new one (3.3.1.3); RETAIN 0 is not stored as retained (3.3.1.3); an absent "
        "Session Expiry Interval means 0 (3.1.2.11.2).",
        "split": split,
    }
    data.update(overrides)
    return data


def write_corpus(
    root: Path, dev: list[dict[str, Any]], test: list[dict[str, Any]]
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, cases in (("dev.jsonl", dev), ("test.jsonl", test)):
        data = "".join(json.dumps(c) + "\n" for c in cases).encode()
        (root / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "cases": len(cases)}
    manifest = {
        "corpus_format": 2,
        "corpus_version": "1.0",
        "created": "2026-10-20",
        "split_seed": 7,
        "sources": {
            v: {
                "url": PINS[v].source_url,
                "html_sha256": "0" * 64,
                "text_file": f"sources/mqtt-{v}.txt",
                "text_sha256": "1" * 64,
            }
            for v in ("3.1.1", "5.0")
        },
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    return root


DEV = [
    make_case("dev-a", "dev"),
    make_case(
        "dev-b",
        "dev",
        kind="edge",
        origin="real-world",
        inspired_by=["https://example.com/question"],
    ),
]
TEST = [make_case("test-secret-a", "test"), make_case("test-secret-b", "test")]


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "benchmarks"
    shutil.copytree(REPO / "benchmarks" / "schema", root / "schema")
    monkeypatch.setenv(corpus.BENCH_DIR_ENV, str(root))
    return root


@pytest.fixture
def demo(bench: Path, tmp_path: Path) -> corpus.Corpus:
    """A registered corpus named demo: two dev cases, two sealed test cases."""
    corpus.register("demo", write_corpus(tmp_path / "corpus", DEV, TEST))
    return corpus.open_corpus("demo")


# Corpus ----------------------------------------------------------------------------


def test_corpus_registers_and_loads(demo: corpus.Corpus, bench: Path) -> None:
    registration = json.loads((bench / "corpora" / "demo.json").read_text("utf-8"))
    assert registration["path"] == "../../corpus"
    assert registration["manifest_sha256"] == demo.manifest_sha256
    dev = demo.cases("dev")
    assert [c.id for c in dev] == ["dev-a", "dev-b"]
    assert [c.stratum for c in dev] == ["scenario", "edge"]
    assert dev[0].versions == ("5.0",)
    assert [c.id for c in stratified_sample(dev, 1, seed=1)] in (["dev-a"], ["dev-b"])
    text = "\n".join(corpus.describe(demo))
    assert "dev: 2 cases" in text
    assert "test: 2 cases" in text
    assert "origin: real-world 1, spec 1" in text


def test_registration_survives_symlinked_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "deeper" / "real"  # like macOS /tmp -> /private/tmp
    shutil.copytree(REPO / "benchmarks" / "schema", real / "benchmarks" / "schema")
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv(corpus.BENCH_DIR_ENV, str(link / "benchmarks"))
    corpus.register("linked", write_corpus(link / "corpus", DEV, TEST))
    assert corpus.open_corpus("linked").root == (real / "corpus").resolve()


def test_corpus_refuses_changes_after_freezing(
    demo: corpus.Corpus, tmp_path: Path
) -> None:
    dev = tmp_path / "corpus" / "dev.jsonl"
    dev.write_text(dev.read_text("utf-8").replace("broker", "server"), "utf-8")
    with pytest.raises(CorpusError, match="does not match the SHA-256"):
        demo.cases("dev")
    manifest = tmp_path / "corpus" / "manifest.json"
    manifest.write_text(manifest.read_text("utf-8") + " ", "utf-8")
    with pytest.raises(CorpusError, match="changed since 'demo' was registered"):
        corpus.open_corpus("demo")
    with pytest.raises(CorpusError, match="no corpus registered as 'other'"):
        corpus.open_corpus("other")


def test_optional_status_and_clarification_refs(bench: Path, tmp_path: Path) -> None:
    noted = make_case("noted", "dev", clarification_refs=["MQTT-537"])
    root = write_corpus(tmp_path / "c", [noted], TEST)
    manifest = root / "manifest.json"
    data = json.loads(manifest.read_text("utf-8"))
    manifest.write_text(json.dumps({**data, "status": "draft"}), "utf-8")
    with pytest.raises(CorpusError, match="draft corpus"):
        corpus.register("noted", root)
    manifest.write_text(json.dumps({**data, "status": "frozen"}), "utf-8")
    corpus.register("noted", root)
    (case,) = corpus.open_corpus("noted").cases("dev")
    assert case.clarification_refs == ("MQTT-537",)
    assert "MQTT-537" not in judge.judge_message(case, "x")


def test_register_refuses_ids_shared_between_splits(
    bench: Path, tmp_path: Path
) -> None:
    root = write_corpus(tmp_path / "c", DEV, [make_case("dev-a", "test")])
    with pytest.raises(CorpusError, match="appear in both dev and test"):
        corpus.register("shared", root)


def test_split_checks_what_the_schema_cannot(tmp_path: Path) -> None:
    case = make_case("dup-case", "dev")
    line = json.dumps(case) + "\n"
    with pytest.raises(CorpusError, match="duplicate case ids: dup-case"):
        corpus.parse_split((line * 2).encode(), "dev", tmp_path / "dev.jsonl")
    with pytest.raises(CorpusError, match=r"split is 'dev' in test\.jsonl"):
        corpus.parse_split(line.encode(), "test", tmp_path / "test.jsonl")
    facts = case["facts"]
    twice = json.dumps({**case, "facts": [facts[0], facts[0], facts[2]]})
    with pytest.raises(CorpusError, match=r"duplicate fact ids \['replace'\]"):
        corpus.parse_split(twice.encode(), "dev", tmp_path / "dev.jsonl")


def test_schema_problems_in_sealed_cases_hide_their_text(tmp_path: Path) -> None:
    short = json.dumps(make_case("leaky", "test", prompt="Too short?")).encode()
    with pytest.raises(CorpusError) as sealed:
        corpus.parse_split(short, "test", tmp_path / "test.jsonl")
    assert "$.prompt: fails minLength" in str(sealed.value)
    assert "Too short" not in str(sealed.value)
    short = json.dumps(make_case("leaky", "dev", prompt="Too short?")).encode()
    with pytest.raises(CorpusError, match="is too short"):
        corpus.parse_split(short, "dev", tmp_path / "dev.jsonl")
    lookup = make_case("lookup-five-facts", "dev", kind="lookup")
    lookup["facts"] = lookup["facts"] * 2
    with pytest.raises(CorpusError, match=r"\$\.facts"):
        corpus.parse_split(json.dumps(lookup).encode(), "dev", tmp_path / "d.jsonl")


def test_case_warnings_compare_with_the_index(bench: Path, tmp_path: Path) -> None:
    assert corpus.case_warnings(CorpusCase.model_validate(DEV[0])) == []
    assert corpus.quote_found(
        "If the RETAIN flag is set to 1 ... store the Application Message", "5.0"
    )
    bad = make_case("warned", "test", reference_answer="word " * 200)
    bad["facts"][0] = {
        **bad["facts"][0],
        "sections": ["9.9.9"],
        "quote": "This sentence is not in the specification at all.",
    }
    warnings = corpus.case_warnings(CorpusCase.model_validate(bad))
    assert any("no section 9.9.9 in MQTT 5.0" in w for w in warnings)
    assert any("quote not found" in w for w in warnings)
    assert any("200 words (limit 150)" in w for w in warnings)
    corpus.register("warned", write_corpus(tmp_path / "w", DEV, [bad]))
    sealed = "\n".join(corpus.describe(corpus.open_corpus("warned")))
    assert "3 warning(s) in 1 sealed case(s)" in sealed
    assert "warned:" not in sealed
    unsealed = "\n".join(corpus.describe(corpus.open_corpus("warned"), unseal=True))
    assert "warning: warned: replace: no section 9.9.9" in unsealed


# Judge -----------------------------------------------------------------------------


def verdicts(
    facts: dict[str, tuple[str, str]], claims: dict[str, tuple[str, str]]
) -> str:
    return json.dumps(
        {
            "facts": [
                {"id": i, "verdict": v, "evidence": e} for i, (v, e) in facts.items()
            ],
            "forbidden": [
                {"id": i, "verdict": v, "evidence": e} for i, (v, e) in claims.items()
            ],
        }
    )


ALL_PRESENT = verdicts(
    {
        "replace": ("present", "replaces the retained message and stores the new one"),
        "zero-not-stored": ("present", "A RETAIN 0 publish is not stored as retained"),
        "expiry-default": ("present", "absent Session Expiry Interval means 0"),
    },
    {"zero-clears": ("not_made", "")},
)


def case_of(data: dict[str, Any]) -> CorpusCase:
    return CorpusCase.model_validate(data)


def test_judge_sees_the_key_and_answer_only() -> None:
    case = case_of(DEV[0])
    text = judge.judge_message(case, "The broker keeps the last retained message.")
    assert case.facts[0].statement in text
    assert RETAIN_SET in text
    assert "zero-clears" in text
    assert "RETAIN 0 must not remove or replace it." in text
    assert "The broker keeps the last retained message." in text
    for hidden in ("essential", "3.1.2.11.2", case.reference_answer, "mcp", "grep"):
        assert hidden not in text
    assert "Question (MQTT 5.0)" in text
    both = case_of(make_case("cmp", "dev", version="both"))
    assert "Question (MQTT 3.1.1 and 5.0)" in judge.judge_message(both, "x")
    none = case_of(make_case("no-claims", "dev", forbidden=[]))
    assert "Wrong claims: none" in judge.judge_message(none, "x")


def test_parse_reply_checks_ids_format_and_evidence() -> None:
    case = case_of(DEV[0])
    parsed, problems, unverified = judge.parse_reply(
        f"```json\n{ALL_PRESENT}\n```", case, GOOD
    )
    assert parsed is not None
    assert problems == []
    assert unverified == []
    assert judge.evidence_found("With RETAIN 1 ... stores the new one", GOOD)
    assert judge.evidence_found("the Server replaces the retained message", GOOD)
    assert not judge.evidence_found("", GOOD)
    assert judge.parse_reply("I think it is fine.", case, GOOD)[1] == [
        "the reply has no JSON object"
    ]
    bad = ALL_PRESENT.replace('"not_made"', '"maybe"')
    assert "does not match the format" in judge.parse_reply(bad, case, GOOD)[1][0]
    missing = verdicts({"replace": ("absent", "")}, {"zero-clears": ("made", "x")})
    _, problems, _ = judge.parse_reply(missing, case, GOOD)
    assert problems == ["no verdict for fact expiry-default, zero-not-stored"]
    invented = ALL_PRESENT.replace("stores the new one", "deletes every topic")
    assert judge.parse_reply(invented, case, GOOD)[2] == ["replace"]


class FakeSession:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = replies
        self.sent: list[str] = []
        self.usage = Usage()

    async def send(self, message: str) -> str:
        self.sent.append(message)
        self.usage.api_calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def fake_judge(
    conversations: list[list[str | Exception]], opened: list[FakeSession]
) -> Callable[[str], AbstractAsyncContextManager[JudgeSession]]:
    @asynccontextmanager
    async def open_session(system: str) -> AsyncIterator[JudgeSession]:
        assert system == judge.JUDGE_SYSTEM
        session = FakeSession(conversations.pop(0))
        opened.append(session)
        yield session

    return open_session


def run_judge(conversation: list[str | Exception]) -> tuple[Judgment, FakeSession]:
    opened: list[FakeSession] = []
    result = asyncio.run(
        judge.judge_answer(
            fake_judge([conversation], opened),
            judge_model="judge-x",
            case=case_of(DEV[0]),
            answer=GOOD,
        )
    )
    return result, opened[0]


def test_judge_answer_retries_unusable_replies() -> None:
    result, session = run_judge(["no idea", ALL_PRESENT])
    assert result.error is None
    assert result.attempts == 2
    assert session.sent[1].startswith("Your reply could not be used:")
    assert result.usage.api_calls == 2
    invented = ALL_PRESENT.replace("stores the new one", "deletes every topic")
    result, session = run_judge([invented, invented, invented])
    assert result.error is None
    assert result.attempts == judge.MAX_ATTEMPTS
    assert result.unverified == ("replace",)
    assert "not a verbatim passage of the answer: replace" in session.sent[1]
    result, _ = run_judge(["a", "b", "c"])
    assert result.error is not None
    assert result.error.startswith("no usable reply")
    result, _ = run_judge([RuntimeError("boom")])
    assert result.error == "RuntimeError: boom"


def test_judge_all_caches_judgments(tmp_path: Path) -> None:
    case = case_of(DEV[0])
    output = tmp_path / "judgments.jsonl"
    opened: list[FakeSession] = []
    conversations: list[list[str | Exception]] = [[ALL_PRESENT]]
    lines: list[str] = []
    first = asyncio.run(
        judge.judge_all(
            [(case, GOOD), (case, GOOD)],
            judge_model="judge-x",
            open_session=fake_judge(conversations, opened),
            output=output,
            log=lines.append,
        )
    )
    assert len(first) == 1
    assert len(opened) == 1
    assert lines[-1].startswith("[1/1] dev-a attempts=1")
    again = asyncio.run(
        judge.judge_all(
            [(case, GOOD)],
            judge_model="judge-x",
            open_session=fake_judge([], opened),
            output=output,
            sealed=True,
            log=lines.append,
        )
    )
    assert again == []
    assert lines[-1] == "1 answers, 1 already judged"
    # Identical judged content would share the cached judgment; this one differs.
    secret = case_of(
        make_case("test-secret-c", "test", prompt="Which RETAIN publishes are kept?")
    )
    missing = verdicts({"replace": ("absent", "")}, {"zero-clears": ("not_made", "")})
    asyncio.run(
        judge.judge_all(
            [(secret, GOOD)],
            judge_model="judge-x",
            open_session=fake_judge([[missing] * judge.MAX_ATTEMPTS], opened),
            output=output,
            sealed=True,
            log=lines.append,
        )
    )
    assert lines[-1] == "[1/1] #1 failed: no usable reply"
    assert "zero-not-stored" in output.read_text("utf-8")
    key = judge.judgment_key("judge-x", case, GOOD)
    assert set(judge.load_judgments([output])) == {key}
    assert judge.judgment_key("judge-y", case, GOOD) != key


def judgment_for(
    case: CorpusCase, answer: str, facts: dict[str, str], made: bool = False
) -> Judgment:
    return Judgment(
        key=judge.judgment_key("judge-x", case, answer),
        case_id=case.id,
        judge_model="judge-x",
        facts=tuple(
            FactVerdict.model_validate({"id": i, "verdict": v})
            for i, v in facts.items()
        ),
        forbidden=(
            ClaimVerdict(id="zero-clears", verdict="made" if made else "not_made"),
        ),
    )


def test_judged_score_formula() -> None:
    case = case_of(DEV[0])
    good = judgment_for(
        case,
        GOOD,
        {
            "replace": "present",
            "zero-not-stored": "present",
            "expiry-default": "absent",
        },
    )
    score = judge.judged_score(case, GOOD, good)
    assert score["fact_recall"] == pytest.approx(0.8)  # (2 + 2) / (2 + 2 + 1)
    assert score["citation_recall"] == pytest.approx(1.0)
    assert score["quality"] == pytest.approx(0.8 * 0.8 + 0.2)
    assert score["passed"]
    assert score["scorer_version"] == judge.scorer_id("judge-x")
    uncited = "RETAIN 1 replaces it; RETAIN 0 is not stored as retained."
    score = judge.judged_score(case, uncited, good)
    assert score["citation_recall"] == 0.0
    assert not score["passed"]
    wrong = judgment_for(
        case,
        GOOD,
        {
            "replace": "present",
            "zero-not-stored": "contradicted",
            "expiry-default": "present",
        },
        made=True,
    )
    score = judge.judged_score(case, GOOD, wrong)
    assert score["errors"] == 2
    assert score["facts_contradicted"] == ["zero-not-stored"]
    assert score["violations"] == ["zero-clears"]
    assert score["quality"] == 0.0
    assert not score["passed"]
    assert score["essential_recall"] == 0.5


# Plans -----------------------------------------------------------------------------


def write_plan(
    bench: Path,
    demo: corpus.Corpus,
    *,
    split: corpus.Split = "dev",
    name: str = "dev-pass",
    judges: tuple[str, ...] = ("judge-x",),
) -> Path:
    text = plan.render_plan(
        name=name,
        description="A synthetic plan.",
        corpus=demo,
        split=split,
        models=["m1", "m2"],
        conditions=["mcp", "grep"],
        epochs=1,
        seed=3,
        judge_models=judges,
        baseline="grep",
        candidate="mcp",
        server_commit=None,
    )
    path = bench / "plans" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")
    return path


def test_plan_pins_corpus_code_and_judge(demo: corpus.Corpus, bench: Path) -> None:
    path = write_plan(bench, demo)
    loaded = plan.load_plan(path)
    assert loaded.id.startswith("dev-pass@")
    assert plan.plan_problems(loaded) == []
    assert loaded.results_path == bench / "runs" / "demo" / "dev" / "dev-pass.jsonl"
    assert plan.judgments_path(loaded.results_path).name == "dev-pass.judgments.jsonl"
    text = path.read_text("utf-8")
    path.write_text(text.replace(base_hash(), "0" * 16), "utf-8")
    assert plan.plan_problems(plan.load_plan(path)) == [
        "the shared instructions changed (hashes.base)"
    ]
    path.write_text(text.replace('scoring_version = "', 'scoring_version = "9'))
    assert "scoring formula" in plan.plan_problems(plan.load_plan(path))[0]
    path.write_text(text.replace('baseline = "grep"', 'baseline = "none"'), "utf-8")
    with pytest.raises(PlanError, match="baseline and candidate"):
        plan.load_plan(path)
    path.write_text(text.replace('models = ["judge-x"]', 'models = ["m1"]'), "utf-8")
    with pytest.raises(PlanError, match="must not be one of the models under test"):
        plan.load_plan(path)


def test_test_plans_must_be_committed(demo: corpus.Corpus, tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    path = write_plan(repo, demo, split="test", name="release")
    assert "is not committed" in plan.commit_problems(path)[0]
    where = path.parent

    def run(*args: str) -> None:
        subprocess.run([git, *args], cwd=where, check=True, capture_output=True)

    run("init", "-q")
    run("add", path.name)
    assert "is not committed" in plan.commit_problems(path)[0]
    run(
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "plan",
    )
    assert plan.commit_problems(path) == []
    path.write_text(path.read_text("utf-8") + "# edited\n", "utf-8")
    assert "uncommitted changes" in plan.commit_problems(path)[0]
    assert any("uncommitted" in p for p in plan.plan_problems(plan.load_plan(path)))


def test_runner_resolves_corpora_and_plans(demo: corpus.Corpus, bench: Path) -> None:
    runner = pytest.importorskip("mqtt_spec_mcp.benchmark.copilot_runner")
    out = ["--output", str(bench / "out.jsonl")]
    args = runner.build_parser().parse_args(
        ["--corpus", "demo", "--split", "test", "--model", "m", *out]
    )
    with pytest.raises(PlanError, match="sealed"):
        runner.resolve(args)
    args = runner.build_parser().parse_args(["--corpus", "demo", "--model", "m", *out])
    setup = runner.resolve(args)
    assert [c.id for c in setup.cases] == ["dev-a", "dev-b"]
    assert setup.labels == runner.Labels("demo", "1.0", "dev", None)
    assert list(setup.conditions) == ["none", "full_spec", "grep", "mcp"]
    path = write_plan(bench, demo)
    args = runner.build_parser().parse_args(["--plan", str(path), "--epochs", "3"])
    with pytest.raises(PlanError, match="the plan fixes the run; drop --epochs"):
        runner.resolve(args)
    other = bench / "other.jsonl"
    judged_runs(demo.cases("dev"), other, plan_id="old@1")
    argv = ["--plan", str(path), "--output", str(other)]
    assert (
        asyncio.run(runner.run_benchmark(runner.build_parser().parse_args(argv))) == 1
    )
    setup = runner.resolve(runner.build_parser().parse_args(["--plan", str(path)]))
    assert list(setup.models) == ["m1", "m2"]
    assert list(setup.conditions) == ["mcp", "grep"]
    assert setup.output == bench / "runs" / "demo" / "dev" / "dev-pass.jsonl"
    assert setup.labels.plan == plan.load_plan(path).id


# Judged reports --------------------------------------------------------------------


def judged_runs(
    cases: list[CorpusCase],
    results: Path,
    *,
    split: str = "dev",
    plan_id: str | None = None,
    skip: int = 0,
) -> None:
    """mcp answers fully and cheaply; grep misses a fact and costs more."""
    judgments = plan.judgments_path(results)
    present = {"replace": "present", "zero-not-stored": "present"}
    runs = [
        (model, condition, case)
        for model in ("m1", "m2")
        for condition in ("mcp", "grep")
        for case in cases
    ]
    for model, condition, case in runs[skip:]:
        answer, facts, tokens = (
            (GOOD, {**present, "expiry-default": "present"}, 1_000)
            if condition == "mcp"
            else (PARTIAL, {**present, "zero-not-stored": "absent"}, 2_000)
        )
        facts.setdefault("expiry-default", "absent")
        append_record(
            results,
            RunRecord(
                runner="test",
                model=model,
                condition=condition,
                case_id=case.id,
                version=case.version,
                epoch=0,
                seed=1,
                answer=answer,
                latency_s=1.0,
                usage=Usage(input_tokens=tokens),
                hashes={
                    "base": "b",
                    "prompt": condition,
                    "reference": "r",
                    "case": case_hash(case),
                },
                corpus="demo",
                corpus_version="1.0",
                split=split,
                plan=plan_id,
            ),
        )
        append_record(judgments, judgment_for(case, answer, facts))


def test_results_files_hold_one_corpus_split_and_plan(
    demo: corpus.Corpus, bench: Path
) -> None:
    results = bench / "runs" / "old.jsonl"
    judged_runs(demo.cases("dev"), results, plan_id="old@1")
    assert output_labels(results) == {("demo", "1.0", "dev", "old@1")}
    assert output_labels(bench / "missing.jsonl") == set()


def test_report_scores_judged_runs(demo: corpus.Corpus, bench: Path) -> None:
    results = bench / "runs" / "dev.jsonl"
    judged_runs(demo.cases("dev"), results)
    out = bench / "report.json"
    argv = [str(results), "--baseline", "grep", "--json-output", str(out)]
    assert report.main(argv) == 1  # no judge model
    assert report.main([*argv, "--judge-model", "judge-x"]) == 0
    payload = json.loads(out.read_text("utf-8"))
    rows = {(r["model"], r["condition"]): r for r in payload["summary"]}
    assert rows[("m1", "mcp")]["quality"] == pytest.approx(1.0)
    assert rows[("m1", "grep")]["fact_recall"] == pytest.approx(0.4)
    assert rows[("m1", "mcp")]["pass_rate"] == 1.0
    pooled = [r for r in payload["comparisons"] if r["model"] == report.POOLED]
    assert pooled[0]["pairs"] == 4
    assert pooled[0]["cases"] == 2
    assert pooled[0]["token_reduction"]["point"] == pytest.approx(0.5)
    assert pooled[0]["pass_delta"]["point"] == pytest.approx(1.0)
    slices = {r["slice"] for r in payload["comparisons_by_slice"]}
    assert slices == {"kind=edge", "kind=scenario", "origin=real-world", "origin=spec"}
    assert payload["scorer_version"] == judge.scorer_id("judge-x")
    plan.judgments_path(results).unlink()
    assert report.main([*argv, "--judge-model", "judge-x"]) == 1


def test_sealed_test_split_reports_only_under_its_plan(
    demo: corpus.Corpus, bench: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_plan(bench, demo, split="test", name="release")
    loaded = plan.load_plan(path)
    cases = demo.cases("test")
    judged_runs(cases, loaded.results_path, split="test", plan_id=loaded.id, skip=1)
    results = str(loaded.results_path)
    assert report.main([results, "--judge-model", "judge-x"]) == 1
    assert "sealed" in capsys.readouterr().err
    out = bench / "release.json"
    assert report.main(["--plan", str(path), "--json-output", str(out)]) == 1
    decision = json.loads(out.read_text("utf-8"))["decision"]
    assert decision["status"] == "INCOMPLETE"
    assert decision["coverage"] == [7, 8]
    judged_runs(cases[:1], loaded.results_path, split="test", plan_id=loaded.id)
    swapped = ["--plan", str(path), "--baseline", "mcp", "--candidate", "grep"]
    assert report.main(swapped) == 1
    assert "the plan fixes the comparison" in capsys.readouterr().err
    assert report.main(["--plan", str(path), "--json-output", str(out)]) == 0
    payload = json.loads(out.read_text("utf-8"))
    assert payload["decision"]["status"] == "PASS"
    assert [c["passed"] for c in payload["decision"]["checks"]] == [True, True]
    text = capsys.readouterr().out
    assert "Decision (release@" in text
    assert "test-secret" not in text


def test_plan_checks_can_target_question_kinds(
    demo: corpus.Corpus, bench: Path
) -> None:
    path = write_plan(bench, demo, split="test", name="release")
    text = path.read_text("utf-8")
    text += """
[[analysis.checks]]
name = "better on scenarios"
metric = "quality_delta"
kinds = ["scenario"]
ci_low_above = 0.0

[[analysis.checks]]
name = "no lookups to judge"
metric = "quality_delta"
kinds = ["lookup"]
ci_low_above = -0.01
"""
    path.write_text(text, "utf-8")
    loaded = plan.load_plan(path)
    assert [c.slice for c in loaded.plan.analysis.checks] == [
        "all",
        "all",
        "kinds=scenario",
        "kinds=lookup",
    ]
    judged_runs(
        demo.cases("test"), loaded.results_path, split="test", plan_id=loaded.id
    )
    out = bench / "kinds.json"
    assert report.main(["--plan", str(path), "--json-output", str(out)]) == 1
    payload = json.loads(out.read_text("utf-8"))
    checks = {c["name"]: c for c in payload["decision"]["checks"]}
    assert checks["better on scenarios"]["passed"]
    assert checks["better on scenarios"]["outcomes"][0]["model"] == report.POOLED
    assert checks["no lookups to judge"]["outcomes"] == []
    assert not checks["no lookups to judge"]["passed"]
    assert payload["decision"]["status"] == "FAIL"
    slices = {r["slice"] for r in payload["comparisons_by_slice"]}
    assert "kinds=scenario" in slices
    path.write_text(text.replace('kinds = ["lookup"]', 'kinds = ["lookup", "lookup"]'))
    with pytest.raises(PlanError, match="kinds repeat"):
        plan.load_plan(path)


def test_panel_scores_are_the_mean_of_the_judges() -> None:
    case = case_of(DEV[0])
    strict = judgment_for(
        case,
        GOOD,
        {"replace": "present", "zero-not-stored": "absent", "expiry-default": "absent"},
    )
    lenient = judgment_for(
        case,
        GOOD,
        {
            "replace": "present",
            "zero-not-stored": "present",
            "expiry-default": "present",
        },
    ).model_copy(update={"judge_model": "judge-y"})
    score = judge.panel_score(case, GOOD, [strict, lenient])
    one = judge.judged_score(case, GOOD, strict)
    other = judge.judged_score(case, GOOD, lenient)
    assert score["quality"] == pytest.approx((one["quality"] + other["quality"]) / 2)
    assert score["passed"] == 0.5
    assert score["facts_found"] == ["replace"]
    assert set(score["judges"]) == {"judge-x", "judge-y"}
    assert score["scorer_version"] == judge.scorer_id(["judge-x", "judge-y"])
    assert judge.panel_score(case, GOOD, [strict]) == one


def test_report_averages_a_panel_and_needs_every_judgment(
    demo: corpus.Corpus, bench: Path
) -> None:
    results = bench / "runs" / "panel.jsonl"
    judged_runs(demo.cases("dev"), results)
    judgments = plan.judgments_path(results)
    for line in judgments.read_text("utf-8").splitlines():
        copy = Judgment.model_validate_json(line)
        case = next(c for c in demo.cases("dev") if c.id == copy.case_id)
        records = [r for r in load_records([results]) if r.case_id == case.id]
        answer = next(
            r.answer
            for r in records
            if judge.judgment_key("judge-x", case, r.answer) == copy.key
        )
        append_record(
            judgments,
            copy.model_copy(
                update={
                    "judge_model": "judge-y",
                    "key": judge.judgment_key("judge-y", case, answer),
                    "facts": tuple(
                        v.model_copy(update={"verdict": "present"}) for v in copy.facts
                    ),
                }
            ),
        )
    out = bench / "panel.json"
    argv = [str(results), "--baseline", "grep", "--json-output", str(out)]
    assert report.main([*argv, "--judge-models", "judge-x", "judge-y"]) == 0
    rows = {
        (r["model"], r["condition"]): r
        for r in json.loads(out.read_text("utf-8"))["summary"]
    }
    assert rows[("m1", "grep")]["fact_recall"] == pytest.approx((0.4 + 1.0) / 2)
    assert rows[("m1", "grep")]["pass_rate"] == pytest.approx(0.5)
    assert report.main([*argv, "--judge-models", "judge-x", "judge-z"]) == 1


def test_bootstrap_resamples_cases_not_pairs() -> None:
    def pair(case_id: str, quality: float) -> tuple[RunRecord, RunRecord]:
        def record(condition: str, q: float) -> RunRecord:
            return RunRecord(
                runner="t",
                model="m",
                condition=condition,
                case_id=case_id,
                version="5.0",
                epoch=0,
                seed=1,
                hashes={},
                score={"quality": q, "passed": q == 1.0},
            )

        return record("grep", 0.5), record("mcp", quality)

    single = [pair(f"c{i}", i / 10) for i in range(10)]
    doubled = [p for p in single for _ in range(2)]
    assert report.bootstrap_ci(doubled, report.quality_delta) == pytest.approx(
        report.bootstrap_ci(single, report.quality_delta)
    )


def test_weighted_tokens_price_cached_input_and_output() -> None:
    usage = Usage(
        input_tokens=1_000,
        cache_read_tokens=600,
        cache_write_tokens=100,
        output_tokens=50,
    )
    assert usage.weighted_tokens() == pytest.approx(300 + 60 + 125 + 250)
    grok_like = Prices(input=200, cache_read=50, cache_write=0, output=600)
    assert usage.weighted_tokens(grok_like) == pytest.approx(300 + 150 + 100 + 150)
    free = Prices(input=0, cache_read=0, cache_write=0, output=0)
    assert usage.weighted_tokens(free) == usage.weighted_tokens()
