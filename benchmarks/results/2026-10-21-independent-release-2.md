# Independent corpus, release 2: excerpt notes and capped fetching

2026-10-21. Plan [release-2](../plans/release-2.toml) (`release-2@a91f8a94e0b8`), server
commit 10402f0, corpus `independent` 1.1.0 test split: the same 165 sealed questions
as [release 1](2026-10-20-independent-release-1.md).

**Decision: PASS.** All three checks were fixed before any release-2 answer existed;
each compares the lower bound of a 95% confidence interval, pooled over the five
models:

| check | result | 95% CI lower bound | needed |
| --- | --- | ---: | ---: |
| better answers than `grep` on complex questions (synthesis, scenario, edge; 130 questions) | pass | +0.19 points | > 0 |
| no more than 1 point worse than `grep` on simple questions (lookup, rule; 28 questions) | pass | −0.69 points | > −1 |
| fewer tokens than `grep` | pass | 19.2% | > 0 |

Against `grep`, pooled over five models and two epochs:
- quality: +0.9 points (CI +0.2 to +1.7);
- complex questions: +1.1 (CI +0.2 to +2.1);
- pass rate: +6.4 points (CI +3.9 to +9.0);
- tokens: 23% fewer (CI 19% to 27%).

Once each model's token prices are applied, the two conditions cost about the same
(`mcp` 2.8% cheaper, CI −0.5% to +5.8%). The complex-question check passed by a
small margin.

## What changed since release 1

Release 1 tied `grep` on quality. `mcp` cited sources better but made twice as many
errors, which cancelled the citation gain. Work on the corpus's dev split (71
questions, never the test split) traced the errors and changed only what the agent
is told; ranking and excerpts are unchanged:

- **Excerpt notes.** A search result heading ends with `(excerpt: N of M characters)`
  when it shows less than 95% of the section. On dev, 8 of 21 `mcp` errors had their
  deciding sentence in a section the pack had cut. Agents had been told to answer
  from the excerpts and not search again.
- **Fetch before claiming silence.** Before saying MQTT does not require, define or
  mention something, leaves it open or optional, or that a rule has no exception,
  the agent fetches the section that would decide it, if that section is excerpted
  or not shown.
- **Caps.** Unmarked sections are complete; at most one fetch and one repeat search
  per version.
  - A first, broad version of the fetch rule kept the quality gain on dev, but the
    GPT and Grok models then fetched in up to 96% of answers, and `mcp` lost its
    token advantage.
  - Replaying those runs showed that fetches of complete sections and cursor pages
    never helped. The facts the GPT and Grok models' fetches surfaced had also been
    answered correctly when they rarely fetched.

The dev passes are recorded in [docs/DECISIONS.md](../../docs/DECISIONS.md).

## Release 1 against release 2 (same sealed questions)

| questions | release-1 quality delta | release-2 quality delta | release-1 tokens saved | release-2 tokens saved |
| --- | --- | --- | --- | --- |
| all questions | -0.2 (-1.1 to +0.6) | +0.9 (+0.2 to +1.7) | +39.3 (+36.3 to +42.0) | +23.3 (+19.2 to +26.9) |
| complex: synthesis, scenario, edge | -0.4 (-1.4 to +0.5) | +1.1 (+0.2 to +2.1) | +40.5 (+37.4 to +43.3) | +25.2 (+20.9 to +29.0) |
|   synthesis | -1.8 (-3.6 to -0.2) | +0.9 (-0.8 to +2.6) | +41.1 (+36.6 to +45.2) | +28.1 (+21.8 to +33.8) |
|   scenario | +0.4 (-1.1 to +1.8) | +1.7 (+0.2 to +3.3) | +39.2 (+32.4 to +45.1) | +21.7 (+14.6 to +27.4) |
|   edge | +1.2 (-0.3 to +2.9) | +0.8 (-0.7 to +2.4) | +40.6 (+34.9 to +46.0) | +22.1 (+13.4 to +30.2) |
| simple: lookup, rule | +0.6 (+0.1 to +1.3) | +0.1 (-0.7 to +0.9) | +27.1 (+18.2 to +35.7) | +6.2 (-8.1 to +18.3) |
| silent | -0.6 (-4.3 to +2.1) | +0.1 (-1.7 to +1.8) | +46.5 (+34.7 to +56.1) | +25.7 (+11.7 to +38.8) |
| MQTT 3.1.1 | +1.2 (-0.1 to +2.5) | +0.8 (-0.8 to +2.3) | +37.2 (+30.3 to +42.8) | +23.2 (+17.2 to +28.5) |
| MQTT 5.0 | -1.4 (-2.8 to -0.3) | +0.7 (-0.5 to +1.8) | +43.5 (+40.1 to +46.7) | +27.4 (+22.8 to +31.5) |
| comparisons (both) | +0.9 (-0.3 to +2.2) | +1.7 (+0.4 to +3.2) | +31.4 (+25.1 to +36.7) | +14.4 (+2.7 to +23.6) |

Quality deltas are in points, token reductions in percent; positive favours `mcp`.

- **Synthesis questions** moved from −1.8 to +0.9 and **MQTT 5.0 questions** from
  −1.4 to +0.7: the two slices where `mcp` trailed in release 1.
