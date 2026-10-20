# Independent corpus, release 1: five models, `mcp` against `grep`

2026-10-20. Plan [release-1](../plans/release-1.toml) (`release-1@c5c6f6462fc2`), server
commit 4bd4586, corpus `independent` 1.1.0.

**Decision: FAIL.** The plan required `mcp` to beat `grep` on both checks, each judged by
the lower bound of a 95% confidence interval pooled over the five models. Both checks
were fixed before any test answer existed.

| check | result | 95% CI lower bound | needed |
| --- | --- | ---: | ---: |
| better answers than `grep` (quality delta) | fail | −1.1 points | > 0 |
| fewer tokens than `grep` (token reduction) | pass | 36.3% | > 0 |

On the 165 sealed test questions, `mcp` answered as well as `grep`, not better:
- quality −0.2 points (CI −1.1 to +0.6);
- 39% fewer tokens (CI 36% to 42%);
- 19% fewer price-weighted tokens;
- 1.31× faster;
- passed 4.3 points more often (CI +2.0 to +6.7).

Both conditions found the same facts (fact recall 0.959 and 0.960). `mcp` cited them
better, but it contradicted a fact or made a wrong claim about twice as often, and
that cancelled the citation gain in the quality score.

## What was measured

- **Corpus.** `independent` 1.1.0: 236 questions written from the OASIS texts by a
  separate session whose brief barred it from this server, its benchmark and any other
  MQTT question-answering tool.
  - Each case states its facts in plain language with a verbatim quote and may list
    wrong claims to watch for.
  - It is split 71 `dev` / 165 `test`. The test split was read only under the plan, and
    only aggregates were seen.
  - Test mix: 42 MQTT 3.1.1, 85 MQTT 5.0 and 38 comparison questions; 58 synthesis, 43
    scenario, 29 edge, 14 lookup, 14 rule and 7 "silent" (where the specification
    defines nothing); 105 inspired by public problem reports and 60 written from the
    specification.
- **Conditions,** run side by side with the same prompts and a 12-call tool budget:
  - `mcp`: this server's tools;
  - `grep`: a line search and an 80-line reader over the same specification text.

  Every question was sent labelled with its version(s).
- **Models,** through the GitHub Copilot SDK, two epochs each: claude-sonnet-5.5,
  gpt-6.1-sol, claude-haiku-5.5, gpt-6-luna and grok-4.7. That is 3,300 runs, all
  successful.
- **Judging.** A panel of claude-sonnet-5 and gpt-5.6-sol, neither under test, graded
  every answer blind (judge prompt version 2), each fact present/absent/contradicted
  and each wrong claim made/not made. An answer's score is the mean of the two judges'
  scores. `quality = clamp(0.8 × fact recall + 0.2 × citation − 0.5 × errors, 0, 1)`,
  where errors are contradicted facts plus wrong claims made. An answer passes when
  every essential fact is present and cited and there are no errors.
- **Statistics.** Intervals come from a seeded bootstrap that resamples questions,
  keeping each question's ten pairs (5 models × 2 epochs) together.
- **Price weighting** uses each model's own token prices as listed by the SDK on the
  day: cache reads, cache writes and output are weighted relative to uncached input.

## Test results (sealed split, aggregates only)

