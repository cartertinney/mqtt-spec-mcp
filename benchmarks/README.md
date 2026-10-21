# Benchmarks

Offline regression checks for the server, plus reports from historical agent
evaluations. This directory is a checkout-only Python package: it is not part of
the installed server and registers no commands.

## Regression checks

These checks need no model, credentials, or network. They require Python 3.10+:

```bash
uv sync --locked
uv run python -m benchmarks.cases
uv run python -m benchmarks.retrieval \
    --min-recall-at-1 0.70 --min-recall-at-5 0.60 \
    --min-hit-rate 0.90 --min-mrr 0.78 --min-lookup-success 1.0
uv run python -m benchmarks.micro --repetitions 10
uv run pytest tests/test_benchmark.py
```

- `cases` validates the bundled cases in [data/](data/). It checks their schema,
  coverage, and the grounding of every fact in its cited section, and requires
  each reference answer to pass the deterministic scorer.
- `retrieval` ranks each case's search query and runs its lookups, with no model
  in the loop. CI fails if any metric falls below the thresholds shown.
- `micro` measures response and schema sizes, index loading, in-process latency,
  and stdio cold start. For approximate `o200k_base` token counts, run it with
  `uv run --group benchmark`; the tokenizer may download its encoding on first use.

The 200 bundled cases cover MQTT 5.0 (120), MQTT 3.1.1 (60), and comparisons (20).
They shaped the server and its scorer, so they are a development and regression
set, not held-out evidence. [AUTHORING.md](AUTHORING.md) records how they were
written and reviewed.

## Agent evaluations

Agent evaluations measure models answering questions with this server's tools
against the same models using a plain-text search baseline. They are run with a
separate private harness, on a corpus written independently of the server. The
harness, the corpus, and the raw runs are not public, so the reports below cannot
be regenerated from this repository.

In brief:

- **Conditions.** Each question gets a fresh session with the same base prompt and
  a 12-call tool budget. The `grep` baseline gets a line search and an 80-line
  reader over the same specification text. The `mcp` condition gets this server,
  limited to the question's MQTT version(s).
- **Judging.** Two judges from different model families, neither under test,
  grade each answer blind against an answer key of facts with source quotes.
  Positive verdicts must quote the answer. Essential facts weigh 2 and supporting
  facts 1, and errors are wrong claims made plus facts contradicted:
  `quality = clamp(0.8 × fact recall + 0.2 × citation − 0.5 × errors, 0, 1)`.
- **Decisions.** A paired bootstrap gives 95% confidence intervals, resampling
  questions. Each release decision checks interval lower bounds against rules in
  an analysis plan committed before the run; see [plans/](plans/).

[DECISIONS.md](../docs/DECISIONS.md) records the harness's design and the dev-split
evidence behind server changes.

## Historical results

The [release-2 report](results/2026-10-21-independent-release-2.md) compared five
models over two epochs on 165 questions. It reported +0.9 quality points
(95% CI +0.2 to +1.7) and 23% fewer tokens (19% to 27%) against `grep`.
Price-weighted cost was approximately equal, with variation by model.

Important qualifications:

- The complex-question check passed narrowly.
- The same test split was used for [release 1](results/2026-10-20-independent-release-1.md)
  and release 2. Release-1 aggregates influenced release-2's decision rule.
- Those questions are not fresh held-out evidence for future changes.
- These results describe particular historical server commits and controlled
  toolsets, not arbitrary client workflows.
- No model evaluation has been run since release 2.

Earlier [bundled-corpus](results/2026-10-19-claude-sonnet-5.md) and
[evidence-pack](results/2026-10-19-evidence-pack-claude-sonnet-5.md) reports are
development history, not independent validation. Commands printed in historical
reports refer to tooling that is no longer in this repository.