- **Errors.** `mcp`'s errors per answer fell from 0.030 to 0.023; `grep`'s were
  0.015 and 0.019. The gap fell from 2× to about 1.2×.
- **The price:** `mcp` now makes more model calls (2.65 per answer, against 2.42),
  so it saves 23% of `grep`'s tokens instead of 39%. `grep` used 4.19 calls.

## Test results (aggregates only)

| model | condition | quality | pass rate | fact recall | answers with an error | tokens/answer | price-weighted tokens/answer | model calls |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude-haiku-5.5 | `grep` | 0.913 | 68.3% | 0.935 | 4.2% | 17,254 | 15,083 | 4.12 |
| claude-haiku-5.5 | `mcp` | 0.918 | 76.1% | 0.943 | 5.8% | 12,470 | 14,969 | 2.20 |
| claude-sonnet-5.5 | `grep` | 0.926 | 66.7% | 0.947 | 2.9% | 9,677 | 8,083 | 3.34 |
| claude-sonnet-5.5 | `mcp` | 0.957 | 81.8% | 0.968 | 2.1% | 10,488 | 10,687 | 2.14 |
| gpt-6-luna | `grep` | 0.924 | 66.4% | 0.931 | 0.9% | 17,842 | 12,023 | 5.08 |
| gpt-6-luna | `mcp` | 0.938 | 73.8% | 0.944 | 1.4% | 10,948 | 11,174 | 2.87 |
| gpt-6.1-sol | `grep` | 0.979 | 87.1% | 0.984 | 0.4% | 9,502 | 8,458 | 3.68 |
| gpt-6.1-sol | `mcp` | 0.975 | 87.9% | 0.977 | 0.6% | 8,279 | 8,264 | 2.68 |
| grok-4.7 | `grep` | 0.981 | 89.7% | 0.982 | 0.3% | 34,143 | 23,050 | 4.75 |
| grok-4.7 | `mcp` | 0.981 | 90.8% | 0.986 | 0.9% | 25,601 | 19,751 | 3.38 |

| model | quality delta, points | pass-rate delta, points | token reduction, % | price-weighted token reduction, % |
| --- | --- | --- | --- | --- |
| claude-haiku-5.5 | +0.5 (-2.0 to +2.9) | +7.7 (+2.1 to +13.0) | +27.7 (+20.7 to +34.0) | +0.8 (-3.9 to +5.5) |
| claude-sonnet-5.5 | +3.1 (+1.1 to +5.0) | +15.2 (+9.4 to +21.1) | -8.4 (-17.9 to +0.5) | -32.2 (-39.5 to -25.5) |
| gpt-6-luna | +1.4 (-0.4 to +3.1) | +7.4 (+1.8 to +13.0) | +38.6 (+34.1 to +43.0) | +7.1 (+3.4 to +10.8) |
| gpt-6.1-sol | -0.4 (-1.4 to +0.6) | +0.8 (-3.3 to +5.0) | +12.9 (+5.3 to +19.1) | +2.3 (-2.0 to +6.3) |
| grok-4.7 | +0.1 (-0.8 to +0.9) | +1.1 (-2.4 to +4.5) | +25.0 (+17.4 to +31.5) | +14.3 (+8.4 to +19.5) |
| all models | +0.9 (+0.2 to +1.7) | +6.4 (+3.9 to +9.0) | +23.3 (+19.2 to +26.9) | +2.8 (-0.5 to +5.8) |

- **claude-sonnet-5.5 gained most:** +3.1 points (CI +1.1 to +5.0) and +15.2 points
  of pass rate. It also became more expensive with `mcp`: 8% more tokens than with
  `grep`, 32% more once priced, because its evidence is written to the prompt cache
  and barely reused in a two-call session.
- **The other four** saved 13% to 39% of `grep`'s tokens. Their quality deltas are
  within ±1.4 points of zero individually.

## Cost

| stage | premium requests |
| --- | ---: |
| dev passes 2 to 5 (710 runs each, mcp and grep, 5 models) | 9,851 |
| dev judging (two judges) | 5,922 |
| release-2 test runs (3,300, plus 2 timeouts retried) | 11,297 |
| release-2 test judging (two judges × 3,300 answers) | 6,869 |
| **total** | **33,939** |

## Caveats

- **Reused test split.** It is the split release 1 ran on. Only its aggregates had
  been seen, but release 1's per-kind aggregates (synthesis −1.8) shaped the choice
  of a complex-question decision rule. All changes were designed on the dev split.
- **Narrow pass.** The complex-question check's lower bound is +0.19 points. A rerun
  could fall either side of zero.
- **Judges.** The judges are AI models (calibrated on dev against blind
  adjudication; see release 1). The corpus was written by one AI author session.
- **Timing is directional only** (uncontrolled environment), and no check uses it.

## Reproduce

```bash
uv run mqtt-spec-bench-plan check benchmarks/plans/release-2.toml
uv run mqtt-spec-bench-copilot --plan benchmarks/plans/release-2.toml --concurrency 16
uv run mqtt-spec-bench-judge --plan benchmarks/plans/release-2.toml --concurrency 10
uv run mqtt-spec-bench-report --plan benchmarks/plans/release-2.toml
```