| model | condition | quality | pass rate | fact recall | runs with an error | tokens/answer | price-weighted tokens/answer | model calls | latency (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude-haiku-5.5 | `grep` | 0.912 | 65.1% | 0.934 | 3.6% | 16,048 | 14,595 | 3.97 | 12.0 |
| claude-haiku-5.5 | `mcp` | 0.919 | 75.9% | 0.942 | 5.1% | 11,509 | 14,023 | 2.15 | 8.6 |
| claude-sonnet-5.5 | `grep` | 0.930 | 67.0% | 0.950 | 2.3% | 9,688 | 8,118 | 3.36 | 8.8 |
| claude-sonnet-5.5 | `mcp` | 0.924 | 75.6% | 0.947 | 4.5% | 9,019 | 10,007 | 2.01 | 7.1 |
| gpt-6-luna | `grep` | 0.942 | 72.6% | 0.948 | 0.8% | 17,833 | 12,207 | 4.96 | 12.1 |
| gpt-6-luna | `mcp` | 0.943 | 75.4% | 0.948 | 1.2% | 7,945 | 9,226 | 2.48 | 9.0 |
| gpt-6.1-sol | `grep` | 0.982 | 87.6% | 0.985 | 0.1% | 9,784 | 8,393 | 3.71 | 13.1 |
| gpt-6.1-sol | `mcp` | 0.974 | 87.6% | 0.981 | 1.1% | 6,734 | 7,417 | 2.45 | 11.5 |
| grok-4.7 | `grep` | 0.981 | 88.8% | 0.981 | 0.1% | 35,655 | 24,564 | 4.88 | 29.4 |
| grok-4.7 | `mcp` | 0.975 | 88.2% | 0.980 | 1.1% | 18,813 | 14,584 | 3.00 | 21.1 |

"Runs with an error" is the share of judge verdicts finding a contradicted fact or a
wrong claim. "Model calls" is model requests per answer, each one premium request.

| model | slice | pairs | quality delta, points | pass-rate delta, points | token reduction, % | price-weighted token reduction, % | speedup |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| claude-haiku-5.5 | all | 330 | +0.7 (-2.0 to +3.2) | +10.8 (+5.5 to +15.9) | +28.3 (+21.3 to +34.4) | +3.9 (-0.7 to +8.3) | 1.39× (1.34 to 1.43) |
| claude-sonnet-5.5 | all | 330 | -0.6 (-2.9 to +1.6) | +8.6 (+2.6 to +14.2) | +6.9 (-0.9 to +13.8) | -23.3 (-30.9 to -16.4) | 1.24× (1.21 to 1.27) |
| gpt-6-luna | all | 330 | +0.0 (-1.5 to +1.5) | +2.9 (-2.3 to +7.9) | +55.5 (+51.5 to +59.1) | +24.4 (+21.1 to +27.6) | 1.35× (1.26 to 1.48) |
| gpt-6.1-sol | all | 330 | -0.8 (-2.2 to +0.1) | +0.0 (-3.6 to +3.8) | +31.2 (+22.7 to +38.4) | +11.6 (+7.4 to +15.5) | 1.14× (1.09 to 1.18) |
| grok-4.7 | all | 330 | -0.6 (-1.5 to +0.2) | -0.6 (-3.9 to +2.7) | +47.2 (+42.8 to +51.1) | +40.6 (+36.5 to +44.0) | 1.39× (1.28 to 1.52) |
| all models | all | 1650 | -0.2 (-1.1 to +0.6) | +4.3 (+2.0 to +6.7) | +39.3 (+36.3 to +42.0) | +18.6 (+15.9 to +21.1) | 1.31× (1.27 to 1.37) |
| all models | 3.1.1 | 420 | +1.2 (-0.1 to +2.5) | +7.3 (+4.4 to +10.2) | +37.2 (+30.3 to +42.8) | +15.1 (+9.1 to +20.4) | 1.32× (1.26 to 1.38) |
| all models | 5.0 | 850 | -1.4 (-2.8 to -0.3) | +1.7 (-2.0 to +5.5) | +43.5 (+40.1 to +46.7) | +24.1 (+21.0 to +26.8) | 1.23× (1.18 to 1.27) |
| all models | both | 380 | +0.9 (-0.3 to +2.2) | +7.0 (+3.0 to +11.3) | +31.4 (+25.1 to +36.7) | +10.3 (+4.3 to +15.7) | 1.49× (1.37 to 1.64) |
| all models | kind=edge | 290 | +1.2 (-0.3 to +2.9) | +6.0 (-0.7 to +14.0) | +40.6 (+34.9 to +46.0) | +19.5 (+13.9 to +24.1) | 1.34× (1.19 to 1.53) |
| all models | kind=lookup | 140 | +0.6 (+0.0 to +1.6) | +3.2 (+0.0 to +8.2) | +16.2 (+7.8 to +23.5) | -9.5 (-19.3 to -0.8) | 1.32× (1.25 to 1.39) |
| all models | kind=rule | 140 | +0.6 (-0.1 to +1.5) | +1.4 (-0.7 to +4.3) | +33.9 (+20.9 to +45.0) | +12.4 (+1.7 to +21.1) | 1.31× (1.14 to 1.49) |
| all models | kind=scenario | 430 | +0.4 (-1.1 to +1.8) | +6.7 (+2.0 to +11.2) | +39.2 (+32.4 to +45.1) | +16.3 (+9.1 to +22.2) | 1.34× (1.26 to 1.41) |
| all models | kind=silent | 70 | -0.6 (-4.3 to +2.1) | +2.9 (-7.1 to +13.6) | +46.5 (+34.7 to +56.1) | +21.9 (+12.3 to +32.3) | 1.24× (1.13 to 1.37) |
| all models | kind=synthesis | 580 | -1.8 (-3.6 to -0.2) | +2.8 (-1.1 to +6.7) | +41.1 (+36.6 to +45.2) | +22.9 (+19.4 to +26.3) | 1.30× (1.23 to 1.38) |
| all models | origin=real-world | 1050 | +0.5 (-0.3 to +1.3) | +6.8 (+4.0 to +9.5) | +39.4 (+35.9 to +42.5) | +18.4 (+14.9 to +21.5) | 1.33× (1.27 to 1.40) |
| all models | origin=spec | 600 | -1.6 (-3.2 to +0.0) | +0.0 (-4.0 to +4.0) | +39.2 (+34.0 to +44.0) | +19.0 (+14.1 to +23.3) | 1.28× (1.22 to 1.35) |

Positive deltas favour `mcp`. Each model row pairs 165 questions × 2 epochs; the pooled
rows pair all five models and resample questions.

### Why quality tied while the pass rate rose

| condition | fact recall | citation recall | every essential fact cited | errors per answer | answers contradicting a fact | answers making a wrong claim |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mcp` | 0.959 | 0.968 | 89.3% | 0.030 | 3.6% | 0.6% |
| `grep` | 0.960 | 0.945 | 83.4% | 0.015 | 2.2% | 0.2% |

- **Better citations.** `mcp` answers cite the section behind each fact more often,
  because its evidence carries section numbers. Passing needs every essential fact
  cited, so the pass rate rises.
- **Twice the errors,** in every model. Each error costs 0.5, which cancels the citation
  gain in the quality score.
- **What the errors look like.** The ones inspected on the dev split were of two
  kinds:
  - concluding from excerpts that the specification leaves something open when a
    sentence elsewhere requires it ("the excerpts do not explicitly say…");
  - getting a detail of a rule wrong.

  With `grep` the model reads the surrounding text in 80-line pages.
- **Where `mcp` falls behind:** MQTT 5.0 questions (−1.4 points, CI −2.8 to −0.3)
  and synthesis questions (−1.8, CI −3.6 to −0.2). Both need rules from several
  sections. These slices were not part of the plan's decision, and with eleven
  slices one could look significant by chance.
- **Where it is ahead,** though the quality difference isn't significant: MQTT 3.1.1
  (+1.2) and comparison questions (+0.9), with clearly higher pass rates (+7.3 and
  +7.0 points).
- **Price-weighted cost.** claude-sonnet-5.5 answers with `grep` in about three cheap
  calls. With `mcp`, its evidence pack is written to the prompt cache at 1.25× the
  input price and barely read back in a two-call session. So `mcp` costs it 23% more
  once priced, although it uses 7% fewer tokens. For the other four models `mcp` is
  cheaper once priced: by 41% (grok-4.7), 24% (gpt-6-luna), 12% (gpt-6.1-sol) and
  4% (claude-haiku-5.5, not significant).
- **Answer length.** The Claude models went over the word limit in 78% to 91% of
  answers in both conditions. Length is not scored.

## Dev split (diagnosis): 71 questions, one epoch

| model | condition | quality | pass rate | fact recall | runs with an error | tokens/answer | price-weighted tokens/answer | model calls | latency (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude-haiku-5.5 | `grep` | 0.883 | 61.3% | 0.904 | 4.9% | 16,244 | 15,514 | 4.06 | 12.9 |
| claude-haiku-5.5 | `mcp` | 0.887 | 71.1% | 0.921 | 8.5% | 10,800 | 13,996 | 2.10 | 8.9 |
| claude-sonnet-5.5 | `full_spec` | 0.990 | 94.4% | 0.987 | 0.0% | 99,088 | 25,955 | 1.00 | 6.1 |
| claude-sonnet-5.5 | `grep` | 0.907 | 57.8% | 0.916 | 1.4% | 9,121 | 7,988 | 3.24 | 8.9 |
| claude-sonnet-5.5 | `mcp` | 0.913 | 72.5% | 0.933 | 4.9% | 9,044 | 10,175 | 2.00 | 7.4 |
| claude-sonnet-5.5 | `none` | 0.812 | 50.0% | 0.849 | 10.6% | 1,213 | 4,399 | 1.00 | 8.0 |
| gpt-6-luna | `grep` | 0.921 | 68.3% | 0.928 | 2.1% | 19,379 | 12,799 | 5.14 | 12.4 |
| gpt-6-luna | `mcp` | 0.935 | 71.1% | 0.941 | 1.4% | 8,841 | 9,959 | 2.55 | 10.5 |
| gpt-6.1-sol | `grep` | 0.982 | 87.3% | 0.982 | 0.0% | 9,364 | 8,405 | 3.68 | 13.5 |
| gpt-6.1-sol | `mcp` | 0.970 | 85.9% | 0.977 | 1.4% | 6,982 | 7,617 | 2.46 | 12.0 |
| grok-4.7 | `grep` | 0.973 | 90.8% | 0.977 | 1.4% | 36,126 | 24,835 | 4.79 | 30.1 |
| grok-4.7 | `mcp` | 0.984 | 91.5% | 0.986 | 0.7% | 21,283 | 17,525 | 3.24 | 26.1 |

| model | slice | pairs | quality delta, points | pass-rate delta, points | token reduction, % | price-weighted token reduction, % | speedup |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| claude-haiku-5.5 | all | 71 | +0.3 (-6.5 to +6.6) | +9.9 (-2.8 to +21.8) | +33.5 (+25.5 to +39.8) | +9.8 (+3.4 to +15.2) | 1.46× (1.38 to 1.53) |
| claude-sonnet-5.5 | all | 71 | +0.7 (-4.2 to +4.9) | +14.8 (+2.8 to +26.8) | +0.9 (-9.8 to +10.2) | -27.4 (-36.8 to -18.3) | 1.20× (1.15 to 1.24) |
| gpt-6-luna | all | 71 | +1.4 (-1.8 to +4.8) | +2.8 (-6.3 to +12.0) | +54.4 (+43.1 to +63.2) | +22.2 (+14.2 to +29.2) | 1.18× (1.07 to 1.31) |
| gpt-6.1-sol | all | 71 | -1.2 (-3.7 to +0.3) | -1.4 (-6.3 to +2.8) | +25.4 (+14.0 to +35.1) | +9.4 (+3.6 to +15.1) | 1.13× (1.07 to 1.19) |
| grok-4.7 | all | 71 | +1.1 (-1.0 to +3.5) | +0.7 (-6.3 to +7.8) | +41.1 (+33.1 to +47.7) | +29.4 (+21.6 to +36.1) | 1.15× (1.02 to 1.30) |
| all models | all | 355 | +0.4 (-1.7 to +2.4) | +5.3 (+0.6 to +10.3) | +36.9 (+32.2 to +40.8) | +14.8 (+10.3 to +18.5) | 1.20× (1.13 to 1.27) |
| all models | 3.1.1 | 90 | +2.8 (-0.5 to +5.7) | +13.9 (+6.1 to +21.7) | +27.5 (+19.4 to +34.1) | +9.2 (+0.2 to +16.2) | 1.27× (1.10 to 1.43) |
| all models | 5.0 | 185 | -0.8 (-4.1 to +2.2) | +0.5 (-5.7 to +6.8) | +40.9 (+34.5 to +46.1) | +20.2 (+14.8 to +25.0) | 1.16× (1.07 to 1.25) |
| all models | both | 80 | +0.8 (-3.1 to +4.3) | +6.9 (-3.8 to +17.5) | +34.1 (+24.0 to +42.7) | +7.8 (-1.5 to +15.9) | 1.23× (1.09 to 1.37) |
| all models | kind=edge | 60 | -4.5 (-10.1 to -0.3) | -2.5 (-7.5 to +2.5) | +38.9 (+28.4 to +48.0) | +15.2 (+5.7 to +25.0) | 1.23× (1.09 to 1.38) |
| all models | kind=lookup | 30 | +1.0 (+0.0 to +2.3) | +5.0 (+0.0 to +11.7) | +15.1 (+0.9 to +25.1) | -8.2 (-23.3 to +1.1) | 1.33× (1.22 to 1.41) |
| all models | kind=rule | 30 | -0.6 (-2.0 to +0.3) | +0.0 (-10.0 to +10.0) | +12.4 (-0.4 to +21.4) | -17.5 (-32.5 to -3.7) | 1.04× (0.95 to 1.14) |
| all models | kind=scenario | 90 | +5.1 (+1.3 to +9.4) | +13.9 (+5.0 to +22.8) | +36.1 (+24.5 to +46.0) | +13.3 (+2.4 to +22.7) | 1.19× (1.06 to 1.32) |
| all models | kind=silent | 20 | +2.0 (+0.0 to +5.8) | -2.5 (-15.0 to +7.5) | +14.8 (-12.3 to +35.0) | +1.5 (-16.3 to +20.1) | 0.94× (0.67 to 1.52) |
| all models | kind=synthesis | 125 | -0.6 (-4.5 to +3.0) | +5.6 (-4.8 to +16.4) | +44.0 (+40.0 to +48.2) | +23.0 (+19.4 to +26.3) | 1.25× (1.14 to 1.36) |
| all models | origin=real-world | 225 | +1.1 (-1.5 to +3.3) | +7.8 (+2.2 to +13.3) | +37.8 (+31.8 to +43.4) | +14.2 (+8.5 to +19.8) | 1.26× (1.17 to 1.35) |
| all models | origin=spec | 130 | -0.6 (-4.2 to +3.2) | +1.1 (-6.9 to +9.6) | +35.4 (+27.9 to +41.5) | +15.7 (+9.3 to +20.9) | 1.11× (1.00 to 1.22) |

The dev split agreed with the test split: a tie on quality (+0.4 points, CI −1.7 to
+2.4), a higher pass rate and about 37% fewer tokens. It also measured two reference
conditions on claude-sonnet-5.5:
- **`none`** (no reference material) scored 0.812: these models know a good deal of
  MQTT, and retrieval adds about 10 points.
- **`full_spec`** (the whole specification in the prompt, about 99,000 tokens per
  answer) scored 0.990, the ceiling.

### What the dev pass changed before the test run

- **Comparison questions are labelled.** They were sent without a version label. This
  corpus's comparison prompts say "both versions" or "the older protocol" rather than
  naming them. Under `mcp`, gpt-6.1-sol followed the server's instruction to ask when
  the user names no version, and asked "Which MQTT version should I cover?" on 3 of
  16 such questions, scoring 0 on each. Every question is now sent with its version
  label, and the label is part of the case hash. That harness change, not a server
  change, raised gpt-6.1-sol's `mcp` score on dev from about 0.91 to 0.95.
- **Judge calibration, round 1.** Two candidate judges graded all 852 dev answers and
  agreed on 94.8% of 3,096 fact verdicts. Blind adjudicators re-graded every answer
  they disagreed on (141) and 40 they agreed on; the adjudicators saw only the
  question, the answer key and the answer.
  - Where the judges agreed, the adjudicators agreed 98.6% of the time.
  - Where they disagreed, the adjudicators sided with claude-sonnet-5 83.7% of the
    time and gpt-5.6-sol 83.4%.
  - The judges leaned in opposite directions. claude-sonnet-5 scored disputed `grep`
    answers above the adjudicators, and gpt-5.6-sol scored them below. Alone they
    gave pooled deltas of −0.4 and +1.0 points.
- **Judge prompt version 2.** The adjudicators themselves split on facts stated more
  widely than the question needs, such as a packet-identifier rule listing three
  packets in a question about one. Version 2 counts such a fact as present when the
  answer states it fully for the cases asked about. Agreement rose to 95.7%
  (kappa 0.65), and the judges' pooled dev deltas came within 0.3 points of each
  other.
- **The panel.** Using both judges and averaging their scores cancels their leanings
  instead of choosing one.

## Cost

| stage | premium requests |
| --- | ---: |
| dev runs (1,044, including 192 re-run after the label fix) | 3,074 |
| dev judging (two judges, prompt versions 1 and 2) | 3,918 |
| test runs (3,300) | 10,880 |
| test judging (two judges × 3,300 answers) | 6,897 |
| **total** | **24,769** |

On top of that came eight blind adjudication agents. All models cost one premium
request per call; `grep` averaged 4.2 calls per answer, and `mcp` 2.4.

## Caveats

- **One corpus,** written by one AI author session from the specification text and
  real-world reports. Its answer keys were reviewed by that session, not by people.
- **AI judges.** The judges and adjudicators are language models. On disputed facts
  the adjudicators were right only by their own reading, and they too split on edge
  cases. Where the two judges agree (about 96% of fact verdicts), adjudicators agreed
  98.6% to 100% of the time.
- **Prices** are the SDK's listed token prices on 2026-10-20.
- **One harness.** The results are from the Copilot SDK harness and its prompts; other
  agent harnesses may use the tools differently.

## Reproduce

```bash
uv run mqtt-spec-bench-corpus register independent ../mqtt-spec-corpus
uv run mqtt-spec-bench-plan check benchmarks/plans/release-1.toml
uv run mqtt-spec-bench-copilot --plan benchmarks/plans/release-1.toml --concurrency 16
uv run mqtt-spec-bench-judge --plan benchmarks/plans/release-1.toml --concurrency 10
uv run mqtt-spec-bench-report --plan benchmarks/plans/release-1.toml
```
