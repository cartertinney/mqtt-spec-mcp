# Status and next steps

Last updated 2026-10-21, after release 2 (server commit 10402f0). [README.md](README.md)
covers using and benchmarking the server; [DECISIONS.md](DECISIONS.md) records each
design decision and the evidence for it. This file is the starting point for picking
the work up again.

## Where things stand

### The server

- Version 0.1.0: an offline MCP server with four read-only tools
  (`mqtt_spec_outline`, `mqtt_spec_search`, `mqtt_spec_get`, `mqtt_spec_lookup`) over
  bundled indexes of MQTT 3.1.1 Plus Errata 01 and MQTT 5.0, each pinned to its OASIS
  source by hash.
- **Search** returns an evidence pack:
  - query-focused excerpts from up to eight sections, plus related requirements;
  - several `;`-separated parts per query;
  - rewrites of everyday spellings (`keepalive`, `LWT`, `broker`);
  - a heading marked `(excerpt: N of M characters)` when it shows only part of
    its section.
- **Agent guidance** (server instructions and each pack's next step):
  - Fetch a section only before saying MQTT is silent, leaves something open or
    optional, or that a rule has no exception, and only when the deciding section
    is excerpted or not listed.
  - At most one fetch and one repeat search per version. Unmarked sections are
    complete.
- 338 tests; CI runs on Python 3.10, 3.12 and 3.13, plus a benchmark-harness job.

### Benchmarks

- **Bundled corpus** (200 cases, deterministic regex scorer 1.3.2). It was used to
  develop the server, so it serves only as a development and regression set.
- **Independent corpus** `independent` 1.1.0.
  - 71 dev and 165 test questions, written in a separate clean-room session from
    the OASIS texts and real-world problem reports.
  - Registered in [benchmarks/corpora/independent.json](benchmarks/corpora/independent.json),
    which expects the corpus checkout beside this repository at
    `../mqtt-spec-corpus`.
  - The corpus's brief and schemas are in that repository.
- **Judging.** A panel of claude-sonnet-5 and gpt-5.6-sol (judge prompt version 2,
  scoring 2.0), calibrated on the dev split against blind adjudication.
- **Results on the sealed test split.** Five models (claude-sonnet-5.5,
  gpt-6.1-sol, claude-haiku-5.5, gpt-6-luna, grok-4.7) × two epochs, `mcp` and
  `grep` side by side. Quality deltas are in points against `grep`, 95% CIs in
  brackets:

| plan | server | decision | quality, all | complex questions | simple questions | tokens saved |
| --- | --- | --- | --- | --- | --- | --- |
| [release-1](benchmarks/plans/release-1.toml) | 4bd4586 | FAIL | −0.2 (−1.1, +0.6) | −0.4 | +0.6 | 39% |
| [release-2](benchmarks/plans/release-2.toml) | 10402f0 | PASS | +0.9 (+0.2, +1.7) | +1.1 (+0.2, +2.1) | +0.1 (−0.7, +0.9) | 23% (19%, 27%) |

Complex questions are the synthesis, scenario and edge kinds; simple ones are lookup
and rule. Reports:
[release 1](benchmarks/results/2026-10-20-independent-release-1.md) and
[release 2](benchmarks/results/2026-10-21-independent-release-2.md).

In short, the tool adds modest, measurable value on questions that need several
sections and is level with `grep` on simple ones. It uses about a quarter fewer
tokens, and about the same cost once each model's token prices are applied.

### Known gaps

- **Remaining losses** on the latest dev pass (pass 5): 49 facts missed or
  contradicted.
  - 33 had the deciding rule in front of the model, which misread it or left it
    out.
  - 11 had the rule in a section the pack cut.
  - 5 had a section that was never shown.

  For comparison, about half of `grep`'s 66 losses are rules it never found.
- **Headroom.** claude-sonnet-5.5 on dev scored:

  | condition | quality |
  | --- | ---: |
  | no reference material | 0.812 |
  | `grep` | 0.913 |
  | `mcp` | 0.958 |
  | whole specification in the prompt (about 10× `mcp`'s tokens) | 0.990 |
- **Errors.** `mcp` still makes slightly more errors per answer than `grep`
  (0.023 against 0.019 on test).
- **Simple questions save few tokens:** 6% on the test split (CI −8% to +18%),
  against 25% on complex ones.
- **claude-sonnet-5.5 pays 32% more with `mcp`** once its prices are applied. Its
  search results are written to the prompt cache and barely reused in a two-call
  session.
- **The complex-question pass was narrow:** its CI lower bound was +0.19 points.
- **The test split is no longer fresh.** It has been run under two plans. Only
  aggregates were seen, but release 1's per-kind results shaped release 2's
  decision rule. The next claim needs new test questions.

### Data that is not in this repository

- **Raw runs and judge verdicts** are in `benchmarks/runs/`, which is gitignored
  (about 62 MB on the development machine). They cover the bundled-corpus passes,
  independent dev passes 1 to 5 and the release 1 and 2 test runs. Reports
  regenerate from these offline; without them, only the curated reports in
  [benchmarks/results/](benchmarks/results/) remain.
- **The independent corpus folder** is a local git repository with no remote.
- **The analysis scripts behind DECISIONS** (replaying recorded tool calls, loss
  anatomy, judge calibration) lived outside the repository. Their methods are
  described under [Analysis methods](#analysis-methods) so they can be rebuilt,
  and next step 1 moves them into the repository.

Back up the corpus to a private repository, and archive `benchmarks/runs/`, before
changing machines.

## Next steps

In order. Steps 1 to 4 can be iterated offline and confirmed with one dev agent pass
each. Step 5 is what a new public claim needs.

1. **Build an offline evidence benchmark.** Agent passes are slow and noisy (see
   [Working on it](#working-on-it)), so the next gains should be found offline.
   - **Inputs:** the dev split's facts, which carry verbatim quotes and sections,
     and the 2,688 `mcp` searches recorded in dev passes 1 to 5 (2,537 distinct).
   - **Measure:** for each recorded search, whether the deciding sentence of each
     fact appears in the pack, with the sentences that qualify it, against the
     pack's size. Compare pack configurations by that coverage against size.
   - **Never use the test split's quotes.**
   - **Moving the tools in:** bring replay and loss anatomy into
     `src/mqtt_spec_mcp/benchmark/`, with tests.
2. **Address evidence that is seen but misused** (33 of the 49 losses).
   - First check offline whether these losses cluster in fragmented excerpts: a
     sentence shown without its neighbours or its list's lead-in, between `...`
     cuts.
   - If they do, show whole paragraphs or lists for the top-ranked sections, and
     trim tail excerpts to keep the size.
3. **Link exceptions at index time** (16 losses: rules cut or never shown).
   Synthesis questions are where `mcp`'s lead is weakest (+0.9 on test, not
   significant).
   - When generating the indexes, link each requirement sentence to the sentences
     elsewhere that qualify it ("unless", "except", "does not apply", "as
     described in section X", "is a Protocol Error").
   - Show those sentences with the requirement.
   - Changing the index format means regenerating and reviewing both indexes.
4. **Cut tokens on simple questions, and claude-sonnet-5.5's cost.**
   - Return a compact pack when one section clearly dominates the ranking.
   - Look into why claude-sonnet-5.5's cache writes outweigh its cache reads.
5. **Validate on fresh questions and a realistic agent setup.**
   - Write corpus 1.2 with new test questions through the same clean-room
     process (`BRIEF.md` in the corpus repository), register it, and run a
     release-3 plan.
   - Separately, check the server in a realistic agent setup, such as Copilot CLI
     or Claude Code with their own file and web tools. The benchmark harness
     controls the toolset tightly.

## Working on it

- **Setup.** Run `uv sync --extra benchmark`. `uv add` and `uv remove` re-sync
  without the extra, so run it again after changing dependencies.
- **Authentication.** The Copilot SDK uses the `gh` login. "No GitHub OAuth token or
  Copilot HMAC key provided" means it has expired: run `gh auth refresh -h github.com`.
- **Dev pass.** `mcp` and `grep` side by side, five models, one epoch:

  ```bash
  uv run mqtt-spec-bench-copilot --corpus independent --conditions mcp grep \
      --models claude-sonnet-5.5 gpt-6.1-sol claude-haiku-5.5 gpt-6-luna grok-4.7 \
      --concurrency 16 --output benchmarks/runs/independent/dev/passN.jsonl
  uv run mqtt-spec-bench-judge benchmarks/runs/independent/dev/passN.jsonl \
      --judge-models claude-sonnet-5 gpt-5.6-sol --concurrency 10
  uv run mqtt-spec-bench-report benchmarks/runs/independent/dev/passN.jsonl \
      --judge-models claude-sonnet-5 gpt-5.6-sol --baseline grep
  ```

- **Release run.**
  1. `mqtt-spec-bench-plan init NAME ...`, then edit the plan's checks.
  2. Commit the plan, and confirm `mqtt-spec-bench-plan check` reports no problems.
  3. Run `mqtt-spec-bench-copilot --plan`, then `mqtt-spec-bench-judge --plan`, then
     `mqtt-spec-bench-report --plan`.

  Any run or judge command resumes when repeated, and retries failures.
- **Cost and time.**

  | | premium requests | wall time |
  | --- | --- | --- |
  | dev pass | about 2,400 to 2,550 to run, about 1,480 to judge | about 25 minutes at concurrency 16, plus about 15 for judging |
  | release run | about 11,300 to run, about 6,900 to judge | about an hour, plus judging |

  A dev pass resolves quality differences of about ±2.5 points on complex questions,
  so effects of about one point need the test split's two epochs, or several dev
  passes.
- **Rules.**
  - Compare conditions only within runs made side by side.
  - Never read `test.jsonl` outside a plan.
  - Judges must not be among the models under test.
  - Timing is directional only (uncontrolled environment).

## Analysis methods

These rebuild the analyses cited in DECISIONS.

- **Replay.** For a run record, build `ToolSession(tools_for(record.condition,
  case.versions))` and invoke the recorded tool calls in order. This reproduces
  exactly what the model saw, provided `server_hash()` equals the record's
  `hashes["reference"]`.
- **Deciding sentence.** Split a fact's quote into sentences and take the one that
  shares the most content words with the fact's statement.
- **Loss classes.** A loss is a fact that either judge marked contradicted, or that
  both marked absent. It is:
  - "rule seen" if the deciding sentence's words appear in order in the replayed
    tool output;
  - "excerpted, rule cut" if a heading of one of the fact's sections appeared;
  - "not shown" otherwise.
- **Fetch usefulness.** A fetch is useful if it shows a fact's deciding sentence for
  the first time in the run. To test it, check whether the same model was credited
  with that fact on the same question in a pass where it rarely fetched.
- **Judge calibration.** Run both judges on every answer. Adjudicate every
  disagreement, plus a sample of agreements, with agents that see only the
  question, the answer key and the answer. Then compare each judge with the
  adjudication.
